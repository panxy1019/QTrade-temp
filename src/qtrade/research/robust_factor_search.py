from __future__ import annotations

import random
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from hashlib import sha256
from statistics import median

from qtrade.data import Bar
from qtrade.research.metrics import PerformanceMetrics
from qtrade.research.multi_asset import DailyMultiAssetBacktester, evaluate_backtest_window
from qtrade.research.walk_forward_runner import WalkForwardCandidate


@dataclass(frozen=True, slots=True)
class ValidationWindow:
    window_id: str
    start: date
    end: date

    def __post_init__(self) -> None:
        if not self.window_id:
            raise ValueError("validation window identity is required")
        if self.start >= self.end:
            raise ValueError("validation window must have a positive date range")


@dataclass(frozen=True, slots=True)
class CandidateWindowResult:
    candidate_id: str
    window_id: str
    start: date
    end: date
    total_return: Decimal
    sharpe: Decimal
    maximum_drawdown: Decimal
    turnover: Decimal
    transaction_costs: Decimal
    number_of_trades: int


@dataclass(frozen=True, slots=True)
class CandidateRobustnessSummary:
    candidate_id: str
    window_count: int
    positive_window_ratio: Decimal
    median_window_sharpe: Decimal
    worst_window_sharpe: Decimal
    sharpe_dispersion: Decimal
    median_window_return: Decimal
    full_period_return: Decimal
    full_period_sharpe: Decimal
    full_period_turnover: Decimal
    full_period_transaction_costs: Decimal


@dataclass(frozen=True, slots=True)
class CandidateRobustnessEvaluation:
    candidate_id: str
    parameters: tuple[tuple[str, str], ...]
    windows: tuple[CandidateWindowResult, ...]
    summary: CandidateRobustnessSummary


@dataclass(frozen=True, slots=True)
class BootstrapSummary:
    method: str
    replications: int
    expected_block_length: int
    seed: int
    observed_total_return: Decimal
    p05_total_return: Decimal
    median_total_return: Decimal
    p95_total_return: Decimal
    probability_positive: Decimal


@dataclass(frozen=True, slots=True)
class RobustnessGateInputs:
    candidate_summary: CandidateRobustnessSummary
    doubled_cost_total_return: Decimal
    parameter_neighbor_positive_ratio: Decimal
    universe_subset_positive_ratio: Decimal
    bootstrap_probability_positive: Decimal


@dataclass(frozen=True, slots=True)
class RobustnessGateResult:
    passed: bool
    criteria: tuple[tuple[str, bool], ...]


def _validate_windows(
    windows: tuple[ValidationWindow, ...], *, selection_start: date, selection_end: date
) -> None:
    if selection_start >= selection_end:
        raise ValueError("selection period must have a positive date range")
    if not windows or len({window.window_id for window in windows}) != len(windows):
        raise ValueError("validation windows must be nonempty and unique")
    ordered = sorted(windows, key=lambda window: window.start)
    if ordered[0].start < selection_start or ordered[-1].end > selection_end:
        raise ValueError("validation windows must stay within the selection period")
    for previous, current in zip(ordered[:-1], ordered[1:], strict=True):
        if current.start <= previous.end:
            raise ValueError("validation windows must not overlap")


def _window_result(
    candidate_id: str,
    window: ValidationWindow,
    metrics: PerformanceMetrics,
) -> CandidateWindowResult:
    return CandidateWindowResult(
        candidate_id=candidate_id,
        window_id=window.window_id,
        start=window.start,
        end=window.end,
        total_return=metrics.total_return,
        sharpe=metrics.sharpe,
        maximum_drawdown=metrics.maximum_drawdown,
        turnover=metrics.turnover,
        transaction_costs=metrics.transaction_costs,
        number_of_trades=metrics.number_of_trades,
    )


