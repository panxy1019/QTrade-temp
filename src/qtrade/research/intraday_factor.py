from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal
from itertools import combinations, product
from math import erfc, floor, log, sqrt
from random import Random

from qtrade.data import Bar, BarQualityGate
from qtrade.market_data import SINA_FIVE_MINUTE_SESSION_TIMES
from qtrade.research.metrics import (
    FactorMetrics,
    FactorObservation,
    PerformanceMetrics,
    evaluate_factor_periods,
    evaluate_returns,
)

_CHINA_TIME = timezone(timedelta(hours=8))
_SIGNAL_TIME = time(10, 0)
_ENTRY_TIME = time(10, 5)
_EXIT_TIME = time(9, 35)
_TIME_STOP_EXIT_TIME = time(15, 0)
_NEXT_SESSION_OPEN_EXIT = "NEXT_SESSION_OPEN"
_BEARISH_OR_TIME_STOP_EXIT = (
    "NEXT_SESSION_THREE_TEN_MINUTE_BEARISH_ELSE_14_55_TIME_STOP"
)
_INTRADAY_EXIT_RULES = frozenset(
    {_NEXT_SESSION_OPEN_EXIT, _BEARISH_OR_TIME_STOP_EXIT}
)
_COST_STRESS_BPS = (0, 5, 10, 20)
_ROBUSTNESS_THRESHOLDS_BPS = tuple(Decimal(value) for value in (0, 5, 10, 20))
_DISCOVERY_THRESHOLDS_BPS = tuple(Decimal(value) for value in (0, 5, 10, 20))
_OPENING_TEN_MINUTE_LABELS = (
    "0930_0940",
    "0940_0950",
    "0950_1000",
)
_OPENING_DIRECTIONS = frozenset({"BULLISH", "BEARISH", "IGNORE"})
_OPENING_COMBINATIONS = frozenset({"ALL", "ANY"})
_TEMPORAL_STABILITY_BLOCK_COUNT = 4
_MAX_TEMPORAL_BLOCK_CONTRIBUTION_SHARE = Decimal("0.60")


@dataclass(frozen=True, slots=True)
class OpeningTripleBullishSpec:
    cost_bps: Decimal = Decimal("5")
    minimum_body_return_bps: Decimal = Decimal("0")
    directions: tuple[str, str, str] = ("BULLISH", "BULLISH", "BULLISH")
    combination: str = "ALL"

    def __post_init__(self) -> None:
        if (
            not self.cost_bps.is_finite()
            or not Decimal("0") <= self.cost_bps <= Decimal("100")
        ):
            raise ValueError("cost_bps must be finite and between 0 and 100")
        if (
            not self.minimum_body_return_bps.is_finite()
            or not Decimal("0")
            <= self.minimum_body_return_bps
            <= Decimal("1000")
        ):
            raise ValueError(
                "minimum_body_return_bps must be finite and between 0 and 1000"
            )
        if len(self.directions) != 3:
            raise ValueError("opening directions must contain exactly three values")
        if any(direction not in _OPENING_DIRECTIONS for direction in self.directions):
            raise ValueError("unsupported opening direction")
        if not any(direction != "IGNORE" for direction in self.directions):
            raise ValueError(
                "opening rule requires at least one completed ten-minute bar"
            )
        if self.combination not in _OPENING_COMBINATIONS:
            raise ValueError("opening combination must be ALL or ANY")

    @property
    def factor_id(self) -> str:
        if self.directions == ("BULLISH", "BULLISH", "BULLISH") and (
            self.combination == "ALL"
        ):
            return "opening-three-ten-minute-bullish-v1"
        return "opening-ten-minute-direction-rule-v1"

    @property
    def factor_name(self) -> str:
        if self.factor_id == "opening-three-ten-minute-bullish-v1":
            return "09:30 起三根十分钟阳线"
        return "自定义开盘十分钟方向规则"

    @property
    def factor_expression(self) -> str:
        clauses = [
            f"{direction.lower()}_10m_{label}"
            for direction, label in zip(
                self.directions,
                _OPENING_TEN_MINUTE_LABELS,
                strict=True,
            )
            if direction != "IGNORE"
        ]
        operator = "AND" if self.combination == "ALL" else "OR"
        return f" {operator} ".join(clauses)

    def directional_score(self, body_returns: tuple[Decimal, ...]) -> Decimal:
        if len(body_returns) != 3:
            raise ValueError("opening rule requires exactly three ten-minute returns")
        scores = [
            body_return if direction == "BULLISH" else -body_return
            for direction, body_return in zip(
                self.directions,
                body_returns,
                strict=True,
            )
            if direction != "IGNORE"
        ]
        return min(scores) if self.combination == "ALL" else max(scores)


@dataclass(frozen=True, slots=True)
class IntradayExitSpec:
    rule: str = _NEXT_SESSION_OPEN_EXIT
    minimum_body_return_bps: Decimal = Decimal("0")

    def __post_init__(self) -> None:
        if self.rule not in _INTRADAY_EXIT_RULES:
            raise ValueError("unsupported intraday exit rule")
        if (
            not self.minimum_body_return_bps.is_finite()
            or not Decimal("0")
            <= self.minimum_body_return_bps
            <= Decimal("1000")
        ):
            raise ValueError(
                "exit minimum_body_return_bps must be finite and between 0 and 1000"
            )
        if (
            self.rule == _NEXT_SESSION_OPEN_EXIT
            and self.minimum_body_return_bps != 0
        ):
            raise ValueError(
                "exit minimum_body_return_bps must be zero for NEXT_SESSION_OPEN"
            )


@dataclass(frozen=True, slots=True)
class IntradayFactorObservation:
    symbol: str
    sector_code: str
    sector_name: str
    trading_day: date
    signal: bool
    signal_at: datetime
    entry_at: datetime
    exit_at: datetime
    signal_close: Decimal
    entry_price: Decimal
    exit_price: Decimal
    exit_condition_score: Decimal
    exit_condition_triggered: bool
    conditional_exit_price: Decimal
    fallback_exit_price: Decimal
    exit_timing_advantage: Decimal
    minimum_ten_minute_body_return: Decimal
    ten_minute_body_returns: tuple[Decimal, Decimal, Decimal]
    directional_rule_score: Decimal
    opening_30m_return: Decimal
    opening_30m_range_position: Decimal
    gross_forward_return: Decimal


@dataclass(frozen=True, slots=True)
class IntradayFactorMetrics:
    number_of_periods: int
    minimum_instruments: int
    ic: Decimal
    rank_ic: Decimal
    icir: Decimal
    quantile_returns: tuple[Decimal, ...]
    factor_turnover: Decimal
    long_short_spread: Decimal


@dataclass(frozen=True, slots=True)
class IntradayEventSummary:
    observation_count: int
    signal_count: int
    signal_rate: Decimal
    mean_signal_forward_return: Decimal
    mean_universe_forward_return: Decimal
    conditional_excess_return: Decimal
    signal_hit_rate: Decimal
    positive_sector_count: int
    sector_count: int
    clustered_signal_session_count: int
    mean_session_conditional_excess_return: Decimal
    newey_west_lag: int
    newey_west_standard_error: Decimal
    newey_west_t_statistic: Decimal
    confidence_interval_95_lower: Decimal
    confidence_interval_95_upper: Decimal


@dataclass(frozen=True, slots=True)
class IntradayExitFactorValidation:
    classification: str
    entry_signal_count: int
    trigger_count: int
    trigger_rate: Decimal
    clustered_trigger_session_count: int
    mean_triggered_timing_advantage: Decimal
    mean_session_triggered_timing_advantage: Decimal
    trigger_hit_rate: Decimal
    newey_west_lag: int
    newey_west_standard_error: Decimal
    newey_west_t_statistic: Decimal
    confidence_interval_95_lower: Decimal
    confidence_interval_95_upper: Decimal
    criteria: Mapping[str, bool]
    passed: bool


@dataclass(frozen=True, slots=True)
class IntradayStabilityBlock:
    block_id: str
    start: date
    end: date
    session_count: int
    observation_count: int
    signal_count: int
    signal_session_count: int
    mean_session_conditional_excess_return: Decimal


@dataclass(frozen=True, slots=True)
class IntradayFactorStability:
    classification: str
    blocks: tuple[IntradayStabilityBlock, ...]
    block_count: int
    positive_block_count: int
    worst_block_mean_conditional_excess_return: Decimal
    maximum_absolute_block_contribution_share: Decimal
    contribution_share_threshold: Decimal
    criteria: Mapping[str, bool]
    passed: bool


@dataclass(frozen=True, slots=True)
class IntradayFactorDay:
    trading_day: date
    signal_count: int
    eligible_symbol_count: int
    strategy_return: Decimal
    benchmark_return: Decimal
    strategy_equity: Decimal
    benchmark_equity: Decimal
    excess_equity: Decimal


@dataclass(frozen=True, slots=True)
class IntradayFactorGroup:
    group_id: str
    group_name: str
    observation_count: int
    signal_count: int
    mean_signal_forward_return: Decimal
    signal_hit_rate: Decimal
    strategy_metrics: PerformanceMetrics
    benchmark_metrics: PerformanceMetrics


@dataclass(frozen=True, slots=True)
class IntradayCostStress:
    cost_bps: int
    strategy_metrics: PerformanceMetrics
    benchmark_metrics: PerformanceMetrics


@dataclass(frozen=True, slots=True)
class IntradayResearchGate:
    passed: bool
    criteria: Mapping[str, bool]


@dataclass(frozen=True, slots=True)
class IntradayRuleCorrelationPair:
    left_rule_id: str
    right_rule_id: str
    correlation: Decimal
    redundant: bool


@dataclass(frozen=True, slots=True)
class IntradayRuleCorrelationResult:
    observation_count: int
    matrix: tuple[tuple[Decimal, ...], ...]
    pairs: tuple[IntradayRuleCorrelationPair, ...]
    redundancy_threshold: Decimal
    maximum_absolute_correlation: Decimal
    passed: bool


@dataclass(frozen=True, slots=True)
class IntradayRuleEffectiveness:
    rule_id: str
    minimum_body_return_bps: Decimal
    observation_count: int
    signal_count: int
    clustered_signal_session_count: int
    mean_session_conditional_excess_return: Decimal
    newey_west_lag: int
    newey_west_standard_error: Decimal
    newey_west_t_statistic: Decimal
    confidence_interval_95_lower: Decimal
    confidence_interval_95_upper: Decimal
    raw_p_value: Decimal
    holm_adjusted_p_value: Decimal
    criteria: Mapping[str, bool]
    passed: bool


