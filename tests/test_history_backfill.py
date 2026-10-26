import json
import os
import subprocess
import sys
from contextlib import contextmanager, nullcontext
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from qtrade.instruments.observed import build_observed_master
from qtrade.market_data.backfill_plan import build_backfill_plan
from qtrade.market_data.sina_minute import SINA_FIVE_MINUTE_SESSION_TIMES
from qtrade.monitoring.data_freshness import signed
from scripts import history_backfill_queue as queue_module
from scripts import history_backfill_store as store_module
from scripts import run_history_backfill as runner
from scripts import run_history_backfill_service as service
from scripts import unified_store
from scripts.history_backfill_queue import HistoryQueue
from scripts.history_backfill_store import HistoryStore, fields, qualify, request_for
from scripts.probe_unified_history import RecordedResult

NOW = datetime(2026, 10, 26, 14, tzinfo=UTC)
DAY = date(2026, 10, 23)
REVISION = "c" * 40


@pytest.fixture(autouse=True)
def platform_writers(monkeypatch):
    if os.name != "nt":
        return
    # Actual Linux directory fsync and flock are cluster acceptance requirements.
    monkeypatch.setattr(unified_store, "_fsync_directory", lambda _: None)
    monkeypatch.setattr(runner, "_fsync_directory", lambda _: None)
    for module in (queue_module, store_module, runner, service):
        for name in ("write_json_atomic", "write_json_immutable"):
            original = getattr(module, name, None)
            if original is None:
                continue

            def write(path, value, original=original):
                try:
                    original(path, value)
                except PermissionError:
                    assert path.is_file() and json.loads(path.read_text(encoding="utf-8")) == value

            monkeypatch.setattr(module, name, write)


@pytest.fixture
def inputs():
    master = build_observed_master(
        vendor_rows=[{"code": "sh.600036", "code_name": "招商银行", "type": "1",
                      "status": "1", "ipoDate": "2002-04-09"}],
        vendor_sha256="a" * 64, source_groups=[{
            "source_id": "legacy", "role": "legacy", "sha256": "b" * 64,
            "rows": [{"symbol": "600036", "security_type": "STOCK"}],
        }], observed_at=NOW, target_count=1,
    )
    master.pop("evidence_sha256")
    master["securities"][0]["frequencies"] = {
        f: {"state": "PLANNED", "bar_count": 0} for f in ("day", "5min")}
    master = signed(master)
    plan = build_backfill_plan(master, requested_end=DAY, observed_at=NOW,
                               code_revision=REVISION, years=1)
    return plan, master


def scope(frequency="5min", **updates):
    return {"symbol": "600036", "instrument_id": "SSE.600036", "security_type": "STOCK",
            "frequency": frequency, "start": str(DAY), "end": str(DAY), **updates}


def rows(frequency="5min", day=DAY):
    if frequency == "day":
        return [[str(day), "sh.600036", "10", "11", "9", "10", "100", "1000", "3", "1"]]
    return [[str(day), datetime.combine(day, t).strftime("%Y%m%d%H%M%S") + "000",
             "sh.600036", "10", "11", "9", "10", "100", "1000", "3"]
            for t in SINA_FIVE_MINUTE_SESSION_TIMES]


def raw_for(value, values=None, **changes):
    frequency = value["frequency"]
    return {"request": request_for(value), "fields": list(fields(frequency)),
            "rows": rows(frequency) if values is None else values,
            "body_complete": True, "vendor_error_code": "0", "transport_error": None,
            "query_attempted": True, **changes}


def setup(tmp_path, inputs):
    queue = HistoryQueue(tmp_path / "history", *inputs)
    return queue, HistoryStore(tmp_path / "history/lake")


def claim(queue, store, **kwargs):
    attempt = queue.claim(now=kwargs.pop("now", NOW), revision=REVISION, **kwargs)
    assert attempt is not None
    store.begin(attempt)
    return attempt


def seal(queue, store, attempt, *, values=None, state=None, now=NOW):
    receipt = store.seal(attempt, raw_for(attempt["scope"], values), state=state)
    queue.finish(store, receipt, now=now)
    return receipt


@pytest.mark.parametrize("frequency", ["day", "5min"])
def test_daily_and_minute_raw_qualification_keeps_price_basis_and_unknown_calendar(frequency):
    normalized, quality = qualify(raw_for(scope(frequency)), scope(frequency))
    assert quality["qualified_rows"] == (1 if frequency == "day" else 48)
    assert quality["window_coverage_certified"] is False
    assert quality["qualified_observed_sessions"] == [str(DAY)]
    assert all(r["historical_available_at"] is None for r in normalized)
    assert all(r["price_basis"] == store_module.PRICE_BASIS for r in normalized)


def test_partial_grid_is_preserved_but_not_qualified_and_missing_weekdays_are_candidates():
    value = scope(start="2026-10-19")
    normalized, report = qualify(raw_for(value, rows()[:3]), value)
    assert len(normalized) == 3 and report["qualified_rows"] == 0
    assert report["partial_sessions"] == {str(DAY): 3}
    assert report["unobserved_weekday_candidates_not_exchange_calendar"] == [
        "2026-10-19", "2026-10-20", "2026-10-21", "2026-10-22"]
    assert {r["session_quality"] for r in normalized} == {"PARTIAL_SESSION_QUARANTINED"}


@pytest.mark.parametrize("frequency", ["day", "5min"])
@pytest.mark.parametrize("change", ["identity", "amount", "adjustflag", "duplicate", "nan",
                                    "order", "weekend", "zero_amount", "fields"])
