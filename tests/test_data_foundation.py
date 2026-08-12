from datetime import UTC, date, datetime
from decimal import Decimal

from qtrade.data import (
    Bar,
    BarQualityGate,
    CorporateAction,
    IndexMembership,
    PointInTimeStore,
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
