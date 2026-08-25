from __future__ import annotations

import json
from collections import Counter, defaultdict
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
from hashlib import sha256
from math import sqrt

from qtrade.data import Bar, BarQualityGate
from qtrade.runtime import (
    CausalDailyTargetBook,
    DailyTarget,
    MinuteOverlayPolicy,
    MinuteRiskOverlayStrategy,
)


@dataclass(frozen=True, slots=True)
class MinuteCurveMetrics:
    total_return: float
    maximum_drawdown: float
    realized_minute_volatility: float
    point_count: int


@dataclass(frozen=True, slots=True)
class MinuteDayExecution:
    observation_count: int
    reduced_exposure_observation_count: int
    exposure_sum: float
    turnover: float
    transaction_cost: float
    reason_counts: Mapping[str, int]


@dataclass(frozen=True, slots=True)
class MinuteBacktestResult:
    policy_id: str
    policy_hash: str
    cost_bps: float
    timestamps: tuple[datetime, ...]
    trading_days: tuple[date, ...]
    symbols: tuple[str, ...]
    base_equity: tuple[float, ...]
    overlay_equity: tuple[float, ...]
    base_by_symbol: Mapping[str, tuple[float, ...]]
    overlay_by_symbol: Mapping[str, tuple[float, ...]]
    turnover_by_symbol: Mapping[str, float]
    transaction_cost_by_symbol: Mapping[str, float]
    reason_counts: Mapping[str, int]
    execution_by_day: Mapping[date, MinuteDayExecution]
    maximum_exposure: float

    def metrics(
        self,
        *,
        days: set[date] | None = None,
        symbols: set[str] | None = None,
    ) -> dict[str, MinuteCurveMetrics]:
        base = self.base_equity
        overlay = self.overlay_equity
        if symbols is not None:
            selected = tuple(symbol for symbol in self.symbols if symbol in symbols)
            if not selected:
                raise ValueError("minute metric symbol selection cannot be empty")
            base = _aggregate(self.base_by_symbol, selected)
            overlay = _aggregate(self.overlay_by_symbol, selected)
        return {
            "base": _curve_metrics(self.trading_days, base, days=days),
            "overlay": _curve_metrics(self.trading_days, overlay, days=days),
        }

    def execution_metrics(self, *, days: set[date] | None = None) -> dict[str, object]:
        if days is not None and not days.issubset(self.execution_by_day):
            raise ValueError("minute execution days are outside the observation window")
        selected = [
            row
            for trading_day, row in self.execution_by_day.items()
            if days is None or trading_day in days
        ]
        if not selected:
            raise ValueError("minute execution day selection cannot be empty")
        observation_count = sum(row.observation_count for row in selected)
        reduced_count = sum(
            row.reduced_exposure_observation_count for row in selected
        )
        exposure_sum = sum(row.exposure_sum for row in selected)
        turnover = sum(row.turnover for row in selected)
        transaction_cost = sum(row.transaction_cost for row in selected)
        reasons: Counter[str] = Counter()
        for row in selected:
            reasons.update(row.reason_counts)
        symbol_count = len(self.symbols)
        return {
            "symbol_count": symbol_count,
            "observation_count": observation_count,
            "reduced_exposure_observation_count": reduced_count,
            "exposure_sum": exposure_sum,
            "mean_exposure": exposure_sum / observation_count,
            "reduced_exposure_ratio": reduced_count / observation_count,
            "turnover": turnover,
            "mean_turnover_per_symbol": turnover / symbol_count,
            "transaction_cost": transaction_cost,
            "mean_transaction_cost_per_symbol": transaction_cost / symbol_count,
            "reason_counts": dict(sorted(reasons.items())),
        }