def test_bad_history_is_rejected_without_fabricating_coverage(frequency, change):
    value = scope(frequency)
    raw = raw_for(value)
    offset = 3 if frequency == "5min" else 2
    if change == "identity":
        raw["rows"][0][offset - 1] = "sz.000001"
    elif change == "amount":
        raw["rows"][0][offset + 5] = "100000000"
    elif change == "adjustflag":
        raw["rows"][0][offset + 6] = "2"
    elif change == "duplicate":
        raw["rows"].append(raw["rows"][0])
    elif change == "nan":
        raw["rows"][0][offset] = "NaN"
    elif change == "zero_amount":
        raw["rows"][0][offset + 5] = "0"
    elif change == "fields":
        raw["fields"] = ["wrong"]
    elif change == "weekend":
        raw["rows"][0][0] = "2026-10-24"
    elif change == "order":
        if frequency == "day":
            raw["rows"] += rows("day", date(2026, 10, 22))
            value["start"] = "2026-10-22"
            raw["request"] = request_for(value)
        else:
            raw["rows"].reverse()
    with pytest.raises((ValueError, ArithmeticError)):
        qualify(raw, value)


def test_vendor_nontrading_observations_do_not_certify_suspension_or_admit_prices():
    values = rows("day")
    values[0][2:8] = ["10", "10", "10", "10", "0", "0"]
    values[0][-1] = "0"
    records, quality = qualify(raw_for(scope("day"), values), scope("day"))
    assert not records and quality["qualified_rows"] == 0
    assert quality["state"] == "VENDOR_NONTRADING_OBSERVATIONS_UNCERTIFIED"
    placeholders = rows()
    for row in placeholders:
        row[3:9] = ["0"] * 6
    records, quality = qualify(raw_for(scope(), placeholders), scope())
    assert not records and quality["vendor_all_zero_rows_by_day"] == {str(DAY): 48}
    placeholders[0][1] = "20261023093000000"
    with pytest.raises(ValueError, match="CLOCK"):
        qualify(raw_for(scope(), placeholders), scope())


def test_empty_window_and_failed_login_are_distinct_and_neither_proves_source_range():
    records, quality = qualify(raw_for(scope(), []), scope())
    assert not records and quality["state"] == "EMPTY_WINDOW_UNEXPLAINED"
    assert quality["qualified_rows"] == 0
    with pytest.raises(ValueError, match="INCOMPLETE"):
        qualify(raw_for(scope(), [], body_complete=False, vendor_error_code=None), scope())


def test_store_seals_raw_before_parse_and_replays_parquet(tmp_path, inputs, monkeypatch):
    queue, store = setup(tmp_path, inputs)
    attempt = claim(queue, store)
    raw = raw_for(attempt["scope"])
    original = store_module.qualify

    def check(*args):
        assert list((store.root / "objects").glob("*.jsonl.gz"))
        return original(*args)

    monkeypatch.setattr(store_module, "qualify", check)
    result = store.seal(attempt, raw)
    assert store.load(attempt["attempt_id"]) == result
    assert result["quality"]["qualified_rows"] > 0 and result["historical_pit_certified"] is False
    saved = {p: p.read_bytes() for p in store.root.rglob("*") if p.is_file()}
    assert store.seal(attempt, raw) == result
    with pytest.raises(ValueError, match="IDEMPOTENCY"):
        store.seal(attempt, raw_for(attempt["scope"], []))
    assert all(p.read_bytes() == value for p, value in saved.items())
    with pytest.raises(FileExistsError):
        store.begin(attempt)


def test_corrupted_raw_parquet_and_receipts_reject_reuse(tmp_path, inputs):
    queue, store = setup(tmp_path, inputs)
    attempt = claim(queue, store)
    result = seal(queue, store, attempt)
    normalized = store.blobs._blob_path(result["normalized"]["sha256"], ".parquet")
    original = normalized.read_bytes()
    normalized.write_bytes(b"tampered")
    with pytest.raises(ValueError, match="SIZE_MISMATCH"):
        store.load(attempt["attempt_id"])
    normalized.write_bytes(original)
    receipt_path = store.path(attempt["attempt_id"], "receipt.json")
    receipt = json.loads(receipt_path.read_text())
    receipt["quality"]["qualified_rows"] += 1
    receipt_path.write_text(json.dumps(receipt))
    with pytest.raises(ValueError, match="IDENTITY"):
        store.load(attempt["attempt_id"])


def test_new_windows_rotate_across_scopes_while_empty_window_waits(tmp_path, inputs):
    queue, store = setup(tmp_path, inputs)
    first = claim(queue, store)
    result = seal(queue, store, first, values=[])
    assert result["quality"]["state"] == "EMPTY_WINDOW_UNEXPLAINED"
    assert queue.claim(now=NOW, revision=REVISION, retry=True) is None
    second = claim(queue, store)
    assert second["scope"]["task_id"] != first["scope"]["task_id"]
    seal(queue, store, second)
    third = claim(queue, store, now=NOW + timedelta(seconds=1))
    assert third["scope"]["task_id"] == first["scope"]["task_id"]
    assert third["scope"]["end"] == "2026-09-30"
    assert first["scope"]["original_candidate_source"] in {
        "SINA_HISTORY_RAW_WITH_TURNOVER", "BAOSTOCK_RAW_5MIN_ADJUSTFLAG_3"}
    assert first["scope"]["execution_source"] == "BAOSTOCK_RAW_ADJUSTFLAG_3"