@dataclass(frozen=True, slots=True)
class IntradayDiscoveryCandidate:
    candidate_id: str
    directions: tuple[str, str, str]
    combination: str
    minimum_body_return_bps: Decimal
    factor_expression: str
    training_observation_count: int
    training_signal_count: int
    training_signal_session_count: int
    training_mean_session_conditional_excess_return: Decimal
    training_newey_west_t_statistic: Decimal
    training_strategy_metrics: PerformanceMetrics
    training_benchmark_metrics: PerformanceMetrics
    training_gate_criteria: Mapping[str, bool]
    training_gate_passed: bool


@dataclass(frozen=True, slots=True)
class IntradaySelectionBiasBlock:
    block_id: int
    start: date
    end: date
    session_count: int


@dataclass(frozen=True, slots=True)
class IntradaySelectionBiasSplit:
    split_id: str
    training_block_ids: tuple[int, ...]
    test_block_ids: tuple[int, ...]
    selected_candidate_id: str
    training_excess_return: Decimal
    test_excess_return: Decimal
    test_average_rank: Decimal
    test_rank_percentile: Decimal
    test_rank_logit: Decimal
    below_median: bool


@dataclass(frozen=True, slots=True)
class IntradaySelectionBiasAudit:
    method: str
    block_count: int
    split_count: int
    selection_metric: str
    rank_tie_method: str
    blocks: tuple[IntradaySelectionBiasBlock, ...]
    splits: tuple[IntradaySelectionBiasSplit, ...]
    below_median_split_count: int
    probability_of_backtest_overfitting: Decimal
    selected_oos_positive_excess_split_count: int
    selected_oos_positive_excess_ratio: Decimal
    median_selected_oos_excess_return: Decimal
    unique_selected_candidate_count: int
    most_selected_candidate_id: str
    most_selected_candidate_count: int
    criteria: Mapping[str, bool]
    passed: bool


@dataclass(frozen=True, slots=True)
class IntradayFactorDiscoveryResult:
    candidate_count: int
    candidates: tuple[IntradayDiscoveryCandidate, ...]
    training_start: date
    training_end: date
    training_session_count: int
    test_start: date
    test_end: date
    test_session_count: int
    selection_known_at: datetime
    selected_candidate_id: str
    selected_directions: tuple[str, str, str]
    selected_minimum_body_return_bps: Decimal
    selected_factor_expression: str
    selected_training_gate_passed: bool
    overfitting_audit: IntradaySelectionBiasAudit
    locked_test_cost_stress: Mapping[int, IntradayCostStress]
    locked_test_cost_stress_criteria: Mapping[str, bool]
    locked_test_cost_stress_passed: bool
    locked_test_sectors: tuple[IntradayFactorGroup, ...]
    locked_test_sector_exclusions: tuple[IntradayFactorGroup, ...]
    locked_test_sector_stress_criteria: Mapping[str, bool]
    locked_test_sector_stress_passed: bool
    test_observation_count: int
    test_signal_count: int
    test_signal_session_count: int
    test_mean_session_conditional_excess_return: Decimal
    test_newey_west_lag: int
    test_newey_west_standard_error: Decimal
    test_newey_west_t_statistic: Decimal
    test_confidence_interval_95_lower: Decimal
    test_confidence_interval_95_upper: Decimal
    test_strategy_metrics: PerformanceMetrics
    test_benchmark_metrics: PerformanceMetrics
    test_gate_criteria: Mapping[str, bool]
    test_gate_passed: bool
    selection_uses_test_returns: bool
    promotion: str
    state_mutation: bool
    external_order_transmission: bool


@dataclass(frozen=True, slots=True)
class IntradayPairedBootstrapSummary:
    method: str
    observation_count: int
    replications: int
    expected_block_length: int
    seed: int
    observed_excess_total_return: Decimal
    p05_excess_total_return: Decimal
    median_excess_total_return: Decimal
    p95_excess_total_return: Decimal
    probability_positive_excess: Decimal


@dataclass(frozen=True, slots=True)
class IntradayThresholdScore:
    minimum_body_return_bps: Decimal
    signal_count: int
    signal_rate: Decimal
    strategy_metrics: PerformanceMetrics
    benchmark_metrics: PerformanceMetrics


@dataclass(frozen=True, slots=True)
class IntradayWalkForwardFold:
    fold_id: str
    training_start: date
    training_end: date
    test_start: date
    test_end: date
    training_session_count: int
    test_session_count: int
    selected_minimum_body_return_bps: Decimal
    selection_known_at: datetime
    training_signal_count: int
    test_signal_count: int
    training_strategy_metrics: PerformanceMetrics
    test_strategy_metrics: PerformanceMetrics
    test_benchmark_metrics: PerformanceMetrics


@dataclass(frozen=True, slots=True)
class IntradayRobustnessValidation:
    classification: str
    candidate_thresholds_bps: tuple[Decimal, ...]
    initial_training_session_count: int
    folds: tuple[IntradayWalkForwardFold, ...]
    stitched_test_session_count: int
    stitched_strategy_metrics: PerformanceMetrics
    stitched_benchmark_metrics: PerformanceMetrics
    stitched_daily: tuple[IntradayFactorDay, ...]
    threshold_sensitivity: tuple[IntradayThresholdScore, ...]
    passed: bool
    criteria: Mapping[str, bool]


@dataclass(frozen=True, slots=True)
class IntradayFactorResearchResult:
    factor_id: str
    factor_name: str
    factor_expression: str
    factor_directions: tuple[str, str, str]
    factor_combination: str
    start: date
    end: date
    selected_session_count: int
    symbol_count: int
    sector_count: int
    observations: tuple[IntradayFactorObservation, ...]
    factor_metrics: IntradayFactorMetrics
    event_summary: IntradayEventSummary
    exit_factor_validation: IntradayExitFactorValidation | None
    stability_validation: IntradayFactorStability
    correlations: Mapping[str, Decimal]
    strategy_metrics: PerformanceMetrics
    benchmark_metrics: PerformanceMetrics
    cost_stress: Mapping[int, IntradayCostStress]
    daily: tuple[IntradayFactorDay, ...]
    sectors: tuple[IntradayFactorGroup, ...]
    symbols: tuple[IntradayFactorGroup, ...]
    robustness_validation: IntradayRobustnessValidation
    research_gate: IntradayResearchGate
    causal_contract: Mapping[str, object]
    promotion: str
    promotion_reasons: tuple[str, ...]
    state_mutation: bool
    external_order_transmission: bool