def run_minute_overlay_backtest(
    bars: Iterable[Bar],
    *,
    policy_id: str,
    policy: MinuteOverlayPolicy,
    cost_bps: float,
) -> MinuteBacktestResult:
    if not policy_id:
        raise ValueError("minute policy identity is required")
    if not 0 <= cost_bps <= 100:
        raise ValueError("minute cost must be between zero and 100 bps")
    records = tuple(bars)
    if not records:
        raise ValueError("minute backtest requires bars")
    issues = BarQualityGate().validate(records)
    if issues:
        raise ValueError(
            "minute backtest bars failed quality gate: "
            + ",".join(sorted({issue.code for issue in issues}))
        )
    grouped: dict[str, list[Bar]] = defaultdict(list)
    for bar in records:
        grouped[bar.symbol].append(bar)
    symbols = tuple(sorted(grouped))
    for values in grouped.values():
        values.sort(key=lambda bar: bar.timestamp)
    timestamps = tuple(bar.timestamp for bar in grouped[symbols[0]])
    trading_days = tuple(bar.trading_day for bar in grouped[symbols[0]])
    if any(
        tuple(bar.timestamp for bar in grouped[symbol]) != timestamps
        or tuple(bar.trading_day for bar in grouped[symbol]) != trading_days
        for symbol in symbols[1:]
    ):
        raise ValueError("minute backtest requires synchronized symbol timestamps")

    policy_payload = {key: str(value) for key, value in asdict(policy).items()}
    policy_hash = sha256(
        json.dumps(
            {"policy_id": policy_id, "policy": policy_payload},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    base_by_symbol: dict[str, tuple[float, ...]] = {}
    overlay_by_symbol: dict[str, tuple[float, ...]] = {}
    turnover: dict[str, float] = {}
    transaction_cost: dict[str, float] = {}
    reasons: Counter[str] = Counter()
    daily_observations: Counter[date] = Counter()
    daily_reduced_observations: Counter[date] = Counter()
    daily_exposure_sums: defaultdict[date, float] = defaultdict(float)
    daily_turnover: defaultdict[date, float] = defaultdict(float)
    daily_transaction_cost: defaultdict[date, float] = defaultdict(float)
    daily_reasons: defaultdict[date, Counter[str]] = defaultdict(Counter)
    maximum_exposure = 0.0
    for symbol in symbols:
        simulation = _simulate_symbol(
            grouped[symbol],
            policy_id=policy_id,
            policy_hash=policy_hash,
            policy=policy,
            cost_rate=cost_bps / 10000,
        )
        base_by_symbol[symbol] = simulation[0]
        overlay_by_symbol[symbol] = simulation[1]
        turnover[symbol] = simulation[2]
        transaction_cost[symbol] = simulation[3]
        reasons.update(simulation[4])
        maximum_exposure = max(maximum_exposure, simulation[5])
        for trading_day, row in simulation[6].items():
            daily_observations[trading_day] += row.observation_count
            daily_reduced_observations[trading_day] += (
                row.reduced_exposure_observation_count
            )
            daily_exposure_sums[trading_day] += row.exposure_sum
            daily_turnover[trading_day] += row.turnover
            daily_transaction_cost[trading_day] += row.transaction_cost
            daily_reasons[trading_day].update(row.reason_counts)
    execution_by_day = {
        trading_day: MinuteDayExecution(
            observation_count=daily_observations[trading_day],
            reduced_exposure_observation_count=daily_reduced_observations[trading_day],
            exposure_sum=daily_exposure_sums[trading_day],
            turnover=daily_turnover[trading_day],
            transaction_cost=daily_transaction_cost[trading_day],
            reason_counts=dict(sorted(daily_reasons[trading_day].items())),
        )
        for trading_day in sorted(daily_observations)
    }
    return MinuteBacktestResult(
        policy_id=policy_id,
        policy_hash=policy_hash,
        cost_bps=cost_bps,
        timestamps=timestamps,
        trading_days=trading_days,
        symbols=symbols,
        base_equity=_aggregate(base_by_symbol, symbols),
        overlay_equity=_aggregate(overlay_by_symbol, symbols),
        base_by_symbol=base_by_symbol,
        overlay_by_symbol=overlay_by_symbol,
        turnover_by_symbol=turnover,
        transaction_cost_by_symbol=transaction_cost,
        reason_counts=dict(sorted(reasons.items())),
        execution_by_day=execution_by_day,
        maximum_exposure=maximum_exposure,
    )


def _simulate_symbol(
    bars: list[Bar],
    *,
    policy_id: str,
    policy_hash: str,
    policy: MinuteOverlayPolicy,
    cost_rate: float,
) -> tuple[
    tuple[float, ...],
    tuple[float, ...],
    float,
    float,
    Counter[str],
    float,
    Mapping[date, MinuteDayExecution],
]:
    targets = CausalDailyTargetBook()
    first_by_day: dict[date, datetime] = {}
    for bar in bars:
        first_by_day.setdefault(bar.trading_day, bar.timestamp)
    for trading_day, first_timestamp in first_by_day.items():
        targets.publish(
            DailyTarget(
                symbol=symbol_from_bars(bars),
                trading_day=trading_day,
                quantity=100,
                strategy_version="constant-unit-daily-target-v1",
                strategy_spec_hash=policy_hash,
                known_at=first_timestamp - timedelta(microseconds=1),
            )
        )
    strategy = MinuteRiskOverlayStrategy(
        symbol=symbol_from_bars(bars),
        lot_size=1,
        overlay_id=policy_id,
        targets=targets,
        policy=policy,
    )
    base_equity = 1.0
    overlay_equity = 1.0
    current_exposure = 0.0
    pending_exposure = 0.0
    turnover = 0.0
    transaction_cost = 0.0
    maximum_exposure = 0.0
    base_curve: list[float] = []
    overlay_curve: list[float] = []
    reasons: Counter[str] = Counter()
    daily_observations: Counter[date] = Counter()
    daily_reduced_observations: Counter[date] = Counter()
    daily_exposure_sums: defaultdict[date, float] = defaultdict(float)
    daily_turnover: defaultdict[date, float] = defaultdict(float)
    daily_transaction_cost: defaultdict[date, float] = defaultdict(float)
    daily_reasons: defaultdict[date, Counter[str]] = defaultdict(Counter)
    previous_close: float | None = None
    for index, bar in enumerate(bars):
        open_price = float(bar.open)
        close_price = float(bar.close)
        if index == 0:
            base_equity *= 1 - cost_rate
        else:
            assert previous_close is not None
            base_equity *= open_price / previous_close
            overlay_equity *= 1 + current_exposure * (
                open_price / previous_close - 1
            )
            change = abs(pending_exposure - current_exposure)
            if change:
                cost = overlay_equity * change * cost_rate
                overlay_equity -= cost
                transaction_cost += cost
                turnover += change
                daily_transaction_cost[bar.trading_day] += cost
                daily_turnover[bar.trading_day] += change
            current_exposure = pending_exposure
        daily_observations[bar.trading_day] += 1
        daily_exposure_sums[bar.trading_day] += current_exposure
        if current_exposure < 1:
            daily_reduced_observations[bar.trading_day] += 1
        base_equity *= close_price / open_price
        overlay_equity *= 1 + current_exposure * (close_price / open_price - 1)
        if base_equity <= 0 or overlay_equity <= 0:
            raise RuntimeError("minute backtest equity became nonpositive")
        decision = strategy.on_bar(bar, round(current_exposure * 100))
        pending_exposure = decision / 100
        if not 0 <= pending_exposure <= 1:
            raise RuntimeError("minute overlay escaped the reduction-only boundary")
        maximum_exposure = max(maximum_exposure, pending_exposure)
        if strategy.snapshot is not None:
            reasons.update(strategy.snapshot.reasons)
            daily_reasons[bar.trading_day].update(strategy.snapshot.reasons)
        base_curve.append(base_equity)
        overlay_curve.append(overlay_equity)
        previous_close = close_price
    return (
        tuple(base_curve),
        tuple(overlay_curve),
        turnover,
        transaction_cost,
        reasons,
        maximum_exposure,
        {
            trading_day: MinuteDayExecution(
                observation_count=daily_observations[trading_day],
                reduced_exposure_observation_count=daily_reduced_observations[
                    trading_day
                ],
                exposure_sum=daily_exposure_sums[trading_day],
                turnover=daily_turnover[trading_day],
                transaction_cost=daily_transaction_cost[trading_day],
                reason_counts=dict(sorted(daily_reasons[trading_day].items())),
            )
            for trading_day in sorted(daily_observations)
        },
    )


def symbol_from_bars(bars: list[Bar]) -> str:
    if not bars:
        raise ValueError("minute symbol bars cannot be empty")
    symbol = bars[0].symbol
    if any(bar.symbol != symbol for bar in bars):
        raise ValueError("minute symbol simulation received mixed symbols")
    return symbol


def _aggregate(
    curves: Mapping[str, tuple[float, ...]], symbols: tuple[str, ...]
) -> tuple[float, ...]:
    length = len(curves[symbols[0]])
    if any(len(curves[symbol]) != length for symbol in symbols):
        raise ValueError("minute symbol curves are not aligned")
    return tuple(
        sum(curves[symbol][index] for symbol in symbols) / len(symbols)
        for index in range(length)
    )


def _curve_metrics(
    observation_days: tuple[date, ...],
    equity: tuple[float, ...],
    *,
    days: set[date] | None,
) -> MinuteCurveMetrics:
    indexes = [
        index
        for index, trading_day in enumerate(observation_days)
        if days is None or trading_day in days
    ]
    if not indexes:
        raise ValueError("minute metric day selection cannot be empty")
    if any(right != left + 1 for left, right in zip(indexes, indexes[1:], strict=False)):
        raise ValueError("minute metric days must form a contiguous observation window")
    first = indexes[0]
    denominator = 1.0 if first == 0 else equity[first - 1]
    normalized = [1.0, *(equity[index] / denominator for index in indexes)]
    peak = normalized[0]
    maximum_drawdown = 0.0
    returns: list[float] = []
    for previous, current in zip(normalized, normalized[1:], strict=False):
        returns.append(current / previous - 1)
        peak = max(peak, current)
        maximum_drawdown = min(maximum_drawdown, current / peak - 1)
    average = sum(returns) / len(returns)
    variance = sum((value - average) ** 2 for value in returns) / len(returns)
    return MinuteCurveMetrics(
        total_return=normalized[-1] - 1,
        maximum_drawdown=maximum_drawdown,
        realized_minute_volatility=sqrt(variance),
        point_count=len(indexes),
    )