def test_global_login_backoff_is_persistent_and_retry_budget_is_independent(tmp_path, inputs):
    queue, store = setup(tmp_path, inputs)
    attempt = claim(queue, store)
    seal(queue, store, attempt, values=[], state="LOGIN_FAILED")
    assert queue.meta["global_next_attempt_at"] == (NOW + timedelta(minutes=30)).isoformat()
    resumed = HistoryQueue(queue.root, *inputs)
    assert resumed.claim(now=NOW + timedelta(minutes=29), revision=REVISION) is None
    retry = claim(resumed, store, now=NOW + timedelta(minutes=30), retry=True)
    assert retry["scope"] == attempt["scope"] and retry["attempt_number"] == 2
    seal(resumed, store, retry, values=[], state="LOGIN_FAILED", now=NOW + timedelta(minutes=30))
    assert resumed.meta["global_next_attempt_at"] == (NOW + timedelta(minutes=90)).isoformat()


def test_crash_before_receipt_and_after_seal_recover_without_skipping_or_double_count(
    tmp_path, inputs,
):
    queue, store = setup(tmp_path, inputs)
    first = claim(queue, store)
    recovered = HistoryQueue(queue.root, *inputs)
    assert recovered.recover(store, now=NOW)["interrupted_attempts"] == 1
    first_state = recovered.tasks[first["scope"]["task_id"]]["windows"][first["scope"]["start"]]
    assert first_state["state"] == "INTERRUPTED" and first_state["qualified_rows"] == 0
    second = claim(recovered, store)
    store.seal(second, raw_for(second["scope"]))
    after = HistoryQueue(queue.root, *inputs)
    assert after.recover(store, now=NOW)["replayed_sealed_receipts"] == 1
    count = after.snapshot(now=NOW)["qualified_rows"]
    assert count > 0
    assert after.recover(store, now=NOW) == {
        "replayed_sealed_receipts": 0, "interrupted_attempts": 0}
    assert after.snapshot(now=NOW)["qualified_rows"] == count


def test_changed_plan_and_master_and_checkpoint_tampering_cannot_resume(tmp_path, inputs):
    queue, store = setup(tmp_path, inputs)
    attempt = claim(queue, store)
    seal(queue, store, attempt)
    changed = deepcopy(inputs[0])
    changed["requested_start"] = "2010-01-01"
    with pytest.raises(ValueError, match="FROZEN_PLAN"):
        HistoryQueue(queue.root, changed, inputs[1])
    changed = deepcopy(inputs[1])
    changed["securities"][0]["listing_date"] = "2025-01-01"
    with pytest.raises(ValueError):
        HistoryQueue(queue.root, inputs[0], signed({k: v for k, v in changed.items()
                                                 if k != "evidence_sha256"}))
    path = queue.task_path(attempt["scope"]["task_id"])
    value = json.loads(path.read_text())
    value["before"] = "2010-01-01"
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="CHECKPOINT_HASH"):
        HistoryQueue(queue.root, *inputs)


def test_shared_lease_yield_is_durable_but_never_advances_window(tmp_path, inputs):
    queue, store = setup(tmp_path, inputs)
    attempt = claim(queue, store)
    seal(queue, store, attempt, values=[], state="YIELDED_TO_SHARED_VENDOR_SESSION")
    task = queue.tasks[attempt["scope"]["task_id"]]
    assert task["before"] is None
    next_attempt = claim(queue, store)
    assert next_attempt["scope"]["task_id"] != attempt["scope"]["task_id"]


@pytest.mark.parametrize("state,age,expected", [
    ("WAITING", 0, None), ("COLLECTING", 0, "ACTIVE"), ("FAILED", 0, "ACTIVE"),
    ("WAITING", 301, "STALE"), ("WAITING", -1, "FUTURE"),
])
def test_current_collection_priority_requires_fresh_idle_evidence(tmp_path, state, age, expected):
    path = tmp_path / "unified-data/service-state.json"
    path.parent.mkdir()
    path.write_text(json.dumps({"state": state,
                                "heartbeat_at": (NOW - timedelta(seconds=age)).isoformat()}))
    result = runner.priority_reason(tmp_path, NOW)
    assert result is None if expected is None else expected in result


def test_capture_retains_rows_and_class_only_on_midstream_failure(tmp_path, inputs, monkeypatch):
    queue, store = setup(tmp_path, inputs)
    attempt = claim(queue, store)
    value = raw_for(attempt["scope"])
    result = RecordedResult(value)
    original = result.next
    calls = 0

    def fail():
        nonlocal calls
        calls += 1
        if calls == 2:
            raise TimeoutError("secret must not be logged")
        return original()

    result.next = fail
    api = Mock()
    api.login.return_value.error_code = "0"
    api.query_history_k_data_plus.return_value = result
    monkeypatch.setattr(runner, "baostock_session", lambda **kwargs: nullcontext())
    runner.capture_vendor(store, attempt, api)
    raw = runner.read_capture(store, attempt)
    assert len(raw["rows"]) == 1 and raw["body_complete"] is False
    assert raw["state"] == "SOURCE_QUERY_FAILED"
    assert raw["transport_error"] == "TimeoutError"
    assert "secret" not in store.path(attempt["attempt_id"], "capture.jsonl").read_text()
    api.logout.assert_called_once()


def test_busy_shared_vendor_lease_does_not_login(tmp_path, inputs, monkeypatch):
    queue, store = setup(tmp_path, inputs)
    attempt = claim(queue, store)

    @contextmanager
    def busy(**kwargs):
        assert kwargs == {"blocking": False}
        raise BlockingIOError
        yield

    monkeypatch.setattr(runner, "baostock_session", busy)
    api = Mock()
    runner.capture_vendor(store, attempt, api)
    raw = runner.read_capture(store, attempt)
    assert raw["state"] == "YIELDED_TO_SHARED_VENDOR_SESSION"
    api.login.assert_not_called()


