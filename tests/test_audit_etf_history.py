from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research import etf_trend
from qtrade.research.data_versions import file_hash
from qtrade.research.qlib_publication import verify_signed
from scripts import audit_etf_history as audit
from scripts.unified_store import UnifiedBatchStore

REVISION = "c"*40
AS_OF = date(2026, 10, 26)


@pytest.fixture
def archives(tmp_path, monkeypatch):
    monkeypatch.setattr("scripts.unified_store._fsync_directory", lambda _: None)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2026, 10, 30, 12, tzinfo=UTC).astimezone(tz or UTC)

    monkeypatch.setattr(etf_trend, "datetime", Clock)
    calendar = Calendar(Path("configs/observer_calendar_2026.json"))
    counter = [0]

    def create(change=None, *, count=None, response_change=None):
        days = audit._days(calendar, date(2026, 8, 1), AS_OF)
        if count is not None:
            days = days[-count:]
        captures = []
        for symbol in ("159915", "510300"):
            rows = [{"day": day, "open": "2", "high": "2.2", "low": "1.9",
                     "close": "2.1", "volume": "1000"} for day in days]
            if change:
                rows = change(symbol, rows)
            captures.append({"source_id": "sina_klines", "request": {
                "symbol": ("sh" if symbol.startswith("5") else "sz")+symbol,
                "scale": "240", "ma": "no", "datalen": "63"},
                "received_at": "2026-10-26T09:00:00+00:00", "body": json.dumps(rows).encode(),
                "status_code": 200, "transport_error": None, "body_complete": True})
            if response_change:
                response_change(symbol, captures[-1])
        bindings = {"master_sha256": "a"*64, "as_of": str(AS_OF),
                    "code_revision": "b"*40, "contract": "ETF_CLOSE_DIAGNOSTIC_V1",
                    "calendar_sha256": signed(calendar.document)["evidence_sha256"]}
        path = tmp_path/str(counter[0])/signed(bindings)["evidence_sha256"]
        counter[0] += 1
        path.mkdir(parents=True)
        raw = UnifiedBatchStore(path/"capture-store").capture(captures)
        receipt = signed({**AUTHORITY, "bindings": bindings, "raw": raw})
        (path/"receipt.json").write_text(json.dumps(receipt))
        value = etf_trend.admit(captures, ["159915", "510300"], calendar, AS_OF,
                               datetime(2026, 10, 26, 9, tzinfo=UTC))
        value.pop("evidence_sha256")
        value.update(bindings=bindings, receipt_sha256=file_hash(path/"receipt.json"))
        (path/"diagnostic.json").write_text(json.dumps(signed(value)))
        return path

    return calendar, create


def snapshot(root):
    return {path.relative_to(root).as_posix(): path.read_bytes()
            for path in root.rglob("*") if path.is_file()}


def test_real_capture_replay_counts_actual_grid_and_is_read_only(archives):
    calendar, create = archives
    archive = create()
    before = snapshot(archive)
    value = audit.audit_etf_history(archive, calendar, REVISION)
    verify_signed(value)
    assert value == audit.audit_etf_history(archive, calendar, REVISION)
    assert snapshot(archive) == before
    assert value["cohort"] == ["159915", "510300"]
    assert value["bindings"]["receipt_sha256"] == file_hash(archive/"receipt.json")
    assert value["bindings"]["calendar_sha256"] == signed(calendar.document)["evidence_sha256"]
    assert len(value["comparison_grid"]) == 55
    assert value["comparison_grid"][0] == "2026-08-03"
    assert value["comparison_grid"][-1] == "2026-10-26"
    assert value["label_capacity_upper_bounds"] == {"lookback_sessions": 20,
        "native_close_t_to_t_plus_1": 34, "after_next_open_close_t_plus_1_to_t_plus_2": 33,
        "observed_labels_evaluated": 0, "execution_return_labels": None}
    assert value["strategy_backtest_ready"] is False
    assert value["master_membership_independently_replayed"] is False
    assert value["stage"] == "SOURCE_COVERAGE_AUDIT_ONLY"
    assert all(value[key] is None for key in (
        "net_returns", "performance_metrics", "forward_return"))
    assert all(value[key] == [] for key in ("orders", "fills", "holdings"))
    assert value["return_adjustment_certified"] is False
    assert value["captures"][0]["amount_present_sessions"] == 0


