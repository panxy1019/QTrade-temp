from __future__ import annotations

from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import datetime
from decimal import ROUND_HALF_UP, Decimal
from enum import StrEnum
from hashlib import sha256
from uuid import uuid4

from qtrade.accounting.ledger import Ledger
from qtrade.audit.log import AuditEvent, AuditLog
from qtrade.domain.models import Order, OrderStatus, Side, Trade, utc_now
from qtrade.execution.reservations import ReservationBook
from qtrade.oms.manager import OrderManager
from qtrade.persistence import SQLiteOperationalJournal


class CommissionPolicy(StrEnum):
    ALL_IN = "ALL_IN"
    DECOMPOSED = "DECOMPOSED"


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    version: str
    commission_rate: Decimal
    sell_tax_rate: Decimal
    minimum_commission: Decimal = Decimal("0")
    commission_policy: CommissionPolicy = CommissionPolicy.ALL_IN
    exchange_handling_rate: Decimal = Decimal("0")
    regulatory_rate: Decimal = Decimal("0")
    transfer_rate: Decimal = Decimal("0")
    source_url: str = "configured://fee-schedule"

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("fee schedule version is required")
        values = (
            self.commission_rate,
            self.sell_tax_rate,
            self.minimum_commission,
            self.exchange_handling_rate,
            self.regulatory_rate,
            self.transfer_rate,
        )
        if any(not value.is_finite() or value < 0 for value in values):
            raise ValueError("fee values must be finite and nonnegative")
        if not self.source_url:
            raise ValueError("fee source is required")
        if self.commission_policy is CommissionPolicy.ALL_IN and (
            self.exchange_handling_rate or self.regulatory_rate
        ):
            raise ValueError("all-in commission cannot add handling or regulatory fees")

    def calculate(self, side: Side, price: Decimal, quantity: int) -> Decimal:
        return self.breakdown(side, price, quantity).total

    def breakdown(self, side: Side, price: Decimal, quantity: int) -> FeeBreakdown:
        notional = price * quantity
        commission = max(notional * self.commission_rate, self.minimum_commission)
        handling = (
            notional * self.exchange_handling_rate
            if self.commission_policy is CommissionPolicy.DECOMPOSED
            else Decimal("0")
        )
        regulatory = (
            notional * self.regulatory_rate
            if self.commission_policy is CommissionPolicy.DECOMPOSED
            else Decimal("0")
        )
        transfer = notional * self.transfer_rate
        tax = notional * self.sell_tax_rate if side is Side.SELL else Decimal("0")
        rounded = tuple(
            value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)
            for value in (commission, handling, regulatory, transfer, tax)
        )
        return FeeBreakdown(*rounded)


@dataclass(frozen=True, slots=True)
class FeeBreakdown:
    broker_commission: Decimal
    exchange_handling: Decimal
    regulatory: Decimal
    transfer: Decimal
    stamp_tax: Decimal

    @property
    def total(self) -> Decimal:
        return sum(
            (
                self.broker_commission,
                self.exchange_handling,
                self.regulatory,
                self.transfer,
                self.stamp_tax,
            ),
            start=Decimal("0"),
        )


class FillProcessingState(StrEnum):
    PENDING = "PENDING"
    APPLIED = "APPLIED"
    FAILED = "FAILED"


@dataclass(slots=True)
class FillRecord:
    state: FillProcessingState
    trade: Trade
    error: str | None = None


class PaperAction(StrEnum):
    ACCEPT = "ACCEPT"
    REJECT = "REJECT"
    DISCONNECT = "DISCONNECT"