def _summarize(
    candidate_id: str,
    windows: tuple[CandidateWindowResult, ...],
    full: PerformanceMetrics,
) -> CandidateRobustnessSummary:
    sharpes = [window.sharpe for window in windows]
    returns = [window.total_return for window in windows]
    return CandidateRobustnessSummary(
        candidate_id=candidate_id,
        window_count=len(windows),
        positive_window_ratio=Decimal(sum(value > 0 for value in returns)) / len(returns),
        median_window_sharpe=median(sharpes),
        worst_window_sharpe=min(sharpes),
        sharpe_dispersion=max(sharpes) - min(sharpes),
        median_window_return=median(returns),
        full_period_return=full.total_return,
        full_period_sharpe=full.sharpe,
        full_period_turnover=full.turnover,
        full_period_transaction_costs=full.transaction_costs,
    )


def evaluate_candidate_grid(
    bars: list[Bar],
    candidates: tuple[WalkForwardCandidate, ...],
    windows: tuple[ValidationWindow, ...],
    *,
    selection_start: date,
    selection_end: date,
    backtester_factory: Callable[[], DailyMultiAssetBacktester],
) -> tuple[CandidateRobustnessEvaluation, ...]:
    if not candidates or len({item.candidate_id for item in candidates}) != len(candidates):
        raise ValueError("candidate grid must be nonempty and unique")
    _validate_windows(windows, selection_start=selection_start, selection_end=selection_end)
    selection_bars = [bar for bar in bars if bar.trading_day <= selection_end]
    if not selection_bars:
        raise ValueError("selection period has no usable bars")
    evaluations: list[CandidateRobustnessEvaluation] = []
    for candidate in candidates:
        result = backtester_factory().run(selection_bars, candidate.model_factory())
        window_results = tuple(
            _window_result(
                candidate.candidate_id,
                window,
                evaluate_backtest_window(result, window.start, window.end),
            )
            for window in windows
        )
        full_metrics = evaluate_backtest_window(result, selection_start, selection_end)
        evaluations.append(
            CandidateRobustnessEvaluation(
                candidate_id=candidate.candidate_id,
                parameters=candidate.parameters,
                windows=window_results,
                summary=_summarize(candidate.candidate_id, window_results, full_metrics),
            )
        )
    return tuple(evaluations)


def rank_candidate_summaries(
    summaries: tuple[CandidateRobustnessSummary, ...],
) -> tuple[CandidateRobustnessSummary, ...]:
    if not summaries or len({item.candidate_id for item in summaries}) != len(summaries):
        raise ValueError("candidate summaries must be nonempty and unique")
    return tuple(
        sorted(
            summaries,
            key=lambda item: (
                -item.positive_window_ratio,
                -item.median_window_sharpe,
                -item.worst_window_sharpe,
                item.sharpe_dispersion,
                -item.median_window_return,
                item.full_period_turnover,
                item.candidate_id,
            ),
        )
    )


def nearest_parameter_neighbor_ids(
    selected: WalkForwardCandidate,
    candidates: tuple[WalkForwardCandidate, ...],
) -> tuple[str, ...]:
    selected_parameters = dict(selected.parameters)
    neighbors = []
    for candidate in candidates:
        if candidate.candidate_id == selected.candidate_id:
            continue
        parameters = dict(candidate.parameters)
        if parameters.keys() != selected_parameters.keys():
            continue
        differences = sum(
            parameters[key] != selected_parameters[key] for key in selected_parameters
        )
        if differences == 1:
            neighbors.append(candidate.candidate_id)
    return tuple(sorted(neighbors))


