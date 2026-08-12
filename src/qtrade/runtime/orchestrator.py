from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Protocol

from qtrade.data import Bar
from qtrade.domain import Instrument, Order, OrderRequest, OrderStatus, Side, Trade
from qtrade.domain.models import utc_now
from qtrade.instruments import TradingCalendar, TradingRule, TradingStatus
from qtrade.risk import RiskContext
from qtrade.runtime.paper import PaperRuntimeBundle


class PaperTargetStrategy(Protocol):
    def on_bar(self, bar: Bar, current_quantity: int) -> int: ...


@dataclass(frozen=True, slots=True)
class PaperBarResult:
    order: Order | None
    trade: Trade | None
    target_quantity: int


_RUNNER_LOCKS_GUARD = Lock()
_RUNNER_LOCKS: dict[tuple[Path, str], Lock] = {}


def _runner_lock(path: Path, checkpoint_key: str) -> Lock:
    key = (path, checkpoint_key)
    with _RUNNER_LOCKS_GUARD:
        return _RUNNER_LOCKS.setdefault(key, Lock())


class PaperBarRunner:
    """Next-bar PAPER/SHADOW strategy orchestrator using the durable order path."""

    def __init__(
        self,
        *,
        bundle: PaperRuntimeBundle,
        instrument: Instrument,
        trading_rule: TradingRule,
        calendar: TradingCalendar,
        strategy: PaperTargetStrategy,
        strategy_id: str,
        portfolio_id: str,
    ) -> None:
        if trading_rule.venue != instrument.exchange:
            raise ValueError("trading rule venue does not match instrument")
        if (
            trading_rule.buy_lot != instrument.lot_size
            or trading_rule.price_tick != instrument.price_tick
        ):
            raise ValueError("trading rule lot/tick does not match instrument")
        if not strategy_id or not portfolio_id:
            raise ValueError("strategy and portfolio identity are required")
        self._bundle = bundle
        self._instrument = instrument
        self._rule = trading_rule
        self._calendar = calendar
        self._strategy = strategy
        self._strategy_id = strategy_id
        self._portfolio_id = portfolio_id
        self._pending_target: int | None = None
        self._last_timestamp: datetime | None = None
        identity = {
            "exchange": instrument.exchange,
            "portfolio_id": portfolio_id,
            "strategy_id": strategy_id,
            "symbol": instrument.symbol,
        }
        digest = sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self._checkpoint_key = f"paper_runner:{digest}"
        self._lock = _runner_lock(bundle.journal.path, self._checkpoint_key)
        self._restore_checkpoint(identity)

    def on_bar(
        self, bar: Bar, *, received_at: datetime | None = None
    ) -> PaperBarResult:
        with self._lock:
            receipt_time = received_at or utc_now()
            try:
                return self._on_bar_locked(bar, receipt_time)
            except Exception as exc:
                self._record_event_failure(bar, receipt_time, exc)
                raise

    def _on_bar_locked(self, bar: Bar, received_at: datetime) -> PaperBarResult:
        self._restore_checkpoint(self._identity())
        if bar.symbol != self._instrument.symbol or bar.exchange != self._instrument.exchange:
            raise ValueError("bar instrument does not match configured instrument")
        if received_at.tzinfo is None:
            raise ValueError("paper event receipt time must be timezone-aware")
        if bar.timestamp > received_at:
            raise ValueError("paper bar timestamp cannot be in the future")
        if not (
            self._rule.effective_from <= bar.trading_day
            and (
                self._rule.effective_to is None
                or bar.trading_day <= self._rule.effective_to
            )
        ):
            raise ValueError("trading rule is not effective for paper trading day")
        if self._last_timestamp is not None and bar.timestamp <= self._last_timestamp:
            raise ValueError("paper bars must be unique and strictly chronological")
        if (
            self._calendar.status(
                bar.exchange, bar.trading_day, known_on=bar.trading_day
            )
            is not TradingStatus.OPEN
        ):
            raise ValueError("paper execution requires a verified open session")
        self._last_timestamp = bar.timestamp
        order: Order | None = None
        trade: Trade | None = None
        current = self._bundle.ledger.account.position_quantity(bar.symbol)
        if self._pending_target is not None and self._pending_target != current:
            delta = self._pending_target - current
            side = Side.BUY if delta > 0 else Side.SELL
            sellable_on = (
                self._calendar.advance_open_days(
                    bar.exchange,
                    bar.trading_day,
                    self._rule.sellability_lag_days,
                    known_on=bar.trading_day,
                )
                if side is Side.BUY
                else None
            )
            request = OrderRequest(
                idempotency_key=(
                    f"PAPER-BAR:{self._strategy_id}:{bar.timestamp.isoformat()}:"
                    f"{self._pending_target}"
                ),
                strategy_id=self._strategy_id,
                portfolio_id=self._portfolio_id,
                symbol=bar.symbol,
                side=side,
                price=bar.open,
                quantity=abs(delta),
                created_at=bar.timestamp,
                trading_day=bar.trading_day,
                sellable_on=sellable_on,
            )
            context = RiskContext(
                account=self._bundle.ledger.account,
                instruments={bar.symbol: self._instrument},
                market_prices={bar.symbol: bar.open},
                market_timestamp=bar.timestamp,
                gateway_connected=self._bundle.gateway.connected,
                reconciliation_ok=self._bundle.metrics.reconciliation_status == "OK",
                now=received_at,
                sellable_quantities={
                    bar.symbol: self._bundle.sellability.sellable_quantity(
                        bar.symbol, bar.trading_day
                    )
                },
            )
            order, _ = self._bundle.service.submit(request, context, now=received_at)
            if order.status in {OrderStatus.ACCEPTED, OrderStatus.PARTIALLY_FILLED}:
                trade, _ = self._bundle.gateway.fill(
                    order,
                    fill_key=f"PAPER-BAR-FILL:{order.internal_order_id}",
                    price=bar.open,
                    quantity=order.remaining_quantity,
                    now=received_at,
                )
        current = self._bundle.ledger.account.position_quantity(bar.symbol)
        next_target = self._strategy.on_bar(bar, current)
        if isinstance(next_target, bool) or not isinstance(next_target, int):
            raise ValueError("long-only PAPER target must be an integer")
        if next_target < 0:
            raise ValueError("long-only PAPER target cannot be negative")
        self._pending_target = next_target
        self._persist_checkpoint()
        return PaperBarResult(order, trade, self._pending_target)

    def _restore_checkpoint(self, identity: dict[str, str]) -> None:
        checkpoint = self._bundle.journal.load_state(self._checkpoint_key)
        if checkpoint is None:
            return
        stored_identity = checkpoint.get("identity")
        if stored_identity != identity or checkpoint.get("schema_version") != 1:
            raise ValueError("paper runner checkpoint identity or schema mismatch")
        pending_target = checkpoint.get("pending_target")
        if pending_target is not None and (
            isinstance(pending_target, bool)
            or not isinstance(pending_target, int)
            or pending_target < 0
        ):
            raise ValueError("paper runner checkpoint target is invalid")
        raw_timestamp = checkpoint.get("last_timestamp")
        if raw_timestamp is not None and not isinstance(raw_timestamp, str):
            raise ValueError("paper runner checkpoint timestamp is invalid")
        last_timestamp = (
            None if raw_timestamp is None else datetime.fromisoformat(raw_timestamp)
        )
        if last_timestamp is not None and last_timestamp.tzinfo is None:
            raise ValueError("paper runner checkpoint timestamp must be timezone-aware")
        self._pending_target = pending_target
        self._last_timestamp = last_timestamp

    def _persist_checkpoint(self) -> None:
        identity = self._identity()
        try:
            self._bundle.journal.save_state(
                self._checkpoint_key,
                {
                    "schema_version": 1,
                    "identity": identity,
                    "pending_target": self._pending_target,
                    "last_timestamp": (
                        None
                        if self._last_timestamp is None
                        else self._last_timestamp.isoformat()
                    ),
                },
            )
        except Exception:
            self._bundle.report_incident("paper orchestrator checkpoint persistence failed")
            raise

    def _identity(self) -> dict[str, str]:
        return {
            "exchange": self._instrument.exchange,
            "portfolio_id": self._portfolio_id,
            "strategy_id": self._strategy_id,
            "symbol": self._instrument.symbol,
        }

    def _record_event_failure(
        self, bar: Bar, received_at: datetime, error: Exception
    ) -> None:
        try:
            self._bundle.journal.save_state(
                f"{self._checkpoint_key}:failure",
                {
                    "schema_version": 1,
                    "status": "FAILED",
                    "identity": self._identity(),
                    "event_timestamp": bar.timestamp.isoformat(),
                    "received_at": received_at.isoformat(),
                    "error_type": type(error).__name__,
                    "last_checkpoint_timestamp": (
                        None
                        if self._last_timestamp is None
                        else self._last_timestamp.isoformat()
                    ),
                },
            )
        finally:
            self._bundle.report_incident("paper orchestrator event processing failed")
