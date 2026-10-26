import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.public_pit_audit import build_audit
from qtrade.research.workflow import digest
from scripts import audit_public_pit as command

NOW = datetime(2026, 10, 26, 13, tzinfo=UTC)


def inputs():
    dataset = {"accepted_symbols": 2, "stocks": [{"symbol": "000001"}, {"symbol": "600001"}],
               "end": "2026-10-23"}
    master = {"source": "BaoStock query_stock_basic", "observed_at": NOW.isoformat(),
              "historical_available_at": None, "rows": [
                  {"code": "sz.000001", "type": "1", "status": "1", "ipoDate": "1991-04-03"},
              ]}
    status = signed({"source": "BaoStock raw daily status backfill", "symbol": "000001",
                     "observed_at": NOW.isoformat(), "certified_at_decision": False,
                     "historical_available_at": None, "rows": [{
                         "date": "2020-01-02", "code": "sz.000001", "isST": "",
                         "tradestatus": "1", "open": "10", "close": "10",
                     }]})
    coverage = signed({**AUTHORITY, "schema_version": "qtrade.pit-repair.v1",
                       "observed_at": NOW.isoformat(), "historical_pit_certified": False,
                       "historical_available_at_known": False, "master_sha256": digest(master),
                       "master_rows": 1, "status_symbols": 1, "status_rows": 1,
                       "source_checksums": {"000001.json": digest(status)}})
    return dict(dataset=dataset, master=master, coverage=coverage,
                statuses={"000001": status}, status_errors={}, attempts={},
                schedule={"outcome": "FAILED", "next_attempt_at": (NOW + timedelta(hours=2))
                          .isoformat()}, now=NOW)


def test_status_coverage_cannot_discharge_availability_or_exact_rule_requirements():
    values = inputs()
    before = deepcopy(values)
    report = build_audit(**values)
    assert values == before
    assert report["content_validated_status_symbols"] == 1
    assert report["status_quality_counts"]["unknown_st_state_rows"] == 1
    assert report["complete_historical_pit_symbols"] == 0
    assert report["exact_rules_certified_symbols"] == 0
    assert report["repair_task_counts"]["PROVE_HISTORICAL_INFORMATION_AVAILABILITY"] == 2
    missing = [t for t in report["repair_tasks"]
               if t["requirement"] == "ACQUIRE_VALIDATED_VENDOR_STATUS"]
    assert missing[0]["symbol"] == "600001"
    assert missing[0]["not_before"] == values["schedule"]["next_attempt_at"]
    assert all(t["execution_allowed"] is False for t in report["repair_tasks"])
    values["statuses"]["000001"]["rows"][0]["isST"] = "0"
    assert build_audit(**values)["evidence_sha256"] != report["evidence_sha256"]


def test_official_duplicates_and_conflicts_are_retained_without_becoming_history():
    values = inputs()
    rows = [{"symbol": "sh.600999", "issues": ["CONFLICT_OUTDATE"]},
            {"symbol": "sh.600999", "issues": ["DUPLICATE_OFFICIAL_IDENTITY"]},
            {"symbol": None, "issues": ["NON_A_OR_UNRESOLVED_IDENTITY"]}]
    values["official"] = {"records": rows, "master_sha256": "old-master"}
    result = build_audit(**values)
    assert result["official_delisting"]["raw_rows"] == 3
    assert result["official_delisting"]["unique_a_identities"] == 1
    assert result["official_delisting"]["review_identities"] == 1
    assert result["official_delisting"]["master_matches_current_source"] is False
    assert result["repair_task_counts"]["ACQUIRE_AND_RECONCILE_DELISTED_PRICE_HISTORY"] == 1
    assert result["historical_available_at"] is None
    assert result["historical_pit_certified"] is False


def test_bad_content_is_a_repair_task_without_hiding_the_other_good_symbol():
    values = inputs()
    values["statuses"]["600001"] = {**deepcopy(values["statuses"]["000001"]),
                                   "symbol": "600001"}
    result = build_audit(**values)
    assert result["content_validated_status_symbols"] == 1
    assert result["issue_counts"]["STATUS_CHECKPOINT_CONTENT_INVALID"] == 1
    assert result["repair_task_counts"]["ACQUIRE_VALIDATED_VENDOR_STATUS"] == 1


def test_offline_command_pins_actual_corrupt_bytes_and_is_idempotent(tmp_path, monkeypatch):
    values = inputs()
    root = tmp_path / "pit-repair"
    (root / "master").mkdir(parents=True)
    (root / "status").mkdir()
    data = tmp_path / "qlib-data-20261023"
    data.mkdir()
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    write(data / "dataset.json", values["dataset"])
    write(root / "coverage.json", values["coverage"])
    write(root / "master" / (digest(values["master"]) + ".json"), values["master"])
    write(root / "status/000001.json", values["statuses"]["000001"])
    write(root / "next-pass.json", values["schedule"])
    monkeypatch.setattr(command, "write_json_immutable", write)
    first = command.run(tmp_path, now=NOW)
    assert command.run(tmp_path, now=NOW) == first
    assert len(list((root / "audits").glob("*.json"))) == 1
    original = (root / "status/000001.json").read_bytes()
    (root / "status/000001.json").write_bytes(b'{"corrupt":true}')
    second = command.run(tmp_path, now=NOW)
    assert second["evidence_sha256"] != first["evidence_sha256"]
    assert second["content_validated_status_symbols"] == 0
    assert second["issue_counts"]["STATUS_CHECKPOINT_INTEGRITY_INVALID"] == 1
    assert second["actual_status_file_sha256"] != first["actual_status_file_sha256"]
    assert (root / "coverage.json").read_bytes() == json.dumps(values["coverage"]).encode()
    assert original != (root / "status/000001.json").read_bytes()


def test_audit_rejects_naive_retry_clocks_instead_of_ignoring_backoff():
    values = inputs()
    values["schedule"]["next_attempt_at"] = "2026-10-26T15:00:00"
    with pytest.raises(ValueError, match="RETRY_CLOCK_INVALID"):
        build_audit(**values)


def test_invalid_vendor_dates_remain_review_issues_without_aborting_other_symbols():
    values = inputs()
    values["master"]["rows"][0].update(ipoDate=12345, outDate="invalid")
    result = build_audit(**values)
    assert result["issue_counts"]["MASTER_IPODATE_INVALID"] == 1
    assert result["issue_counts"]["MASTER_OUTDATE_INVALID"] == 1
    assert result["content_validated_status_symbols"] == 1