def test_timeout_and_priority_preemption_reap_child_and_preserve_capture(
    tmp_path, inputs, monkeypatch,
):
    queue, store = setup(tmp_path, inputs)
    attempt = claim(queue, store)
    child = Mock()
    child.poll.return_value = None
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *args, **kwargs: child)
    monkeypatch.setattr(runner, "priority_reason", lambda *args: None)
    monkeypatch.setattr(runner.shutil, "disk_usage", lambda _: SimpleNamespace(
        free=runner.RESERVE + runner.WRITE_HEADROOM))
    times = iter([0, 30])
    monkeypatch.setattr(runner.time, "monotonic", lambda: next(times))
    raw, state = runner.download(store, attempt, base=tmp_path, seconds=25, stop=Event())
    assert state == "QUERY_TIMEOUT" and raw["rows"] == []
    child.terminate.assert_called_once()
    child.wait.assert_called_once()
    child.reset_mock()
    monkeypatch.setattr(runner.time, "monotonic", lambda: 0)
    reasons = iter([None, "CURRENT_PRIORITY_WORK_ACTIVE"])
    monkeypatch.setattr(runner, "priority_reason", lambda *args: next(reasons))
    _, state = runner.download(store, attempt, base=tmp_path, seconds=25, stop=Event())
    assert state == "YIELDED_TO_CURRENT_COLLECTION"
    child.terminate.assert_called_once()


def test_kill_fallback_reaps_a_child_ignoring_terminate():
    child = Mock()
    child.poll.return_value = None
    child.wait.side_effect = [subprocess.TimeoutExpired("capture", 3), 0]
    runner.reap(child)
    child.kill.assert_called_once()
    assert child.wait.call_count == 2


def test_real_pass_seals_two_frequencies_and_resume_keeps_old_bytes(tmp_path, inputs, monkeypatch):
    @contextmanager
    def lease(*args):
        yield

    monkeypatch.setattr(runner, "worker_lease", lease)
    monkeypatch.setattr(runner, "priority_reason", lambda *args: None)
    monkeypatch.setattr(runner.shutil, "disk_usage", lambda _: SimpleNamespace(
        free=runner.RESERVE + runner.WRITE_HEADROOM))

    def download(store, attempt, **kwargs):
        return raw_for(attempt["scope"]), None

    root = tmp_path / "execution"
    result = runner.run_pass(tmp_path, root, *inputs, revision=REVISION,
                             new_windows=2, retry_windows=0, downloader=download)
    assert result["frozen_scope_count"] == 2 and result["attempted_windows"] == 2
    assert result["qualified_rows"] == 49
    saved = {p: p.read_bytes() for p in (root / "lake").rglob("*") if p.is_file()}
    second = runner.run_pass(tmp_path, root, *inputs, revision=REVISION,
                             new_windows=0, retry_windows=1, downloader=download)
    assert second["attempted_windows"] == 0 and second["qualified_rows"] == 49
    assert all(p.read_bytes() == value for p, value in saved.items())
    assert (tmp_path / "unified-data/backfill/execution-latest.json").exists()
    assert result["ten_year_history_certified"] is False


@pytest.mark.parametrize("block", ["disk", "current", "live"])
def test_pass_guards_prevent_download(tmp_path, inputs, monkeypatch, block):
    @contextmanager
    def lease(*args):
        yield

    monkeypatch.setattr(runner, "worker_lease", lease)
    monkeypatch.setattr(runner, "priority_reason", lambda *args: "ACTIVE" if block == "current"
                        else None)
    monkeypatch.setattr(runner.shutil, "disk_usage", lambda _: SimpleNamespace(
        free=runner.RESERVE - 1 if block == "disk" else runner.RESERVE + runner.WRITE_HEADROOM))
    download = Mock()
    if block == "live":
        monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
        with pytest.raises(RuntimeError, match="LIVE_TRADING"):
            runner.run_pass(tmp_path, tmp_path / "history", *inputs, revision=REVISION,
                            downloader=download)
    else:
        result = runner.run_pass(tmp_path, tmp_path / "history", *inputs, revision=REVISION,
                                 downloader=download)
        assert result["attempted_windows"] == 0
        assert result["state"] == ("STORAGE_RESERVE_PAUSED" if block == "disk"
                                   else "YIELDED_TO_CURRENT_COLLECTION")
    download.assert_not_called()


def test_source_backoff_is_durable_before_task_completion_and_replay_is_idempotent(
    tmp_path, inputs, monkeypatch,
):
    queue, store = setup(tmp_path, inputs)
    attempt = claim(queue, store)
    receipt = store.seal(attempt, raw_for(attempt["scope"], []), state="LOGIN_FAILED")
    monkeypatch.setattr(queue, "save_task", Mock(side_effect=RuntimeError("crash")))
    with pytest.raises(RuntimeError, match="crash"):
        queue.finish(store, receipt, now=NOW)
    resumed = HistoryQueue(queue.root, *inputs)
    assert resumed.meta["global_next_attempt_at"] == (NOW + timedelta(minutes=30)).isoformat()
    assert resumed.tasks[attempt["scope"]["task_id"]]["active"] is not None
    assert resumed.recover(store, now=NOW)["replayed_sealed_receipts"] == 1
    assert resumed.meta["global_failures"] == 1


