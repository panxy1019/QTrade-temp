from dataclasses import replace
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

import pytest

from qtrade.data import Bar
from qtrade.market_data import SINA_FIVE_MINUTE_SESSION_TIMES
from qtrade.research.intraday_factor import (
    OpeningTripleBullishSpec,
    run_opening_triple_bullish_research,
)

CHINA = timezone(timedelta(hours=8))


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
