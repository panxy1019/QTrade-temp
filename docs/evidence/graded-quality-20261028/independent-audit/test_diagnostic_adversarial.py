"""Negative-contract tests for the new diagnostic path; never modifies repo/state."""

import json
import sqlite3
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.data_quality import SCHEMA, classify_task, validate_quality
from qtrade.research.pit_quality import status_quality
from qtrade.research.workflow import digest
from scripts import build_data_quality_summary as builder
from scripts.collect_pit_evidence import FIELDS
from scripts.collect_recent_status import window_scope

NOW = datetime(2026, 10, 28, 2, tzinfo=UTC)
DAY = "2026-10-27"


def task(symbol="600036", universe="b"*64, state="FAILED"):
    return {"symbol": symbol, "frequency": "day", "trading_day": DAY,
            "state": state, "desired_end": DAY+"T07:00:00+00:00", "completed_end": None,
            "last_error": "SOURCE_MISSING", "universe_version": universe}


def publication():
    case = classify_task(task(), [], [], now=NOW)
    return signed({**AUTHORITY, "schema_version": SCHEMA, "observed_at": NOW.isoformat(),
                   "universe_version": "b"*64,
                   "source_revision": "a"*40, "production_admission_effect": "NONE",
                   "execution_admitted": False, "policy_status": "DIAGNOSTIC_ONLY_PENDING_APPROVAL",
                   "cases": [case], "case_counts": {"UNKNOWN": 1}, "task_counts": [],
                   "source_issues": [], "recent_status": {"available": False, "content_verified_symbols": 0},
                   "historical_pit_certified": False})


def test_valid_diagnostic_fixture_is_accepted_before_mutation():
    assert validate_quality(publication(), now=NOW)["case_counts"] == {"UNKNOWN": 1}


@pytest.mark.parametrize("defect", ["future_source", "source_after_summary", "future_recent",
                                    "wrong_counts", "invalid_revision", "elevated_case_authority",
                                    "malformed_source_issues"])
def test_resigned_semantically_invalid_diagnostic_is_rejected(defect):
    value = publication()
    if defect == "future_source":
        value["cases"][0]["source_observed_at"] = (NOW+timedelta(days=1)).isoformat()
    elif defect == "source_after_summary":
        value["observed_at"] = (NOW-timedelta(hours=2)).isoformat()
        value["cases"][0]["source_observed_at"] = (NOW-timedelta(hours=1)).isoformat()
    elif defect == "future_recent":
        value["recent_status"] = {"available": True, "observed_at": (NOW+timedelta(days=1)).isoformat(),
                                  "window_start": "2026-10-23", "window_end": DAY,
                                  "content_verified_symbols": 1,
                                  "progress": {"scope_symbols": 1, "completed_symbols": 1, "unresolved_symbols": 0}}
    elif defect == "wrong_counts":
        value["case_counts"] = {"COMPLETE": 5000}
    elif defect == "invalid_revision":
        value["source_revision"] = "not-a-source-revision"
    elif defect == "malformed_source_issues":
        value["source_issues"] = None
    else:
        value["cases"][0]["live_trading_enabled"] = True
    value = signed({k: v for k, v in value.items() if k != "evidence_sha256"})
    with pytest.raises((ValueError, KeyError, TypeError)):
        validate_quality(value, now=NOW)


@pytest.fixture
def build_fixture(tmp_path, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz)

    def write(path, value, **kwargs):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    monkeypatch.setattr(builder, "datetime", Clock)
    monkeypatch.setattr(builder, "current_universe", lambda _: "b"*64)
    monkeypatch.setattr(builder, "write_json_immutable", write)
    monkeypatch.setattr(builder, "write_json_atomic", write)
    database = tmp_path/"fixture.sqlite"
    with sqlite3.connect(database) as conn:
        conn.execute("CREATE TABLE collection_tasks(symbol,frequency,trading_day,state,desired_end,completed_end,last_error,universe_version)")
        row = task()
        conn.execute("INSERT INTO collection_tasks VALUES(?,?,?,?,?,?,?,?)", tuple(row.values()))
    return tmp_path, database


def test_retained_other_universe_failure_does_not_break_current_diagnostics(build_fixture):
    base, database = build_fixture
    with sqlite3.connect(database) as conn:
        row = task(universe="e"*64)
        conn.execute("INSERT INTO collection_tasks VALUES(?,?,?,?,?,?,?,?)", tuple(row.values()))
    result = builder.build(base, database, revision="a"*40)
    keys = [(r["symbol"], r["frequency"], r["trading_day"]) for r in result["cases"]]
    assert len(keys) == len(set(keys)) == 1


def test_record_outside_pointer_symbol_scope_is_quarantined(build_fixture):
    base, database = build_fixture
    calendar = Calendar(builder.REPO/"configs/observer_calendar_2026.json")
    scope = window_scope({"stocks": [{"symbol": "600037"}]}, calendar, NOW)
    request = {"code": "sh.600036", "fields": FIELDS, "start_date": scope["start"],
               "end_date": scope["end"], "frequency": "d", "adjustflag": "3"}
    record = {**AUTHORITY, "schema_version": "qtrade.recent-status-observation.v1",
              "source": "BaoStock raw daily status backfill", "symbol": "600036",
              "window_sha256": digest(scope), "request": request,
              "query_started_at": NOW.isoformat(), "observed_at": NOW.isoformat(),
              "system_available_at": NOW.isoformat(), "historical_available_at": None,
              "certified_at_decision": False, "historical_pit_certified": False,
              "raw_transport_bytes_captured": False, "sdk_error_code": "0",
              "source_revision": "a"*40, "requested_days_complete": True,
              "rows": [{"date": d, "code": "sh.600036", "tradestatus": "0", "isST": "0"}
                       for d in scope["expected_days"]]}
    record["quality"] = status_quality(record, record["symbol"], NOW, start=scope["start"], end=scope["end"])
    record = signed(record)
    path = base/"recent-status/windows"/digest(scope)/"status/600037.json"
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps(record))
    pointer = signed({**AUTHORITY, "schema_version": "qtrade.recent-status-coverage.v1",
                      "scope": scope, "window_sha256": digest(scope), "source_revision": "a"*40,
                      "observed_at": NOW.isoformat(), "status_checksums": {"600037": record["evidence_sha256"]},
                      "collection_progress": {"scope_symbols": 1, "completed_symbols": 1, "unresolved_symbols": 0}})
    (base/"recent-status/current.json").write_text(json.dumps(pointer))
    result = builder.build(base, database, revision="a"*40)
    assert result["recent_status"]["content_verified_symbols"] == 0
    assert any(r["reason"] == "RECENT_STATUS_CONTENT_QUARANTINED" for r in result["source_issues"])


def test_nonobject_json_in_optional_channel_does_not_break_market_publisher(tmp_path):
    from scripts.publish_research_snapshot import quality_publication
    path = tmp_path/"quality.json"
    path.write_text("[]")
    result = quality_publication(path)
    assert result["state"] == "NO_VERIFIED_QUALITY_PUBLICATION"


@pytest.mark.parametrize("source", ["supplemental", "recent_pointer", "issuer_index"])
def test_one_nonobject_optional_evidence_source_is_quarantined_not_global_failure(build_fixture, source):
    base, database = build_fixture
    paths = {"supplemental": "data-quality/supplemental-status/600036.json",
             "recent_pointer": "recent-status/current.json", "issuer_index": "data-quality/issuer-starts.json"}
    path = base/paths[source]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("[]")
    result = builder.build(base, database, revision="a"*40)
    assert len(result["cases"]) == 1
    assert result["source_issues"]
