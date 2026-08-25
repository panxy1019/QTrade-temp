from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from qtrade.data import Bar
from qtrade.research import (
    PriceFactorSpec,
    RankedFactorAllocation,
    diagnose_price_factors,
    skipped_momentum,
)


def _bars() -> list[Bar]:
    records = []
    for index in range(80):
        for symbol, step in (
            ("510300", Decimal("0.10")),
            ("510500", Decimal("0.06")),
            ("510050", Decimal("0.02")),
            ("512100", Decimal("-0.01")),
            ("512880", Decimal("-0.03")),
        ):
            close = Decimal("10") + step * index
            session = date(2025, 1, 1) + timedelta(days=index)
            records.append(
                Bar(
                    symbol,
                    "SSE",
                    datetime(2025, 1, 1, 7, tzinfo=UTC) + timedelta(days=index),
                    session,
                    close,
                    close,
                    close,
                    close,
                    Decimal("10000"),
                )
            )
    return sorted(records, key=lambda bar: (bar.timestamp, bar.symbol))


def test_skipped_momentum_excludes_recent_prices_and_validates_window():
    closes = [Decimal(value) for value in (10, 12, 15, 30)]
    assert skipped_momentum(closes, 2, skip=1) == Decimal("0.5")
    with pytest.raises(ValueError, match="skip"):
        skipped_momentum(closes, 2, skip=-1)


def test_ranked_factor_allocation_combines_comparable_cross_sectional_ranks():
    model = RankedFactorAllocation(
        (
            (PriceFactorSpec("momentum", 2), Decimal("1")),
            (PriceFactorSpec("low_volatility", 2), Decimal("1")),
        ),
        top_k=1,
        rebalance_every=2,
        reserve=Decimal("0"),
    )
    history = {
        "A": (Decimal("10"), Decimal("11"), Decimal("12")),
        "B": (Decimal("10"), Decimal("15"), Decimal("20")),
        "C": (Decimal("10"), Decimal("10.1"), Decimal("10.2")),
    }
    assert model.weights(history, session_index=1) is None
    assert model.weights(history, session_index=2) == {"A": Decimal("1")}


def test_factor_diagnostics_use_only_forward_labels_inside_discovery_window():
    factors = (
        PriceFactorSpec("momentum", 10),
        PriceFactorSpec("reversal", 5),
        PriceFactorSpec("low_volatility", 10),
    )
    diagnostics = diagnose_price_factors(
        _bars(),
        factors,
        start=date(2025, 1, 21),
        end=date(2025, 3, 11),
        sample_every=5,
        forward_sessions=5,
        quantiles=5,
    )
    assert {item.factor_id for item in diagnostics} == {
        "momentum-10",
        "reversal-5",
        "low_volatility-10",
    }
    assert all(item.periods > 1 and item.instruments_minimum == 5 for item in diagnostics)
    assert diagnostics[0].metrics.rank_ic > 0
    assert diagnostics[1].metrics.rank_ic < 0


def test_price_factor_specs_reject_ambiguous_or_duplicate_definitions():
    with pytest.raises(ValueError, match="only momentum"):
        PriceFactorSpec("reversal", 5, 1)
    duplicate = PriceFactorSpec("momentum", 20)
    with pytest.raises(ValueError, match="unique"):
        RankedFactorAllocation(
            ((duplicate, Decimal("1")), (duplicate, Decimal("1"))), top_k=1
        )
