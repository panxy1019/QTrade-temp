from __future__ import annotations

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


@dataclass(frozen=True, slots=True)
class FeeSchedule:
    version: str
    commission_rate: Decimal
    sell_tax_rate: Decimal
    minimum_commission: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if not self.version:
            raise ValueError("fee schedule version is required")
        values = (self.commission_rate, self.sell_tax_rate, self.minimum_commission)
        if any(not value.is_finite() or value < 0 for value in values):
            raise ValueError("fee values must be finite and nonnegative")

    def calculate(self, side: Side, price: Decimal, quantity: int) -> Decimal:
        notional = price * quantity
        commission = max(notional * self.commission_rate, self.minimum_commission)
        tax = notional * self.sell_tax_rate if side is Side.SELL else Decimal("0")
        return (commission + tax).quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


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

    def disconnect(self) -> None:
        self.connected = False

    def reconnect(self) -> None:
        self.connected = True

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
            self._ledger.book(trade)
            self._oms.apply_fill(order, quantity, now=timestamp)
            self.reservations.consume(order.internal_order_id, quantity)
        except Exception as exc:
            record.state = FillProcessingState.FAILED
            record.error = type(exc).__name__
            if self._journal is not None:
                self._journal.mark_fill(fill_key, "FAILED", type(exc).__name__)
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
        if self._journal is not None:
            self._journal.mark_fill(fill_key, "APPLIED")
        return trade, True

    def cancel(
        self,
        order: Order,
        *,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> Order:
        key = idempotency_key or f"cancel:{order.internal_order_id}"
        existing = self._cancel_results.get(key)
        if existing is not None:
            if existing.internal_order_id != order.internal_order_id:
                raise ValueError("cancel idempotency key conflict")
            return existing
        timestamp = now or utc_now()
        self._oms.transition(order, OrderStatus.CANCEL_PENDING, now=timestamp)
        cancelled = self._oms.transition(order, OrderStatus.CANCELLED, now=timestamp)
        self.reservations.release(order.internal_order_id)
        self._cancel_results[key] = cancelled
        return cancelled