class PaperGateway:
    """Simulation-only gateway. It contains no external transport."""

    external_order_transmission = False

    def __init__(
        self,
        oms: OrderManager,
        ledger: Ledger,
        audit: AuditLog,
        fees: FeeSchedule,
        reservations: ReservationBook | None = None,
        journal: SQLiteOperationalJournal | None = None,
        incident_handler: Callable[[str], None] | None = None,
        state_change_handler: Callable[[], None] | None = None,
        durable_fill_handler: Callable[[str, Trade, Order], bool] | None = None,
        fill_observer: Callable[[Trade], None] | None = None,
        observer_failure_handler: Callable[[], None] | None = None,
    ) -> None:
        self._oms = oms
        self._ledger = ledger
        self._audit = audit
        self._fees = fees
        self.reservations = reservations or ReservationBook()
        self._journal = journal
        self._fills: dict[str, FillRecord] = {}
        self._orders_by_client_id: dict[str, tuple[Order, tuple[object, ...]]] = {}
        self._cancel_results: dict[str, Order] = {}
        self.connected = True
        self._next_action = PaperAction.ACCEPT
        self._incident_handler = incident_handler
        self._state_change_handler = state_change_handler
        self._durable_fill_handler = durable_fill_handler
        self._fill_observer = fill_observer
        self._observer_failure_handler = observer_failure_handler

    def inject_next_action(self, action: PaperAction) -> None:
        self._next_action = action

    def submit(self, order: Order, *, now: datetime | None = None) -> Order:
        timestamp = now or utc_now()
        fingerprint = (
            order.request.symbol,
            order.request.side,
            order.request.price,
            order.request.quantity,
        )
        existing = self._orders_by_client_id.get(order.internal_order_id)
        if existing is not None:
            if existing[1] != fingerprint:
                raise ValueError("client order ID conflicts with prior submission")
            return existing[0]
        if not self.connected:
            raise ConnectionError("paper gateway disconnected")
        if order.status is not OrderStatus.RISK_PASSED:
            raise ValueError("only a risk-passed order can be submitted")
        action = self._next_action
        self._next_action = PaperAction.ACCEPT
        if action is PaperAction.DISCONNECT:
            self.connected = False
            self._report_incident("gateway disconnected during submission")
            raise ConnectionError("injected gateway disconnect")
        broker_order_id = f"PAPER-{uuid4()}"
        self._oms.transition(order, OrderStatus.SUBMITTED, now=timestamp)
        if action is PaperAction.REJECT:
            return self._oms.transition(
                order,
                OrderStatus.REJECTED,
                now=timestamp,
                reason="injected paper rejection",
                broker_order_id=broker_order_id,
            )
        self._oms.transition(
            order, OrderStatus.ACCEPTED, now=timestamp, broker_order_id=broker_order_id
        )
        self._audit.record(
            AuditEvent(
                event_type="PAPER_ORDER_ACCEPTED",
                service="paper_gateway",
                message=f"broker_order_id={broker_order_id}",
                order_id=order.internal_order_id,
                timestamp=timestamp,
            )
        )
        self._orders_by_client_id[order.internal_order_id] = (order, fingerprint)
        return order

    def lookup(self, client_order_id: str) -> Order | None:
        entry = self._orders_by_client_id.get(client_order_id)
        return None if entry is None else entry[0]

    def restore_submission(self, order: Order) -> None:
        if order.status is OrderStatus.SUBMISSION_UNKNOWN:
            raise ValueError("uncertain submissions require external reconciliation")
        if order.status in {
            OrderStatus.ACCEPTED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.FILLED,
            OrderStatus.CANCEL_PENDING,
            OrderStatus.CANCELLED,
            OrderStatus.REJECTED,
        }:
            fingerprint = (
                order.request.symbol,
                order.request.side,
                order.request.price,
                order.request.quantity,
            )
            self._orders_by_client_id[order.internal_order_id] = (order, fingerprint)

    def disconnect(self) -> None:
        self.connected = False
        self._report_incident("gateway disconnected")

    def reconnect(self) -> None:
        self.connected = True

    def _report_incident(self, reason: str) -> None:
        if self._incident_handler is not None:
            self._incident_handler(reason)

    def fill(
        self,
        order: Order,
        *,
        fill_key: str,
        price: Decimal,
        quantity: int,
        now: datetime | None = None,
    ) -> tuple[Trade, bool]:
        if not fill_key:
            raise ValueError("fill_key is required")
        existing = self._fills.get(fill_key)
        if existing is not None:
            if (
                existing.trade.internal_order_id != order.internal_order_id
                or existing.trade.price != price
                or existing.trade.quantity != quantity
            ):
                raise ValueError("fill_key reused with different fill data")
            if existing.state is FillProcessingState.APPLIED:
                return existing.trade, False
            raise RuntimeError(
                "fill processing is incomplete; reconciliation is required before replay"
            )
        if order.status not in {
            OrderStatus.ACCEPTED,
            OrderStatus.PARTIALLY_FILLED,
            OrderStatus.CANCEL_PENDING,
        }:
            raise ValueError("order cannot receive a fill in its current state")
        if quantity <= 0 or quantity > order.remaining_quantity:
            raise ValueError("invalid fill quantity")
        timestamp = now or utc_now()
        trade = Trade(
            trade_id=f"PAPER-TRADE-{uuid4()}",
            internal_order_id=order.internal_order_id,
            symbol=order.request.symbol,
            side=order.request.side,
            price=price,
            quantity=quantity,
            fee=self._fees.calculate(order.request.side, price, quantity),
            timestamp=timestamp,
        )
        payload_hash = sha256(f"{order.internal_order_id}|{price}|{quantity}".encode()).hexdigest()
        if self._journal is not None:
            created = self._journal.begin_fill(fill_key, order.internal_order_id, payload_hash)
            if not created:
                durable = self._journal.get_fill(fill_key)
                if durable is not None and durable.state == "APPLIED":
                    raise RuntimeError(
                        "durable fill was already applied; recover trade state before replay"
                    )
                raise RuntimeError(
                    "durable fill processing is incomplete; reconciliation is required"
                )
        record = FillRecord(FillProcessingState.PENDING, trade)
        self._fills[fill_key] = record
        try:
            projection_committed = False
            if self._durable_fill_handler is not None:
                projection_committed = self._durable_fill_handler(fill_key, trade, order)
            if not projection_committed:
                self._ledger.book(trade)
                self._oms.apply_fill(order, quantity, now=timestamp)
                self.reservations.consume(order.internal_order_id, quantity)
        except Exception as exc:
            durable_applied = False
            if self._journal is not None:
                durable = self._journal.get_fill(fill_key)
                if durable is not None and durable.state == "PENDING":
                    self._journal.mark_fill(fill_key, "FAILED", type(exc).__name__)
                durable_applied = durable is not None and durable.state == "APPLIED"
            record.state = (
                FillProcessingState.APPLIED if durable_applied else FillProcessingState.FAILED
            )
            record.error = type(exc).__name__
            with suppress(Exception):
                self._report_incident("fill processing failed")
            with suppress(Exception):
                self._audit.record(
                    AuditEvent(
                        event_type="FILL_PROCESSING_FAILED",
                        service="paper_gateway",
                        message="fill processing failed; reconciliation required",
                        severity="ERROR",
                        order_id=order.internal_order_id,
                        timestamp=timestamp,
                    )
                )
            raise
        record.state = FillProcessingState.APPLIED
        if self._journal is not None and self._durable_fill_handler is None:
            self._journal.mark_fill(fill_key, "APPLIED")
        self._report_state_change()
        if self._fill_observer is not None:
            try:
                self._fill_observer(trade)
            except Exception:
                if self._observer_failure_handler is not None:
                    with suppress(Exception):
                        self._observer_failure_handler()
        return trade, True

    def cancel(
        self,
        order: Order,
        *,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> Order:
        cancelled, created = self.cancel_with_result(
            order, idempotency_key=idempotency_key, now=now
        )
        if created:
            self._report_state_change()
        return cancelled

    def cancel_with_result(
        self,
        order: Order,
        *,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> tuple[Order, bool]:
        key = idempotency_key or f"cancel:{order.internal_order_id}"
        existing = self._cancel_results.get(key)
        if existing is not None:
            if existing.internal_order_id != order.internal_order_id:
                raise ValueError("cancel idempotency key conflict")
            return existing, False
        timestamp = now or utc_now()
        self._oms.transition(order, OrderStatus.CANCEL_PENDING, now=timestamp)
        cancelled = self._oms.transition(order, OrderStatus.CANCELLED, now=timestamp)
        self.reservations.release(order.internal_order_id)
        self._cancel_results[key] = cancelled
        return cancelled, True

    def _report_state_change(self) -> None:
        if self._state_change_handler is not None:
            self._state_change_handler()