def test_datalen_request_never_invents_returned_rows_or_mature_labels(archives):
    calendar, create = archives
    value = audit.audit_etf_history(create(count=21), calendar, REVISION)
    assert all(row["request"]["datalen"] == "63" for row in value["captures"])
    assert all(row["raw_row_count"] == 21 for row in value["captures"])
    assert len(value["comparison_grid"]) == 21
    assert value["label_capacity_upper_bounds"]["native_close_t_to_t_plus_1"] == 0
    assert value["grid_state"] == "INSUFFICIENT_COMMON_CONTIGUOUS_HISTORY"


def test_inventory_rejects_outside_closed_future_duplicate_and_bad_ohlcv(archives):
    calendar, create = archives

    def damage(symbol, rows):
        if symbol == "510300":
            return rows
        sample = rows[0]
        rows += [{**sample, "day": day} for day in ("2026-07-22", "2026-08-01", "2026-10-27")]
        rows.append(next(row.copy() for row in rows if row["day"] == "2026-08-10"))
        rows = [row for row in rows if row["day"] != "2026-08-11"]
        for row in rows:
            if row["day"] == "2026-08-12":
                row["high"] = "1"
            elif row["day"] == "2026-08-13":
                row["volume"] = "-1"
            elif row["day"] == "2026-08-14":
                row["amount"] = "999999"
        return rows

    value = audit.audit_etf_history(create(damage), calendar, REVISION)
    report = value["captures"][0]
    rejected = {row["date"]: row["reason"] for row in report["rejected_days"]}
    assert rejected["2026-07-22"] == "CALENDAR_OUTSIDE_VERIFIED_HORIZON"
    assert rejected["2026-08-01"] == "NOT_EXCHANGE_SESSION"
    assert rejected["2026-10-27"] == "AFTER_ARCHIVE_AS_OF"
    assert "2026-08-10" in rejected and "2026-08-12" in rejected
    assert rejected["2026-08-13"] == "INVALID_SOURCE_NUMBER"
    assert rejected["2026-08-14"] == "VWAP_OR_AMOUNT_VOLUME_UNIT_MISMATCH"
    assert report["duplicate_dates"] == [{"date": "2026-08-10", "count": 2}]
    assert "2026-08-11" in report["missing_sessions"]
    assert len(value["common_sessions"]) == 50
    assert len(value["common_contiguous_runs"]) == 2
    assert value["comparison_grid"][0] == "2026-08-17"
    assert "CALENDAR_EXTENSION_REQUIRES_INDEPENDENT_EVIDENCE" in value["missing_inputs"]


def test_latest_excluded_etf_stays_in_fixed_cohort_and_prevents_asof_grid(archives):
    calendar, create = archives
    archive = create(lambda symbol, rows: rows[:-1] if symbol == "159915" else rows)
    value = audit.audit_etf_history(archive, calendar, REVISION)
    assert value["cohort"] == ["159915", "510300"]
    assert value["excluded_from_latest_diagnostic"] == [{"symbol": "159915",
        "reason": "INCOMPLETE_OR_INVALID_21_SESSION_SOURCE"}]
    assert value["captures"][0]["missing_sessions"] == ["2026-10-26"]
    assert value["comparison_grid"] == []
    assert value["label_capacity_upper_bounds"]["native_close_t_to_t_plus_1"] == 0


@pytest.mark.parametrize("bad", ["NaN", None, "2026-08-03T15:00:00", "2026-13-40"])
def test_malformed_source_dates_remain_visible_and_never_bypassed(archives, bad):
    calendar, create = archives

    def damage(symbol, rows):
        rows[0]["day"] = bad
        return rows

    value = audit.audit_etf_history(create(damage), calendar, REVISION)
    assert all(row["row_issues"] for row in value["captures"])
    if bad == "2026-08-03T15:00:00":
        assert value["comparison_grid"][0] == "2026-08-04"
        assert value["captures"][0]["missing_sessions"] == ["2026-08-03"]
    else:
        assert value["comparison_grid"] == []
    assert value["strategy_backtest_ready"] is False