def run_opening_triple_bullish_research(
    bars: list[Bar],
    *,
    sectors: Mapping[str, tuple[str, str]],
    start: date,
    end: date,
    spec: OpeningTripleBullishSpec | None = None,
    exit_spec: IntradayExitSpec | None = None,
    quantiles: int = 5,
) -> IntradayFactorResearchResult:
    selected_spec = spec or OpeningTripleBullishSpec()
    selected_exit_spec = exit_spec or IntradayExitSpec()
    if start > end:
        raise ValueError("factor research start cannot follow end")
    if quantiles < 2:
        raise ValueError("factor research quantiles must be at least two")
    if not bars:
        raise ValueError("intraday factor research requires bars")
    issues = BarQualityGate().validate(bars)
    if issues:
        raise ValueError(
            "intraday factor bars failed quality gate: "
            + ",".join(sorted({issue.code for issue in issues}))
        )

    grouped: dict[tuple[str, date], list[Bar]] = defaultdict(list)
    symbols = sorted({bar.symbol for bar in bars})
    sessions = sorted({bar.trading_day for bar in bars})
    if set(sectors) != set(symbols):
        raise ValueError("sector map must exactly cover the factor universe")
    for bar in bars:
        grouped[(bar.symbol, bar.trading_day)].append(bar)
    for values in grouped.values():
        values.sort(key=lambda item: item.timestamp)

    if start not in sessions or end not in sessions:
        raise ValueError("factor boundaries must be verified trading sessions")
    start_index = sessions.index(start)
    end_index = sessions.index(end)
    if end_index + 1 >= len(sessions):
        raise ValueError("factor research requires a next verified trading session")
    selected_days = sessions[start_index : end_index + 1]
    required_days = sessions[start_index : end_index + 2]
    _validate_source_grid(grouped, symbols=symbols, trading_days=required_days)

    threshold = selected_spec.minimum_body_return_bps / Decimal("10000")
    exit_threshold = (
        selected_exit_spec.minimum_body_return_bps / Decimal("10000")
    )
    observations: list[IntradayFactorObservation] = []
    for day_index, trading_day in enumerate(selected_days):
        exit_day = sessions[start_index + day_index + 1]
        for symbol in symbols:
            day_bars = grouped[(symbol, trading_day)]
            exit_bars = grouped[(symbol, exit_day)]
            first_six = day_bars[:6]
            ten_minute_returns = (
                first_six[1].close / first_six[0].open - Decimal("1"),
                first_six[3].close / first_six[2].open - Decimal("1"),
                first_six[5].close / first_six[4].open - Decimal("1"),
            )
            minimum_ten_minute_body_return = min(ten_minute_returns)
            directional_rule_score = selected_spec.directional_score(
                ten_minute_returns
            )
            signal = directional_rule_score > threshold
            signal_bar = first_six[-1]
            entry_bar = day_bars[6]
            if _china_time(signal_bar.timestamp) != _SIGNAL_TIME:
                raise ValueError("factor signal must be known at the 10:00 close")
            if _china_time(entry_bar.timestamp) != _ENTRY_TIME:
                raise ValueError("factor entry must use the 10:05 source-bar open")
            exit_condition_score = Decimal("0")
            exit_condition_triggered = False
            conditional_exit_price = exit_bars[0].open
            fallback_exit_price = exit_bars[0].open
            exit_timing_advantage = Decimal("0")
            if selected_exit_spec.rule == _NEXT_SESSION_OPEN_EXIT:
                exit_bar = exit_bars[0]
                if _china_time(exit_bar.timestamp) != _EXIT_TIME:
                    raise ValueError(
                        "factor exit must use the next-session first open"
                    )
            else:
                exit_first_six = exit_bars[:6]
                exit_ten_minute_returns = (
                    exit_first_six[1].close / exit_first_six[0].open
                    - Decimal("1"),
                    exit_first_six[3].close / exit_first_six[2].open
                    - Decimal("1"),
                    exit_first_six[5].close / exit_first_six[4].open
                    - Decimal("1"),
                )
                exit_condition_score = min(
                    -value for value in exit_ten_minute_returns
                )
                exit_condition_triggered = exit_condition_score > exit_threshold
                exit_condition_bar = exit_first_six[-1]
                if _china_time(exit_condition_bar.timestamp) != _SIGNAL_TIME:
                    raise ValueError(
                        "factor exit condition must be known at the next-session "
                        "10:00 close"
                    )
                conditional_exit_bar = exit_bars[6]
                fallback_exit_bar = exit_bars[-1]
                conditional_exit_price = conditional_exit_bar.open
                fallback_exit_price = fallback_exit_bar.open
                exit_timing_advantage = (
                    conditional_exit_price / fallback_exit_price
                    - Decimal("1")
                )
                exit_bar = (
                    conditional_exit_bar
                    if exit_condition_triggered
                    else fallback_exit_bar
                )
                expected_exit_time = (
                    _ENTRY_TIME
                    if exit_condition_triggered
                    else _TIME_STOP_EXIT_TIME
                )
                if _china_time(exit_bar.timestamp) != expected_exit_time:
                    raise ValueError(
                        "factor exit must use the next source open or 14:55 "
                        "time-stop open"
                    )
            opening_high = max(bar.high for bar in first_six)
            opening_low = min(bar.low for bar in first_six)
            opening_range = opening_high - opening_low
            range_position = (
                Decimal("0.5")
                if opening_range == 0
                else (signal_bar.close - opening_low) / opening_range
            )
            sector_code, sector_name = sectors[symbol]
            observations.append(
                IntradayFactorObservation(
                    symbol=symbol,
                    sector_code=sector_code,
                    sector_name=sector_name,
                    trading_day=trading_day,
                    signal=signal,
                    signal_at=signal_bar.timestamp,
                    entry_at=entry_bar.timestamp,
                    exit_at=exit_bar.timestamp,
                    signal_close=signal_bar.close,
                    entry_price=entry_bar.open,
                    exit_price=exit_bar.open,
                    exit_condition_score=exit_condition_score,
                    exit_condition_triggered=exit_condition_triggered,
                    conditional_exit_price=conditional_exit_price,
                    fallback_exit_price=fallback_exit_price,
                    exit_timing_advantage=exit_timing_advantage,
                    minimum_ten_minute_body_return=(
                        minimum_ten_minute_body_return
                    ),
                    ten_minute_body_returns=ten_minute_returns,
                    directional_rule_score=directional_rule_score,
                    opening_30m_return=(
                        signal_bar.close / first_six[0].open - Decimal("1")
                    ),
                    opening_30m_range_position=range_position,
                    gross_forward_return=(
                        exit_bar.open / entry_bar.open - Decimal("1")
                    ),
                )
            )

    factor_periods = _factor_periods(observations, selected_days)
    if min(len(period) for period in factor_periods) < quantiles:
        raise ValueError("factor research has fewer instruments than quantiles")
    raw_factor_metrics = evaluate_factor_periods(
        factor_periods,
        quantiles=quantiles,
    )
    factor_metrics = _factor_metrics(
        raw_factor_metrics,
        factor_periods,
        observations=observations,
        trading_days=selected_days,
    )
    correlations = {
        "opening_30m_return": _pearson(
            [Decimal(int(item.signal)) for item in observations],
            [item.opening_30m_return for item in observations],
        ),
        "opening_30m_range_position": _pearson(
            [Decimal(int(item.signal)) for item in observations],
            [item.opening_30m_range_position for item in observations],
        ),
    }

    base_evaluation = _evaluate_observations(
        observations,
        selected_days,
        cost_bps=selected_spec.cost_bps,
    )
    stress = {
        cost: IntradayCostStress(
            cost_bps=cost,
            strategy_metrics=evaluation[0],
            benchmark_metrics=evaluation[1],
        )
        for cost in _COST_STRESS_BPS
        for evaluation in (
            _evaluate_observations(
                observations,
                selected_days,
                cost_bps=Decimal(cost),
            ),
        )
    }
    sector_groups = _group_results(
        observations,
        selected_days,
        cost_bps=selected_spec.cost_bps,
        key=lambda item: (item.sector_code, item.sector_name),
    )
    symbol_groups = _group_results(
        observations,
        selected_days,
        cost_bps=selected_spec.cost_bps,
        key=lambda item: (item.symbol, item.symbol),
    )
    event_summary = _event_summary(observations, sector_groups)
    exit_factor_validation = (
        None
        if selected_exit_spec.rule == _NEXT_SESSION_OPEN_EXIT
        else _exit_factor_validation(observations)
    )
    stability = _temporal_stability_validation(observations, selected_days)
    robustness = _run_robustness_validation(
        observations,
        selected_days,
        cost_bps=selected_spec.cost_bps,
    )
    criteria = {
        "minimum_observation_count": event_summary.observation_count >= 250,
        "minimum_signal_count": event_summary.signal_count >= 20,
        "positive_mean_signal_return": (
            event_summary.mean_signal_forward_return > 0
        ),
        "positive_conditional_excess_return": (
            event_summary.conditional_excess_return > 0
        ),
        "minimum_ten_clustered_signal_sessions": (
            event_summary.clustered_signal_session_count >= 10
        ),
        "newey_west_t_statistic_above_1_96": (
            event_summary.newey_west_t_statistic > Decimal("1.96")
        ),
        "temporal_stability_passed": stability.passed,
        "positive_rank_ic": factor_metrics.rank_ic > 0,
        "positive_sector_ratio_at_least_60pct": (
            event_summary.positive_sector_count * 5
            >= event_summary.sector_count * 3
        ),
        "maximum_absolute_factor_correlation_below_0_90": (
            max(abs(value) for value in correlations.values()) < Decimal("0.90")
        ),
        "base_cost_total_return_positive": base_evaluation[0].total_return > 0,
        "base_cost_excess_return_positive": (
            base_evaluation[0].excess_return > 0
        ),
        "walk_forward_oos_total_return_positive": (
            robustness.stitched_strategy_metrics.total_return > 0
        ),
        "walk_forward_oos_excess_return_positive": (
            robustness.stitched_strategy_metrics.excess_return > 0
        ),
        "walk_forward_positive_fold_ratio_at_least_60pct": robustness.criteria[
            "positive_fold_ratio_at_least_60pct"
        ],
        "threshold_sensitivity_majority_positive": robustness.criteria[
            "threshold_sensitivity_majority_positive"
        ],
    }
    if exit_factor_validation is not None:
        criteria["exit_factor_effectiveness_passed"] = (
            exit_factor_validation.passed
        )
    causal_contract: Mapping[str, object]
    if selected_exit_spec.rule == _NEXT_SESSION_OPEN_EXIT:
        causal_contract = {
            "factor_known_at": "THIRD_TEN_MINUTE_BAR_CLOSE_10_00",
            "entry_at": "NEXT_SOURCE_FIVE_MINUTE_BAR_OPEN_10_05",
            "exit_at": (
                "NEXT_TRADING_SESSION_FIRST_FIVE_MINUTE_BAR_OPEN_T_PLUS_ONE"
            ),
            "source_bar_timestamp_convention": "BAR_END_TIME",
            "entry_bar_label": "10_05",
            "entry_interval_start": "10_00",
            "exit_bar_label": "NEXT_SESSION_09_35",
            "exit_interval_start": "NEXT_SESSION_09_30",
            "same_bar_execution": False,
            "t_plus_one_compliant": True,
        }
    else:
        causal_contract = {
            "factor_known_at": "THIRD_TEN_MINUTE_BAR_CLOSE_10_00",
            "entry_at": "NEXT_SOURCE_FIVE_MINUTE_BAR_OPEN_10_05",
            "exit_condition_known_at": (
                "NEXT_SESSION_THIRD_TEN_MINUTE_BAR_CLOSE_10_00_T_PLUS_ONE"
            ),
            "conditional_exit_at": (
                "NEXT_SOURCE_FIVE_MINUTE_BAR_OPEN_10_05_T_PLUS_ONE"
            ),
            "fallback_exit_at": (
                "FINAL_SOURCE_FIVE_MINUTE_BAR_OPEN_14_55_T_PLUS_ONE"
            ),
            "source_bar_timestamp_convention": "BAR_END_TIME",
            "entry_bar_label": "10_05",
            "entry_interval_start": "10_00",
            "conditional_exit_bar_label": "NEXT_SESSION_10_05",
            "conditional_exit_interval_start": "NEXT_SESSION_10_00",
            "fallback_exit_bar_label": "NEXT_SESSION_15_00",
            "fallback_exit_interval_start": "NEXT_SESSION_14_55",
            "same_bar_execution": False,
            "t_plus_one_compliant": True,
        }
    return IntradayFactorResearchResult(
        factor_id=selected_spec.factor_id,
        factor_name=selected_spec.factor_name,
        factor_expression=selected_spec.factor_expression,
        factor_directions=selected_spec.directions,
        factor_combination=selected_spec.combination,
        start=start,
        end=end,
        selected_session_count=len(selected_days),
        symbol_count=len(symbols),
        sector_count=len({value[0] for value in sectors.values()}),
        observations=tuple(observations),
        factor_metrics=factor_metrics,
        event_summary=event_summary,
        exit_factor_validation=exit_factor_validation,
        stability_validation=stability,
        correlations=correlations,
        strategy_metrics=base_evaluation[0],
        benchmark_metrics=base_evaluation[1],
        cost_stress=stress,
        daily=base_evaluation[2],
        sectors=sector_groups,
        symbols=symbol_groups,
        robustness_validation=robustness,
        research_gate=IntradayResearchGate(
            passed=all(criteria.values()),
            criteria=criteria,
        ),
        causal_contract=causal_contract,
        promotion="NOT_ELIGIBLE",
        promotion_reasons=(
            "CURRENT_UNIVERSE_NON_PIT",
            "QFQ_MAY_CONTAIN_FUTURE_ADJUSTMENT_KNOWLEDGE",
            "USER_HYPOTHESIS_FORMED_AFTER_DATA_WAS_AVAILABLE",
            "NO_AUTHORIZED_FORWARD_PAPER_EVIDENCE",
            "NO_AUTHORIZED_REALTIME_OR_L2_PROVIDER",
        ),
        state_mutation=False,
        external_order_transmission=False,
    )


