from __future__ import annotations

import json
from collections.abc import Callable, Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime
from decimal import ROUND_DOWN, Decimal
from hashlib import sha256
from typing import Protocol

from qtrade.data import Bar


class RuntimeStateStore(Protocol):
    def load_state(self, key: str) -> dict[str, object] | None: ...

    def save_state(self, key: str, value: dict[str, object]) -> None: ...


@dataclass(frozen=True, slots=True)
class DailyTarget:
    symbol: str
    trading_day: date
    quantity: int
    strategy_version: str
    strategy_spec_hash: str
    known_at: datetime

    def __post_init__(self) -> None:
        if not self.symbol or not self.strategy_version or not self.strategy_spec_hash:
            raise ValueError("daily target identity is required")
        if (
            isinstance(self.quantity, bool)
            or not isinstance(self.quantity, int)
            or self.quantity < 0
        ):
            raise ValueError("daily target quantity must be a nonnegative integer")
        if self.known_at.tzinfo is None:
            raise ValueError("daily target known_at must be timezone-aware")


class CausalDailyTargetBook:
    """Immutable per-session targets that were known before a minute bar began."""

    def __init__(
        self,
        *,
        state_store: RuntimeStateStore | None = None,
        state_key: str = "minute_overlay:daily_targets:v1",
    ) -> None:
        if not state_key:
            raise ValueError("daily target state key is required")
        self._state_store = state_store
        self._state_key = state_key
        self._targets: dict[tuple[str, date], DailyTarget] = {}
        self._restore()

    def publish(self, target: DailyTarget) -> None:
        key = (target.symbol, target.trading_day)
        existing = self._targets.get(key)
        if existing is not None and existing != target:
            raise ValueError("daily target is immutable after publication")
        if existing is None:
            self._targets[key] = target
            self._persist()

    def target_for(
        self, symbol: str, trading_day: date, *, bar_started_at: datetime
    ) -> DailyTarget | None:
        if bar_started_at.tzinfo is None:
            raise ValueError("minute bar timestamp must be timezone-aware")
        target = self._targets.get((symbol, trading_day))
        if target is None or target.known_at > bar_started_at:
            return None
        return target

    def _persist(self) -> None:
        if self._state_store is None:
            return
        rows = [
            {
                "symbol": value.symbol,
                "trading_day": value.trading_day.isoformat(),
                "quantity": value.quantity,
                "strategy_version": value.strategy_version,
                "strategy_spec_hash": value.strategy_spec_hash,
                "known_at": value.known_at.isoformat(),
            }
            for value in sorted(
                self._targets.values(), key=lambda item: (item.trading_day, item.symbol)
            )
        ]
        self._state_store.save_state(
            self._state_key, {"schema_version": 1, "targets": rows}
        )

    def _restore(self) -> None:
        if self._state_store is None:
            return
        value = self._state_store.load_state(self._state_key)
        if value is None:
            return
        raw_targets = value.get("targets")
        if value.get("schema_version") != 1 or not isinstance(raw_targets, list):
            raise ValueError("daily target checkpoint is malformed")
        for raw in raw_targets:
            if (
                not isinstance(raw, dict)
                or isinstance(raw.get("quantity"), bool)
                or not isinstance(raw.get("quantity"), int)
            ):
                raise ValueError("daily target checkpoint row is malformed")
            quantity = raw["quantity"]
            assert isinstance(quantity, int)
            target = DailyTarget(
                symbol=str(raw["symbol"]),
                trading_day=date.fromisoformat(str(raw["trading_day"])),
                quantity=quantity,
                strategy_version=str(raw["strategy_version"]),
                strategy_spec_hash=str(raw["strategy_spec_hash"]),
                known_at=datetime.fromisoformat(str(raw["known_at"])),
            )
            key = (target.symbol, target.trading_day)
            if key in self._targets:
                raise ValueError("daily target checkpoint contains duplicates")
            self._targets[key] = target