@pytest.mark.parametrize("damage", ["raw", "receipt", "calendar"])
def test_changed_input_identity_is_rejected_before_audit(archives, damage):
    calendar, create = archives
    archive = create()
    if damage == "raw":
        blob, = (archive/"capture-store"/"objects").glob("*")
        blob.write_bytes(blob.read_bytes()[:-1]+b"x")
    elif damage == "receipt":
        path = archive/"receipt.json"
        value = json.loads(path.read_bytes())
        value["raw"]["captures"] = 1
        value.pop("evidence_sha256")
        path.write_text(json.dumps(signed(value)))
    else:
        doc = {**calendar.document, "weekday_closures": []}
        calendar = Calendar(doc)
    with pytest.raises(ValueError):
        audit.audit_etf_history(archive, calendar, REVISION)


def legacy_files(tmp_path, reports, *, change=None, offset=0):
    rows = []
    for report in reports:
        for current in report["normalized"]:
            day = date.fromisoformat(current["date"])+timedelta(days=offset)
            row = {"symbol": report["symbol"],
                "exchange": "SSE" if report["symbol"].startswith("5") else "SZSE",
                "timestamp": f"{day}T07:00:00+00:00", "trading_day": day,
                **{key: current[key] for key in audit.FIELDS}, "data_version": "legacy-test-v1"}
            rows.append(row)
    if change:
        rows = change(rows)
    data = tmp_path/"legacy.parquet"
    pq.write_table(pa.Table.from_pylist(rows), data)
    manifest = tmp_path/"legacy-manifest.json"
    manifest.write_text(json.dumps(signed({"manifest": {
        "schema_version": "qtrade.bar.v1", "quality_status": "PASSED",
        "data_version": "legacy-test-v1", "source": "fixture native prices",
        "checksum": file_hash(data)}})))
    return manifest, data


@pytest.mark.parametrize("kind", ["equal", "mismatch", "duplicate", "invalid", "none"])
def test_optional_verified_legacy_overlap_does_not_certify_equivalence(archives, tmp_path, kind):
    calendar, create = archives
    archive = create()
    initial = audit.audit_etf_history(archive, calendar, REVISION)

    def change(rows):
        if kind == "mismatch":
            rows[0]["volume"] = "1001"
        elif kind == "duplicate":
            rows.append(rows[0].copy())
        elif kind == "invalid":
            rows[0]["exchange"] = "SSE"
        return rows

    manifest, data = legacy_files(tmp_path, initial["captures"], change=change,
                                  offset=-365 if kind == "none" else 0)
    before = snapshot(archive), manifest.read_bytes(), data.read_bytes()
    result = audit.audit_etf_history(archive, calendar, REVISION,
                                     legacy_manifest=manifest, legacy_bars=data)["legacy_overlap"]
    assert (snapshot(archive), manifest.read_bytes(), data.read_bytes()) == before
    assert result["bars_sha256"] == file_hash(data)
    assert result["source_equivalence_certified"] is False
    assert result["adjustment_certified"] is False
    assert result["combined_history_admitted"] is False
    if kind == "equal":
        assert result["state"] == "EXACT_OHLCV_OVERLAP_ONLY"
        assert result["equal_ohlcv_pairs"] == 110
    elif kind == "none":
        assert result["state"] == "NO_OVERLAP" and result["overlap_pairs"] == 0
    else:
        assert result["state"] == "OVERLAP_DIFFERENCES"
        assert result["mismatched_pairs"]+result["invalid_pairs"] == 1
        assert len(result["details"]) == 1


