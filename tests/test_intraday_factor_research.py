from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from qtrade.data import Bar
from qtrade.market_data import SINA_FIVE_MINUTE_SESSION_TIMES
from qtrade.research.intraday_factor import (
    IntradayExitSpec,
    OpeningTripleBullishSpec,
    _holm_adjusted_p_values,
    _newey_west_mean_statistics,
    _temporal_stability_validation,
    discover_opening_direction_rules,
    evaluate_opening_rule_correlations,
    evaluate_opening_rule_effectiveness,
    paired_stationary_bootstrap_excess_returns,
    run_opening_triple_bullish_research,
)

CHINA = timezone(timedelta(hours=8))


def test_newey_west_significance_fails_closed_for_empty_or_zero_variance() -> None:
    assert _newey_west_mean_statistics([]) == (
        Decimal("0"),
        0,
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
        Decimal("0"),
    )
    mean_value, lag, standard_error, t_statistic, lower, upper = (
        _newey_west_mean_statistics([Decimal("0.01")] * 10)
    )
    assert mean_value == lower == upper == Decimal("0.01")
    assert lag > 0
    assert standard_error == 0
    assert t_statistic == 0


def test_holm_adjustment_is_monotonic_in_sorted_hypothesis_order() -> None:
    assert _holm_adjusted_p_values(
        [Decimal("0.01"), Decimal("0.04"), Decimal("0.03")]
    ) == [Decimal("0.03"), Decimal("0.06"), Decimal("0.06")]


def test_paired_stationary_bootstrap_preserves_pairing_and_is_deterministic() -> None:
    strategy = tuple(
        Decimal("0.01") if index % 4 else Decimal("-0.006")
        for index in range(60)
    )
    benchmark = tuple(
        Decimal("0.004") if index % 5 else Decimal("-0.003")
        for index in range(60)
    )
    first = paired_stationary_bootstrap_excess_returns(
        strategy,
        benchmark,
        replications=200,
        expected_block_length=5,
        seed=20260827,
    )
    second = paired_stationary_bootstrap_excess_returns(
        strategy,
        benchmark,
        replications=200,
        expected_block_length=5,
        seed=20260827,
    )

    assert first == second
    assert first.observation_count == 60
    assert first.observed_excess_total_return == (
        _compound_for_test(strategy) - _compound_for_test(benchmark)
    )
    assert (
        first.p05_excess_total_return
        <= first.median_excess_total_return
        <= first.p95_excess_total_return
    )
    assert Decimal("0") <= first.probability_positive_excess <= Decimal("1")
    with pytest.raises(ValueError, match="equal-length"):
        paired_stationary_bootstrap_excess_returns(
            strategy,
            benchmark[:-1],
        )
    with pytest.raises(ValueError, match="at least 20"):
        paired_stationary_bootstrap_excess_returns(
            strategy[:10],
            benchmark[:10],
        )


def _compound_for_test(values: tuple[Decimal, ...]) -> Decimal:
    wealth = Decimal("1")
    for value in values:
        wealth *= Decimal("1") + value
    return wealth - Decimal("1")