@pytest.mark.parametrize("change", ["conflict", "regression", "superset"])
def test_retry_preserves_accepted_coverage_on_regression_and_quarantines_changed_prices(
    tmp_path, inputs, change,
):
    queue, store = setup(tmp_path, inputs)
    # Visit the other scope if necessary; no code or frozen plan identity is rewritten.
    attempt = claim(queue, store)
    if attempt["scope"]["frequency"] != "5min":
        seal(queue, store, attempt)
        attempt = claim(queue, store)
    values = rows(day=date(2026, 10, 22)) + rows()[:3]
    first = seal(queue, store, attempt, values=values)
    assert first["quality"]["qualified_rows"] == 48
    retry = claim(queue, store, now=NOW + timedelta(minutes=30), retry=True)
    if change == "regression":
        newer = rows()
    else:
        newer = rows(day=date(2026, 10, 22)) + rows()
        if change == "conflict":
            for row in newer:
                row[6] = "10.5"
    seal(queue, store, retry, values=newer, now=NOW + timedelta(minutes=30))
    window = queue.tasks[attempt["scope"]["task_id"]]["windows"][attempt["scope"]["start"]]
    assert window["qualified_rows"] == (96 if change == "superset" else 48)
    assert window["accepted_attempt_id"] == (retry["attempt_id"] if change == "superset"
                                              else attempt["attempt_id"])
    assert window["source_conflict"] == {
        "conflict": "QUALIFIED_OVERLAP_CHANGED_QUARANTINED",
        "regression": "QUALIFIED_COVERAGE_REGRESSION_PRIOR_RETAINED", "superset": None,
    }[change]
    assert store.load(attempt["attempt_id"]) == first


def test_window_empty_retries_reach_six_hour_cap_without_blocking_new_scopes(tmp_path, inputs):
    queue, store = setup(tmp_path, inputs)
    attempt = claim(queue, store)
    at = NOW
    for _ in range(7):
        seal(queue, store, attempt, values=[], now=at)
        window = queue.tasks[attempt["scope"]["task_id"]]["windows"][attempt["scope"]["start"]]
        due = datetime.fromisoformat(window["next_attempt_at"])
        delay = due - at
        assert timedelta(minutes=30) <= delay <= timedelta(hours=6)
        at = due
        attempt = claim(queue, store, now=at, retry=True)
    assert window["failures"] == 7 and delay == timedelta(hours=6)


def test_child_entry_preserves_exact_claim_and_installs_independent_hard_deadline(
    tmp_path, inputs, monkeypatch,
):
    queue, store = setup(tmp_path, inputs)
    attempt = claim(queue, store)
    monkeypatch.setenv("QTRADE_BAOSTOCK_FREE_DATA_ACK", "true")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setattr(sys, "argv", ["history", "--capture-root", str(store.root),
                                     "--capture-attempt", attempt["attempt_id"],
                                     "--capture-seconds", "1.2"])
    capture = Mock()
    alarm = Mock()
    monkeypatch.setattr(runner, "capture_vendor", capture)
    monkeypatch.setattr(runner, "import_module", lambda _: "fake_public_source")
    monkeypatch.setattr(runner.signal, "SIGALRM", 14, raising=False)
    monkeypatch.setattr(runner.signal, "signal", Mock())
    monkeypatch.setattr(runner.signal, "alarm", alarm, raising=False)
    runner.main()
    assert capture.call_args.args[1:] == (attempt, "fake_public_source")
    alarm.assert_called_once_with(2)


def test_capture_stream_to_parquet_both_frequencies_and_fresh_process_resume(
    tmp_path, inputs, monkeypatch,
):
    monkeypatch.setattr(runner, "baostock_session", lambda **kwargs: nullcontext())
    monkeypatch.setattr(runner, "worker_lease", lambda *args: nullcontext())
    monkeypatch.setattr(runner, "priority_reason", lambda *args: None)
    monkeypatch.setattr(runner.shutil, "disk_usage", lambda _: SimpleNamespace(
        free=runner.RESERVE + runner.WRITE_HEADROOM))

    def capture(store, attempt, **kwargs):
        api = Mock()
        api.login.return_value.error_code = "0"
        api.query_history_k_data_plus.return_value = RecordedResult(raw_for(attempt["scope"]))
        runner.capture_vendor(store, attempt, api)
        raw = runner.read_capture(store, attempt)
        assert raw["state"] == "CAPTURED" and raw["capture_observed_at"] is not None
        return raw, None

    root = tmp_path / "execution"
    result = runner.run_pass(tmp_path, root, *inputs, revision=REVISION,
                             new_windows=2, retry_windows=0, downloader=capture)
    assert result["stage_counts_this_pass"] == {
        "requested": 2, "downloaded": 2, "downloaded_rows": 49, "parsed_rows": 49,
        "qualified_rows_including_reobservations": 49, "newly_qualified_rows": 49,
        "independent_source_reconciled": 0, "published_qlib_versions": 0,
    }
    queue = HistoryQueue(root, *inputs)
    store = HistoryStore(root / "lake")
    for task in queue.tasks.values():
        for window in task["windows"].values():
            assert store.load(window["accepted_attempt_id"])["quality"]["qualified_rows"] in {1, 48}


@pytest.mark.skipif(os.name == "nt", reason="Real Linux flock/SIGALRM acceptance on cluster")
def test_child_inherits_writer_lease_and_hard_deadline_releases_it_without_parent(
    tmp_path, inputs,
):
    queue, store = setup(tmp_path, inputs)
    attempt = claim(queue, store)
    fake = tmp_path / "public_source_fixture"
    fake.mkdir()
    (fake / "baostock.py").write_text(
        "import time\ndef login():\n    time.sleep(3600)\n", encoding="utf-8")
    env = {**os.environ, "LIVE_TRADING_ENABLED": "false", "QTRADE_BAOSTOCK_FREE_DATA_ACK": "true",
           "PYTHONPATH": os.pathsep.join((str(fake), str(runner.REPO / "src"), str(runner.REPO)))}
    lease_dir = tmp_path / "local-leases"
    with runner.worker_lease(lease_dir) as handle:
        child = subprocess.Popen([
            sys.executable, "-m", "scripts.run_history_backfill", "--capture-root", str(store.root),
            "--capture-attempt", attempt["attempt_id"], "--capture-seconds", "2",
        ], cwd=runner.REPO, env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
            pass_fds=(handle.fileno(),))
    try:
        # Closing the parent's descriptor has the same flock effect as parent death.
        with pytest.raises(BlockingIOError), runner.worker_lease(lease_dir):
            pass
        assert child.wait(timeout=10) == 124
        with runner.worker_lease(lease_dir):
            assert Path(lease_dir / "history-backfill.lock").exists()
        assert runner.read_capture(store, attempt)["body_complete"] is False
    finally:
        runner.reap(child)


