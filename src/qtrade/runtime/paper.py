from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from zoneinfo import ZoneInfo

from qtrade.accounting import AcquisitionLot, Ledger, SellabilityLedger
from qtrade.audit import AuditEvent, AuditLog
from qtrade.control import PlatformRuntime
from qtrade.domain import (
    AccountSnapshot,
    Order,
    OrderRequest,
    OrderStatus,
    RuntimeMode,
    Side,
    Trade,
)
from qtrade.domain.models import Position
from qtrade.execution import FeeSchedule, PaperGateway, ReservationBook
from qtrade.execution.reservations import Reservation
from qtrade.monitoring import MetricsRegistry
from qtrade.oms import OrderManager
from qtrade.persistence import SQLiteOperationalJournal
from qtrade.reconciliation import Reconciler, ReconciliationResult
from qtrade.reporting import DailyPaperReport, DailyReportStore
from qtrade.risk import KillState, RiskEngine, RiskLimits
from qtrade.service import TradingService

_CHINA_ZONE = ZoneInfo("Asia/Shanghai")


def _restored_decimal(value: object, field: str, *, minimum: Decimal | None = None) -> Decimal:
    try:
        restored = Decimal(str(value))
    except (ArithmeticError, ValueError) as error:
        raise ValueError(f"durable {field} is invalid") from error
    if not restored.is_finite() or minimum is not None and restored < minimum:
        raise ValueError(f"durable {field} is invalid")
    return restored


def _restored_int(value: object, field: str, *, minimum: int = 0) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < minimum:
        raise ValueError(f"durable {field} is invalid")
    return value


def _restored_datetime(value: object, field: str) -> datetime:
    if not isinstance(value, str):
        raise ValueError(f"durable {field} is invalid")
    restored = datetime.fromisoformat(value)
    if restored.tzinfo is None:
        raise ValueError(f"durable {field} is invalid")
    return restored


@dataclass(slots=True)
class PaperRuntimeBundle:
    runtime: PlatformRuntime
    service: TradingService
    gateway: PaperGateway
    risk: RiskEngine
    oms: OrderManager
    ledger: Ledger
    metrics: MetricsRegistry
    audit: AuditLog
    journal: SQLiteOperationalJournal
    sellability: SellabilityLedger
    _incident_handler: Callable[[str], None] = field(repr=False)

    def report_incident(self, reason: str) -> None:
        self._incident_handler(reason)

    def generate_daily_report(
        self,
        *,
        trading_day: date,
        data_version: str,
        strategy_versions: tuple[str, ...],
        report_root: Path,
        generated_at: datetime | None = None,
    ) -> tuple[Path, str]:
        with self.risk.dispatch_gate:
            return self._generate_daily_report(
                trading_day=trading_day,
                data_version=data_version,
                strategy_versions=strategy_versions,
                report_root=report_root,
                generated_at=generated_at,
            )

    def _generate_daily_report(
        self,
        *,
        trading_day: date,
        data_version: str,
        strategy_versions: tuple[str, ...],
        report_root: Path,
        generated_at: datetime | None,
    ) -> tuple[Path, str]:
        timestamp = generated_at or datetime.now(UTC)
        positions: tuple[dict[str, object], ...] = tuple(
            {
                "instrument": position.symbol,
                "quantity": position.quantity,
                "average_cost": str(position.average_cost),
                "realized_pnl": str(position.realized_pnl),
            }
            for position in sorted(
                self.ledger.account.positions.values(), key=lambda item: item.symbol
            )
        )
        trades = tuple(
            payload
            for payload in self.journal.applied_trade_payloads()
            if datetime.fromisoformat(str(payload["timestamp"]))
            .astimezone(_CHINA_ZONE)
            .date()
            == trading_day
        )
        incidents = tuple(
            event.message
            for event in self.journal.audit_incidents_for_day(trading_day, limit=1000)
        )
        realized = sum(
            (position.realized_pnl for position in self.ledger.account.positions.values()),
            start=Decimal("0"),
        )
        report = DailyPaperReport(
            f"paper-{trading_day.isoformat()}",
            trading_day,
            timestamp,
            data_version,
            strategy_versions,
            (
                ("gateway_connected", str(self.metrics.gateway_connected)),
                ("reconciliation", self.metrics.reconciliation_status),
            ),
            (),
            trades,
            positions,
            (("cash", str(self.ledger.account.cash)), ("realized", str(realized))),
            (("kill_state", self.risk.state.value),),
            (("status", "PASSED" if self.metrics.reconciliation_status == "OK" else "FAILED"),),
            incidents,
            tuple((version, "UNASSESSED") for version in strategy_versions),
        )
        return DailyReportStore(report_root).save(report)

    def reconcile_and_recover(self, *, actor: str) -> None:
        uncertain_fills = len(self.journal.uncertain_fills())
        uncertain_orders = sum(
            order.status
            in {
                OrderStatus.RISK_PASSED,
                OrderStatus.SUBMITTED,
                OrderStatus.SUBMISSION_UNKNOWN,
                OrderStatus.ACCEPTED,
                OrderStatus.PARTIALLY_FILLED,
                OrderStatus.CANCEL_PENDING,
            }
            for order in self.oms.orders
        )
        result = ReconciliationResult(
            ok=uncertain_fills == 0 and uncertain_orders == 0,
            differences=(
                ()
                if uncertain_fills == 0 and uncertain_orders == 0
                else ("uncertain_fills_or_submissions",)
            ),
        )
        if not result.ok:
            raise ValueError("paper reconciliation found uncertain state")
        self.gateway.reconnect()
        attestation = Reconciler().issue_attestation(
            result,
            gateway_connected=True,
            uncertain_fills=uncertain_fills,
            now=datetime.now(UTC),
            validity=timedelta(minutes=5),
        )
        if self.risk.state is KillState.HALTED:
            self.risk.set_state(
                KillState.NORMAL,
                actor=actor,
                reason="explicit paper recovery after reconciliation",
                reconciliation_ok=True,
            )
        self.runtime.attest_readiness(attestation)