def _bars(
    *,
    days: tuple[date, ...],
    symbols: tuple[str, ...],
    signal_symbols: set[str],
) -> list[Bar]:
    records: list[Bar] = []
    for symbol_index, symbol in enumerate(symbols):
        previous_close = Decimal("100") + Decimal(symbol_index)
        for day_index, trading_day in enumerate(days):
            day_open = previous_close
            values: list[tuple[Decimal, Decimal]] = []
            running = day_open
            for bar_index, _ in enumerate(SINA_FIVE_MINUTE_SESSION_TIMES):
                bar_open = running
                if bar_index < 6 and symbol in signal_symbols:
                    bar_close = bar_open * Decimal("1.001")
                elif bar_index in {4, 5}:
                    bar_close = bar_open * Decimal("0.999")
                else:
                    bar_close = bar_open
                if bar_index == 6:
                    # A deliberate gap proves entry uses the next 5-minute open,
                    # not the 10:00 signal close.
                    bar_open = bar_open * Decimal("1.02")
                    bar_close = bar_open
                values.append((bar_open, bar_close))
                running = bar_close
            # The next session open determines the T+1 exit return.
            previous_close = values[-1][1] * (
                Decimal("1.01")
                if symbol in signal_symbols and day_index < len(days) - 1
                else Decimal("1")
            )
            for bar_time, (bar_open, bar_close) in zip(
                SINA_FIVE_MINUTE_SESSION_TIMES, values, strict=True
            ):
                timestamp = datetime.combine(
                    trading_day,
                    bar_time,
                    CHINA,
                )
                records.append(
                    Bar(
                        symbol=symbol,
                        exchange="SSE",
                        timestamp=timestamp,
                        trading_day=trading_day,
                        open=bar_open,
                        high=max(bar_open, bar_close),
                        low=min(bar_open, bar_close),
                        close=bar_close,
                        volume=Decimal("1000") + Decimal(bar_index),
                    )
                )
    return records


def test_three_ten_minute_bullish_signal_executes_next_bar_and_exits_t_plus_one() -> None:
    days = tuple(date(2026, 8, 3) + timedelta(days=index) for index in range(6))
    symbols = tuple(f"6000{index:02d}" for index in range(5))
    result = run_opening_triple_bullish_research(
        _bars(days=days, symbols=symbols, signal_symbols={symbols[0], symbols[1]}),
        sectors={symbol: (f"S{index}", f"板块{index}") for index, symbol in enumerate(symbols)},
        start=days[0],
        end=days[-2],
        spec=OpeningTripleBullishSpec(cost_bps=Decimal("5")),
        quantiles=5,
    )

    first = next(item for item in result.observations if item.signal)
    assert first.signal_at.timetz().replace(tzinfo=None) == time(10, 0)
    assert first.entry_at.timetz().replace(tzinfo=None) == time(10, 5)
    assert first.exit_at.timetz().replace(tzinfo=None) == time(9, 35)
    assert first.exit_at.date() == first.trading_day + timedelta(days=1)
    assert first.entry_price != first.signal_close
    assert first.gross_forward_return == first.exit_price / first.entry_price - 1
    assert result.causal_contract == {
        "factor_known_at": "THIRD_TEN_MINUTE_BAR_CLOSE_10_00",
        "entry_at": "NEXT_SOURCE_FIVE_MINUTE_BAR_OPEN_10_05",
        "exit_at": "NEXT_TRADING_SESSION_FIRST_FIVE_MINUTE_BAR_OPEN_T_PLUS_ONE",
        "source_bar_timestamp_convention": "BAR_END_TIME",
        "entry_bar_label": "10_05",
        "entry_interval_start": "10_00",
        "exit_bar_label": "NEXT_SESSION_09_35",
        "exit_interval_start": "NEXT_SESSION_09_30",
        "same_bar_execution": False,
        "t_plus_one_compliant": True,
    }
    assert result.external_order_transmission is False
    assert result.state_mutation is False