def _run_robustness_validation(
    observations: list[IntradayFactorObservation],
    trading_days: list[date],
    *,
    cost_bps: Decimal,
) -> IntradayRobustnessValidation:
    if len(trading_days) < 4:
        raise ValueError("factor robustness requires at least four sessions")
    initial_training_session_count = len(trading_days) // 2
    remaining_session_count = len(trading_days) - initial_training_session_count
    fold_count = min(6, max(1, remaining_session_count // 5))
    base_fold_size, larger_fold_count = divmod(remaining_session_count, fold_count)
    test_fold_sizes = [
        base_fold_size + int(index < larger_fold_count)
        for index in range(fold_count)
    ]

    folds: list[IntradayWalkForwardFold] = []
    stitched_observations: list[IntradayFactorObservation] = []
    stitched_days: list[date] = []
    test_offset = initial_training_session_count
    for fold_index, test_fold_size in enumerate(test_fold_sizes, start=1):
        training_days = trading_days[:test_offset]
        test_days = trading_days[test_offset : test_offset + test_fold_size]
        training_scores = [
            _threshold_score(
                observations,
                training_days,
                threshold_bps=threshold_bps,
                cost_bps=cost_bps,
            )
            for threshold_bps in _ROBUSTNESS_THRESHOLDS_BPS
        ]
        selected = max(
            training_scores,
            key=lambda item: (
                item.strategy_metrics.total_return,
                item.strategy_metrics.excess_return,
                -item.minimum_body_return_bps,
            ),
        )
        test_rows = _threshold_observations(
            observations,
            test_days,
            threshold_bps=selected.minimum_body_return_bps,
        )
        test_strategy, test_benchmark, _ = _evaluate_observations(
            test_rows,
            test_days,
            cost_bps=cost_bps,
        )
        folds.append(
            IntradayWalkForwardFold(
                fold_id=f"WF-{fold_index:02d}",
                training_start=training_days[0],
                training_end=training_days[-1],
                test_start=test_days[0],
                test_end=test_days[-1],
                training_session_count=len(training_days),
                test_session_count=len(test_days),
                selected_minimum_body_return_bps=(
                    selected.minimum_body_return_bps
                ),
                selection_known_at=datetime.combine(
                    test_days[0],
                    _EXIT_TIME,
                    _CHINA_TIME,
                ),
                training_signal_count=selected.signal_count,
                test_signal_count=sum(item.signal for item in test_rows),
                training_strategy_metrics=selected.strategy_metrics,
                test_strategy_metrics=test_strategy,
                test_benchmark_metrics=test_benchmark,
            )
        )
        stitched_observations.extend(test_rows)
        stitched_days.extend(test_days)
        test_offset += test_fold_size

    stitched_strategy, stitched_benchmark, stitched_daily = _evaluate_observations(
        stitched_observations,
        stitched_days,
        cost_bps=cost_bps,
    )
    threshold_sensitivity = tuple(
        _threshold_score(
            observations,
            stitched_days,
            threshold_bps=threshold_bps,
            cost_bps=cost_bps,
        )
        for threshold_bps in _ROBUSTNESS_THRESHOLDS_BPS
    )
    positive_fold_count = sum(
        fold.test_strategy_metrics.total_return > 0 for fold in folds
    )
    positive_threshold_count = sum(
        score.strategy_metrics.total_return > 0
        for score in threshold_sensitivity
    )
    criteria = {
        "minimum_two_disjoint_test_folds": len(folds) >= 2,
        "every_test_fold_has_signal": all(
            fold.test_signal_count > 0 for fold in folds
        ),
        "causal_selection_known_before_test_signal": all(
            fold.selection_known_at
            < datetime.combine(fold.test_start, _SIGNAL_TIME, _CHINA_TIME)
            for fold in folds
        ),
        "stitched_oos_total_return_positive": stitched_strategy.total_return > 0,
        "stitched_oos_excess_return_positive": stitched_strategy.excess_return > 0,
        "positive_fold_ratio_at_least_60pct": (
            positive_fold_count * 5 >= len(folds) * 3
        ),
        "threshold_sensitivity_majority_positive": (
            positive_threshold_count * 2 >= len(threshold_sensitivity)
        ),
        "worst_threshold_total_return_non_negative": min(
            score.strategy_metrics.total_return
            for score in threshold_sensitivity
        )
        >= 0,
    }
    return IntradayRobustnessValidation(
        classification=(
            "REUSED_CHRONOLOGICAL_WALK_FORWARD_DIAGNOSTIC_NOT_INDEPENDENT"
        ),
        candidate_thresholds_bps=_ROBUSTNESS_THRESHOLDS_BPS,
        initial_training_session_count=initial_training_session_count,
        folds=tuple(folds),
        stitched_test_session_count=len(stitched_days),
        stitched_strategy_metrics=stitched_strategy,
        stitched_benchmark_metrics=stitched_benchmark,
        stitched_daily=stitched_daily,
        threshold_sensitivity=threshold_sensitivity,
        passed=all(criteria.values()),
        criteria=criteria,
    )


def _threshold_score(
    observations: list[IntradayFactorObservation],
    trading_days: list[date],
    *,
    threshold_bps: Decimal,
    cost_bps: Decimal,
) -> IntradayThresholdScore:
    rows = _threshold_observations(
        observations,
        trading_days,
        threshold_bps=threshold_bps,
    )
    strategy, benchmark, _ = _evaluate_observations(
        rows,
        trading_days,
        cost_bps=cost_bps,
    )
    signal_count = sum(item.signal for item in rows)
    return IntradayThresholdScore(
        minimum_body_return_bps=threshold_bps,
        signal_count=signal_count,
        signal_rate=Decimal(signal_count) / len(rows),
        strategy_metrics=strategy,
        benchmark_metrics=benchmark,
    )


def _threshold_observations(
    observations: list[IntradayFactorObservation],
    trading_days: list[date],
    *,
    threshold_bps: Decimal,
) -> list[IntradayFactorObservation]:
    selected_days = set(trading_days)
    threshold = threshold_bps / Decimal("10000")
    return [
        replace(
            item,
            signal=item.directional_rule_score > threshold,
        )
        for item in observations
        if item.trading_day in selected_days
    ]


def _validate_source_grid(
    grouped: Mapping[tuple[str, date], list[Bar]],
    *,
    symbols: list[str],
    trading_days: list[date],
) -> None:
    expected_times = tuple(SINA_FIVE_MINUTE_SESSION_TIMES)
    for symbol in symbols:
        for trading_day in trading_days:
            values = grouped.get((symbol, trading_day), [])
            actual_times = tuple(_china_time(bar.timestamp) for bar in values)
            if len(values) != 48 or actual_times != expected_times:
                raise ValueError(
                    "intraday factor research requires the exact 48-bar source grid"
                )


def _china_time(value: datetime) -> time:
    if value.tzinfo is None:
        raise ValueError("intraday factor timestamps must be timezone-aware")
    return value.astimezone(_CHINA_TIME).time().replace(tzinfo=None)


def _factor_periods(
    observations: list[IntradayFactorObservation],
    trading_days: list[date],
) -> list[list[FactorObservation]]:
    by_day: dict[date, list[IntradayFactorObservation]] = defaultdict(list)
    for item in observations:
        by_day[item.trading_day].append(item)
    return [
        [
            FactorObservation(
                instrument_id=item.symbol,
                factor_value=Decimal(int(item.signal)),
                forward_return=item.gross_forward_return,
                as_of=trading_day,
                known_at=item.signal_at,
            )
            for item in sorted(by_day[trading_day], key=lambda row: row.symbol)
        ]
        for trading_day in trading_days
    ]


def _factor_metrics(
    metrics: FactorMetrics,
    periods: list[list[FactorObservation]],
    *,
    observations: list[IntradayFactorObservation],
    trading_days: list[date],
) -> IntradayFactorMetrics:
    signal_returns = [
        item.gross_forward_return for item in observations if item.signal
    ]
    non_signal_returns = [
        item.gross_forward_return for item in observations if not item.signal
    ]
    signal_mean = (
        Decimal("0")
        if not signal_returns
        else sum(signal_returns, start=Decimal("0")) / len(signal_returns)
    )
    non_signal_mean = (
        Decimal("0")
        if not non_signal_returns
        else sum(non_signal_returns, start=Decimal("0")) / len(non_signal_returns)
    )
    return IntradayFactorMetrics(
        number_of_periods=len(periods),
        minimum_instruments=min(len(period) for period in periods),
        ic=metrics.ic,
        rank_ic=metrics.rank_ic,
        icir=metrics.icir,
        quantile_returns=(non_signal_mean, signal_mean),
        factor_turnover=_signal_set_turnover(observations, trading_days),
        long_short_spread=signal_mean - non_signal_mean,
    )


def _signal_set_turnover(
    observations: list[IntradayFactorObservation], trading_days: list[date]
) -> Decimal:
    selected: dict[date, set[str]] = defaultdict(set)
    for item in observations:
        if item.signal:
            selected[item.trading_day].add(item.symbol)
    distances: list[Decimal] = []
    for previous_day, current_day in zip(
        trading_days,
        trading_days[1:],
        strict=False,
    ):
        previous = selected[previous_day]
        current = selected[current_day]
        union = previous | current
        distances.append(
            Decimal("0")
            if not union
            else Decimal(len(previous ^ current)) / len(union)
        )
    return (
        Decimal("0")
        if not distances
        else sum(distances, start=Decimal("0")) / len(distances)
    )


def _net_round_trip_return(gross_return: Decimal, cost_bps: Decimal) -> Decimal:
    rate = cost_bps / Decimal("10000")
    return (Decimal("1") + gross_return) * (Decimal("1") - rate) ** 2 - Decimal(
        "1"
    )


def _evaluate_observations(
    observations: list[IntradayFactorObservation],
    trading_days: list[date],
    *,
    cost_bps: Decimal,
) -> tuple[PerformanceMetrics, PerformanceMetrics, tuple[IntradayFactorDay, ...]]:
    by_day: dict[date, list[IntradayFactorObservation]] = defaultdict(list)
    for item in observations:
        by_day[item.trading_day].append(item)
    strategy_returns: list[Decimal] = []
    benchmark_returns: list[Decimal] = []
    strategy_trade_returns: list[Decimal] = []
    benchmark_trade_returns: list[Decimal] = []
    strategy_costs = Decimal("0")
    benchmark_costs = Decimal("0")
    active_days = 0
    strategy_equity = Decimal("1")
    benchmark_equity = Decimal("1")
    daily: list[IntradayFactorDay] = []
    for trading_day in trading_days:
        rows = by_day[trading_day]
        selected = [item for item in rows if item.signal]
        selected_net = [
            _net_round_trip_return(item.gross_forward_return, cost_bps)
            for item in selected
        ]
        benchmark_net = [
            _net_round_trip_return(item.gross_forward_return, cost_bps)
            for item in rows
        ]
        strategy_return = (
            Decimal("0")
            if not selected_net
            else sum(selected_net, start=Decimal("0")) / len(selected_net)
        )
        benchmark_return = sum(benchmark_net, start=Decimal("0")) / len(
            benchmark_net
        )
        strategy_returns.append(strategy_return)
        benchmark_returns.append(benchmark_return)
        strategy_trade_returns.extend(selected_net)
        benchmark_trade_returns.extend(benchmark_net)
        if selected:
            active_days += 1
            gross = sum(
                (item.gross_forward_return for item in selected),
                start=Decimal("0"),
            ) / len(selected)
            strategy_costs += gross - strategy_return
        benchmark_gross = sum(
            (item.gross_forward_return for item in rows),
            start=Decimal("0"),
        ) / len(rows)
        benchmark_costs += benchmark_gross - benchmark_return
        strategy_equity *= Decimal("1") + strategy_return
        benchmark_equity *= Decimal("1") + benchmark_return
        daily.append(
            IntradayFactorDay(
                trading_day=trading_day,
                signal_count=len(selected),
                eligible_symbol_count=len(rows),
                strategy_return=strategy_return,
                benchmark_return=benchmark_return,
                strategy_equity=strategy_equity,
                benchmark_equity=benchmark_equity,
                excess_equity=strategy_equity - benchmark_equity,
            )
        )
    strategy_metrics = evaluate_returns(
        strategy_returns,
        benchmark_returns=benchmark_returns,
        turnover=Decimal(active_days * 2),
        exposure=Decimal(active_days) / len(trading_days),
        trade_pnls=strategy_trade_returns,
        transaction_costs=strategy_costs,
    )
    benchmark_metrics = evaluate_returns(
        benchmark_returns,
        benchmark_returns=benchmark_returns,
        turnover=Decimal(len(trading_days) * 2),
        exposure=Decimal("1"),
        trade_pnls=benchmark_trade_returns,
        transaction_costs=benchmark_costs,
    )
    return strategy_metrics, benchmark_metrics, tuple(daily)


def _group_results(
    observations: list[IntradayFactorObservation],
    trading_days: list[date],
    *,
    cost_bps: Decimal,
    key: Callable[[IntradayFactorObservation], tuple[str, str]],
) -> tuple[IntradayFactorGroup, ...]:
    groups: dict[tuple[str, str], list[IntradayFactorObservation]] = defaultdict(list)
    for item in observations:
        group_key = key(item)
        if (
            not isinstance(group_key, tuple)
            or len(group_key) != 2
            or not all(isinstance(value, str) and value for value in group_key)
        ):
            raise ValueError("factor group identity must contain two strings")
        groups[group_key].append(item)
    output: list[IntradayFactorGroup] = []
    for (group_id, group_name), rows in sorted(groups.items()):
        strategy, benchmark, _ = _evaluate_observations(
            rows,
            trading_days,
            cost_bps=cost_bps,
        )
        signals = [item.gross_forward_return for item in rows if item.signal]
        output.append(
            IntradayFactorGroup(
                group_id=group_id,
                group_name=group_name,
                observation_count=len(rows),
                signal_count=len(signals),
                mean_signal_forward_return=(
                    Decimal("0")
                    if not signals
                    else sum(signals, start=Decimal("0")) / len(signals)
                ),
                signal_hit_rate=(
                    Decimal("0")
                    if not signals
                    else Decimal(sum(value > 0 for value in signals)) / len(signals)
                ),
                strategy_metrics=strategy,
                benchmark_metrics=benchmark,
            )
        )
    return tuple(output)


def _event_summary(
    observations: list[IntradayFactorObservation],
    sectors: tuple[IntradayFactorGroup, ...],
) -> IntradayEventSummary:
    signals = [item.gross_forward_return for item in observations if item.signal]
    universe = [item.gross_forward_return for item in observations]
    mean_signal = (
        Decimal("0")
        if not signals
        else sum(signals, start=Decimal("0")) / len(signals)
    )
    mean_universe = sum(universe, start=Decimal("0")) / len(universe)
    session_excess_returns = [
        value for _, value in _session_conditional_excess_returns(observations)
    ]
    (
        session_excess_mean,
        newey_west_lag,
        newey_west_standard_error,
        newey_west_t_statistic,
        confidence_lower,
        confidence_upper,
    ) = _newey_west_mean_statistics(session_excess_returns)
    return IntradayEventSummary(
        observation_count=len(observations),
        signal_count=len(signals),
        signal_rate=Decimal(len(signals)) / len(observations),
        mean_signal_forward_return=mean_signal,
        mean_universe_forward_return=mean_universe,
        conditional_excess_return=mean_signal - mean_universe,
        signal_hit_rate=(
            Decimal("0")
            if not signals
            else Decimal(sum(value > 0 for value in signals)) / len(signals)
        ),
        positive_sector_count=sum(
            item.mean_signal_forward_return > 0 for item in sectors
        ),
        sector_count=len(sectors),
        clustered_signal_session_count=len(session_excess_returns),
        mean_session_conditional_excess_return=session_excess_mean,
        newey_west_lag=newey_west_lag,
        newey_west_standard_error=newey_west_standard_error,
        newey_west_t_statistic=newey_west_t_statistic,
        confidence_interval_95_lower=confidence_lower,
        confidence_interval_95_upper=confidence_upper,
    )


def _exit_factor_validation(
    observations: list[IntradayFactorObservation],
) -> IntradayExitFactorValidation:
    entries = [item for item in observations if item.signal]
    triggers = [item for item in entries if item.exit_condition_triggered]
    by_day: dict[date, list[Decimal]] = defaultdict(list)
    for item in triggers:
        exit_day = item.exit_at.astimezone(_CHINA_TIME).date()
        by_day[exit_day].append(item.exit_timing_advantage)
    session_advantages = [
        sum(by_day[trading_day], start=Decimal("0"))
        / len(by_day[trading_day])
        for trading_day in sorted(by_day)
    ]
    (
        session_mean,
        newey_west_lag,
        newey_west_standard_error,
        newey_west_t_statistic,
        confidence_lower,
        confidence_upper,
    ) = _newey_west_mean_statistics(session_advantages)
    raw_mean = (
        Decimal("0")
        if not triggers
        else sum(
            (item.exit_timing_advantage for item in triggers),
            start=Decimal("0"),
        )
        / len(triggers)
    )
    criteria = {
        "minimum_twenty_exit_triggers": len(triggers) >= 20,
        "minimum_ten_clustered_exit_trigger_sessions": (
            len(session_advantages) >= 10
        ),
        "positive_mean_session_exit_timing_advantage": session_mean > 0,
        "exit_timing_newey_west_t_statistic_above_1_96": (
            newey_west_t_statistic > Decimal("1.96")
        ),
    }
    return IntradayExitFactorValidation(
        classification="TRIGGERED_EXIT_TIMING_ADVANTAGE_DIAGNOSTIC",
        entry_signal_count=len(entries),
        trigger_count=len(triggers),
        trigger_rate=(
            Decimal("0")
            if not entries
            else Decimal(len(triggers)) / len(entries)
        ),
        clustered_trigger_session_count=len(session_advantages),
        mean_triggered_timing_advantage=raw_mean,
        mean_session_triggered_timing_advantage=session_mean,
        trigger_hit_rate=(
            Decimal("0")
            if not triggers
            else Decimal(
                sum(item.exit_timing_advantage > 0 for item in triggers)
            )
            / len(triggers)
        ),
        newey_west_lag=newey_west_lag,
        newey_west_standard_error=newey_west_standard_error,
        newey_west_t_statistic=newey_west_t_statistic,
        confidence_interval_95_lower=confidence_lower,
        confidence_interval_95_upper=confidence_upper,
        criteria=criteria,
        passed=all(criteria.values()),
    )


def _temporal_stability_validation(
    observations: list[IntradayFactorObservation],
    trading_days: list[date],
) -> IntradayFactorStability:
    if len(trading_days) < _TEMPORAL_STABILITY_BLOCK_COUNT:
        raise ValueError("factor stability requires at least four sessions")
    if trading_days != sorted(set(trading_days)):
        raise ValueError("factor stability sessions must be unique and ordered")
    rows_by_day: dict[date, list[IntradayFactorObservation]] = defaultdict(list)
    for observation in observations:
        rows_by_day[observation.trading_day].append(observation)
    if set(rows_by_day) != set(trading_days):
        raise ValueError("factor stability observations must cover every session")
    session_excess = dict(_session_conditional_excess_returns(observations))
    base_size, larger_block_count = divmod(
        len(trading_days),
        _TEMPORAL_STABILITY_BLOCK_COUNT,
    )
    block_sizes = [
        base_size + int(index < larger_block_count)
        for index in range(_TEMPORAL_STABILITY_BLOCK_COUNT)
    ]
    blocks: list[IntradayStabilityBlock] = []
    offset = 0
    for index, block_size in enumerate(block_sizes, start=1):
        block_days = trading_days[offset : offset + block_size]
        block_rows = [row for day in block_days for row in rows_by_day[day]]
        block_session_excess = [
            session_excess[day] for day in block_days if day in session_excess
        ]
        blocks.append(
            IntradayStabilityBlock(
                block_id=f"TS-{index:02d}",
                start=block_days[0],
                end=block_days[-1],
                session_count=len(block_days),
                observation_count=len(block_rows),
                signal_count=sum(row.signal for row in block_rows),
                signal_session_count=len(block_session_excess),
                mean_session_conditional_excess_return=(
                    Decimal("0")
                    if not block_session_excess
                    else sum(block_session_excess, start=Decimal("0"))
                    / len(block_session_excess)
                ),
            )
        )
        offset += block_size
    block_means = [
        block.mean_session_conditional_excess_return for block in blocks
    ]
    absolute_sum = sum((abs(value) for value in block_means), start=Decimal("0"))
    maximum_share = (
        Decimal("0")
        if absolute_sum == 0
        else max(abs(value) for value in block_means) / absolute_sum
    )
    positive_block_count = sum(value > 0 for value in block_means)
    criteria = {
        "minimum_four_contiguous_blocks": len(blocks) == 4,
        "every_block_has_signal_session": all(
            block.signal_session_count > 0 for block in blocks
        ),
        "positive_block_ratio_at_least_75pct": (
            positive_block_count * 4 >= len(blocks) * 3
        ),
        "maximum_absolute_block_contribution_share_below_0_60": (
            maximum_share < _MAX_TEMPORAL_BLOCK_CONTRIBUTION_SHARE
        ),
    }
    return IntradayFactorStability(
        classification=(
            "CONTIGUOUS_SESSION_BLOCK_FACTOR_STABILITY_DIAGNOSTIC"
        ),
        blocks=tuple(blocks),
        block_count=len(blocks),
        positive_block_count=positive_block_count,
        worst_block_mean_conditional_excess_return=min(block_means),
        maximum_absolute_block_contribution_share=maximum_share,
        contribution_share_threshold=(
            _MAX_TEMPORAL_BLOCK_CONTRIBUTION_SHARE
        ),
        criteria=criteria,
        passed=all(criteria.values()),
    )


def _session_conditional_excess_returns(
    observations: list[IntradayFactorObservation],
) -> list[tuple[date, Decimal]]:
    by_day: dict[date, list[IntradayFactorObservation]] = defaultdict(list)
    for observation in observations:
        by_day[observation.trading_day].append(observation)
    output: list[tuple[date, Decimal]] = []
    for trading_day in sorted(by_day):
        rows = by_day[trading_day]
        session_signals = [
            item.gross_forward_return for item in rows if item.signal
        ]
        if not session_signals:
            continue
        session_universe = [item.gross_forward_return for item in rows]
        output.append(
            (
                trading_day,
                sum(session_signals, start=Decimal("0")) / len(session_signals)
                - sum(session_universe, start=Decimal("0"))
                / len(session_universe),
            )
        )
    return output


def _newey_west_mean_statistics(
    values: list[Decimal],
) -> tuple[Decimal, int, Decimal, Decimal, Decimal, Decimal]:
    if not values:
        zero = Decimal("0")
        return zero, 0, zero, zero, zero, zero
    count = len(values)
    decimal_count = Decimal(count)
    mean_value = sum(values, start=Decimal("0")) / decimal_count
    if count < 2:
        zero = Decimal("0")
        return mean_value, 0, zero, zero, mean_value, mean_value
    lag = min(
        count - 1,
        floor(4 * (count / 100) ** (2 / 9)),
    )
    deviations = [value - mean_value for value in values]
    long_run_variance = (
        sum((value * value for value in deviations), start=Decimal("0"))
        / decimal_count
    )
    for offset in range(1, lag + 1):
        covariance = sum(
            (
                deviations[index] * deviations[index - offset]
                for index in range(offset, count)
            ),
            start=Decimal("0"),
        ) / decimal_count
        weight = Decimal(lag + 1 - offset) / Decimal(lag + 1)
        long_run_variance += Decimal("2") * weight * covariance
    variance_of_mean = max(Decimal("0"), long_run_variance) / decimal_count
    standard_error = variance_of_mean.sqrt()
    t_statistic = (
        Decimal("0") if standard_error == 0 else mean_value / standard_error
    )
    margin = Decimal("1.96") * standard_error
    return (
        mean_value,
        lag,
        standard_error,
        t_statistic,
        mean_value - margin,
        mean_value + margin,
    )


def _pearson(left: list[Decimal], right: list[Decimal]) -> Decimal:
    if len(left) != len(right) or len(left) < 2:
        raise ValueError("factor correlation series must align with at least two rows")
    count = Decimal(len(left))
    left_mean = sum(left, start=Decimal("0")) / count
    right_mean = sum(right, start=Decimal("0")) / count
    covariance = sum(
        ((x - left_mean) * (y - right_mean) for x, y in zip(left, right, strict=True)),
        start=Decimal("0"),
    )
    left_variance = sum(
        ((value - left_mean) ** 2 for value in left),
        start=Decimal("0"),
    )
    right_variance = sum(
        ((value - right_mean) ** 2 for value in right),
        start=Decimal("0"),
    )
    denominator = (left_variance * right_variance).sqrt()
    return Decimal("0") if denominator == 0 else covariance / denominator


def discover_opening_direction_rules(
    observations: tuple[IntradayFactorObservation, ...],
    *,
    trading_days: tuple[date, ...],
    cost_bps: Decimal,
) -> IntradayFactorDiscoveryResult:
    if len(trading_days) < 40:
        raise ValueError("intraday factor discovery requires at least 40 sessions")
    if len(trading_days) > 120:
        raise ValueError("intraday factor discovery is limited to 120 sessions")
    if trading_days != tuple(sorted(set(trading_days))):
        raise ValueError("intraday factor discovery sessions must be unique and ordered")
    if not observations:
        raise ValueError("intraday factor discovery requires observations")
    if {item.trading_day for item in observations} != set(trading_days):
        raise ValueError("intraday factor discovery observations must match sessions")
    if (
        not cost_bps.is_finite()
        or not Decimal("0") <= cost_bps <= Decimal("100")
    ):
        raise ValueError("cost_bps must be finite and between 0 and 100")

    training_session_count = len(trading_days) // 2
    training_days = trading_days[:training_session_count]
    test_days = trading_days[training_session_count:]
    training_day_set = set(training_days)
    candidates: list[IntradayDiscoveryCandidate] = []
    candidate_daily: dict[str, tuple[IntradayFactorDay, ...]] = {}
    candidate_selection_daily: dict[
        str,
        tuple[tuple[date, int, int, Decimal | None], ...],
    ] = {}
    direction_code = {"BULLISH": "U", "BEARISH": "D", "IGNORE": "X"}
    for first, second, third in product(
        ("BULLISH", "BEARISH", "IGNORE"),
        repeat=3,
    ):
        directions = (first, second, third)
        if directions == ("IGNORE", "IGNORE", "IGNORE"):
            continue
        for threshold_bps in _DISCOVERY_THRESHOLDS_BPS:
            candidate_id = "DIR-{}{}{}-T{:04d}".format(
                *(direction_code[value] for value in directions),
                int(threshold_bps),
            )
            spec = OpeningTripleBullishSpec(
                cost_bps=cost_bps,
                minimum_body_return_bps=threshold_bps,
                directions=directions,
                combination="ALL",
            )
            rule_rows = _opening_rule_observations(
                observations,
                trading_days=trading_days,
                spec=spec,
            )
            training_rows = [
                item
                for item in rule_rows
                if item.trading_day in training_day_set
            ]
            (
                training_signal_count,
                training_signal_sessions,
                training_session_mean,
                _,
                _,
                training_t_statistic,
                _,
                _,
            ) = _opening_rule_session_statistics(training_rows)
            training_strategy, training_benchmark, _ = _evaluate_observations(
                training_rows,
                list(training_days),
                cost_bps=cost_bps,
            )
            _, _, full_daily = _evaluate_observations(
                rule_rows,
                list(trading_days),
                cost_bps=cost_bps,
            )
            conditional_excess_by_day = dict(
                _session_conditional_excess_returns(rule_rows)
            )
            criteria = {
                "minimum_250_training_observations": len(training_rows) >= 250,
                "minimum_twenty_training_signals": training_signal_count >= 20,
                "minimum_ten_training_signal_sessions": (
                    training_signal_sessions >= 10
                ),
                "positive_training_session_conditional_excess": (
                    training_session_mean > 0
                ),
                "training_newey_west_t_statistic_above_1_96": (
                    training_t_statistic > Decimal("1.96")
                ),
            }
            candidate_daily[candidate_id] = full_daily
            candidate_selection_daily[candidate_id] = tuple(
                (
                    row.trading_day,
                    row.eligible_symbol_count,
                    row.signal_count,
                    conditional_excess_by_day.get(row.trading_day),
                )
                for row in full_daily
            )
            candidates.append(
                IntradayDiscoveryCandidate(
                    candidate_id=candidate_id,
                    directions=directions,
                    combination="ALL",
                    minimum_body_return_bps=threshold_bps,
                    factor_expression=spec.factor_expression,
                    training_observation_count=len(training_rows),
                    training_signal_count=training_signal_count,
                    training_signal_session_count=training_signal_sessions,
                    training_mean_session_conditional_excess_return=(
                        training_session_mean
                    ),
                    training_newey_west_t_statistic=training_t_statistic,
                    training_strategy_metrics=training_strategy,
                    training_benchmark_metrics=training_benchmark,
                    training_gate_criteria=criteria,
                    training_gate_passed=all(criteria.values()),
                )
            )
    selected = min(
        candidates,
        key=lambda item: (
            not item.training_gate_passed,
            -item.training_newey_west_t_statistic,
            -item.training_mean_session_conditional_excess_return,
            item.minimum_body_return_bps,
            item.candidate_id,
        ),
    )
    selected_spec = OpeningTripleBullishSpec(
        cost_bps=cost_bps,
        minimum_body_return_bps=selected.minimum_body_return_bps,
        directions=selected.directions,
        combination="ALL",
    )
    test_rows = _opening_rule_observations(
        observations,
        trading_days=test_days,
        spec=selected_spec,
    )
    (
        test_signal_count,
        test_signal_sessions,
        test_session_mean,
        test_newey_west_lag,
        test_newey_west_standard_error,
        test_newey_west_t_statistic,
        test_confidence_lower,
        test_confidence_upper,
    ) = _opening_rule_session_statistics(test_rows)
    test_strategy, test_benchmark, _ = _evaluate_observations(
        test_rows,
        list(test_days),
        cost_bps=cost_bps,
    )
    locked_test_cost_stress: dict[int, IntradayCostStress] = {}
    for fixed_cost_bps in _COST_STRESS_BPS:
        stress_strategy, stress_benchmark, _ = _evaluate_observations(
            test_rows,
            list(test_days),
            cost_bps=Decimal(fixed_cost_bps),
        )
        locked_test_cost_stress[fixed_cost_bps] = IntradayCostStress(
            cost_bps=fixed_cost_bps,
            strategy_metrics=stress_strategy,
            benchmark_metrics=stress_benchmark,
        )
    fixed_cost_returns = tuple(
        locked_test_cost_stress[value].strategy_metrics.total_return
        for value in _COST_STRESS_BPS
    )
    locked_test_cost_stress_criteria = {
        "zero_cost_locked_test_total_return_positive": (
            locked_test_cost_stress[0].strategy_metrics.total_return > 0
        ),
        "all_fixed_cost_locked_test_total_returns_positive": all(
            value > 0 for value in fixed_cost_returns
        ),
        "twenty_bps_locked_test_excess_return_positive": (
            locked_test_cost_stress[20].strategy_metrics.excess_return > 0
        ),
        "locked_test_total_return_non_increasing_with_cost": all(
            left >= right
            for left, right in zip(
                fixed_cost_returns[:-1],
                fixed_cost_returns[1:],
                strict=True,
            )
        ),
    }
    locked_test_sectors = _group_results(
        test_rows,
        list(test_days),
        cost_bps=cost_bps,
        key=lambda item: (item.sector_code, item.sector_name),
    )
    sector_identities = tuple(
        (row.group_id, row.group_name) for row in locked_test_sectors
    )
    locked_test_sector_exclusions: list[IntradayFactorGroup] = []
    for excluded_sector_code, excluded_sector_name in sector_identities:
        remaining_rows = [
            row
            for row in test_rows
            if row.sector_code != excluded_sector_code
        ]
        exclusion = _group_results(
            remaining_rows,
            list(test_days),
            cost_bps=cost_bps,
            key=lambda _item: (
                excluded_sector_code,
                excluded_sector_name,
            ),
        )
        if len(exclusion) != 1:
            raise RuntimeError("sector exclusion must produce exactly one result")
        locked_test_sector_exclusions.append(exclusion[0])
    positive_sector_count = sum(
        row.strategy_metrics.total_return > 0 for row in locked_test_sectors
    )
    positive_exclusion_count = sum(
        row.strategy_metrics.total_return > 0
        for row in locked_test_sector_exclusions
    )
    locked_test_sector_stress_criteria = {
        "every_locked_test_sector_has_signal": all(
            row.signal_count > 0 for row in locked_test_sectors
        ),
        "positive_locked_test_sector_ratio_at_least_0_60": (
            Decimal(positive_sector_count) / len(locked_test_sectors)
            >= Decimal("0.60")
        ),
        "positive_leave_one_sector_out_ratio_at_least_0_80": (
            Decimal(positive_exclusion_count)
            / len(locked_test_sector_exclusions)
            >= Decimal("0.80")
        ),
        "worst_leave_one_sector_out_total_return_non_negative": min(
            row.strategy_metrics.total_return
            for row in locked_test_sector_exclusions
        )
        >= 0,
    }
    test_criteria = {
        "minimum_250_test_observations": len(test_rows) >= 250,
        "minimum_twenty_test_signals": test_signal_count >= 20,
        "minimum_ten_test_signal_sessions": test_signal_sessions >= 10,
        "positive_test_session_conditional_excess": test_session_mean > 0,
        "test_newey_west_t_statistic_above_1_96": (
            test_newey_west_t_statistic > Decimal("1.96")
        ),
        "test_strategy_total_return_positive": test_strategy.total_return > 0,
        "test_strategy_excess_return_positive": test_strategy.excess_return > 0,
    }
    selection_known_at = datetime.combine(
        test_days[0],
        _EXIT_TIME,
        _CHINA_TIME,
    )
    overfitting_audit = _combinatorial_selection_bias_audit(
        tuple(candidates),
        candidate_daily,
        candidate_selection_daily,
        trading_days=trading_days,
    )
    return IntradayFactorDiscoveryResult(
        candidate_count=len(candidates),
        candidates=tuple(candidates),
        training_start=training_days[0],
        training_end=training_days[-1],
        training_session_count=len(training_days),
        test_start=test_days[0],
        test_end=test_days[-1],
        test_session_count=len(test_days),
        selection_known_at=selection_known_at,
        selected_candidate_id=selected.candidate_id,
        selected_directions=selected.directions,
        selected_minimum_body_return_bps=(
            selected.minimum_body_return_bps
        ),
        selected_factor_expression=selected.factor_expression,
        selected_training_gate_passed=selected.training_gate_passed,
        overfitting_audit=overfitting_audit,
        locked_test_cost_stress=locked_test_cost_stress,
        locked_test_cost_stress_criteria=locked_test_cost_stress_criteria,
        locked_test_cost_stress_passed=all(
            locked_test_cost_stress_criteria.values()
        ),
        locked_test_sectors=locked_test_sectors,
        locked_test_sector_exclusions=tuple(
            locked_test_sector_exclusions
        ),
        locked_test_sector_stress_criteria=(
            locked_test_sector_stress_criteria
        ),
        locked_test_sector_stress_passed=all(
            locked_test_sector_stress_criteria.values()
        ),
        test_observation_count=len(test_rows),
        test_signal_count=test_signal_count,
        test_signal_session_count=test_signal_sessions,
        test_mean_session_conditional_excess_return=test_session_mean,
        test_newey_west_lag=test_newey_west_lag,
        test_newey_west_standard_error=test_newey_west_standard_error,
        test_newey_west_t_statistic=test_newey_west_t_statistic,
        test_confidence_interval_95_lower=test_confidence_lower,
        test_confidence_interval_95_upper=test_confidence_upper,
        test_strategy_metrics=test_strategy,
        test_benchmark_metrics=test_benchmark,
        test_gate_criteria=test_criteria,
        test_gate_passed=all(test_criteria.values()),
        selection_uses_test_returns=False,
        promotion="NOT_ELIGIBLE",
        state_mutation=False,
        external_order_transmission=False,
    )


def _combinatorial_selection_bias_audit(
    candidates: tuple[IntradayDiscoveryCandidate, ...],
    candidate_daily: Mapping[str, tuple[IntradayFactorDay, ...]],
    candidate_selection_daily: Mapping[
        str,
        tuple[tuple[date, int, int, Decimal | None], ...],
    ],
    *,
    trading_days: tuple[date, ...],
) -> IntradaySelectionBiasAudit:
    block_count = 8
    base_size, remainder = divmod(len(trading_days), block_count)
    blocks: list[tuple[date, ...]] = []
    offset = 0
    for block_index in range(block_count):
        block_size = base_size + (1 if block_index < remainder else 0)
        blocks.append(trading_days[offset : offset + block_size])
        offset += block_size
    block_records = tuple(
        IntradaySelectionBiasBlock(
            block_id=index + 1,
            start=block[0],
            end=block[-1],
            session_count=len(block),
        )
        for index, block in enumerate(blocks)
    )

    split_rows: list[IntradaySelectionBiasSplit] = []
    selected_counts: dict[str, int] = defaultdict(int)
    block_ids = tuple(range(block_count))
    for split_index, training_block_indexes in enumerate(
        combinations(block_ids, block_count // 2),
        start=1,
    ):
        training_block_set = set(training_block_indexes)
        test_block_indexes = tuple(
            value for value in block_ids if value not in training_block_set
        )
        training_days = frozenset(
            day
            for block_index in training_block_indexes
            for day in blocks[block_index]
        )
        test_days = frozenset(
            day
            for block_index in test_block_indexes
            for day in blocks[block_index]
        )
        training_ranks = {
            candidate.candidate_id: _discovery_selection_rank(
                candidate_selection_daily[candidate.candidate_id],
                trading_days=training_days,
                minimum_body_return_bps=(
                    candidate.minimum_body_return_bps
                ),
            )
            for candidate in candidates
        }
        selected = min(
            candidates,
            key=lambda candidate: (
                not training_ranks[candidate.candidate_id][0],
                -training_ranks[candidate.candidate_id][1],
                -training_ranks[candidate.candidate_id][2],
                candidate.minimum_body_return_bps,
                candidate.candidate_id,
            ),
        )
        training_excess_return = _compounded_excess_return(
            candidate_daily[selected.candidate_id],
            trading_days=training_days,
        )
        test_scores = {
            candidate.candidate_id: _compounded_excess_return(
                candidate_daily[candidate.candidate_id],
                trading_days=test_days,
            )
            for candidate in candidates
        }
        test_ranks = {
            candidate.candidate_id: _discovery_selection_rank(
                candidate_selection_daily[candidate.candidate_id],
                trading_days=test_days,
                minimum_body_return_bps=(
                    candidate.minimum_body_return_bps
                ),
            )
            for candidate in candidates
        }
        selected_test_score = test_scores[selected.candidate_id]
        selected_test_rank = test_ranks[selected.candidate_id]
        lower_count = sum(
            rank < selected_test_rank for rank in test_ranks.values()
        )
        equal_count = sum(
            rank == selected_test_rank for rank in test_ranks.values()
        )
        average_rank = Decimal(lower_count) + (
            Decimal(equal_count) + Decimal("1")
        ) / Decimal("2")
        rank_percentile = (
            average_rank - Decimal("0.5")
        ) / Decimal(len(candidates))
        rank_logit = Decimal(
            str(log(float(rank_percentile / (Decimal("1") - rank_percentile))))
        )
        below_median = rank_logit <= 0
        selected_counts[selected.candidate_id] += 1
        split_rows.append(
            IntradaySelectionBiasSplit(
                split_id=f"CSCV-{split_index:03d}",
                training_block_ids=tuple(
                    value + 1 for value in training_block_indexes
                ),
                test_block_ids=tuple(
                    value + 1 for value in test_block_indexes
                ),
                selected_candidate_id=selected.candidate_id,
                training_excess_return=training_excess_return,
                test_excess_return=selected_test_score,
                test_average_rank=average_rank,
                test_rank_percentile=rank_percentile,
                test_rank_logit=rank_logit,
                below_median=below_median,
            )
        )

    below_median_count = sum(row.below_median for row in split_rows)
    positive_oos_count = sum(row.test_excess_return > 0 for row in split_rows)
    ordered_oos = sorted(row.test_excess_return for row in split_rows)
    middle = len(ordered_oos) // 2
    median_oos = (ordered_oos[middle - 1] + ordered_oos[middle]) / Decimal("2")
    most_selected_candidate_id, most_selected_count = min(
        selected_counts.items(),
        key=lambda item: (-item[1], item[0]),
    )
    split_count = len(split_rows)
    probability_of_backtest_overfitting = (
        Decimal(below_median_count) / split_count
    )
    selected_oos_positive_excess_ratio = Decimal(positive_oos_count) / split_count
    criteria = {
        "probability_of_backtest_overfitting_below_0_25": (
            probability_of_backtest_overfitting < Decimal("0.25")
        ),
        "selected_oos_positive_excess_ratio_at_least_0_60": (
            selected_oos_positive_excess_ratio >= Decimal("0.60")
        ),
        "median_selected_oos_excess_return_positive": median_oos > 0,
    }
    return IntradaySelectionBiasAudit(
        method="COMBINATORIALLY_SYMMETRIC_CROSS_VALIDATION",
        block_count=block_count,
        split_count=split_count,
        selection_metric=(
            "TRAINING_GATE_THEN_NEWEY_WEST_T_THEN_SESSION_CONDITIONAL_EXCESS"
        ),
        rank_tie_method="AVERAGE_RANK",
        blocks=block_records,
        splits=tuple(split_rows),
        below_median_split_count=below_median_count,
        probability_of_backtest_overfitting=probability_of_backtest_overfitting,
        selected_oos_positive_excess_split_count=positive_oos_count,
        selected_oos_positive_excess_ratio=selected_oos_positive_excess_ratio,
        median_selected_oos_excess_return=median_oos,
        unique_selected_candidate_count=len(selected_counts),
        most_selected_candidate_id=most_selected_candidate_id,
        most_selected_candidate_count=most_selected_count,
        criteria=criteria,
        passed=all(criteria.values()),
    )


def _discovery_selection_rank(
    daily: tuple[tuple[date, int, int, Decimal | None], ...],
    *,
    trading_days: frozenset[date],
    minimum_body_return_bps: Decimal,
) -> tuple[bool, Decimal, Decimal, Decimal]:
    selected = [row for row in daily if row[0] in trading_days]
    observation_count = sum(row[1] for row in selected)
    signal_count = sum(row[2] for row in selected)
    session_returns = [
        value
        for _, _, _, value in selected
        if value is not None
    ]
    session_mean, _, _, t_statistic, _, _ = _newey_west_mean_statistics(
        session_returns
    )
    passed = all(
        (
            observation_count >= 250,
            signal_count >= 20,
            len(session_returns) >= 10,
            session_mean > 0,
            t_statistic > Decimal("1.96"),
        )
    )
    return passed, t_statistic, session_mean, -minimum_body_return_bps


def _compounded_excess_return(
    daily: tuple[IntradayFactorDay, ...],
    *,
    trading_days: frozenset[date],
) -> Decimal:
    strategy_equity = Decimal("1")
    benchmark_equity = Decimal("1")
    for row in daily:
        if row.trading_day not in trading_days:
            continue
        strategy_equity *= Decimal("1") + row.strategy_return
        benchmark_equity *= Decimal("1") + row.benchmark_return
    return strategy_equity - benchmark_equity


def _opening_rule_observations(
    observations: tuple[IntradayFactorObservation, ...],
    *,
    trading_days: tuple[date, ...],
    spec: OpeningTripleBullishSpec,
) -> list[IntradayFactorObservation]:
    selected_days = set(trading_days)
    threshold = spec.minimum_body_return_bps / Decimal("10000")
    output: list[IntradayFactorObservation] = []
    for observation in observations:
        if observation.trading_day not in selected_days:
            continue
        score = spec.directional_score(observation.ten_minute_body_returns)
        output.append(
            replace(
                observation,
                signal=score > threshold,
                directional_rule_score=score,
            )
        )
    return output


def _opening_rule_session_statistics(
    observations: list[IntradayFactorObservation],
) -> tuple[int, int, Decimal, int, Decimal, Decimal, Decimal, Decimal]:
    signal_count = sum(item.signal for item in observations)
    session_returns = [
        value for _, value in _session_conditional_excess_returns(observations)
    ]
    (
        mean_value,
        lag,
        standard_error,
        t_statistic,
        confidence_lower,
        confidence_upper,
    ) = _newey_west_mean_statistics(session_returns)
    return (
        signal_count,
        len(session_returns),
        mean_value,
        lag,
        standard_error,
        t_statistic,
        confidence_lower,
        confidence_upper,
    )


def evaluate_opening_rule_correlations(
    observations: tuple[IntradayFactorObservation, ...],
    specs: tuple[OpeningTripleBullishSpec, ...],
    *,
    redundancy_threshold: Decimal = Decimal("0.90"),
) -> IntradayRuleCorrelationResult:
    if not 2 <= len(specs) <= 8:
        raise ValueError("factor correlation requires between two and eight rules")
    if not observations:
        raise ValueError("factor correlation requires observations")
    if (
        not redundancy_threshold.is_finite()
        or redundancy_threshold <= 0
        or redundancy_threshold > 1
    ):
        raise ValueError("redundancy threshold must be finite and in (0, 1]")
    score_series = [
        [
            spec.directional_score(observation.ten_minute_body_returns)
            for observation in observations
        ]
        for spec in specs
    ]
    matrix: list[list[Decimal]] = [
        [Decimal("0") for _ in specs] for _ in specs
    ]
    pairs: list[IntradayRuleCorrelationPair] = []
    maximum_absolute_correlation = Decimal("0")
    for left_index, left in enumerate(score_series):
        for right_index in range(left_index, len(specs)):
            right = score_series[right_index]
            correlation = (
                Decimal("1")
                if left_index == right_index or left == right
                else _pearson(left, right)
            )
            matrix[left_index][right_index] = correlation
            matrix[right_index][left_index] = correlation
            if left_index == right_index:
                continue
            absolute_correlation = abs(correlation)
            maximum_absolute_correlation = max(
                maximum_absolute_correlation,
                absolute_correlation,
            )
            pairs.append(
                IntradayRuleCorrelationPair(
                    left_rule_id=f"R{left_index + 1:02d}",
                    right_rule_id=f"R{right_index + 1:02d}",
                    correlation=correlation,
                    redundant=absolute_correlation >= redundancy_threshold,
                )
            )
    return IntradayRuleCorrelationResult(
        observation_count=len(observations),
        matrix=tuple(tuple(row) for row in matrix),
        pairs=tuple(pairs),
        redundancy_threshold=redundancy_threshold,
        maximum_absolute_correlation=maximum_absolute_correlation,
        passed=not any(pair.redundant for pair in pairs),
    )


def evaluate_opening_rule_effectiveness(
    observations: tuple[IntradayFactorObservation, ...],
    specs: tuple[OpeningTripleBullishSpec, ...],
    *,
    family_alpha: Decimal = Decimal("0.05"),
) -> tuple[IntradayRuleEffectiveness, ...]:
    if not 2 <= len(specs) <= 8:
        raise ValueError("factor effectiveness requires between two and eight rules")
    if not observations:
        raise ValueError("factor effectiveness requires observations")
    if (
        not family_alpha.is_finite()
        or family_alpha <= 0
        or family_alpha >= 1
    ):
        raise ValueError("factor effectiveness alpha must be finite and in (0, 1)")
    statistics: list[
        tuple[
            int,
            int,
            Decimal,
            int,
            Decimal,
            Decimal,
            Decimal,
            Decimal,
            Decimal,
        ]
    ] = []
    raw_p_values: list[Decimal] = []
    for spec in specs:
        threshold = spec.minimum_body_return_bps / Decimal("10000")
        rule_observations = [
            replace(
                observation,
                signal=(score := spec.directional_score(
                    observation.ten_minute_body_returns
                ))
                > threshold,
                directional_rule_score=score,
            )
            for observation in observations
        ]
        session_returns = [
            value
            for _, value in _session_conditional_excess_returns(
                rule_observations
            )
        ]
        (
            mean_value,
            lag,
            standard_error,
            t_statistic,
            confidence_lower,
            confidence_upper,
        ) = _newey_west_mean_statistics(session_returns)
        raw_p_value = Decimal(
            str(erfc(abs(float(t_statistic)) / sqrt(2)))
        )
        statistics.append(
            (
                sum(observation.signal for observation in rule_observations),
                len(session_returns),
                mean_value,
                lag,
                standard_error,
                t_statistic,
                confidence_lower,
                confidence_upper,
                raw_p_value,
            )
        )
        raw_p_values.append(raw_p_value)
    adjusted_p_values = _holm_adjusted_p_values(raw_p_values)
    output: list[IntradayRuleEffectiveness] = []
    for index, (spec, statistic, adjusted_p_value) in enumerate(
        zip(specs, statistics, adjusted_p_values, strict=True),
        start=1,
    ):
        (
            signal_count,
            signal_session_count,
            mean_value,
            lag,
            standard_error,
            t_statistic,
            confidence_lower,
            confidence_upper,
            raw_p_value,
        ) = statistic
        criteria = {
            "minimum_observation_count": len(observations) >= 250,
            "minimum_signal_count": signal_count >= 20,
            "minimum_ten_clustered_signal_sessions": signal_session_count >= 10,
            "positive_session_conditional_excess": mean_value > 0,
            "newey_west_t_statistic_above_1_96": (
                t_statistic > Decimal("1.96")
            ),
            "holm_adjusted_p_value_at_most_0_05": (
                adjusted_p_value <= family_alpha
            ),
        }
        output.append(
            IntradayRuleEffectiveness(
                rule_id=f"R{index:02d}",
                minimum_body_return_bps=spec.minimum_body_return_bps,
                observation_count=len(observations),
                signal_count=signal_count,
                clustered_signal_session_count=signal_session_count,
                mean_session_conditional_excess_return=mean_value,
                newey_west_lag=lag,
                newey_west_standard_error=standard_error,
                newey_west_t_statistic=t_statistic,
                confidence_interval_95_lower=confidence_lower,
                confidence_interval_95_upper=confidence_upper,
                raw_p_value=raw_p_value,
                holm_adjusted_p_value=adjusted_p_value,
                criteria=criteria,
                passed=all(criteria.values()),
            )
        )
    return tuple(output)


def _holm_adjusted_p_values(values: list[Decimal]) -> list[Decimal]:
    if not values:
        raise ValueError("Holm adjustment requires p-values")
    if any(not value.is_finite() or not 0 <= value <= 1 for value in values):
        raise ValueError("Holm adjustment requires finite p-values in [0, 1]")
    adjusted = [Decimal("0") for _ in values]
    running_maximum = Decimal("0")
    for rank, index in enumerate(sorted(range(len(values)), key=values.__getitem__)):
        candidate = min(
            Decimal("1"),
            values[index] * Decimal(len(values) - rank),
        )
        running_maximum = max(running_maximum, candidate)
        adjusted[index] = running_maximum
    return adjusted


def paired_stationary_bootstrap_excess_returns(
    strategy_returns: tuple[Decimal, ...],
    benchmark_returns: tuple[Decimal, ...],
    *,
    replications: int = 1000,
    expected_block_length: int = 10,
    seed: int = 20260827,
) -> IntradayPairedBootstrapSummary:
    if len(strategy_returns) != len(benchmark_returns):
        raise ValueError("paired bootstrap requires equal-length return series")
    if len(strategy_returns) < 20:
        raise ValueError("paired bootstrap requires at least 20 observations")
    if replications < 100:
        raise ValueError("paired bootstrap replications must be at least 100")
    if not 2 <= expected_block_length <= len(strategy_returns):
        raise ValueError(
            "paired bootstrap expected block length must be within the sample"
        )
    if isinstance(seed, bool) or not isinstance(seed, int):
        raise ValueError("paired bootstrap seed must be an integer")
    if any(
        not value.is_finite() or value <= Decimal("-1")
        for value in (*strategy_returns, *benchmark_returns)
    ):
        raise ValueError(
            "paired bootstrap returns must be finite and greater than -1"
        )

    observed_excess = _compound_returns(strategy_returns) - _compound_returns(
        benchmark_returns
    )
    generator = Random(seed)
    restart_probability = 1 / expected_block_length
    samples: list[Decimal] = []
    for _ in range(replications):
        index = generator.randrange(len(strategy_returns))
        strategy_wealth = Decimal("1")
        benchmark_wealth = Decimal("1")
        for _ in strategy_returns:
            strategy_wealth *= Decimal("1") + strategy_returns[index]
            benchmark_wealth *= Decimal("1") + benchmark_returns[index]
            if generator.random() < restart_probability:
                index = generator.randrange(len(strategy_returns))
            else:
                index = (index + 1) % len(strategy_returns)
        samples.append(strategy_wealth - benchmark_wealth)
    samples.sort()
    return IntradayPairedBootstrapSummary(
        method="PAIRED_STATIONARY_BOOTSTRAP",
        observation_count=len(strategy_returns),
        replications=replications,
        expected_block_length=expected_block_length,
        seed=seed,
        observed_excess_total_return=observed_excess,
        p05_excess_total_return=_bootstrap_percentile(samples, Decimal("0.05")),
        median_excess_total_return=_bootstrap_percentile(
            samples,
            Decimal("0.5"),
        ),
        p95_excess_total_return=_bootstrap_percentile(samples, Decimal("0.95")),
        probability_positive_excess=(
            Decimal(sum(value > 0 for value in samples)) / replications
        ),
    )


def _compound_returns(values: tuple[Decimal, ...]) -> Decimal:
    wealth = Decimal("1")
    for value in values:
        wealth *= Decimal("1") + value
    return wealth - Decimal("1")


def _bootstrap_percentile(values: list[Decimal], quantile: Decimal) -> Decimal:
    position = Decimal(len(values) - 1) * quantile
    lower = int(position)
    upper = min(lower + 1, len(values) - 1)
    fraction = position - lower
    return values[lower] + (values[upper] - values[lower]) * fraction