def deterministic_universe_subsets(
    symbols: tuple[str, ...],
    *,
    subset_count: int,
    keep_ratio: Decimal,
    seed: int,
) -> tuple[tuple[str, ...], ...]:
    if len(symbols) < 3 or len(set(symbols)) != len(symbols):
        raise ValueError("universe must contain at least three unique symbols")
    if subset_count <= 0:
        raise ValueError("subset count must be positive")
    if not Decimal("0.5") <= keep_ratio < Decimal("1"):
        raise ValueError("keep ratio must be in [0.5, 1)")
    keep_count = max(2, int(Decimal(len(symbols)) * keep_ratio))
    subsets: list[tuple[str, ...]] = []
    attempts = 0
    while len(subsets) < subset_count and attempts < subset_count * 20:
        ranked = sorted(
            symbols,
            key=lambda symbol: sha256(
                f"{seed}:{attempts}:{symbol}".encode()
            ).digest(),
        )
        subset = tuple(sorted(ranked[:keep_count]))
        if subset not in subsets:
            subsets.append(subset)
        attempts += 1
    if len(subsets) != subset_count:
        raise ValueError("requested more unique deterministic subsets than available")
    return tuple(subsets)


def _compound(returns: tuple[Decimal, ...]) -> Decimal:
    wealth = Decimal("1")
    for value in returns:
        if not value.is_finite() or value <= Decimal("-1"):
            raise ValueError("bootstrap returns must be finite and greater than -1")
        wealth *= Decimal("1") + value
    return wealth - Decimal("1")


def _percentile(values: list[Decimal], quantile: Decimal) -> Decimal:
    position = Decimal(len(values) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] + (values[upper] - values[lower]) * fraction


def stationary_bootstrap_total_returns(
    returns: tuple[Decimal, ...],
    *,
    replications: int = 1000,
    expected_block_length: int = 20,
    seed: int = 20260825,
) -> BootstrapSummary:
    if len(returns) < 20:
        raise ValueError("stationary bootstrap requires at least 20 observations")
    if replications < 100:
        raise ValueError("bootstrap replications must be at least 100")
    if not 2 <= expected_block_length <= len(returns):
        raise ValueError("expected block length must be between two and sample length")
    observed = _compound(returns)
    restart_probability = 1 / expected_block_length
    generator = random.Random(seed)
    samples: list[Decimal] = []
    for _ in range(replications):
        index = generator.randrange(len(returns))
        sampled: list[Decimal] = []
        for _ in returns:
            sampled.append(returns[index])
            if generator.random() < restart_probability:
                index = generator.randrange(len(returns))
            else:
                index = (index + 1) % len(returns)
        samples.append(_compound(tuple(sampled)))
    samples.sort()
    return BootstrapSummary(
        method="stationary_bootstrap",
        replications=replications,
        expected_block_length=expected_block_length,
        seed=seed,
        observed_total_return=observed,
        p05_total_return=_percentile(samples, Decimal("0.05")),
        median_total_return=_percentile(samples, Decimal("0.5")),
        p95_total_return=_percentile(samples, Decimal("0.95")),
        probability_positive=Decimal(sum(value > 0 for value in samples))
        / replications,
    )


def evaluate_robustness_gate(inputs: RobustnessGateInputs) -> RobustnessGateResult:
    criteria = (
        (
            "positive_window_ratio_at_least_0.75",
            inputs.candidate_summary.positive_window_ratio >= Decimal("0.75"),
        ),
        (
            "median_window_sharpe_above_zero",
            inputs.candidate_summary.median_window_sharpe > 0,
        ),
        (
            "worst_window_sharpe_at_least_minus_1",
            inputs.candidate_summary.worst_window_sharpe >= Decimal("-1"),
        ),
        ("doubled_cost_total_return_above_zero", inputs.doubled_cost_total_return > 0),
        (
            "parameter_neighbor_positive_ratio_at_least_0.5",
            inputs.parameter_neighbor_positive_ratio >= Decimal("0.5"),
        ),
        (
            "universe_subset_positive_ratio_at_least_two_thirds",
            inputs.universe_subset_positive_ratio >= Decimal(2) / Decimal(3),
        ),
        (
            "bootstrap_probability_positive_at_least_0.75",
            inputs.bootstrap_probability_positive >= Decimal("0.75"),
        ),
    )
    return RobustnessGateResult(all(passed for _, passed in criteria), criteria)