def idle_marker(base, now=NOW):
    path = base / "unified-data/service-state.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"state": "WAITING", "heartbeat_at": now.isoformat()}))


@pytest.mark.parametrize("payload,expected", [
    (None, None), (b"{}", "PIT_SOURCE_BACKOFF_INVALID"), (b'{"outcome":"RUNNING"}', None),
    (b'{"outcome":"FAILED","next_attempt_at":"2026-10-26T14:51:00+00:00"}',
     "PIT_SOURCE_BACKOFF_ACTIVE"),
    (b'{"outcome":"FAILED","next_attempt_at":"2026-10-26T13:59:59+00:00"}', None),
    (b'{"outcome":"FAILED","next_attempt_at":"2026-10-26T14:00:00+00:00"}', None),
    (b"bad json", "PIT_SOURCE_BACKOFF_INVALID"),
    (b"[]", "PIT_SOURCE_BACKOFF_INVALID"),
    (b'{"next_attempt_at":null}', "PIT_SOURCE_BACKOFF_INVALID"),
    (b'{"next_attempt_at":"2026-10-26T14:51:00"}', "PIT_SOURCE_BACKOFF_INVALID"),
    (b"a" * 64001, "PIT_SOURCE_BACKOFF_INVALID"),
], ids=["missing", "unset", "running_unset", "future", "expired", "due", "broken",
        "array", "null", "naive", "oversize"])
def test_pit_global_deadline_is_respected_and_corruption_fails_closed(tmp_path, payload, expected):
    idle_marker(tmp_path)
    path = tmp_path / "pit-repair/next-pass.json"
    if payload is not None:
        path.parent.mkdir()
        path.write_bytes(payload)
    assert runner.priority_reason(tmp_path, NOW) == expected


def test_pit_backoff_pass_makes_zero_requests_and_expiry_allows_resume(
    tmp_path, inputs, monkeypatch,
):
    monkeypatch.setattr(runner, "worker_lease", lambda *args: nullcontext())
    monkeypatch.setattr(runner.shutil, "disk_usage", lambda _: SimpleNamespace(
        free=runner.RESERVE + runner.WRITE_HEADROOM))
    now = datetime.now(UTC)
    idle_marker(tmp_path, now)
    marker = tmp_path / "pit-repair/next-pass.json"
    marker.parent.mkdir()
    marker.write_text(json.dumps({"outcome": "FAILED", "returncode": 1, "consecutive_failures": 3,
                                  "next_attempt_at": (now + timedelta(hours=1)).isoformat()}))
    saved = marker.read_bytes()
    download = Mock(side_effect=lambda store, attempt, **kwargs: (raw_for(attempt["scope"]), None))
    root = tmp_path / "history"
    result = runner.run_pass(tmp_path, root, *inputs, revision=REVISION,
                             new_windows=1, retry_windows=0, downloader=download)
    assert result["priority_reason"] == "PIT_SOURCE_BACKOFF_ACTIVE"
    assert result["attempted_windows"] == result["stage_counts_this_pass"]["requested"] == 0
    download.assert_not_called()
    assert not list((root / "lake/attempts").glob("*/request.json"))
    assert marker.read_bytes() == saved
    marker.write_text(json.dumps({"outcome": "FAILED", "returncode": 1, "consecutive_failures": 3,
                                  "next_attempt_at": (now - timedelta(seconds=1)).isoformat()}))
    resumed = runner.run_pass(tmp_path, root, *inputs, revision=REVISION,
                              new_windows=1, retry_windows=0, downloader=download)
    assert resumed["attempted_windows"] == 1 and resumed["qualified_rows"] > 0
    assert download.call_count == 1


@pytest.mark.parametrize("outcome", ["SUCCEEDED", "PARTIAL"])
def test_normal_pit_schedule_future_deadline_allows_history_pass(
    tmp_path, inputs, monkeypatch, outcome,
):
    monkeypatch.setattr(runner, "worker_lease", lambda *args: nullcontext())
    monkeypatch.setattr(runner.shutil, "disk_usage", lambda _: SimpleNamespace(
        free=runner.RESERVE + runner.WRITE_HEADROOM))
    now = datetime.now(UTC)
    idle_marker(tmp_path, now)
    marker = tmp_path / "pit-repair/next-pass.json"
    marker.parent.mkdir()
    marker.write_text(json.dumps({
        "mode": "BOUNDED_UNIVERSE_QUEUE_V2", "outcome": outcome, "returncode": 0,
        "receipt_valid": True, "consecutive_failures": 0,
        "next_attempt_at": (now + timedelta(hours=6)).isoformat(),
    }))
    download = Mock(side_effect=lambda store, attempt, **kwargs: (raw_for(attempt["scope"]), None))
    result = runner.run_pass(tmp_path, tmp_path / "history", *inputs, revision=REVISION,
                             new_windows=1, retry_windows=0, downloader=download)
    assert result["priority_reason"] is None and result["attempted_windows"] == 1
    assert download.call_count == 1


