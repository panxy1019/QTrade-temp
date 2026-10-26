import json
from dataclasses import replace
from datetime import date, datetime
from decimal import Decimal

import pytest

from qtrade.market_data.sina_incremental import completed_klines, completed_quotes, reconcile_day
from qtrade.market_data.sina_minute import SINA_FIVE_MINUTE_SESSION_TIMES
from qtrade.monitoring.data_freshness import Calendar

DAY = date(2026, 10, 26)
NOW = datetime.fromisoformat("2026-10-26T17:00:00+08:00")
CALENDAR = Calendar({"valid_from": "2026-10-01", "valid_through": "2026-10-31",
                     "weekday_closures": ["2026-10-01", "2026-10-02", "2026-10-05",
                                          "2026-10-06", "2026-10-07"]})


def daily_rows():
    return [{"day": str(DAY), "open": "10", "high": "11", "low": "9", "close": "10.5",
             "volume": "100"}]


def parse(rows, frequency="day", now=NOW, **kwargs):
    return completed_klines(json.dumps(rows).encode(), symbol="600036", security_type="STOCK",
                            frequency=frequency, requested_day=DAY, observed_at=now,
                            calendar=CALENDAR, **kwargs)


def quote_raw(**updates):
    values = [""] * 34
    for i, v in {0: "招商银行", 1: "10", 3: "10.5", 4: "11", 5: "9", 8: "100",
                 9: "1020", 30: str(DAY), 31: "15:34:59", 32: "00"}.items():
        values[i] = v
    for key, value in updates.items():
        values[int(key)] = value
    return ('var hq_str_sh600036="' + ",".join(values) + '";').encode("gb18030")


def quote(raw=None, now=NOW):
    return completed_quotes(raw or quote_raw(), requested={"600036": "STOCK"},
                            requested_day=DAY, observed_at=now, calendar=CALENDAR)["600036"]


def test_missing_amount_stays_unknown_until_exact_endpoint_match():
    day = parse(daily_rows())[0]
    assert day.amount is None and day.vwap is None
    result = reconcile_day(day, quote())
    assert result.amount == Decimal("1020") and result.vwap == Decimal("10.2")
    assert result.bar.timestamp.hour == 15 and result.source_clock.minute == 34
    assert result.row()["historical_available_at"] is None
    assert "NOT_INDEPENDENT" in result.row()["amount_basis"]


@pytest.mark.parametrize("field,value", [("1", "10.01"), ("8", "101"), ("3", "10.6")])
def test_any_daily_quote_price_or_volume_difference_blocks_amount_attachment(field, value):
    with pytest.raises(ValueError, match="MISMATCH"):
        reconcile_day(parse(daily_rows())[0], quote(quote_raw(**{field: value})))


@pytest.mark.parametrize("updates", [{"30": "2026-10-23"}, {"31": "17:01:00"},
                                     {"31": "14:59:59"}, {"9": "102000"},
                                     {"8": "0"}, {"9": "NaN"}])
def test_bad_quote_clock_amount_or_volume_is_not_an_admitted_bar(updates):
    with pytest.raises(ValueError):
        quote(quote_raw(**updates))


def test_bounded_minute_keeps_only_completed_grid_bars_and_verifies_full_sessions():
    rows = [{**daily_rows()[0], "day": f"{DAY} {clock}", "amount": "1020"}
            for clock in SINA_FIVE_MINUTE_SESSION_TIMES]
    assert len(parse(rows, "5min", require_complete_session=True)) == 48
    morning = parse(rows, "5min", now=datetime.fromisoformat("2026-10-26T10:02:00+08:00"))
    assert len(morning) == 6 and morning[-1].bar.timestamp.strftime("%H:%M") == "10:00"
    with pytest.raises(ValueError, match="INCOMPLETE"):
        parse(rows[:-1], "5min", require_complete_session=True)
    with pytest.raises(ValueError, match="GRID"):
        parse([{**rows[0], "day": f"{DAY} 13:00:00"}], "5min")
    with pytest.raises(ValueError, match="DUPLICATE"):
        parse([rows[0], rows[0]], "5min")


def test_daily_cutoff_missing_data_and_identity_fail_closed_without_suspension_claim():
    with pytest.raises(ValueError, match="NOT_COMPLETE"):
        parse(daily_rows(), now=datetime.fromisoformat("2026-10-26T16:29:59+08:00"))
    with pytest.raises(ValueError, match="REASON_UNKNOWN"):
        parse([{**daily_rows()[0], "day": "2026-10-23"}])
    with pytest.raises(ValueError, match="IDENTITY"):
        quote(quote_raw().replace(b"sh600036", b"sz000001"))
    with pytest.raises(ValueError, match="MISMATCH"):
        reconcile_day(parse(daily_rows())[0], replace(quote(), frequency="5min"))
