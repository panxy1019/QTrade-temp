from copy import deepcopy
from datetime import UTC, date, datetime

import pytest

from qtrade.instruments.observed import build_observed_master
from qtrade.market_data.backfill_plan import build_backfill_plan, month_window
from qtrade.monitoring.data_freshness import signed

NOW = datetime(2026, 10, 26, 11, tzinfo=UTC)


@pytest.fixture
def master():
    value = build_observed_master(
        vendor_rows=[{"code": "sh.600036", "code_name": "招商银行", "type": "1",
                      "status": "1", "ipoDate": "2002-04-09"}],
        vendor_sha256="a" * 64, source_groups=[{
            "source_id": "legacy", "role": "legacy", "sha256": "b" * 64,
            "rows": [{"symbol": "600036", "security_type": "STOCK"}],
        }], observed_at=NOW, target_count=1,
    )
    value.pop("evidence_sha256")
    value["securities"][0]["frequencies"] = {
        f: {"state": "CONTENT_COVERAGE_VERIFIED", "bar_count": 2000,
            "observed_start": "2016-10-24", "observed_end": "2026-10-23"}
        for f in ("day", "5min")
    }
    return signed(value)


def plan(master, **kwargs):
    return build_backfill_plan(master, requested_end=date(2026, 10, 23),
                               observed_at=NOW, code_revision="c" * 40, **kwargs)


def test_legacy_coverage_never_becomes_new_amount_or_raw_minute_admission(master):
    original = deepcopy(master)
    value = plan(master)
    assert master == original
    assert value["frequency_counts"] == {"day": 1, "5min": 1}
    assert value["admitted_bar_count"] == 0 and value["source_probe_complete"] is False
    assert value["execution_policy"]["enabled"] is False
    for task in value["tasks"]:
        assert task["state"] == "WAITING_FOR_SOURCE_PROBE"
        assert task["legacy_observation"]["bar_count"] == 2000
        assert task["historical_available_at"] is None
    assert value["tasks"][0]["task_id"] != value["tasks"][1]["task_id"]


def test_month_windows_cover_boundaries_once_and_are_bounded():
    start, end = date(2016, 2, 29), date(2026, 10, 23)
    windows = []
    before = None
    while (window := month_window(start, end, before=before)) is not None:
        windows.append(window)
        before = window[0]
    assert windows[0][1] == end and windows[-1][0] == start
    assert all(0 <= (right-left).days < 31 for left, right in windows)
    assert all((a[0]-b[1]).days == 1 for a, b in zip(windows, windows[1:], strict=False))
    with pytest.raises(ValueError, match="REVERSED"):
        month_window(end, start)


def test_new_listing_claim_retained_without_faking_full_history_or_discarding_symbol(master):
    master.pop("evidence_sha256")
    master["securities"][0]["listing_date"] = "2025-01-02"
    task = plan(signed(master))["tasks"][0]
    assert task["vendor_listing_date_claim"] == "2025-01-02"
    assert task["listing_claim_used_as_authoritative_boundary"] is False
    assert task["requested_start"] == "2016-10-23"
    assert task["admitted_bar_count"] == 0


@pytest.mark.parametrize("change", ["hash", "live", "count", "duplicate", "identity", "available"])
def test_invalid_master_cannot_create_work(master, change):
    master.pop("evidence_sha256")
    if change == "live":
        master["live_trading_enabled"] = True
    elif change == "count":
        master["target_actual"] = 2
    elif change == "duplicate":
        master["securities"].append(deepcopy(master["securities"][0]))
        master["target_actual"] = 2
    elif change == "identity":
        master["securities"][0]["instrument_id"] = "SZSE.600036"
    elif change == "available":
        master["securities"][0]["available_at"] = "2016-01-01"
    master = signed(master)
    if change == "hash":
        master["target_actual"] += 1
    with pytest.raises(ValueError):
        plan(master)


@pytest.mark.parametrize("years", [0, 11])
def test_unbounded_requests_rejected(master, years):
    with pytest.raises(ValueError, match="BOUND"):
        plan(master, years=years)


def test_future_and_current_day_never_become_historical_work(master):
    with pytest.raises(ValueError, match="CLOCK"):
        build_backfill_plan(master, requested_end=NOW.date(), observed_at=NOW,
                            code_revision="c" * 40)
    with pytest.raises(ValueError, match="CLOCK"):
        build_backfill_plan(master, requested_end=date(2026, 10, 23), observed_at=NOW,
                            code_revision="main")
