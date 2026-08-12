from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from threading import Lock
from typing import Protocol

from qtrade.data import Bar
from qtrade.domain import Instrument, Order, OrderRequest, OrderStatus, Side, Trade
from qtrade.instruments import TradingCalendar, TradingRule, TradingStatus
from qtrade.market_data import AvailableBar, CanonicalTick
from qtrade.risk import RiskContext
from qtrade.runtime.paper import PaperRuntimeBundle


class FinalizedBarStrategy(Protocol):
    def on_bar(self, bar: Bar, current_quantity: int) -> int: ...


@dataclass(frozen=True, slots=True)
class PendingTarget:
    quantity: int
    decided_at: datetime
    signal_bar_timestamp: datetime


@dataclass(frozen=True, slots=True)
class RealDataPaperResult:
    order: Order | None
    trade: Trade | None
    pending_target: PendingTarget | None


class RealDataPaperRunner:
    """Causal PAPER runner: finalized bars decide; later observed ticks execute."""

    def __init__(
        self,
        *,
        bundle: PaperRuntimeBundle,
        instrument: Instrument,
        trading_rule: TradingRule,
        calendar: TradingCalendar,
        strategy: FinalizedBarStrategy,
        strategy_id: str,
        portfolio_id: str,
    ) -> None:
        if trading_rule.venue != instrument.exchange:
            raise ValueError("trading rule venue does not match instrument")
        if not strategy_id or not portfolio_id:
            raise ValueError("strategy and portfolio identity are required")
        self._bundle = bundle
        self._instrument = instrument
        self._rule = trading_rule
        self._calendar = calendar
        self._strategy = strategy
        self._strategy_id = strategy_id
        self._portfolio_id = portfolio_id
        self._pending: PendingTarget | None = None
        self._last_bar: datetime | None = None
        self._last_tick_ordinal: int | None = None
        self._lock = Lock()

    def on_finalized_bar(self, value: AvailableBar) -> PendingTarget:
        with self._lock:
            bar = value.bar
            if bar.symbol != self._instrument.symbol or bar.exchange != self._instrument.exchange:
                raise ValueError("finalized bar instrument mismatch")
            if value.available_at.tzinfo is None or value.available_at < bar.timestamp:
                raise ValueError("finalized bar availability is invalid")
            if self._last_bar is not None and bar.timestamp <= self._last_bar:
                raise ValueError("finalized bars must be strictly chronological")
            current = self._bundle.ledger.account.position_quantity(bar.symbol)
            target = self._strategy.on_bar(bar, current)
            if isinstance(target, bool) or not isinstance(target, int) or target < 0:
                raise ValueError("long-only realtime PAPER target must be a nonnegative integer")
            self._last_bar = bar.timestamp
            self._pending = PendingTarget(target, value.available_at, bar.timestamp)
            return self._pending

    def on_tick(self, tick: CanonicalTick) -> RealDataPaperResult:
        with self._lock:
            if (
                tick.instrument != self._instrument.symbol
                or tick.exchange != self._instrument.exchange
            ):
                raise ValueError("tick instrument mismatch")
            if tick.ingest_ordinal is None:
                raise ValueError("realtime PAPER requires a recorded ingest ordinal")
            if (
                self._last_tick_ordinal is not None
                and tick.ingest_ordinal <= self._last_tick_ordinal
            ):
                raise ValueError("realtime PAPER ticks must follow ingest order")
            self._last_tick_ordinal = tick.ingest_ordinal
            pending = self._pending
            if pending is None or tick.timestamp_received < pending.decided_at:
                return RealDataPaperResult(None, None, pending)
            current = self._bundle.ledger.account.position_quantity(tick.instrument)
            delta = pending.quantity - current
            if delta == 0:
                self._pending = None
                return RealDataPaperResult(None, None, None)
            trading_day = tick.timestamp_exchange.date()
            self._validate_session(trading_day)
            side = Side.BUY if delta > 0 else Side.SELL
            sellable_on = (
                self._calendar.advance_open_days(
                    tick.exchange,
                    trading_day,
                    self._rule.sellability_lag_days,
                    known_on=trading_day,
                )
                if side is Side.BUY
                else None
            )
            request = OrderRequest(
                idempotency_key=(
                    f"REAL-DATA-PAPER:{self._strategy_id}:{self._portfolio_id}:"
                    f"{pending.signal_bar_timestamp.isoformat()}:{pending.quantity}"
                ),
                strategy_id=self._strategy_id,
                portfolio_id=self._portfolio_id,
                symbol=tick.instrument,
                side=side,
                price=tick.price,
                quantity=abs(delta),
                created_at=tick.timestamp_received,
                trading_day=trading_day,
                sellable_on=sellable_on,
            )
            context = RiskContext(
                self._bundle.ledger.account,
                {tick.instrument: self._instrument},
                {tick.instrument: tick.price},
                tick.timestamp_exchange,
                self._bundle.gateway.connected,
                self._bundle.metrics.reconciliation_status == "OK",
                tick.timestamp_received,
                sellable_quantities={
                    tick.instrument: self._bundle.sellability.sellable_quantity(
                        tick.instrument, trading_day
                    )
                },
            )
            order, _ = self._bundle.service.submit(request, context, now=tick.timestamp_received)
            trade: Trade | None = None
            if order.status in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}:
                trade, _ = self._bundle.gateway.fill(
                    order,
                    fill_key=f"REAL-DATA-PAPER-FILL:{order.internal_order_id}",
                    price=tick.price,
                    quantity=order.remaining_quantity,
                    now=tick.timestamp_received,
                )
            self._pending = None
            return RealDataPaperResult(order, trade, None)

    def _validate_session(self, trading_day: date) -> None:
        if self._calendar.status(
            self._instrument.exchange, trading_day, known_on=trading_day
        ) is not TradingStatus.OPEN:
            raise ValueError("realtime PAPER requires a verified open session")
        if not (
            self._rule.effective_from <= trading_day
            and (self._rule.effective_to is None or trading_day <= self._rule.effective_to)
        ):
            raise ValueError("trading rule is not effective")
