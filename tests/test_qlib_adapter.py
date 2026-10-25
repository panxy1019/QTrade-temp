from copy import deepcopy
from decimal import Decimal

import pytest

from qtrade.research.qlib_adapter import (
    calendar_boundary_tail,
    causal_capacity,
    normalize_daily,
    verify_reused_bars,
)


def bar(day, price=10, volume=1000):
    return {
        "date": day,
        "open": price,
        "high": price + 1,
        "low": price - 1,
        "close": price,
        "volume": volume,
        "amount": price * volume,
    }


def test_split_adjustment_preserves_returns_cash_turnover_and_raw_units():
    rows = [bar("2020-01-02"), bar("2020-01-06", 5, 2000)]
    result = normalize_daily(
        rows, [{"d": "2019-01-01", "f": 2}, {"d": "2020-01-06", "f": 4}], symbol="600036"
    )
    assert [r["date"] for r in result] == ["2020-01-02", "2020-01-06"]
    assert [r["close"] for r in result] == [1, 1]
    for row in result:
        assert row["vwap"] * row["volume"] == pytest.approx(row["amount"])
        assert row["close"] / row["factor"] == pytest.approx(row["raw_close"])
        assert row["volume"] * row["factor"] == pytest.approx(row["raw_volume"])
    assert result[1]["buy_block"] == 0


def test_future_factor_is_not_backfilled():
    with pytest.raises(ValueError, match="NO_FACTOR_KNOWN"):
        normalize_daily([bar("2020-01-02")], [{"d": "2020-01-03", "f": 1}], symbol="600036")


@pytest.mark.parametrize("field,value", [("amount", 100), ("close", "NaN"), ("volume", -1)])
def test_invalid_units_and_nonfinite_inputs_fail_closed(field, value):
    row = bar("2020-01-02")
    row[field] = value
    with pytest.raises(ValueError):
        normalize_daily([row], [{"d": "2010-01-01", "f": 1}], symbol="600036")


def test_missing_or_nontraded_days_are_never_price_filled():
    result = normalize_daily(
        [bar("2020-01-02"), bar("2020-01-03", volume=0), bar("2020-01-07")],
        [{"d": "2010-01-01", "f": 1}],
        symbol="688001",
    )
    assert [r["date"] for r in result] == ["2020-01-02", "2020-01-07"]
    assert all(row["buy_block"] == 1 for row in result)


def test_existing_bar_drift_and_missing_days_are_not_silently_replaced():
    source = [bar("2020-01-02")]
    existing = [{**source[0], "trading_day": "2020-01-02"}]
    assert verify_reused_bars(source, existing) == 1
    changed = deepcopy(existing)
    changed[0]["close"] = Decimal("10.02")
    with pytest.raises(ValueError, match="REUSED_OHLC_CHANGED"):
        verify_reused_bars(source, changed)
    changed[0]["trading_day"] = "2020-01-03"
    with pytest.raises(ValueError, match="ABSENT_FROM_RAW_SOURCE"):
        verify_reused_bars(source, changed)


def test_fixed_splits_purge_future_label_overlap_and_keep_trading_off():
    import json
    from pathlib import Path

    config = json.loads(Path("configs/qlib_daily_baseline.json").read_text())
    assert config["segments"]["train"][1] < "2021-12-31" < config["segments"]["valid"][0]
    assert config["segments"]["valid"][1] < "2023-12-29" < config["segments"]["test"][0]
    assert config["portfolio"]["hold_thresh"] >= 1
    assert config["exchange"]["deal_price"] == "open"


def test_capacity_is_causal_zero_on_resume_and_uses_current_share_units():
    rows = [
        {"date": "2020-01-02", "raw_volume": 1000, "factor": 0.1},
        {"date": "2020-01-03", "raw_volume": 999999999, "factor": 0.2},
        {"date": "2020-01-07", "raw_volume": 2000, "factor": 0.2},
    ]
    result = causal_capacity(rows, ["2020-01-02", "2020-01-03", "2020-01-06", "2020-01-07"])
    assert result == {"2020-01-02": 0, "2020-01-03": 50, "2020-01-07": 0}


def test_calendar_boundary_tail_uses_verified_exchange_sessions_not_fake_bars():
    import json
    from pathlib import Path

    calendar = json.loads(Path("configs/observer_calendar_2026.json").read_text())
    assert calendar_boundary_tail("2026-10-23", calendar) == [
        "2026-10-26",
        "2026-10-27",
        "2026-10-28",
    ]
    with pytest.raises(ValueError, match="OUTSIDE_VERIFIED_HORIZON"):
        calendar_boundary_tail("2026-12-31", calendar)
