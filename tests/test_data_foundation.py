from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from qtrade.data import (
    Bar,
    BarQualityGate,
    CorporateAction,
    IndexMembership,
    PointInTimeStore,
    PriceAdjustment,
    apply_backward_adjustments,
)


def bar(day: int, close: str = "10", **changes):
    values = {
        "symbol": "510300",
        "exchange": "SSE",
        "timestamp": datetime(2026, 8, day, 7, tzinfo=UTC),
        "trading_day": date(2026, 8, day),
        "open": Decimal("10"),
        "high": Decimal("11"),
        "low": Decimal("9"),
        "close": Decimal(close),
        "volume": Decimal("1000"),
    }
    values.update(changes)
    return Bar(**values)


def test_quality_gate_accepts_valid_ordered_bars():
    issues = BarQualityGate().validate(
        [bar(10), bar(11, "10.5")],
        expected_symbol="510300",
        expected_trading_days={date(2026, 8, 10), date(2026, 8, 11)},
    )
    assert issues == ()


def test_quality_gate_reports_multiple_failures():
    duplicate = bar(10, close="20", volume=Decimal("-1"), high=Decimal("11"))
    issues = BarQualityGate().validate([bar(10), duplicate], expected_symbol="000001")
    codes = {issue.code for issue in issues}
    assert {"DUPLICATE", "SYMBOL_MISMATCH", "NEGATIVE_VOLUME", "INVALID_OHLC"} <= codes
    assert {"TIMESTAMP_DISORDER", "ABNORMAL_JUMP"} <= codes


def test_quality_gate_groups_temporal_checks_by_instrument_and_rejects_nonfinite():
    def other(day: int, close: str) -> Bar:
        price = Decimal(close)
        return bar(
            day,
            close,
            symbol="000001",
            exchange="SZSE",
            timestamp=datetime(2026, 8, day, 7, tzinfo=UTC),
            open=price,
            high=price + 1,
            low=price - 1,
        )

    interleaved = [bar(10, "10"), other(10, "1000"), bar(11, "10.1"), other(11, "1001")]
    assert BarQualityGate().validate(interleaved) == ()
    issues = BarQualityGate().validate([bar(12, close="NaN")])
    assert {issue.code for issue in issues} == {"NON_FINITE_VALUE"}


def test_point_in_time_universe_hides_future_known_membership():
    store = PointInTimeStore()
    store.add_membership(
        IndexMembership(
            index_symbol="000300",
            member_symbol="600000",
            valid_from=date(2026, 1, 1),
            valid_to=None,
            known_at=datetime(2025, 12, 20, tzinfo=UTC),
        )
    )
    store.add_membership(
        IndexMembership(
            index_symbol="000300",
            member_symbol="000001",
            valid_from=date(2026, 6, 1),
            valid_to=None,
            known_at=datetime(2026, 5, 20, tzinfo=UTC),
        )
    )
    historical_knowledge = datetime(2026, 3, 1, tzinfo=UTC)
    assert store.universe("000300", as_of=date(2026, 7, 1), known_at=historical_knowledge) == {
        "600000"
    }


def test_point_in_time_corporate_actions_hide_future_announcements():
    store = PointInTimeStore()
    store.add_corporate_action(
        CorporateAction(
            symbol="600000",
            action_type="DIVIDEND",
            effective_date=date(2026, 6, 1),
            announced_at=datetime(2026, 5, 15, tzinfo=UTC),
            value=Decimal("0.1"),
        )
    )
    assert (
        store.corporate_actions(
            "600000", through=date(2026, 6, 2), known_at=datetime(2026, 5, 1, tzinfo=UTC)
        )
        == ()
    )


def test_sourced_point_in_time_adjustments_apply_only_to_prior_history():
    bars = [bar(10, "20"), bar(11, "10", open=Decimal("10"))]
    adjustment = PriceAdjustment(
        "split-510300-20260811",
        "510300",
        date(2026, 8, 11),
        datetime(2026, 8, 9, tzinfo=UTC),
        Decimal("0.5"),
        "licensed-vendor://corporate-actions/v1",
    )
    result = apply_backward_adjustments(
        bars, [adjustment], known_at=datetime(2026, 8, 12, tzinfo=UTC)
    )
    assert result.bars[0].close == Decimal("10.0")
    assert result.bars[0].volume == Decimal("2000")
    assert result.bars[1].close == Decimal("10")
    assert result.adjustment_ids == ("split-510300-20260811",)


def test_future_known_adjustment_is_not_applied_and_bad_factor_rejects():
    adjustment = PriceAdjustment(
        "future",
        "510300",
        date(2026, 8, 11),
        datetime(2026, 8, 12, tzinfo=UTC),
        Decimal("0.5"),
        "licensed-vendor://corporate-actions/v1",
    )
    result = apply_backward_adjustments(
        [bar(10, "20"), bar(11, "10", open=Decimal("10"))],
        [adjustment],
        known_at=datetime(2026, 8, 11, tzinfo=UTC),
    )
    assert result.bars[0].close == Decimal("20")
    assert result.adjustment_ids == ()
    with pytest.raises(ValueError, match="finite and positive"):
        PriceAdjustment(
            "bad",
            "510300",
            date(2026, 8, 11),
            datetime(2026, 8, 10, tzinfo=UTC),
            Decimal("NaN"),
            "source",
        )
