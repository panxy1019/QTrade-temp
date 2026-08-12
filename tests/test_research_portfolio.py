from datetime import date
from decimal import Decimal

import pytest

from qtrade.portfolio import PortfolioConstraints, construct_targets
from qtrade.research import TimeSplit, evaluate_returns


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
    )
    assert metrics.total_return == Decimal("0.019494")
    assert metrics.maximum_drawdown == Decimal("-0.02")
    assert metrics.benchmark_return == Decimal("0.01")
    assert metrics.win_rate == Decimal("2") / Decimal("3")
