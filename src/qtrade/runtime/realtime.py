from __future__ import annotations

import json
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from datetime import date, datetime
from hashlib import sha256
from threading import Lock
from typing import Protocol

from qtrade.data import Bar
from qtrade.domain import Instrument, Order, OrderRequest, OrderStatus, Side, Trade
from qtrade.domain.models import utc_now
from qtrade.instruments import TradingCalendar, TradingRule, TradingStatus
from qtrade.market_data import AvailableBar, CanonicalTick, MarketDataReadinessGate
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
        strategy_version: str,
        strategy_spec_hash: str,
        portfolio_id: str,
        data_version: str,
        provider: str,
        bar_interval: str,
        market_readiness: MarketDataReadinessGate,
        clock: Callable[[], datetime] = utc_now,
    ) -> None:
        if trading_rule.venue != instrument.exchange:
            raise ValueError("trading rule venue does not match instrument")
        if not all(
            value.strip()
            for value in (
                strategy_id,
                strategy_version,
                strategy_spec_hash,
                portfolio_id,
                data_version,
                provider,
                bar_interval,
            )
        ):
            raise ValueError("strategy, portfolio, and market-data identity are required")
        self._bundle = bundle
        self._instrument = instrument
        self._rule = trading_rule
        self._calendar = calendar
        self._strategy = strategy
        self._strategy_id = strategy_id
        self._strategy_version = strategy_version
        self._strategy_spec_hash = strategy_spec_hash
        self._portfolio_id = portfolio_id
        self._data_version = data_version
        self._provider = provider
        self._bar_interval = bar_interval
        self._market_readiness = market_readiness
        self._clock = clock
        self._pending: PendingTarget | None = None
        self._inflight_order_id: str | None = None
        self._last_bar: datetime | None = None
        self._last_tick_ordinal: int | None = None
        self._lock = Lock()
        identity = self._identity()
        digest = sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self._checkpoint_key = f"real_data_paper:{digest}"
        self._restore_checkpoint(identity)

    def on_finalized_bar(self, value: AvailableBar) -> PendingTarget:
        with self._lock:
            try:
                bar = value.bar
                if (
                    bar.symbol != self._instrument.symbol
                    or bar.exchange != self._instrument.exchange
                ):
                    raise ValueError("finalized bar instrument mismatch")
                if value.available_at.tzinfo is None or value.available_at < bar.timestamp:
                    raise ValueError("finalized bar availability is invalid")
                if self._last_bar is not None and bar.timestamp <= self._last_bar:
                    raise ValueError("finalized bars must be strictly chronological")
                current = self._bundle.ledger.account.position_quantity(bar.symbol)
                target = self._strategy.on_bar(bar, current)
                if isinstance(target, bool) or not isinstance(target, int) or target < 0:
                    raise ValueError(
                        "long-only realtime PAPER target must be a nonnegative integer"
                    )
                self._last_bar = bar.timestamp
                self._pending = PendingTarget(target, value.available_at, bar.timestamp)
                self._persist_checkpoint()
                return self._pending
            except Exception:
                self._bundle.report_incident("realtime PAPER bar processing failed")
                raise

    def on_tick(self, tick: CanonicalTick) -> RealDataPaperResult:
        with self._lock:
            try:
                return self._on_tick_locked(tick)
            except Exception:
                self._bundle.report_incident("realtime PAPER tick processing failed")
                with suppress(Exception):
                    self._persist_checkpoint()
                raise

    def _on_tick_locked(self, tick: CanonicalTick) -> RealDataPaperResult:
        self._market_readiness.require(tick, now=self._clock())
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
        self._persist_checkpoint()
        pending = self._pending
        if pending is None or tick.timestamp_received < pending.decided_at:
            return RealDataPaperResult(None, None, pending)
        current = self._bundle.ledger.account.position_quantity(tick.instrument)
        delta = pending.quantity - current
        if delta == 0:
            self._pending = None
            self._persist_checkpoint()
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
        self._inflight_order_id = order.internal_order_id
        self._persist_checkpoint()
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
        self._inflight_order_id = None
        self._persist_checkpoint()
        return RealDataPaperResult(order, trade, None)

    def _identity(self) -> dict[str, str]:
        return {
            "exchange": self._instrument.exchange,
            "portfolio_id": self._portfolio_id,
            "strategy_id": self._strategy_id,
            "strategy_version": self._strategy_version,
            "strategy_spec_hash": self._strategy_spec_hash,
            "symbol": self._instrument.symbol,
            "data_version": self._data_version,
            "provider": self._provider,
            "bar_interval": self._bar_interval,
            "rule_id": self._rule.rule_id,
            "rule_source_version": self._rule.source_version,
        }

    def _persist_checkpoint(self) -> None:
        self._bundle.journal.save_state(
            self._checkpoint_key,
            {
                "schema_version": 1,
                "identity": self._identity(),
                "pending": (
                    None
                    if self._pending is None
                    else {
                        "quantity": self._pending.quantity,
                        "decided_at": self._pending.decided_at.isoformat(),
                        "signal_bar_timestamp": self._pending.signal_bar_timestamp.isoformat(),
                    }
                ),
                "inflight_order_id": self._inflight_order_id,
                "last_bar": None if self._last_bar is None else self._last_bar.isoformat(),
                "last_tick_ordinal": self._last_tick_ordinal,
            },
        )

    def _restore_checkpoint(self, identity: dict[str, str]) -> None:
        value = self._bundle.journal.load_state(self._checkpoint_key)
        if value is None:
            return
        if value.get("schema_version") != 1 or value.get("identity") != identity:
            raise ValueError("realtime PAPER checkpoint identity mismatch")
        pending = value.get("pending")
        if isinstance(pending, dict):
            self._pending = PendingTarget(
                int(pending["quantity"]),
                datetime.fromisoformat(str(pending["decided_at"])),
                datetime.fromisoformat(str(pending["signal_bar_timestamp"])),
            )
        inflight = value.get("inflight_order_id")
        self._inflight_order_id = None if inflight is None else str(inflight)
        last_bar = value.get("last_bar")
        self._last_bar = None if last_bar is None else datetime.fromisoformat(str(last_bar))
        ordinal = value.get("last_tick_ordinal")
        if ordinal is not None and not isinstance(ordinal, int):
            raise ValueError("realtime PAPER checkpoint ordinal is invalid")
        self._last_tick_ordinal = ordinal

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
