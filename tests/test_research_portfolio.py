from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from qtrade.portfolio import PortfolioConstraints, construct_targets
from qtrade.research import (
    FactorObservation,
    TimeSplit,
    WalkForwardConfig,
    build_walk_forward_splits,
    evaluate_factor,
    evaluate_factor_periods,
    evaluate_returns,
)
from qtrade.research.factors import liquidity_turnover, momentum, realized_volatility, reversal


def test_time_split_rejects_temporal_leakage():
    with pytest.raises(ValueError, match="chronological"):
        TimeSplit(
            train_start=date(2025, 1, 1),
            train_end=date(2025, 12, 31),
            validation_start=date(2025, 6, 1),
            validation_end=date(2026, 1, 31),
            test_start=date(2026, 2, 1),
            test_end=date(2026, 3, 1),
        )


def test_time_split_rejects_equal_adjacent_boundaries():
    with pytest.raises(ValueError, match="non-overlapping"):
        TimeSplit(
            train_start=date(2025, 1, 1),
            train_end=date(2025, 12, 31),
            validation_start=date(2025, 12, 31),
            validation_end=date(2026, 1, 31),
            test_start=date(2026, 2, 1),
            test_end=date(2026, 3, 1),
        )


def test_top_k_targets_respect_lots_cash_reserve_and_weight_cap():
    targets = construct_targets(
        {"A": Decimal("3"), "B": Decimal("2"), "C": Decimal("1")},
        {"A": Decimal("10"), "B": Decimal("20"), "C": Decimal("5")},
        {"A": 100, "B": 100, "C": 100},
        Decimal("100000"),
        PortfolioConstraints(
            top_k=2, max_weight=Decimal("0.4"), cash_reserve_fraction=Decimal("0.1")
        ),
    )
    assert [(target.symbol, target.quantity) for target in targets] == [("A", 4000), ("B", 2000)]
    assert sum(target.target_weight for target in targets) == Decimal("0.8")


def test_metrics_include_cost_aware_return_drawdown_and_benchmark():
    metrics = evaluate_returns(
        [Decimal("0.01"), Decimal("-0.02"), Decimal("0.03")],
        benchmark_returns=[Decimal("0"), Decimal("0.01"), Decimal("0")],
        turnover=Decimal("0.4"),
        exposure=Decimal("0.8"),
        trade_pnls=[Decimal("10"), Decimal("-5")],
        transaction_costs=Decimal("2"),
    )
    assert metrics.total_return == Decimal("0.019494")
    assert metrics.maximum_drawdown == Decimal("-0.02")
    assert metrics.benchmark_return == Decimal("0.01")
    assert metrics.win_rate == Decimal("2") / Decimal("3")
    assert metrics.profit_loss_ratio == Decimal("2")
    assert metrics.number_of_trades == 2
    assert metrics.transaction_costs == Decimal("2")


def test_empty_benchmark_is_not_silently_replaced():
    with pytest.raises(ValueError, match="benchmark length"):
        evaluate_returns([Decimal("0.01")], benchmark_returns=[])


def test_factor_metrics_cover_ic_rank_quantiles_turnover_and_spread():
    result = evaluate_factor(
        [Decimal("1"), Decimal("2"), Decimal("3"), Decimal("4")],
        [Decimal("0.01"), Decimal("0.02"), Decimal("0.03"), Decimal("0.04")],
        previous_factor_values=[Decimal("4"), Decimal("2"), Decimal("3"), Decimal("1")],
        quantiles=2,
    )
    assert result.ic == Decimal("1")
    assert result.rank_ic == Decimal("1")
    assert result.quantile_returns == (Decimal("0.015"), Decimal("0.035"))
    assert result.long_short_spread == Decimal("0.020")
    assert result.factor_turnover == Decimal("0.5")


def test_walk_forward_splits_use_strict_chronological_held_out_windows():
    sessions = [date(2026, 1, day) for day in range(1, 13)]
    splits = build_walk_forward_splits(
        sessions,
        WalkForwardConfig(4, 2, 2, 2),
    )
    assert len(splits) == 3
    assert splits[0].train_start == date(2026, 1, 1)
    assert splits[0].test_start == date(2026, 1, 7)
    assert splits[1].train_start == date(2026, 1, 3)
    assert splits[1].test_start == date(2026, 1, 9)


def test_walk_forward_rejects_unsorted_or_insufficient_sessions():
    config = WalkForwardConfig(2, 1, 1, 1)
    with pytest.raises(ValueError, match="sorted and unique"):
        build_walk_forward_splits([date(2026, 1, 2), date(2026, 1, 1)], config)
    with pytest.raises(ValueError, match="insufficient"):
        build_walk_forward_splits([date(2026, 1, 1)], config)


def test_walk_forward_embargo_purges_label_horizon_and_tests_do_not_overlap():
    sessions = [date(2026, 1, day) for day in range(1, 16)]
    splits = build_walk_forward_splits(
        sessions,
        WalkForwardConfig(4, 2, 2, 2, embargo_sessions=1),
    )
    assert splits[0].train_end == date(2026, 1, 4)
    assert splits[0].validation_start == date(2026, 1, 6)
    assert splits[0].validation_end == date(2026, 1, 7)
    assert splits[0].test_start == date(2026, 1, 9)
    with pytest.raises(ValueError, match="cannot overlap"):
        WalkForwardConfig(4, 2, 3, 2)


def test_baseline_factors_are_deterministic_and_input_validated():
    closes = [Decimal("10"), Decimal("11"), Decimal("10"), Decimal("12")]
    volumes = [Decimal("100"), Decimal("200"), Decimal("100"), Decimal("400")]
    assert momentum(closes, 2) == Decimal("12") / Decimal("11") - Decimal("1")
    assert reversal(closes, 2) == -momentum(closes, 2)
    assert realized_volatility(closes, 3) > 0
    assert liquidity_turnover(closes, volumes, free_float_units=Decimal("10000")) == Decimal(
        "0.02"
    )


def test_multi_period_factor_evaluation_computes_nonzero_icir():
    def period(day: int, values: list[tuple[str, str, str]]) -> list[FactorObservation]:
        return [
            FactorObservation(
                symbol,
                Decimal(factor),
                Decimal(forward_return),
                date(2026, 1, day),
                datetime(2026, 1, day, tzinfo=UTC),
            )
            for symbol, factor, forward_return in values
        ]

    result = evaluate_factor_periods(
        [
            period(
                1,
                [("A", "1", ".01"), ("B", "2", ".02"), ("C", "3", ".03"), ("D", "4", ".04")],
            ),
            period(
                2,
                [("A", "4", ".01"), ("B", "3", ".02"), ("C", "2", ".04"), ("D", "1", ".03")],
            ),
        ],
        quantiles=2,
    )
    assert result.icir != 0
    assert result.factor_turnover == Decimal("1")


def test_factor_observation_rejects_future_known_data_and_length_mismatch():
    with pytest.raises(ValueError, match="future-known"):
        FactorObservation(
            "A",
            Decimal("1"),
            Decimal(".01"),
            date(2026, 1, 1),
            datetime(2026, 1, 2, tzinfo=UTC),
        )
    with pytest.raises(ValueError, match="must align"):
        evaluate_factor([Decimal("1"), Decimal("2")], [Decimal(".01")], quantiles=2)
