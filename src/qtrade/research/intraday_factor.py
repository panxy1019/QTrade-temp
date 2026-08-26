from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

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
_COST_STRESS_BPS = (0, 5, 10, 20)


@dataclass(frozen=True, slots=True)
class OpeningTripleBullishSpec:
    cost_bps: Decimal = Decimal("5")
    minimum_body_return_bps: Decimal = Decimal("0")

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

    @property
    def factor_id(self) -> str:
        return "opening-three-ten-minute-bullish-v1"


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
class IntradayFactorResearchResult:
    factor_id: str
    start: date
    end: date
    selected_session_count: int
    symbol_count: int
    sector_count: int
    observations: tuple[IntradayFactorObservation, ...]
    factor_metrics: IntradayFactorMetrics
    event_summary: IntradayEventSummary
    correlations: Mapping[str, Decimal]
    strategy_metrics: PerformanceMetrics
    benchmark_metrics: PerformanceMetrics
    cost_stress: Mapping[int, IntradayCostStress]
    daily: tuple[IntradayFactorDay, ...]
    sectors: tuple[IntradayFactorGroup, ...]
    symbols: tuple[IntradayFactorGroup, ...]
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
    quantiles: int = 5,
) -> IntradayFactorResearchResult:
    selected_spec = spec or OpeningTripleBullishSpec()
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
    observations: list[IntradayFactorObservation] = []
    for day_index, trading_day in enumerate(selected_days):
        exit_day = sessions[start_index + day_index + 1]
        for symbol in symbols:
            day_bars = grouped[(symbol, trading_day)]
            exit_bars = grouped[(symbol, exit_day)]
            first_six = day_bars[:6]
            ten_minute_returns = tuple(
                pair[-1].close / pair[0].open - Decimal("1")
                for pair in (first_six[0:2], first_six[2:4], first_six[4:6])
            )
            signal = all(value > threshold for value in ten_minute_returns)
            signal_bar = first_six[-1]
            entry_bar = day_bars[6]
            exit_bar = exit_bars[0]
            if _china_time(signal_bar.timestamp) != _SIGNAL_TIME:
                raise ValueError("factor signal must be known at the 10:00 close")
            if _china_time(entry_bar.timestamp) != _ENTRY_TIME:
                raise ValueError("factor entry must use the 10:05 source-bar open")
            if _china_time(exit_bar.timestamp) != _EXIT_TIME:
                raise ValueError("factor exit must use the next-session first open")
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
    criteria = {
        "minimum_observation_count": event_summary.observation_count >= 250,
        "minimum_signal_count": event_summary.signal_count >= 20,
        "positive_mean_signal_return": (
            event_summary.mean_signal_forward_return > 0
        ),
        "positive_conditional_excess_return": (
            event_summary.conditional_excess_return > 0
        ),
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
    }
    return IntradayFactorResearchResult(
        factor_id=selected_spec.factor_id,
        start=start,
        end=end,
        selected_session_count=len(selected_days),
        symbol_count=len(symbols),
        sector_count=len({value[0] for value in sectors.values()}),
        observations=tuple(observations),
        factor_metrics=factor_metrics,
        event_summary=event_summary,
        correlations=correlations,
        strategy_metrics=base_evaluation[0],
        benchmark_metrics=base_evaluation[1],
        cost_stress=stress,
        daily=base_evaluation[2],
        sectors=sector_groups,
        symbols=symbol_groups,
        research_gate=IntradayResearchGate(
            passed=all(criteria.values()),
            criteria=criteria,
        ),
        causal_contract={
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
        },
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