def test_three_ten_minute_bearish_exit_is_causal_and_has_a_time_stop() -> None:
    days = tuple(date(2026, 8, 3) + timedelta(days=index) for index in range(6))
    symbols = tuple(f"6002{index:02d}" for index in range(5))
    records = _bars(
        days=days,
        symbols=symbols,
        signal_symbols={symbols[0], symbols[1]},
    )
    bearish_exit_symbol = symbols[0]
    bearish_exit_day = days[1]
    bearish: list[Bar] = []
    for bar in records:
        bar_time = bar.timestamp.timetz().replace(tzinfo=None)
        if (
            bar.symbol == bearish_exit_symbol
            and bar.trading_day == bearish_exit_day
            and bar_time in {time(9, 40), time(9, 50), time(10, 0)}
        ):
            pair_open_index = {time(9, 40): 0, time(9, 50): 2, time(10, 0): 4}[
                bar_time
            ]
            pair_open = next(
                candidate.open
                for candidate in records
                if candidate.symbol == bearish_exit_symbol
                and candidate.trading_day == bearish_exit_day
                and candidate.timestamp.timetz().replace(tzinfo=None)
                == SINA_FIVE_MINUTE_SESSION_TIMES[pair_open_index]
            )
            close = pair_open * Decimal("0.999")
            bearish.append(replace(bar, close=close, low=min(bar.low, close)))
        else:
            bearish.append(bar)

    result = run_opening_triple_bullish_research(
        bearish,
        sectors={
            symbol: (f"S{index}", f"板块{index}")
            for index, symbol in enumerate(symbols)
        },
        start=days[0],
        end=days[-2],
        exit_spec=IntradayExitSpec(
            rule=(
                "NEXT_SESSION_THREE_TEN_MINUTE_BEARISH_"
                "ELSE_14_55_TIME_STOP"
            ),
            minimum_body_return_bps=Decimal("5"),
        ),
        quantiles=5,
    )

    triggered = next(
        item
        for item in result.observations
        if item.trading_day == days[0] and item.symbol == bearish_exit_symbol
    )
    timed_out = next(
        item
        for item in result.observations
        if item.trading_day == days[0] and item.symbol == symbols[1]
    )
    assert triggered.signal is True
    assert triggered.exit_condition_triggered is True
    assert triggered.exit_condition_score > Decimal("0.00099")
    assert triggered.conditional_exit_price == triggered.exit_price
    assert triggered.exit_timing_advantage == (
        triggered.conditional_exit_price / triggered.fallback_exit_price
        - Decimal("1")
    )
    assert triggered.exit_at.date() == bearish_exit_day
    assert triggered.exit_at.timetz().replace(tzinfo=None) == time(10, 5)
    assert timed_out.signal is True
    assert timed_out.exit_condition_triggered is False
    assert timed_out.exit_at.date() == bearish_exit_day
    assert timed_out.exit_at.timetz().replace(tzinfo=None) == time(15, 0)
    assert triggered.exit_at > datetime.combine(
        bearish_exit_day,
        time(10, 0),
        CHINA,
    )
    assert result.causal_contract == {
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
    validation = result.exit_factor_validation
    assert validation is not None
    assert validation.entry_signal_count > validation.trigger_count == 1
    assert validation.clustered_trigger_session_count == 1
    assert validation.mean_triggered_timing_advantage == (
        triggered.exit_timing_advantage
    )
    assert validation.criteria["minimum_twenty_exit_triggers"] is False
    assert validation.criteria[
        "minimum_ten_clustered_exit_trigger_sessions"
    ] is False
    assert validation.passed is False
    assert result.research_gate.criteria[
        "exit_factor_effectiveness_passed"
    ] is False


def test_intraday_exit_spec_rejects_unsafe_or_ignored_parameters() -> None:
    with pytest.raises(ValueError, match="unsupported intraday exit rule"):
        IntradayExitSpec(rule="EVAL")
    with pytest.raises(ValueError, match="finite and between 0 and 1000"):
        IntradayExitSpec(
            rule=(
                "NEXT_SESSION_THREE_TEN_MINUTE_BEARISH_"
                "ELSE_14_55_TIME_STOP"
            ),
            minimum_body_return_bps=Decimal("1001"),
        )
    with pytest.raises(ValueError, match="must be zero for NEXT_SESSION_OPEN"):
        IntradayExitSpec(minimum_body_return_bps=Decimal("1"))


def test_bounded_opening_rule_discovery_locks_training_before_disjoint_test() -> None:
    days = tuple(date(2026, 6, 1) + timedelta(days=index) for index in range(41))
    symbols = tuple(f"6003{index:02d}" for index in range(5))
    research = run_opening_triple_bullish_research(
        _bars(
            days=days,
            symbols=symbols,
            signal_symbols={symbols[0], symbols[1]},
        ),
        sectors={
            symbol: (f"S{index}", f"板块{index}")
            for index, symbol in enumerate(symbols)
        },
        start=days[0],
        end=days[-2],
        spec=OpeningTripleBullishSpec(cost_bps=Decimal("5")),
        quantiles=5,
    )

    first = discover_opening_direction_rules(
        research.observations,
        trading_days=days[:-1],
        cost_bps=Decimal("5"),
    )
    second = discover_opening_direction_rules(
        research.observations,
        trading_days=days[:-1],
        cost_bps=Decimal("5"),
    )

    assert first == second
    assert first.candidate_count == len(first.candidates) == 104
    assert first.training_session_count == first.test_session_count == 20
    assert first.training_end < first.test_start
    assert first.selection_known_at.date() == first.test_start
    assert first.selection_known_at.time() == time(9, 35)
    assert first.selection_known_at < datetime.combine(
        first.test_start,
        time(10, 0),
        CHINA,
    )
    assert first.selected_candidate_id in {
        candidate.candidate_id for candidate in first.candidates
    }
    assert len(first.selected_directions) == 3
    assert any(direction != "IGNORE" for direction in first.selected_directions)
    assert first.selected_minimum_body_return_bps in {
        Decimal("0"), Decimal("5"), Decimal("10"), Decimal("20")
    }
    assert first.test_observation_count == 20 * len(symbols)
    assert first.selection_uses_test_returns is False
    assert first.promotion == "NOT_ELIGIBLE"
    assert first.state_mutation is False
    assert first.external_order_transmission is False


def test_bounded_opening_direction_rule_uses_only_completed_ten_minute_bars() -> None:
    days = tuple(date(2026, 8, 3) + timedelta(days=index) for index in range(6))
    symbols = tuple(f"6004{index:02d}" for index in range(5))
    records = _bars(days=days, symbols=symbols, signal_symbols=set())
    mixed_symbol = symbols[0]
    mixed: list[Bar] = []
    for bar in records:
        bar_time = bar.timestamp.timetz().replace(tzinfo=None)
        if bar.symbol == mixed_symbol and bar_time == time(9, 40):
            close = bar.open * Decimal("1.001")
            mixed.append(replace(bar, close=close, high=max(bar.high, close)))
        elif bar.symbol == mixed_symbol and bar_time == time(9, 50):
            close = bar.open * Decimal("0.999")
            mixed.append(replace(bar, close=close, low=min(bar.low, close)))
        else:
            mixed.append(bar)

    result = run_opening_triple_bullish_research(
        mixed,
        sectors={symbol: (f"S{index}", f"板块{index}") for index, symbol in enumerate(symbols)},
        start=days[0],
        end=days[-2],
        spec=OpeningTripleBullishSpec(
            directions=("BULLISH", "BEARISH", "IGNORE"),
            combination="ALL",
        ),
        quantiles=5,
    )

    selected = [item for item in result.observations if item.signal]
    assert selected
    assert {item.symbol for item in selected} == {mixed_symbol}
    assert result.factor_id == "opening-ten-minute-direction-rule-v1"
    assert result.factor_expression == (
        "bullish_10m_0930_0940 AND bearish_10m_0940_0950"
    )
    assert result.factor_directions == ("BULLISH", "BEARISH", "IGNORE")
    assert result.factor_combination == "ALL"
    assert all(item.signal_at.time() == time(10, 0) for item in selected)
    assert all(item.entry_at.time() == time(10, 5) for item in selected)
    assert all(item.exit_at.date() > item.trading_day for item in selected)


def test_bounded_opening_direction_rule_rejects_unsafe_or_empty_definitions() -> None:
    with pytest.raises(ValueError, match="directions must contain exactly three"):
        OpeningTripleBullishSpec(directions=("BULLISH", "BEARISH"))  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unsupported opening direction"):
        OpeningTripleBullishSpec(directions=("BULLISH", "FUTURE", "IGNORE"))
    with pytest.raises(ValueError, match="at least one completed ten-minute bar"):
        OpeningTripleBullishSpec(directions=("IGNORE", "IGNORE", "IGNORE"))
    with pytest.raises(ValueError, match="combination must be ALL or ANY"):
        OpeningTripleBullishSpec(combination="EVAL")

    any_rule = OpeningTripleBullishSpec(
        directions=("BULLISH", "BEARISH", "IGNORE"),
        combination="ANY",
    )
    assert any_rule.factor_expression == (
        "bullish_10m_0930_0940 OR bearish_10m_0940_0950"
    )
    assert any_rule.directional_score(
        (Decimal("-0.001"), Decimal("-0.002"), Decimal("0"))
    ) == Decimal("0.002")


def test_opening_rule_correlations_are_symmetric_and_flag_duplicate_scores() -> None:
    days = tuple(date(2026, 7, 1) + timedelta(days=index) for index in range(7))
    symbols = tuple(f"6005{index:02d}" for index in range(10))
    result = run_opening_triple_bullish_research(
        _bars(days=days, symbols=symbols, signal_symbols=set(symbols[:4])),
        sectors={
            symbol: (f"S{index // 2}", f"板块{index // 2}")
            for index, symbol in enumerate(symbols)
        },
        start=days[0],
        end=days[-2],
    )
    default = OpeningTripleBullishSpec()
    mixed = OpeningTripleBullishSpec(
        directions=("BULLISH", "BEARISH", "IGNORE"),
        combination="ANY",
    )
    thresholded = OpeningTripleBullishSpec(
        minimum_body_return_bps=Decimal("30")
    )

    correlations = evaluate_opening_rule_correlations(
        result.observations,
        (default, default, mixed),
    )

    assert correlations.observation_count == 60
    assert correlations.matrix[0][0] == correlations.matrix[1][1] == 1
    assert correlations.matrix[0][1] == correlations.matrix[1][0] == 1
    assert correlations.pairs[0].left_rule_id == "R01"
    assert correlations.pairs[0].right_rule_id == "R02"
    assert correlations.pairs[0].redundant is True
    assert correlations.maximum_absolute_correlation == 1
    assert correlations.passed is False
    assert all(
        correlations.matrix[left][right] == correlations.matrix[right][left]
        for left in range(3)
        for right in range(3)
    )

    with pytest.raises(ValueError, match="between two and eight"):
        evaluate_opening_rule_correlations(result.observations, (default,))

    effectiveness = evaluate_opening_rule_effectiveness(
        result.observations,
        (default, default, thresholded, mixed),
    )
    assert [row.rule_id for row in effectiveness] == [
        "R01",
        "R02",
        "R03",
        "R04",
    ]
    assert effectiveness[0].signal_count == effectiveness[1].signal_count
    assert effectiveness[0].raw_p_value == effectiveness[1].raw_p_value
    assert effectiveness[0].holm_adjusted_p_value == (
        effectiveness[1].holm_adjusted_p_value
    )
    assert effectiveness[2].minimum_body_return_bps == Decimal("30")
    assert effectiveness[2].signal_count < effectiveness[0].signal_count
    assert all(
        Decimal("0") <= row.raw_p_value <= row.holm_adjusted_p_value <= 1
        for row in effectiveness
    )
    assert all(row.observation_count == 60 for row in effectiveness)
    assert all(row.passed is False for row in effectiveness)

    with pytest.raises(ValueError, match="between two and eight"):
        evaluate_opening_rule_effectiveness(result.observations, (default,))


def test_temporal_stability_uses_four_contiguous_blocks_and_flags_concentration() -> None:
    days = tuple(date(2026, 7, 1) + timedelta(days=index) for index in range(9))
    symbols = tuple(f"6006{index:02d}" for index in range(10))
    selected_days = list(days[:-1])
    result = run_opening_triple_bullish_research(
        _bars(days=days, symbols=symbols, signal_symbols=set(symbols[:4])),
        sectors={
            symbol: (f"S{index // 2}", f"板块{index // 2}")
            for index, symbol in enumerate(symbols)
        },
        start=selected_days[0],
        end=selected_days[-1],
    )
    returns_by_day = {
        trading_day: Decimal("0.10") if index < 2 else Decimal("-0.01")
        for index, trading_day in enumerate(selected_days)
    }
    observations = [
        replace(
            observation,
            gross_forward_return=(
                returns_by_day[observation.trading_day]
                if observation.signal
                else Decimal("0")
            ),
        )
        for observation in result.observations
    ]

    stability = _temporal_stability_validation(observations, selected_days)

    assert stability.block_count == 4
    assert [block.session_count for block in stability.blocks] == [2, 2, 2, 2]
    assert [block.start for block in stability.blocks] == selected_days[::2]
    assert [block.end for block in stability.blocks] == selected_days[1::2]
    assert all(block.signal_session_count == 2 for block in stability.blocks)
    assert stability.positive_block_count == 1
    assert stability.worst_block_mean_conditional_excess_return < 0
    assert stability.maximum_absolute_block_contribution_share > Decimal("0.60")
    assert stability.criteria["positive_block_ratio_at_least_75pct"] is False
    assert stability.criteria[
        "maximum_absolute_block_contribution_share_below_0_60"
    ] is False
    assert stability.passed is False


def test_research_reports_factor_effectiveness_correlation_cost_and_sector_stability() -> None:
    days = tuple(date(2026, 7, 1) + timedelta(days=index) for index in range(7))
    symbols = tuple(f"6001{index:02d}" for index in range(10))
    sectors = {
        symbol: (f"S{index // 2}", f"板块{index // 2}")
        for index, symbol in enumerate(symbols)
    }
    result = run_opening_triple_bullish_research(
        _bars(days=days, symbols=symbols, signal_symbols=set(symbols[:4])),
        sectors=sectors,
        start=days[0],
        end=days[-2],
        spec=OpeningTripleBullishSpec(cost_bps=Decimal("5")),
        quantiles=5,
    )

    assert result.factor_metrics.number_of_periods == 6
    assert result.factor_metrics.minimum_instruments == 10
    assert len(result.factor_metrics.quantile_returns) == 2
    assert result.factor_metrics.factor_turnover == 0
    assert result.event_summary.signal_count == 24
    assert result.event_summary.observation_count == 60
    assert result.event_summary.signal_rate == Decimal("0.4")
    assert result.event_summary.clustered_signal_session_count == 6
    assert 0 <= result.event_summary.newey_west_lag < 6
    assert result.event_summary.newey_west_standard_error.is_finite()
    assert result.event_summary.newey_west_t_statistic.is_finite()
    assert (
        result.event_summary.confidence_interval_95_lower
        <= result.event_summary.mean_session_conditional_excess_return
        <= result.event_summary.confidence_interval_95_upper
    )
    assert set(result.correlations) == {
        "opening_30m_return",
        "opening_30m_range_position",
    }
    assert all(Decimal("-1") <= value <= Decimal("1") for value in result.correlations.values())
    assert set(result.cost_stress) == {0, 5, 10, 20}
    assert result.strategy_metrics.number_of_periods == 6
    assert result.strategy_metrics.number_of_trades == 24
    assert result.strategy_metrics.total_return > result.benchmark_metrics.total_return
    assert len(result.daily) == 6
    assert len(result.sectors) == 5
    assert len(result.symbols) == 10
    assert result.stability_validation.block_count == 4
    assert result.stability_validation.blocks[0].start == days[0]
    assert result.stability_validation.blocks[-1].end == days[-2]
    assert all(
        left.end < right.start
        for left, right in zip(
            result.stability_validation.blocks[:-1],
            result.stability_validation.blocks[1:],
            strict=True,
        )
    )
    assert result.robustness_validation.candidate_thresholds_bps == (
        Decimal("0"),
        Decimal("5"),
        Decimal("10"),
        Decimal("20"),
    )
    assert result.robustness_validation.initial_training_session_count == 3
    assert result.robustness_validation.stitched_test_session_count == 3
    assert len(result.robustness_validation.folds) == 1
    fold = result.robustness_validation.folds[0]
    assert fold.training_end < fold.test_start
    assert fold.selection_known_at.timetz().replace(tzinfo=None) == time(9, 35)
    assert result.robustness_validation.criteria[
        "causal_selection_known_before_test_signal"
    ] is True
    assert result.research_gate.criteria["minimum_signal_count"] is True
    assert "minimum_ten_clustered_signal_sessions" in result.research_gate.criteria
    assert "newey_west_t_statistic_above_1_96" in result.research_gate.criteria
    assert "temporal_stability_passed" in result.research_gate.criteria
    assert result.promotion == "NOT_ELIGIBLE"
    assert "CURRENT_UNIVERSE_NON_PIT" in result.promotion_reasons


def test_intraday_factor_rejects_missing_grid_or_last_day_without_exit() -> None:
    days = tuple(date(2026, 8, 3) + timedelta(days=index) for index in range(3))
    symbols = tuple(f"6002{index:02d}" for index in range(5))
    sectors = {symbol: ("S", "板块") for symbol in symbols}
    records = _bars(days=days, symbols=symbols, signal_symbols={symbols[0]})

    with pytest.raises(ValueError, match="next verified trading session"):
        run_opening_triple_bullish_research(
            records,
            sectors=sectors,
            start=days[0],
            end=days[-1],
        )

    missing = records[:-1]
    with pytest.raises(ValueError, match="exact 48-bar source grid"):
        run_opening_triple_bullish_research(
            missing,
            sectors=sectors,
            start=days[0],
            end=days[-2],
        )


def test_first_walk_forward_selection_does_not_read_its_test_returns() -> None:
    days = tuple(date(2026, 7, 1) + timedelta(days=index) for index in range(21))
    symbols = tuple(f"6003{index:02d}" for index in range(5))
    sectors = {
        symbol: (f"S{index}", f"板块{index}")
        for index, symbol in enumerate(symbols)
    }
    records = _bars(
        days=days,
        symbols=symbols,
        signal_symbols={symbols[0], symbols[1]},
    )
    original = run_opening_triple_bullish_research(
        records,
        sectors=sectors,
        start=days[0],
        end=days[-2],
        quantiles=5,
    )
    first_fold = original.robustness_validation.folds[0]
    changed_exit_day = first_fold.test_start + timedelta(days=1)
    perturbed = [
        replace(
            bar,
            open=bar.open * Decimal("1.1"),
            high=max(bar.high, bar.open * Decimal("1.1")),
        )
        if bar.trading_day == changed_exit_day
        and bar.timestamp.timetz().replace(tzinfo=None) == time(9, 35)
        else bar
        for bar in records
    ]
    rerun = run_opening_triple_bullish_research(
        perturbed,
        sectors=sectors,
        start=days[0],
        end=days[-2],
        quantiles=5,
    )
    rerun_first_fold = rerun.robustness_validation.folds[0]

    assert rerun_first_fold.selected_minimum_body_return_bps == (
        first_fold.selected_minimum_body_return_bps
    )
    assert rerun_first_fold.training_strategy_metrics == (
        first_fold.training_strategy_metrics
    )
    assert rerun_first_fold.test_strategy_metrics != first_fold.test_strategy_metrics