def build_paper_runtime(
    *,
    mode: RuntimeMode,
    opening_cash: Decimal,
    limits: RiskLimits,
    fees: FeeSchedule,
    journal_path: Path,
) -> PaperRuntimeBundle:
    if mode not in {RuntimeMode.PAPER, RuntimeMode.SHADOW}:
        raise ValueError("runnable runtime must be PAPER or SHADOW")
    journal = SQLiteOperationalJournal(journal_path)
    audit = AuditLog(journal)
    metrics = MetricsRegistry()
    ledger = Ledger(opening_cash, audit)
    oms = OrderManager(audit)
    reservations = ReservationBook()
    sellability = SellabilityLedger()
    risk = RiskEngine(limits, audit)
    runtime = PlatformRuntime(mode, risk, audit, metrics)

    restored = journal.load_state("account")
    if restored is not None:
        ledger.account.cash = _restored_decimal(
            restored.get("cash"), "account cash", minimum=Decimal("0")
        )
        raw_positions = restored.get("positions")
        if not isinstance(raw_positions, dict):
            raise ValueError("durable account positions are invalid")
        for symbol, raw in raw_positions.items():
            if not isinstance(symbol, str) or not symbol or not isinstance(raw, dict):
                raise ValueError("durable account position is invalid")
            ledger.account.positions[symbol] = Position(
                symbol=symbol,
                quantity=_restored_int(
                    raw.get("quantity"), "position quantity"
                ),
                average_cost=_restored_decimal(
                    raw.get("average_cost"),
                    "position average cost",
                    minimum=Decimal("0"),
                ),
                realized_pnl=_restored_decimal(
                    raw.get("realized_pnl"), "position realized PnL"
                ),
            )
        risk.set_state(
            KillState.HALTED,
            actor="restart-recovery",
            reason="restored durable account; OMS reconciliation required",
        )
        metrics.reconciliation_status = "UNKNOWN"

    restored_runtime = journal.load_state("runtime")
    if restored_runtime is not None:
        raw_orders = restored_runtime.get("orders")
        if not isinstance(raw_orders, list):
            raise ValueError("durable orders are invalid")
        for raw in raw_orders:
            if not isinstance(raw, dict) or not isinstance(raw.get("request"), dict):
                raise ValueError("durable order is invalid")
            request_data = raw["request"]
            assert isinstance(request_data, dict)
            price = _restored_decimal(
                request_data.get("price"), "order price", minimum=Decimal("0")
            )
            if price == 0:
                raise ValueError("durable order price is invalid")
            quantity = _restored_int(
                request_data.get("quantity"), "order quantity", minimum=1
            )
            request = OrderRequest(
                idempotency_key=str(request_data["idempotency_key"]),
                strategy_id=str(request_data["strategy_id"]),
                portfolio_id=str(request_data["portfolio_id"]),
                symbol=str(request_data["symbol"]),
                side=Side(str(request_data["side"])),
                price=price,
                quantity=quantity,
                created_at=_restored_datetime(
                    request_data.get("created_at"), "order request timestamp"
                ),
                trading_day=(
                    None
                    if request_data.get("trading_day") is None
                    else date.fromisoformat(str(request_data["trading_day"]))
                ),
                sellable_on=(
                    None
                    if request_data.get("sellable_on") is None
                    else date.fromisoformat(str(request_data["sellable_on"]))
                ),
            )
            filled_quantity = _restored_int(
                raw.get("filled_quantity"), "filled quantity"
            )
            if filled_quantity > quantity:
                raise ValueError("durable filled quantity is invalid")
            created_at = _restored_datetime(
                raw.get("created_at"), "order created timestamp"
            )
            updated_at = _restored_datetime(
                raw.get("updated_at"), "order updated timestamp"
            )
            if updated_at < created_at:
                raise ValueError("durable order timestamp is invalid")
            oms.restore(
                Order(
                    internal_order_id=str(raw["internal_order_id"]),
                    request=request,
                    status=OrderStatus(str(raw["status"])),
                    broker_order_id=(
                        None
                        if raw["broker_order_id"] is None
                        else str(raw["broker_order_id"])
                    ),
                    filled_quantity=filled_quantity,
                    rejection_reason=(
                        None
                        if raw["rejection_reason"] is None
                        else str(raw["rejection_reason"])
                    ),
                    created_at=created_at,
                    updated_at=updated_at,
                )
            )
        raw_reservations = restored_runtime.get("reservations", [])
        if isinstance(raw_reservations, list):
            for raw in raw_reservations:
                if isinstance(raw, dict):
                    reservations.restore(
                        Reservation(
                            str(raw["order_id"]),
                            str(raw["symbol"]),
                            Side(str(raw["side"])),
                            int(raw["remaining_quantity"]),
                            Decimal(str(raw["remaining_cash"])),
                        )
                    )
        raw_lots = restored_runtime.get("sellability_lots", [])
        if isinstance(raw_lots, list):
            for raw in raw_lots:
                if isinstance(raw, dict):
                    sellability.restore(
                        str(raw["symbol"]),
                        AcquisitionLot(
                            int(raw["quantity"]),
                            date.fromisoformat(str(raw["acquired_on"])),
                            date.fromisoformat(str(raw["sellable_on"])),
                        ),
                    )

    def incident(reason: str) -> None:
        metrics.gateway_connected = False
        metrics.reconciliation_status = "FAILED"
        runtime.kill(actor="operational-incident", reason=reason)

    def persist_account() -> None:
        account_payload, runtime_payload = state_payloads()
        try:
            journal.save_states({"account": account_payload, "runtime": runtime_payload})
        except Exception:
            incident("runtime state persistence failed")
            raise

    def state_payloads(
        *,
        account_override: AccountSnapshot | None = None,
        order_override: Order | None = None,
        reservations_override: list[dict[str, object]] | None = None,
        sellability_override: tuple[tuple[str, AcquisitionLot], ...] | None = None,
    ) -> tuple[dict[str, object], dict[str, object]]:
        account = ledger.account if account_override is None else account_override
        positions = account.positions
        cash = account.cash
        orders = [
            (
                order_override
                if order_override
                and item.internal_order_id == order_override.internal_order_id
                else item
            )
            for item in oms.orders
        ]
        account_payload: dict[str, object] = {
                "cash": str(cash),
                "positions": {
                    symbol: {
                        "quantity": position.quantity,
                        "average_cost": str(position.average_cost),
                        "realized_pnl": str(position.realized_pnl),
                    }
                    for symbol, position in positions.items()
                },
            }
        runtime_payload: dict[str, object] = {
                "orders": [
                    serialize_order(order) for order in orders
                ],
                "reservations": reservations_override if reservations_override is not None else [
                    {
                        "order_id": item.order_id,
                        "symbol": item.symbol,
                        "side": item.side.value,
                        "remaining_quantity": item.remaining_quantity,
                        "remaining_cash": str(item.remaining_cash),
                    }
                    for item in reservations.snapshot()
                ],
                "sellability_lots": [
                    {
                        "symbol": symbol,
                        "quantity": lot.quantity,
                        "acquired_on": lot.acquired_on.isoformat(),
                        "sellable_on": lot.sellable_on.isoformat(),
                    }
                    for symbol, lot in (
                        sellability.snapshot()
                        if sellability_override is None
                        else sellability_override
                    )
                ],
            }
        return account_payload, runtime_payload

    def serialize_order(order: Order) -> dict[str, object]:
        return {
                        "internal_order_id": order.internal_order_id,
                        "request": {
                            "idempotency_key": order.request.idempotency_key,
                            "strategy_id": order.request.strategy_id,
                            "portfolio_id": order.request.portfolio_id,
                            "symbol": order.request.symbol,
                            "side": order.request.side.value,
                            "price": str(order.request.price),
                            "quantity": order.request.quantity,
                            "created_at": order.request.created_at.isoformat(),
                            "trading_day": (
                                None
                                if order.request.trading_day is None
                                else order.request.trading_day.isoformat()
                            ),
                            "sellable_on": (
                                None
                                if order.request.sellable_on is None
                                else order.request.sellable_on.isoformat()
                            ),
                        },
                        "status": order.status.value,
                        "broker_order_id": order.broker_order_id,
                        "filled_quantity": order.filled_quantity,
                        "rejection_reason": order.rejection_reason,
                        "created_at": order.created_at.isoformat(),
                        "updated_at": order.updated_at.isoformat(),
                    }

    def durable_fill(fill_key: str, trade: Trade, order: Order) -> bool:
        with risk.dispatch_gate:
            projected_account = ledger.project(trade)
            projected_order = oms.project_fill(order, trade.quantity, now=trade.timestamp)
            reservation_projection = reservations.project_consume(
                order.internal_order_id, trade.quantity
            )
            sellability_projection = sellability.snapshot()
            if order.request.trading_day is not None:
                sellability_projection = sellability.project_fill(
                    trade.symbol,
                    trade.side,
                    trade.quantity,
                    order.request.trading_day,
                    order.request.sellable_on,
                )
            projected_reservations: list[dict[str, object]] = [
                {
                    "order_id": item.order_id,
                    "symbol": item.symbol,
                    "side": item.side.value,
                    "remaining_quantity": item.remaining_quantity,
                    "remaining_cash": str(item.remaining_cash),
                }
                for item in reservation_projection
            ]
            account_payload, runtime_payload = state_payloads(
                account_override=projected_account,
                order_override=projected_order,
                reservations_override=projected_reservations,
                sellability_override=sellability_projection,
            )
            journal.commit_fill_effect(
                fill_key,
                account_state=account_payload,
                runtime_state=runtime_payload,
                trade_payload={
                    "trade_id": trade.trade_id,
                    "order_id": trade.internal_order_id,
                    "instrument": trade.symbol,
                    "side": trade.side.value,
                    "price": str(trade.price),
                    "quantity": trade.quantity,
                    "fee": str(trade.fee),
                    "timestamp": trade.timestamp.isoformat(),
                },
            )
            ledger.commit_projection(projected_account, trade)
            oms.commit_projection(order, projected_order)
            reservations.replace(reservation_projection)
            sellability.replace(sellability_projection)
            audit.record(
                AuditEvent(
                    event_type="TRADE_BOOKED",
                    service="accounting",
                    message=f"trade_id={trade.trade_id}; quantity={trade.quantity}",
                    order_id=trade.internal_order_id,
                    timestamp=trade.timestamp,
                )
            )
            audit.record(
                AuditEvent(
                    event_type="ORDER_TRANSITION",
                    service="oms",
                    message=f"status={projected_order.status.value}; reason=",
                    strategy_id=order.request.strategy_id,
                    portfolio_id=order.request.portfolio_id,
                    order_id=order.internal_order_id,
                    timestamp=projected_order.updated_at,
                )
            )
        return True

    def observe_submission(order: Order, completed_at: datetime) -> None:
        metrics.record_order(created_at=order.created_at, completed_at=completed_at)
        if order.status in {OrderStatus.RISK_REJECTED, OrderStatus.REJECTED}:
            metrics.record_reject()

    gateway = PaperGateway(
        oms,
        ledger,
        audit,
        fees,
        reservations=reservations,
        journal=journal,
        incident_handler=incident,
        state_change_handler=persist_account,
        durable_fill_handler=durable_fill,
        fill_observer=lambda _trade: metrics.record_fill(),
        observer_failure_handler=metrics.mark_degraded,
        mutation_gate=risk.dispatch_gate,
    )
    for restored_order in oms.orders:
        if restored_order.status is not OrderStatus.SUBMISSION_UNKNOWN:
            gateway.restore_submission(restored_order)
    service = TradingService(
        risk,
        oms,
        gateway,
        mode,
        reservations=reservations,
        submission_guard=runtime.ensure_submission_allowed,
        state_change_handler=persist_account,
        submission_observer=observe_submission,
        cancel_observer=lambda _order: metrics.record_cancel(),
        observer_failure_handler=metrics.mark_degraded,
    )
    return PaperRuntimeBundle(
        runtime,
        service,
        gateway,
        risk,
        oms,
        ledger,
        metrics,
        audit,
        journal,
        sellability,
        incident,
    )
