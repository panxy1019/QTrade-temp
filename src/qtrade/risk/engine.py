from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from decimal import Decimal
from enum import StrEnum
from threading import RLock

from qtrade.audit.log import AuditEvent, AuditLog
from qtrade.domain.models import AccountSnapshot, Instrument, OrderRequest, Side, utc_now


class KillState(StrEnum):
    NORMAL = "NORMAL"
    PAUSED = "PAUSED"
    REDUCE_ONLY = "REDUCE_ONLY"
    HALTED = "HALTED"


@dataclass(frozen=True, slots=True)
class RiskLimits:
    max_order_value: Decimal
    max_position_quantity: int
    max_gross_exposure: Decimal
    max_price_deviation: Decimal = Decimal("0.10")
    market_data_max_age: timedelta = timedelta(seconds=30)
    cash_reserve: Decimal = Decimal("0")
    estimated_buy_fee_rate: Decimal = Decimal("0")
    minimum_buy_fee: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        decimal_values = (
            self.max_order_value,
            self.max_gross_exposure,
            self.max_price_deviation,
            self.cash_reserve,
            self.estimated_buy_fee_rate,
            self.minimum_buy_fee,
        )
        if any(not value.is_finite() or value < 0 for value in decimal_values):
            raise ValueError("risk limits must be finite and nonnegative")
        if self.max_position_quantity < 0:
            raise ValueError("position limit must be nonnegative")
        if self.market_data_max_age <= timedelta(0):
            raise ValueError("market data max age must be positive")


@dataclass(frozen=True, slots=True)
class RiskContext:
    account: AccountSnapshot
    instruments: dict[str, Instrument]
    market_prices: dict[str, Decimal]
    market_timestamp: datetime
    gateway_connected: bool
    reconciliation_ok: bool
    now: datetime
    reserved_cash: Decimal = Decimal("0")
    reserved_sell_quantities: dict[str, int] | None = None
    sellable_quantities: dict[str, int] | None = None
    reserved_buy_quantities: dict[str, int] | None = None
    reserved_buy_notional: Decimal = Decimal("0")


@dataclass(frozen=True, slots=True)
class RiskDecision:
    passed: bool
    reason: str = ""


class RiskEngine:
    def __init__(self, limits: RiskLimits, audit: AuditLog) -> None:
        self._limits = limits
        self._audit = audit
        self._state = KillState.NORMAL
        self.dispatch_gate = RLock()

    @property
    def state(self) -> KillState:
        return self._state

    def required_buy_cash(self, request: OrderRequest) -> Decimal:
        order_value = request.price * request.quantity
        estimated_fee = max(
            order_value * self._limits.estimated_buy_fee_rate,
            self._limits.minimum_buy_fee,
        )
        return order_value + estimated_fee

    def set_state(
        self,
        state: KillState,
        *,
        actor: str,
        reason: str,
        reconciliation_ok: bool = False,
        now: datetime | None = None,
    ) -> None:
        with self.dispatch_gate:
            if not actor.strip():
                raise ValueError("an explicit actor is required for risk state changes")
            if not reason.strip():
                raise ValueError("an explicit reason is required for risk state changes")
            if (
                self._state is KillState.HALTED
                and state is not KillState.HALTED
                and not reconciliation_ok
            ):
                raise ValueError("leaving HALTED requires successful reconciliation")
            if state is KillState.NORMAL and self._audit.degraded:
                raise ValueError("cannot resume while audit is degraded")
            prior = self._state
            event = AuditEvent(
                event_type="RISK_STATE_CHANGED",
                service="risk",
                message=f"{prior.value}->{state.value}; actor={actor}; reason={reason}",
                severity="WARNING" if state is not KillState.NORMAL else "INFO",
                timestamp=now or utc_now(),
            )
            safety_rank = {
                KillState.NORMAL: 0,
                KillState.REDUCE_ONLY: 1,
                KillState.PAUSED: 2,
                KillState.HALTED: 3,
            }
            if safety_rank[state] >= safety_rank[prior]:
                self._state = state
                self._audit.record(event)
            else:
                self._audit.record(event)
                self._state = state

    def assess(self, request: OrderRequest, context: RiskContext) -> RiskDecision:
        reason = self._rejection_reason(request, context)
        decision = RiskDecision(passed=reason is None, reason=reason or "")
        self._audit.record(
            AuditEvent(
                event_type="RISK_PASSED" if decision.passed else "RISK_REJECTED",
                service="risk",
                message=decision.reason or "all configured checks passed",
                severity="INFO" if decision.passed else "WARNING",
                strategy_id=request.strategy_id,
                portfolio_id=request.portfolio_id,
                timestamp=context.now,
            )
        )
        return decision

    def _rejection_reason(self, request: OrderRequest, context: RiskContext) -> str | None:
        if self._state in {KillState.HALTED, KillState.PAUSED}:
            return f"risk state is {self._state.value}"
        if not context.gateway_connected:
            return "gateway disconnected"
        if not context.reconciliation_ok:
            return "reconciliation failed or unknown"
        instrument = context.instruments.get(request.symbol)
        if instrument is None:
            return "invalid symbol"
        if not instrument.tradable:
            return "instrument is not tradable"
        if request.price <= 0:
            return "invalid price"
        if request.quantity <= 0 or request.quantity % instrument.lot_size != 0:
            return "invalid quantity for date-effective lot size"
        if request.price % instrument.price_tick != 0:
            return "price is not aligned to date-effective tick size"
        current_quantity = context.account.position_quantity(request.symbol)
        if request.side is Side.SELL and context.sellable_quantities is None:
            return "sellability data unavailable"
        verified_sellable = (
            current_quantity
            if context.sellable_quantities is None
            else context.sellable_quantities.get(request.symbol, 0)
        )
        reserved_sell = (context.reserved_sell_quantities or {}).get(request.symbol, 0)
        sellable_quantity = min(current_quantity, verified_sellable) - reserved_sell
        reserved_buy = (context.reserved_buy_quantities or {}).get(request.symbol, 0)
        if self._state is KillState.REDUCE_ONLY and (
            request.side is Side.BUY or request.quantity > sellable_quantity
        ):
            return "order does not reduce exposure in REDUCE_ONLY"
        if request.side is Side.SELL and request.quantity > sellable_quantity:
            return "insufficient position"
        if context.now - context.market_timestamp > self._limits.market_data_max_age:
            return "stale market data"
        if context.market_timestamp > context.now:
            return "market data timestamp is in the future"
        market_price = context.market_prices.get(request.symbol)
        if market_price is None or market_price <= 0:
            return "market price unavailable"
        deviation = abs(request.price - market_price) / market_price
        if deviation > self._limits.max_price_deviation:
            return "abnormal price deviation"
        order_value = request.price * request.quantity
        if order_value > self._limits.max_order_value:
            return "order value limit exceeded"
        projected_quantity = (
            current_quantity
            + reserved_buy
            + (request.quantity if request.side is Side.BUY else -request.quantity)
        )
        if projected_quantity > self._limits.max_position_quantity:
            return "position limit exceeded"
        if request.side is Side.BUY and (
            self.required_buy_cash(request)
            > context.account.cash - context.reserved_cash - self._limits.cash_reserve
        ):
            return "insufficient cash"
        gross = context.account.gross_market_value(context.market_prices)
        projected_gross = (
            gross
            + context.reserved_buy_notional
            + (order_value if request.side is Side.BUY else -order_value)
        )
        if projected_gross > self._limits.max_gross_exposure:
            return "gross exposure limit exceeded"
        return None