@dataclass(frozen=True, slots=True)
class MinuteOverlayPolicy:
    short_window: int
    long_window: int
    volatility_window: int
    maximum_minute_volatility: Decimal
    maximum_intraday_drawdown: Decimal
    reduction_multiplier: Decimal
    cooldown_bars: int

    def __post_init__(self) -> None:
        windows = (self.short_window, self.long_window, self.volatility_window)
        if any(isinstance(value, bool) or not isinstance(value, int) for value in windows):
            raise ValueError("minute overlay windows must be integers")
        if not 1 < self.short_window < self.long_window:
            raise ValueError("minute overlay windows must satisfy 1 < short < long")
        if self.volatility_window < 2:
            raise ValueError("minute overlay volatility window must be at least two")
        if (
            not self.maximum_minute_volatility.is_finite()
            or self.maximum_minute_volatility <= 0
        ):
            raise ValueError("maximum minute volatility must be finite and positive")
        if (
            not self.maximum_intraday_drawdown.is_finite()
            or not Decimal("0") < self.maximum_intraday_drawdown < Decimal("1")
        ):
            raise ValueError("maximum intraday drawdown must be in (0, 1)")
        if (
            not self.reduction_multiplier.is_finite()
            or not Decimal("0") <= self.reduction_multiplier < Decimal("1")
        ):
            raise ValueError("minute reduction multiplier must be in [0, 1)")
        if (
            isinstance(self.cooldown_bars, bool)
            or not isinstance(self.cooldown_bars, int)
            or self.cooldown_bars < 0
        ):
            raise ValueError("minute overlay cooldown cannot be negative")


@dataclass(frozen=True, slots=True)
class MinuteOverlaySnapshot:
    symbol: str
    ready: bool
    trading_day: date
    last_bar_timestamp: datetime
    base_target_quantity: int
    corrected_target_quantity: int
    multiplier: Decimal
    reasons: tuple[str, ...]
    minute_volatility: Decimal | None
    intraday_drawdown: Decimal
    cooldown_bars_remaining: int
    strategy_version: str | None

    def __post_init__(self) -> None:
        if not self.symbol or self.last_bar_timestamp.tzinfo is None:
            raise ValueError("minute overlay snapshot identity is invalid")
        quantities = (self.base_target_quantity, self.corrected_target_quantity)
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in quantities
        ):
            raise ValueError("minute overlay snapshot quantity is invalid")
        if self.corrected_target_quantity > self.base_target_quantity:
            raise ValueError("minute overlay snapshot cannot increase the daily target")
        decimals = (self.multiplier, self.intraday_drawdown)
        if any(not value.is_finite() for value in decimals):
            raise ValueError("minute overlay snapshot values must be finite")
        if not Decimal("0") <= self.multiplier <= Decimal("1"):
            raise ValueError("minute overlay snapshot multiplier must be in [0, 1]")
        if self.minute_volatility is not None and (
            not self.minute_volatility.is_finite() or self.minute_volatility < 0
        ):
            raise ValueError("minute overlay snapshot volatility is invalid")
        if self.cooldown_bars_remaining < 0:
            raise ValueError("minute overlay snapshot cooldown is invalid")

    def to_payload(self) -> dict[str, object]:
        return {
            "symbol": self.symbol,
            "ready": self.ready,
            "trading_day": self.trading_day.isoformat(),
            "last_bar_timestamp": self.last_bar_timestamp.isoformat(),
            "base_target_quantity": self.base_target_quantity,
            "corrected_target_quantity": self.corrected_target_quantity,
            "multiplier": str(self.multiplier),
            "reasons": list(self.reasons),
            "minute_volatility": (
                None if self.minute_volatility is None else str(self.minute_volatility)
            ),
            "intraday_drawdown": str(self.intraday_drawdown),
            "cooldown_bars_remaining": self.cooldown_bars_remaining,
            "strategy_version": self.strategy_version,
        }


