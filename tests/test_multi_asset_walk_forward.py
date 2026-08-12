from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from qtrade.data import Bar
from qtrade.research import (
    DailyMultiAssetBacktester,
    EqualWeightBuyHold,
    MomentumAllocation,
    MultiAssetWalkForwardRunner,
    TimeSplit,
    WalkForwardCandidate,
)


def bars() -> list[Bar]:
    records = []
    start = date(2024, 1, 1)
    for index in range(24):
        for symbol, step in (("510300", Decimal("0.1")), ("510500", Decimal("-0.02"))):
            close = Decimal("10") + step * index
            records.append(
                Bar(
                    symbol,
                    "SSE",
                    datetime(2024, 1, 1, 7, tzinfo=UTC) + timedelta(days=index),
                    start + timedelta(days=index),
                    close,
                    close,
                    close,
                    close,
                    Decimal("10000"),
                )
            )
    return sorted(records, key=lambda value: (value.timestamp, value.symbol))


def candidates() -> tuple[WalkForwardCandidate, ...]:
    return (
        WalkForwardCandidate(
            "hold",
            lambda: EqualWeightBuyHold(("510300",), reserve=Decimal("0")),
        ),
        WalkForwardCandidate(
            "momentum",
            lambda: MomentumAllocation((2,), top_k=1, rebalance_every=2, reserve=Decimal("0")),
            (("horizons", "2"), ("top_k", "1")),
        ),
    )


def runner() -> MultiAssetWalkForwardRunner:
    return MultiAssetWalkForwardRunner(
        candidates(),
        backtester_factory=lambda: DailyMultiAssetBacktester(
            initial_cash=Decimal("100000"),
            transaction_cost_rate=Decimal("0"),
            slippage_rate=Decimal("0"),
        ),
    )


def test_walk_forward_selects_on_validation_and_scores_nonoverlapping_tests():
    splits = (
        TimeSplit(
            date(2024, 1, 1), date(2024, 1, 8), date(2024, 1, 9),
            date(2024, 1, 12), date(2024, 1, 13), date(2024, 1, 16),
        ),
        TimeSplit(
            date(2024, 1, 17), date(2024, 1, 18), date(2024, 1, 19),
            date(2024, 1, 20), date(2024, 1, 21), date(2024, 1, 24),
        ),
    )
    result = runner().run(bars(), splits)
    assert len(result.folds) == 2
    assert result.folds[0].split.test_end < result.folds[1].split.test_start
    assert result.folds[0].validation_scores
    assert result.aggregate_metrics.number_of_trades == sum(
        fold.test_metrics.number_of_trades for fold in result.folds
    )
    assert len(result.result_hash) == 64


def test_walk_forward_result_hash_binds_candidate_specification():
    split = TimeSplit(
        date(2024, 1, 1), date(2024, 1, 8), date(2024, 1, 9),
        date(2024, 1, 12), date(2024, 1, 13), date(2024, 1, 16),
    )
    baseline = runner().run(bars(), (split,))
    changed = MultiAssetWalkForwardRunner(
        (
            candidates()[0],
            WalkForwardCandidate(
                "momentum",
                lambda: MomentumAllocation(
                    (2,), top_k=1, rebalance_every=2, reserve=Decimal("0")
                ),
                (("horizons", "2"), ("top_k", "1"), ("revision", "changed")),
            ),
        ),
        backtester_factory=lambda: DailyMultiAssetBacktester(
            initial_cash=Decimal("100000"),
            transaction_cost_rate=Decimal("0"),
            slippage_rate=Decimal("0"),
        ),
    ).run(bars(), (split,))
    assert baseline.result_hash != changed.result_hash


def test_walk_forward_rejects_overlapping_test_windows():
    split = TimeSplit(
        date(2024, 1, 1), date(2024, 1, 8), date(2024, 1, 9),
        date(2024, 1, 12), date(2024, 1, 13), date(2024, 1, 16),
    )
    with pytest.raises(ValueError, match="must be disjoint"):
        runner().run(bars(), (split, split))