@pytest.mark.parametrize("updates,expected", [
    ({"outcome": "FAILED"}, "PIT_SOURCE_BACKOFF_ACTIVE"),
    ({"receipt_valid": False}, "PIT_SOURCE_BACKOFF_ACTIVE"),
    ({"returncode": 1}, "PIT_SOURCE_BACKOFF_ACTIVE"),
    ({"consecutive_failures": 1}, "PIT_SOURCE_BACKOFF_ACTIVE"),
    ({"outcome": "RUNNING", "consecutive_failures": 3}, None),
    ({"outcome": "SUCCEEDED"}, None),
    ({"outcome": "UNKNOWN"}, "PIT_SOURCE_BACKOFF_INVALID"),
    ({"returncode": None}, "PIT_SOURCE_BACKOFF_INVALID"),
    ({"returncode": True}, "PIT_SOURCE_BACKOFF_INVALID"),
    ({"receipt_valid": "true"}, "PIT_SOURCE_BACKOFF_INVALID"),
    ({"consecutive_failures": -1}, "PIT_SOURCE_BACKOFF_INVALID"),
    ({"next_attempt_at": None}, "PIT_SOURCE_BACKOFF_INVALID"),
])
def test_pit_failure_signals_override_normal_scheduler_outcome(tmp_path, updates, expected):
    idle_marker(tmp_path)
    marker = tmp_path / "pit-repair/next-pass.json"
    marker.parent.mkdir()
    value = {"outcome": "PARTIAL", "returncode": 0, "receipt_valid": True,
             "consecutive_failures": 0, "next_attempt_at": "2026-10-26T14:51:00+00:00"}
    marker.write_text(json.dumps({**value, **updates}))
    assert runner.priority_reason(tmp_path, NOW) == expected


@pytest.mark.parametrize("value", [
    {"next_attempt_at": "2026-10-26T14:51:00+00:00"},
    {"outcome": "PARTIAL", "returncode": 0, "next_attempt_at": "2026-10-26T14:51:00+00:00"},
    {"outcome": "FAILED", "returncode": 1},
])
def test_unclassifiable_pit_marker_stays_closed(tmp_path, value):
    marker = tmp_path / "pit-repair/next-pass.json"
    marker.parent.mkdir()
    marker.write_text(json.dumps(value))
    assert runner.pit_backoff_reason(tmp_path, NOW) == "PIT_SOURCE_BACKOFF_INVALID"


def test_legacy_success_schedule_is_a_normal_interval_without_receipt_flag(tmp_path):
    from scripts.run_pit_repair import finish_pass

    marker = tmp_path / "pit-repair/next-pass.json"
    marker.parent.mkdir()
    value = finish_pass(990, 0, NOW, 3)
    assert value["outcome"] == "SUCCEEDED" and "receipt_valid" not in value
    marker.write_text(json.dumps(value))
    assert runner.pit_backoff_reason(tmp_path, NOW) is None


def test_capture_checks_shared_deadline_after_lease_before_login(tmp_path, inputs, monkeypatch):
    queue, store = setup(tmp_path, inputs)
    attempt = claim(queue, store)
    monkeypatch.setattr(runner, "baostock_session", lambda **kwargs: nullcontext())
    api = Mock()
    runner.capture_vendor(store, attempt, api, before_login=lambda: "PIT_SOURCE_BACKOFF_ACTIVE")
    api.login.assert_not_called()
    assert runner.read_capture(store, attempt)["state"] == "YIELDED_TO_CURRENT_COLLECTION"


def test_stopped_download_reaps_active_child_before_return(tmp_path, inputs, monkeypatch):
    queue, store = setup(tmp_path, inputs)
    attempt = claim(queue, store)
    stop = Event()
    child = Mock(returncode=-15)
    child.poll.return_value = None

    def spawn(*args, **kwargs):
        stop.set()
        return child

    monkeypatch.setattr(runner.subprocess, "Popen", spawn)
    monkeypatch.setattr(runner, "priority_reason", lambda *args: None)
    _, state = runner.download(store, attempt, base=tmp_path, seconds=25, stop=stop)
    assert state == "INTERRUPTED"
    child.terminate.assert_called_once()
    child.wait.assert_called_once_with(timeout=3)


def test_group_stopped_child_is_interrupted_without_global_source_failure(
    tmp_path, inputs, monkeypatch,
):
    queue, store = setup(tmp_path, inputs)
    attempt = claim(queue, store)
    stop = Event()
    child = Mock(returncode=-15)
    child.poll.return_value = -15

    def spawn(*args, **kwargs):
        stop.set()
        return child

    monkeypatch.setattr(runner.subprocess, "Popen", spawn)
    monkeypatch.setattr(runner, "priority_reason", lambda *args: None)
    raw, state = runner.download(store, attempt, base=tmp_path, seconds=25, stop=stop)
    assert state == "INTERRUPTED"
    receipt = store.seal(attempt, raw, state=state)
    queue.finish(store, receipt, now=NOW)
    assert queue.meta["global_next_attempt_at"] is None


def test_service_300_second_cadence_heartbeat_and_clean_stop(tmp_path, inputs, monkeypatch):
    monkeypatch.setenv("QTRADE_BAOSTOCK_FREE_DATA_ACK", "true")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setattr(service, "worker_lease", lambda *args: nullcontext())
    stop = Event()
    real_wait = stop.wait
    waits = []

    def wait(seconds):
        waits.append(seconds)
        stop.set()
        return real_wait(0)

    monkeypatch.setattr(stop, "wait", wait)
    monotonic = iter([0, 40])
    monkeypatch.setattr(service.time, "monotonic", lambda: next(monotonic))

    def one_pass(*args, **kwargs):
        assert kwargs["stop"] is stop and kwargs["seconds"] == 240
        during = json.loads((tmp_path / "unified-data/backfill/service-state.json").read_text())
        assert during["state"] == "RUNNING" and during["live_trading_enabled"] is False
        return {"state": "YIELDED_TO_CURRENT_COLLECTION", "evidence_sha256": "a" * 64}

    run = Mock(side_effect=one_pass)
    monkeypatch.setattr(service, "run_pass", run)
    state = service.serve(tmp_path, tmp_path / "history", *inputs, revision=REVISION, stop=stop)
    assert run.call_count == 1 and waits == [260]
    assert state["state"] == "STOPPED" and state["completed_passes"] == 1
    assert state["last_execution_sha256"] == "a" * 64 and state["next_pass_not_before"] is None