class MinuteRiskOverlayStrategy:
    """Causal 1-minute risk overlay that can only preserve or reduce a daily target."""

    def __init__(
        self,
        *,
        symbol: str,
        lot_size: int,
        overlay_id: str,
        targets: CausalDailyTargetBook,
        policy: MinuteOverlayPolicy,
        state_store: RuntimeStateStore | None = None,
    ) -> None:
        if not symbol or not overlay_id:
            raise ValueError("minute overlay identity is required")
        if lot_size <= 0:
            raise ValueError("minute overlay lot size must be positive")
        self._symbol = symbol
        self._lot_size = lot_size
        self._overlay_id = overlay_id
        self._targets = targets
        self._policy = policy
        self._state_store = state_store
        identity = {
            "overlay_id": overlay_id,
            "symbol": symbol,
            "lot_size": lot_size,
            "policy": {key: str(value) for key, value in asdict(policy).items()},
        }
        digest = sha256(
            json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        self._state_key = f"minute_overlay:strategy:{digest}"
        self._identity = identity
        self._closes: list[Decimal] = []
        self._last_bar: datetime | None = None
        self._trading_day: date | None = None
        self._session_peak: Decimal | None = None
        self._multiplier = Decimal("0")
        self._cooldown_remaining = 0
        self._snapshot: MinuteOverlaySnapshot | None = None
        self._restore()

    @property
    def snapshot(self) -> MinuteOverlaySnapshot | None:
        return self._snapshot

    def on_bar(self, bar: Bar, current_quantity: int) -> int:
        del current_quantity
        if bar.symbol != self._symbol:
            raise ValueError("minute overlay received a different symbol")
        if bar.timestamp.tzinfo is None:
            raise ValueError("minute overlay bar timestamp must be timezone-aware")
        if self._last_bar is not None and bar.timestamp <= self._last_bar:
            raise ValueError("minute overlay bars must be strictly chronological")
        if not bar.close.is_finite() or bar.close <= 0:
            raise ValueError("minute overlay close must be finite and positive")

        if self._trading_day != bar.trading_day:
            self._trading_day = bar.trading_day
            self._session_peak = bar.close
        else:
            self._session_peak = max(self._session_peak or bar.close, bar.close)
        self._last_bar = bar.timestamp
        self._closes.append(bar.close)
        required_closes = max(
            self._policy.long_window, self._policy.volatility_window + 1
        )
        self._closes = self._closes[-required_closes:]

        target = self._targets.target_for(
            self._symbol, bar.trading_day, bar_started_at=bar.timestamp
        )
        base_quantity = 0 if target is None else target.quantity
        reasons: list[str] = []
        ready = len(self._closes) >= required_closes and target is not None
        raw_multiplier = Decimal("1")
        minute_volatility: Decimal | None = None
        peak = self._session_peak or bar.close
        drawdown = bar.close / peak - Decimal("1")

        if target is None:
            reasons.append("NO_CAUSAL_DAILY_TARGET")
        insufficient_history = len(self._closes) < required_closes
        if insufficient_history:
            raw_multiplier = Decimal("0")
            reasons.append("INSUFFICIENT_MINUTE_HISTORY")
        else:
            returns = [
                self._closes[index] / self._closes[index - 1] - Decimal("1")
                for index in range(
                    len(self._closes) - self._policy.volatility_window,
                    len(self._closes),
                )
            ]
            average = sum(returns, start=Decimal("0")) / len(returns)
            variance = sum(
                ((value - average) ** 2 for value in returns), start=Decimal("0")
            ) / len(returns)
            minute_volatility = variance.sqrt()
            if minute_volatility >= self._policy.maximum_minute_volatility:
                raw_multiplier = min(
                    raw_multiplier, self._policy.reduction_multiplier
                )
                reasons.append("MINUTE_VOLATILITY_LIMIT")
            short_average = sum(
                self._closes[-self._policy.short_window :], start=Decimal("0")
            ) / self._policy.short_window
            long_average = sum(
                self._closes[-self._policy.long_window :], start=Decimal("0")
            ) / self._policy.long_window
            if short_average < long_average:
                raw_multiplier = min(
                    raw_multiplier, self._policy.reduction_multiplier
                )
                reasons.append("MINUTE_TREND_WEAK")
        if drawdown <= -self._policy.maximum_intraday_drawdown:
            raw_multiplier = Decimal("0")
            reasons.append("INTRADAY_DRAWDOWN_LIMIT")

        if insufficient_history:
            self._multiplier = Decimal("0")
            self._cooldown_remaining = 0
        elif raw_multiplier < Decimal("1"):
            self._multiplier = min(self._multiplier, raw_multiplier)
            self._cooldown_remaining = self._policy.cooldown_bars
        elif self._cooldown_remaining > 0:
            self._cooldown_remaining -= 1
            reasons.append("RECOVERY_COOLDOWN")
        else:
            self._multiplier = Decimal("1")

        corrected = int(
            (Decimal(base_quantity) * self._multiplier / self._lot_size).to_integral_value(
                rounding=ROUND_DOWN
            )
        ) * self._lot_size
        if corrected > base_quantity:
            raise RuntimeError("minute overlay cannot increase the daily target")
        self._snapshot = MinuteOverlaySnapshot(
            symbol=self._symbol,
            ready=ready,
            trading_day=bar.trading_day,
            last_bar_timestamp=bar.timestamp,
            base_target_quantity=base_quantity,
            corrected_target_quantity=corrected,
            multiplier=self._multiplier,
            reasons=tuple(reasons),
            minute_volatility=minute_volatility,
            intraday_drawdown=drawdown,
            cooldown_bars_remaining=self._cooldown_remaining,
            strategy_version=None if target is None else target.strategy_version,
        )
        self._persist()
        return corrected

    def _persist(self) -> None:
        if self._state_store is None:
            return
        self._state_store.save_state(
            self._state_key,
            {
                "schema_version": 1,
                "identity": self._identity,
                "closes": [str(value) for value in self._closes],
                "last_bar": None if self._last_bar is None else self._last_bar.isoformat(),
                "trading_day": (
                    None if self._trading_day is None else self._trading_day.isoformat()
                ),
                "session_peak": (
                    None if self._session_peak is None else str(self._session_peak)
                ),
                "multiplier": str(self._multiplier),
                "cooldown_remaining": self._cooldown_remaining,
                "snapshot": (
                    None if self._snapshot is None else self._snapshot.to_payload()
                ),
            },
        )

    def _restore(self) -> None:
        if self._state_store is None:
            return
        value = self._state_store.load_state(self._state_key)
        if value is None:
            return
        if value.get("schema_version") != 1 or value.get("identity") != self._identity:
            raise ValueError("minute overlay checkpoint identity mismatch")
        raw_closes = value.get("closes")
        if not isinstance(raw_closes, list):
            raise ValueError("minute overlay checkpoint closes are malformed")
        self._closes = [Decimal(str(item)) for item in raw_closes]
        raw_last_bar = value.get("last_bar")
        self._last_bar = (
            None if raw_last_bar is None else datetime.fromisoformat(str(raw_last_bar))
        )
        raw_day = value.get("trading_day")
        self._trading_day = None if raw_day is None else date.fromisoformat(str(raw_day))
        raw_peak = value.get("session_peak")
        self._session_peak = None if raw_peak is None else Decimal(str(raw_peak))
        self._multiplier = Decimal(str(value["multiplier"]))
        raw_cooldown = value["cooldown_remaining"]
        if (
            isinstance(raw_cooldown, bool)
            or not isinstance(raw_cooldown, int)
            or raw_cooldown < 0
        ):
            raise ValueError("minute overlay checkpoint cooldown is malformed")
        self._cooldown_remaining = raw_cooldown
        raw_snapshot = value.get("snapshot")
        if isinstance(raw_snapshot, dict):
            volatility = raw_snapshot.get("minute_volatility")
            strategy_version = raw_snapshot.get("strategy_version")
            reasons = raw_snapshot.get("reasons")
            ready = raw_snapshot.get("ready")
            base_quantity = raw_snapshot.get("base_target_quantity")
            corrected_quantity = raw_snapshot.get("corrected_target_quantity")
            snapshot_cooldown = raw_snapshot.get("cooldown_bars_remaining")
            if (
                type(ready) is not bool
                or any(
                    isinstance(item, bool) or not isinstance(item, int)
                    for item in (base_quantity, corrected_quantity, snapshot_cooldown)
                )
                or not isinstance(reasons, list)
            ):
                raise ValueError("minute overlay checkpoint snapshot is malformed")
            assert isinstance(ready, bool)
            assert isinstance(base_quantity, int)
            assert isinstance(corrected_quantity, int)
            assert isinstance(snapshot_cooldown, int)
            self._snapshot = MinuteOverlaySnapshot(
                symbol=str(raw_snapshot["symbol"]),
                ready=ready,
                trading_day=date.fromisoformat(str(raw_snapshot["trading_day"])),
                last_bar_timestamp=datetime.fromisoformat(
                    str(raw_snapshot["last_bar_timestamp"])
                ),
                base_target_quantity=base_quantity,
                corrected_target_quantity=corrected_quantity,
                multiplier=Decimal(str(raw_snapshot["multiplier"])),
                reasons=tuple(str(item) for item in reasons),
                minute_volatility=(
                    None if volatility is None else Decimal(str(volatility))
                ),
                intraday_drawdown=Decimal(str(raw_snapshot["intraday_drawdown"])),
                cooldown_bars_remaining=snapshot_cooldown,
                strategy_version=(
                    None if strategy_version is None else str(strategy_version)
                ),
            )


class MinuteOverlayMonitor:
    """Read-only aggregate readiness for a configured minute-overlay portfolio."""

    def __init__(
        self,
        *,
        provider: str,
        strategies: Mapping[str, MinuteRiskOverlayStrategy],
        provider_connected: Callable[[], bool],
        bar_interval: str = "1m",
    ) -> None:
        if not provider or bar_interval != "1m":
            raise ValueError("minute overlay monitor requires a provider and 1m bars")
        if not strategies or any(
            not symbol or strategy.snapshot is not None and strategy.snapshot.symbol != symbol
            for symbol, strategy in strategies.items()
        ):
            raise ValueError("minute overlay monitor strategy mapping is invalid")
        self._provider = provider
        self._strategies = dict(strategies)
        self._provider_connected = provider_connected
        self._bar_interval = bar_interval

    def snapshot(self) -> dict[str, object]:
        connected = self._provider_connected()
        snapshots = [
            strategy.snapshot.to_payload()
            for _, strategy in sorted(self._strategies.items())
            if strategy.snapshot is not None
        ]
        ready_count = sum(bool(value["ready"]) for value in snapshots)
        reasons: list[str] = []
        if not connected:
            reasons.append("REALTIME_PROVIDER_DISCONNECTED")
        if len(snapshots) != len(self._strategies):
            reasons.append("MINUTE_BARS_NOT_OBSERVED")
        if ready_count != len(self._strategies):
            reasons.append("MINUTE_OVERLAY_NOT_FULLY_WARM")
        readiness = (
            "READY"
            if connected and ready_count == len(self._strategies)
            else "NOT_READY"
        )
        return {
            "classification": "CAUSAL_MINUTE_RISK_OVERLAY_PAPER_SHADOW_ONLY",
            "readiness": readiness,
            "provider": self._provider,
            "provider_connected": connected,
            "bar_interval": self._bar_interval,
            "symbol_count": len(self._strategies),
            "ready_symbol_count": ready_count,
            "external_order_transmission": False,
            "promotion": "NOT_ELIGIBLE",
            "reasons": reasons,
            "symbols": snapshots,
        }
