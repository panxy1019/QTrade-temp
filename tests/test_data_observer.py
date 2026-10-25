import json
import os
from datetime import datetime
from hashlib import sha256

import pytest

from scripts.run_data_observer import (
    AUTHORITY,
    REPO,
    Calendar,
    Observer,
    freshness,
    read_report,
    signed,
)


def at(value: str) -> datetime:
    return datetime.fromisoformat(value + "+08:00")


@pytest.fixture
def calendar() -> Calendar:
    return Calendar(REPO / "configs/observer_calendar_2026.json")


@pytest.mark.parametrize(
    ("clock", "kind", "expected"),
    [
        ("2026-10-25T12:00:00", "daily", "2026-10-23T15:00:00"),
        ("2026-10-26T10:00:00", "daily", "2026-10-23T15:00:00"),
        ("2026-10-26T16:31:00", "daily", "2026-10-26T15:00:00"),
        ("2026-10-05T12:00:00", "daily", "2026-09-30T15:00:00"),
        ("2026-09-25T12:00:00", "minute", "2026-09-24T15:00:00"),
        ("2026-10-26T09:35:00", "minute", "2026-10-23T15:00:00"),
        ("2026-10-26T10:00:00", "minute", "2026-10-26T09:55:00"),
        ("2026-10-26T12:30:00", "minute", "2026-10-26T11:30:00"),
        ("2026-10-26T13:03:00", "minute", "2026-10-26T11:30:00"),
        ("2026-10-26T15:02:00", "minute", "2026-10-26T15:00:00"),
    ],
)
def test_market_clock(calendar, clock, kind, expected):
    assert calendar.expected(at(clock), kind) == at(expected)


def test_calendar_expiry_fails_closed(calendar):
    with pytest.raises(ValueError, match="CALENDAR"):
        calendar.expected(at("2027-01-03T12:00:00"), "daily")


def report(latest="2026-10-23T15:00:00+08:00"):
    return {
        **AUTHORITY,
        "completed_at": "2026-10-25T09:00:00+08:00",
        "latest_by_symbol": {"510300": latest, "510500": latest},
        "required_symbol_count": 2,
        "manifest": {"data_version": "test", "checksum": "x"},
    }


def test_successful_fetch_of_old_data_is_stale(calendar):
    now = at("2026-10-25T12:00:00")
    old = report("2026-08-21T15:00:00+08:00")
    result = freshness(old, calendar.expected(now, "daily"), now, "daily")
    assert result["status"] == "STALE_OR_INCOMPLETE"
    assert result["stale_symbols"] == ["510300", "510500"]


def test_weekend_does_not_mislabel_friday_data(calendar):
    now = at("2026-10-25T12:00:00")
    assert freshness(report(), calendar.expected(now, "daily"), now, "daily")["status"] == "FRESH"


def test_partial_coverage_cannot_be_fresh(calendar):
    value = report()
    value["required_symbol_count"] = 3
    now = at("2026-10-25T12:00:00")
    assert freshness(value, calendar.expected(now, "daily"), now, "daily")["status"] != "FRESH"


def test_future_data_is_rejected(calendar):
    now = at("2026-10-25T12:00:00")
    with pytest.raises(ValueError, match="MARKET_CLOCK"):
        freshness(
            report("2026-10-26T15:00:00+08:00"), calendar.expected(now, "daily"), now, "daily"
        )


def test_report_integrity_and_no_authority_escalation(tmp_path):
    data = tmp_path / "bars.parquet"
    data.write_bytes(b"checksum-fixture")
    value = report()
    value["manifest"]["checksum"] = sha256(data.read_bytes()).hexdigest()
    path = tmp_path / "report.json"
    path.write_text(json.dumps(signed(value)))
    assert read_report(path)["real_data_paper_readiness"] is False
    data.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="PARQUET"):
        read_report(path)
    data.write_bytes(b"checksum-fixture")
    value["real_data_paper_readiness"] = True
    path.write_text(json.dumps(signed(value)))
    with pytest.raises(ValueError, match="AUTHORITY"):
        read_report(path)


def test_failed_collection_retains_previous_report_and_records_failure(
    tmp_path, calendar, monkeypatch
):
    observer = Observer(tmp_path, calendar)
    observer.tasks["daily"] = {"report_path": "/previous/immutable/report.json"}

    def fail(*args, **kwargs):
        raise TimeoutError("sensitive provider response must not enter state")

    monkeypatch.setattr("scripts.run_data_observer.subprocess.run", fail)
    if os.name == "nt":
        # The existing directory-fsync publisher is Linux-only; exercise failure state here.
        monkeypatch.setattr(
            "scripts.run_data_observer.write_json_atomic",
            lambda path, value: path.write_text(json.dumps(value)),
        )
    observer.run_one("daily")
    state = json.loads((tmp_path / "collector-state.json").read_text())
    assert state["daily"]["report_path"] == "/previous/immutable/report.json"
    assert state["daily"]["outcome"] == "FAILED"
    assert state["daily"]["error"] == "TimeoutError"
    assert "sensitive" not in json.dumps(state)