def test_service_heartbeat_updates_while_pass_runs_and_shutdown_joins_it(
    tmp_path, inputs, monkeypatch,
):
    monkeypatch.setenv("QTRADE_BAOSTOCK_FREE_DATA_ACK", "true")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setattr(service, "worker_lease", lambda *args: nullcontext())
    monkeypatch.setattr(service, "HEARTBEAT_SECONDS", 0.01)
    written = Event()
    real_write = service.write_json_atomic
    timestamps = []

    def write(path, value):
        real_write(path, value)
        if value["state"] == "RUNNING":
            timestamps.append(value["heartbeat_at"])
            if len(timestamps) >= 2:
                written.set()

    monkeypatch.setattr(service, "write_json_atomic", write)
    stop = Event()

    def one_pass(*args, **kwargs):
        assert written.wait(2), "independent heartbeat did not advance during pass"
        stop.set()
        return {"state": "STOPPED", "evidence_sha256": "b" * 64}

    monkeypatch.setattr(service, "run_pass", one_pass)
    result = service.serve(tmp_path, tmp_path / "history", *inputs, revision=REVISION, stop=stop)
    assert result["state"] == "STOPPED" and len(set(timestamps)) >= 2


def test_service_stop_reaps_capture_and_publishes_final_heartbeat_and_execution(
    tmp_path, inputs, monkeypatch,
):
    monkeypatch.setenv("QTRADE_BAOSTOCK_FREE_DATA_ACK", "true")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setattr(service, "worker_lease", lambda *args: nullcontext())
    monkeypatch.setattr(runner, "worker_lease", lambda *args: nullcontext())
    monkeypatch.setattr(runner.shutil, "disk_usage", lambda _: SimpleNamespace(
        free=runner.RESERVE + runner.WRITE_HEADROOM))
    idle_marker(tmp_path, datetime.now(UTC))
    stop = Event()
    child = Mock(returncode=-15)
    child.poll.return_value = None

    def spawn(*args, **kwargs):
        stop.set()
        return child

    monkeypatch.setattr(runner.subprocess, "Popen", spawn)
    result = service.serve(tmp_path, tmp_path / "history", *inputs, revision=REVISION,
                           stop=stop, new_windows=1, retry_windows=0)
    child.terminate.assert_called_once()
    child.wait.assert_called_once_with(timeout=3)
    assert result["state"] == "STOPPED" and result["last_pass_state"] == "INTERRUPTED"
    latest = json.loads((tmp_path / "unified-data/backfill/execution-latest.json").read_text())
    assert latest["state"] == "INTERRUPTED" and latest["attempted_windows"] == 1
    assert latest["stage_counts_this_pass"]["requested"] == 0


def test_service_heartbeat_failure_stops_work_and_preserves_class_only_failure(
    tmp_path, inputs, monkeypatch,
):
    monkeypatch.setenv("QTRADE_BAOSTOCK_FREE_DATA_ACK", "true")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setattr(service, "worker_lease", lambda *args: nullcontext())
    monkeypatch.setattr(service, "HEARTBEAT_SECONDS", 0.01)
    real_write = service.write_json_atomic
    running_updates = 0

    def write(path, value):
        nonlocal running_updates
        if value["state"] == "RUNNING":
            running_updates += 1
            if running_updates == 2:
                raise PermissionError("DO_NOT_LOG_MESSAGE")
        real_write(path, value)

    monkeypatch.setattr(service, "write_json_atomic", write)
    stop = Event()

    def one_pass(*args, **kwargs):
        assert stop.wait(2), "heartbeat failure did not stop the active pass"
        return {"state": "STOPPED", "evidence_sha256": "b" * 64}

    monkeypatch.setattr(service, "run_pass", one_pass)
    with pytest.raises(RuntimeError, match="HISTORY_SERVICE_HEARTBEAT_FAILED"):
        service.serve(tmp_path, tmp_path / "history", *inputs, revision=REVISION, stop=stop)
    text = (tmp_path / "unified-data/backfill/service-state.json").read_text()
    assert "DO_NOT_LOG_MESSAGE" not in text
    state = json.loads(text)
    assert state["state"] == "FAILED" and state["last_error_class"] == "PermissionError"


def test_service_pass_failure_retains_previous_execution_and_waits_full_period(
    tmp_path, inputs, monkeypatch,
):
    monkeypatch.setenv("QTRADE_BAOSTOCK_FREE_DATA_ACK", "true")
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setattr(service, "worker_lease", lambda *args: nullcontext())
    path = tmp_path / "unified-data/backfill/execution-latest.json"
    path.parent.mkdir(parents=True)
    previous = b'{"previous":"preserved"}'
    path.write_bytes(previous)
    stop = Event()
    monkeypatch.setattr(service, "run_pass", Mock(side_effect=ValueError("DO_NOT_LOG_MESSAGE")))
    monkeypatch.setattr(stop, "wait", lambda seconds: stop.set())
    result = service.serve(tmp_path, tmp_path / "history", *inputs, revision=REVISION, stop=stop)
    assert result["failed_passes"] == 1 and result["last_error_class"] == "ValueError"
    assert result["last_pass_state"] == "FAILED" and result["completed_passes"] == 0
    assert path.read_bytes() == previous
