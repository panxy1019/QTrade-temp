from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class StrategyHealthStatus(StrEnum):
    HEALTHY = "HEALTHY"
    WATCH = "WATCH"
    DEGRADED = "DEGRADED"
    HALTED = "HALTED"


@dataclass(frozen=True, slots=True)
class StrategyHealthInput:
    strategy_id: str
    as_of: datetime
    sample_count: int
    rolling_return: Decimal
    rolling_sharpe: Decimal
    rolling_volatility: Decimal
    rolling_drawdown: Decimal
    turnover: Decimal
    transaction_cost: Decimal
    exposure: Decimal
    signal_drift: Decimal
    factor_ic: Decimal
    factor_drift: Decimal
    execution_deviation: Decimal
    data_stale: bool = False

    def __post_init__(self) -> None:
        if not self.strategy_id or self.as_of.tzinfo is None or self.sample_count < 0:
            raise ValueError("strategy health identity, time and samples are invalid")
        values = (
            self.rolling_return,
            self.rolling_sharpe,
            self.rolling_volatility,
            self.rolling_drawdown,
            self.turnover,
            self.transaction_cost,
            self.exposure,
            self.signal_drift,
            self.factor_ic,
            self.factor_drift,
            self.execution_deviation,
        )
        if any(not value.is_finite() for value in values):
            raise ValueError("strategy health metrics must be finite")
        if self.rolling_drawdown > 0 or any(
            value < 0
            for value in (
                self.rolling_volatility,
                self.turnover,
                self.transaction_cost,
                self.exposure,
                self.signal_drift,
                self.factor_drift,
                self.execution_deviation,
            )
        ):
            raise ValueError("strategy health metric domains are invalid")


@dataclass(frozen=True, slots=True)
class StrategyHealthPolicy:
    minimum_samples: int = 20
    watch_sharpe: Decimal = Decimal("0.5")
    degraded_sharpe: Decimal = Decimal("0")
    watch_drawdown: Decimal = Decimal("0.15")
    degraded_drawdown: Decimal = Decimal("0.25")
    maximum_factor_drift: Decimal = Decimal("0.3")
    maximum_execution_deviation: Decimal = Decimal("0.02")


@dataclass(frozen=True, slots=True)
class StrategyHealthSnapshot:
    strategy_id: str
    as_of: datetime
    status: StrategyHealthStatus
    reasons: tuple[str, ...]
    metrics: StrategyHealthInput


@dataclass(frozen=True, slots=True)
class ResearchTask:
    task_id: str
    strategy_id: str
    trigger: str
    reason_codes: tuple[str, ...]
    created_at: datetime


class StrategyHealthMonitor:
    def __init__(self, policy: StrategyHealthPolicy | None = None) -> None:
        self._policy = policy or StrategyHealthPolicy()
        self._open_task_keys: set[tuple[str, tuple[str, ...]]] = set()

    def evaluate(
        self, value: StrategyHealthInput
    ) -> tuple[StrategyHealthSnapshot, ResearchTask | None]:
        policy = self._policy
        reasons: list[str] = []
        status = StrategyHealthStatus.HEALTHY
        if value.data_stale:
            status = StrategyHealthStatus.HALTED
            reasons.append("DATA_STALE")
        elif value.sample_count < policy.minimum_samples:
            status = StrategyHealthStatus.WATCH
            reasons.append("INSUFFICIENT_SAMPLES")
        else:
            degraded = (
                value.rolling_sharpe < policy.degraded_sharpe
                or abs(value.rolling_drawdown) > policy.degraded_drawdown
                or value.factor_drift > policy.maximum_factor_drift
                or value.execution_deviation > policy.maximum_execution_deviation
            )
            watched = (
                value.rolling_sharpe < policy.watch_sharpe
                or abs(value.rolling_drawdown) > policy.watch_drawdown
            )
            if degraded:
                status = StrategyHealthStatus.DEGRADED
                if value.rolling_sharpe < policy.degraded_sharpe:
                    reasons.append("SHARPE_DEGRADED")
                if abs(value.rolling_drawdown) > policy.degraded_drawdown:
                    reasons.append("DRAWDOWN_DEGRADED")
                if value.factor_drift > policy.maximum_factor_drift:
                    reasons.append("FACTOR_DRIFT")
                if value.execution_deviation > policy.maximum_execution_deviation:
                    reasons.append("EXECUTION_DEVIATION")
            elif watched:
                status = StrategyHealthStatus.WATCH
                reasons.append("PERFORMANCE_WATCH")
        snapshot = StrategyHealthSnapshot(
            value.strategy_id, value.as_of, status, tuple(reasons), value
        )
        task: ResearchTask | None = None
        if status is StrategyHealthStatus.DEGRADED:
            key = (value.strategy_id, tuple(reasons))
            if key not in self._open_task_keys:
                self._open_task_keys.add(key)
                task = ResearchTask(
                    f"health:{value.strategy_id}:{value.as_of.isoformat()}",
                    value.strategy_id,
                    "HEALTH_DEGRADED",
                    tuple(reasons),
                    value.as_of,
                )
        return snapshot, task