def test_legacy_hash_tamper_and_half_input_pair_fail_closed(archives, tmp_path):
    calendar, create = archives
    archive = create()
    reports = audit.audit_etf_history(archive, calendar, REVISION)["captures"]
    manifest, data = legacy_files(tmp_path, reports)
    with pytest.raises(ValueError, match="PAIR_REQUIRED"):
        audit.audit_etf_history(archive, calendar, REVISION, legacy_manifest=manifest)
    data.write_bytes(data.read_bytes()+b"tamper")
    with pytest.raises(ValueError, match="HASH_INVALID"):
        audit.audit_etf_history(archive, calendar, REVISION,
                               legacy_manifest=manifest, legacy_bars=data)


def test_cli_emits_signed_json_and_never_leaks_private_errors(archives, tmp_path, capsys):
    calendar, create = archives
    archive = create(count=21)
    path = tmp_path/"calendar.json"
    path.write_text(json.dumps(calendar.document))
    assert audit.main(["--archive", str(archive), "--calendar", str(path),
                       "--revision", REVISION]) == 0
    value = json.loads(capsys.readouterr().out)
    verify_signed(value)
    assert value["stage"] == "SOURCE_COVERAGE_AUDIT_ONLY"
    with pytest.raises(SystemExit) as error:
        audit.main(["--archive", str(tmp_path/"private-missing"), "--calendar", str(path),
                    "--revision", REVISION])
    assert error.value.code == 2
    stderr = capsys.readouterr().err
    assert "private-missing" not in stderr and "ETF_HISTORY_AUDIT_FAILED" in stderr


def test_saved_audit_must_replay_original_raw_even_after_resigning(archives, tmp_path):
    calendar, create = archives
    archive = create()
    value = audit.audit_etf_history(archive, calendar, REVISION)
    path = tmp_path/"audit.json"
    path.write_text(json.dumps(value))
    before = snapshot(archive)
    assert audit.verify_etf_history_audit(path, archive, calendar) == value
    value["captures"][0]["normalized"][0]["close"] = "2.11"
    value.pop("evidence_sha256")
    path.write_text(json.dumps(signed(value)))
    with pytest.raises(ValueError, match="RAW_REPLAY_MISMATCH"):
        audit.verify_etf_history_audit(path, archive, calendar)
    assert snapshot(archive) == before


@pytest.mark.parametrize("damage", ["status", "truncated", "transport"])
def test_unsuccessful_response_does_not_admit_plausible_body(archives, damage):
    calendar, create = archives

    def response_change(symbol, response):
        if symbol == "159915":
            if damage == "status":
                response["status_code"] = 503
            elif damage == "truncated":
                response["body_complete"] = False
            else:
                response["transport_error"] = "SOURCE_HTTP_TRANSPORT"

    result = audit.audit_etf_history(create(response_change=response_change), calendar, REVISION)
    report = result["captures"][0]
    assert report["raw_row_count"] == 55
    assert report["normalized"] == []
    assert {row["reason"] for row in report["rejected_days"]} == {
        "ETF_SOURCE_RESPONSE_INCOMPLETE"}
    assert result["cohort"] == ["159915", "510300"] and result["comparison_grid"] == []


def test_amount_is_preserved_only_when_actual_body_supplies_valid_amount(archives):
    calendar, create = archives

    def amount(symbol, rows):
        rows[0]["amount"] = "2100"
        return rows

    result = audit.audit_etf_history(create(amount), calendar, REVISION)
    for report in result["captures"]:
        assert report["amount_present_sessions"] == 1
        assert report["normalized"][0]["amount"] == "2100"
        assert report["normalized"][0]["vwap"] == "2.1"
        assert report["normalized"][1]["amount"] is None


def test_overlap_details_are_bounded_without_losing_mismatch_totals(archives, tmp_path):
    calendar, create = archives
    archive = create()
    reports = audit.audit_etf_history(archive, calendar, REVISION)["captures"]

    def change(rows):
        for row in rows:
            row["volume"] = "1001"
        return rows

    manifest, data = legacy_files(tmp_path, reports, change=change)
    result = audit.audit_etf_history(archive, calendar, REVISION,
        legacy_manifest=manifest, legacy_bars=data)["legacy_overlap"]
    assert result["mismatched_pairs"] == 110
    assert len(result["details"]) == 50 and result["details_truncated"] is True
