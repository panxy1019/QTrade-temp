"""Offline tests of one source scheduler across observed and delisted receipts."""

import json
import os
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from threading import Event
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_delisted_history_queue import local_writers as delisted_writers  # noqa: F401
from test_delisted_history_queue import raw_for as delisted_raw
from test_history_backfill import REVISION, idle_marker, raw_for
from test_history_backfill import inputs as observed_fixture
from test_history_backfill import platform_writers as history_writers  # noqa: F401
from test_public_delisted_plan import compile_bundle, write_inputs
from test_public_delisted_plan import inputs as official_fixture
from test_public_delisted_plan import platform_writers as official_writers  # noqa: F401

from qtrade.monitoring.data_freshness import signed
from scripts import audit_delisted_history as delisted_audit
from scripts import audit_history_backfill as observed_audit
from scripts import history_source_gate as gate_module
from scripts import run_history_backfill as runner
from scripts import run_history_backfill_service as service
from scripts.delisted_history_queue import DelistedHistoryQueue
from scripts.history_backfill_queue import HistoryQueue, read_signed
from scripts.history_backfill_store import HistoryStore
from scripts.history_source_gate import HistorySourceGate, gate_path, read_gate

REAL_WORKER_LEASE = runner.worker_lease
observed_inputs = observed_fixture
official_inputs = official_fixture


@pytest.fixture
def runtime(tmp_path, observed_inputs, official_inputs, monkeypatch):
    observed = HistoryQueue(tmp_path / "history", *observed_inputs)
    manifest = compile_bundle(tmp_path, write_inputs(tmp_path, official_inputs))
    bundle = tmp_path / "pit-repair/delisted-plans" / manifest["plan_sha256"]
    probe = tmp_path / "delisted-execution"
    monkeypatch.setattr(runner, "worker_lease", lambda *_: nullcontext())
    monkeypatch.setattr(runner.shutil, "disk_usage", lambda _: SimpleNamespace(
        free=runner.RESERVE + runner.WRITE_HEADROOM))
    idle_marker(tmp_path, datetime.now(UTC))
    return SimpleNamespace(base=tmp_path, observed=observed, probe=probe, bundle=bundle,
                           inputs=observed_inputs)


def run_observed(runtime, downloader=None, **kwargs):
    return runner.run_pass(runtime.base, runtime.observed.root, *runtime.inputs,
                           revision=REVISION, new_windows=1, retry_windows=0,
                           downloader=downloader or Mock(side_effect=lambda store, attempt, **_: (
                               raw_for(attempt["scope"]), None)), **kwargs)


def run_probe(runtime, downloader=None, **kwargs):
    return runner.run_pass(runtime.base, runtime.probe, None, None,
                           revision=REVISION, probe_bundle=runtime.bundle,
                           observed_root=runtime.observed.root, new_windows=1, retry_windows=0,
                           downloader=downloader or Mock(side_effect=lambda store, attempt, **_: (
                               delisted_raw(attempt), None)), **kwargs)


def expire(runtime):
    value = read_gate(runtime.base)
    value["next_attempt_at"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    gate_module.write_json_atomic(gate_path(runtime.base), signed({
        k: v for k, v in value.items() if k != "evidence_sha256"}))


def failed_download(store, attempt, **kwargs):
    return delisted_raw(attempt, rows=False, requested=False, complete=False), "LOGIN_FAILED"


def test_single_runner_keeps_delisted_prices_and_coverage_separate(runtime):
    active = run_observed(runtime)
    active_pointer = runtime.base / "unified-data/backfill/execution-latest.json"
    saved_pointer = active_pointer.read_bytes()
    frozen = {path: path.read_bytes() for path in runtime.bundle.rglob("*") if path.is_file()}
    original_store = {p: p.read_bytes() for p in (runtime.observed.root / "lake").rglob("*")
                      if p.is_file()}
    result = run_probe(runtime)
    assert result["schema_version"] == "qtrade.delisted-history-progress.v1"
    assert result["attempted_windows"] == result["qualified_rows"] == 1
    assert result["current_universe_coverage_delta"] == 0
    assert result["historical_pit_certified"] is False
    assert "universe_version" not in result
    assert active_pointer.read_bytes() == saved_pointer
    pointer = read_signed(runtime.base / "unified-data/backfill/delisted-execution-latest.json")
    assert pointer["progress_sha256"] == result["evidence_sha256"]
    assert all(path.read_bytes() == payload for path, payload in frozen.items())
    assert all(path.read_bytes() == payload for path, payload in original_store.items())
    assert HistoryQueue(runtime.observed.root, *runtime.inputs).snapshot(
        now=datetime.now(UTC))["qualified_rows"] == active["qualified_rows"]
    observed_checked = observed_audit.audit_history(
        runtime.observed.root, revision=REVISION, now=datetime.now(UTC),
        lease_dir=runtime.base / "audit-leases")
    probe_checked = delisted_audit.audit_delisted(
        runtime.probe, runtime.bundle, revision=REVISION, now=datetime.now(UTC),
        lease_dir=runtime.base / "audit-leases")
    assert observed_checked["state"] == probe_checked["state"] == "VERIFIED"
    assert probe_checked["verified_accepted_rows"] == 1
    assert probe_checked["current_universe_coverage_delta"] == 0


def test_empty_probe_remains_unknown_gap_with_zero_verified_coverage(runtime):
    result = run_probe(runtime, Mock(side_effect=lambda store, attempt, **_: (
        delisted_raw(attempt, rows=False), None)))
    assert result["attempted_windows"] == 1 and result["qualified_rows"] == 0
    assert result["all_requested_windows_examined"] is False
    assert result["window_states"] == {"EMPTY_WINDOW_UNEXPLAINED": 1}
    checked = delisted_audit.audit_delisted(
        runtime.probe, runtime.bundle, revision=REVISION, now=datetime.now(UTC),
        lease_dir=runtime.base / "audit-leases")
    assert checked["state"] == "VERIFIED" and checked["verified_accepted_rows"] == 0
    assert checked["gap_examples"][0]["state"] == "EMPTY_WINDOW_UNEXPLAINED"


def test_legacy_observed_failure_blocks_probe_then_success_resets_shared_clock(runtime):
    queue = runtime.observed
    now = datetime.now(UTC)
    store = HistoryStore(queue.root / "lake")
    attempt = queue.claim(now=now, revision=REVISION)
    store.begin(attempt)
    receipt = store.seal(attempt, raw_for(attempt["scope"], [], body_complete=False,
                                        query_attempted=False), state="LOGIN_FAILED")
    queue.finish(store, receipt, now=now)
    deadline = queue.meta["global_next_attempt_at"]
    download = Mock(side_effect=lambda store, attempt, **_: (delisted_raw(attempt), None))
    result = run_probe(runtime, download)
    assert result["attempted_windows"] == 0
    assert result["priority_reason"] == "HISTORY_SOURCE_BACKOFF_ACTIVE"
    assert result["global_next_attempt_at"] == deadline
    assert read_gate(runtime.base)["consecutive_failures"] == 1
    download.assert_not_called()
    expire(runtime)
    assert run_probe(runtime, download)["qualified_rows"] == 1
    assert read_gate(runtime.base)["consecutive_failures"] == 0
    assert read_gate(runtime.base)["next_attempt_at"] is None
    # The observed root's old deadline cannot resurrect after a cross-root success.
    assert run_observed(runtime)["attempted_windows"] == 1


def test_probe_failure_blocks_observed_and_retries_share_consecutive_failure_count(runtime):
    failed = run_probe(runtime, Mock(side_effect=failed_download))
    assert failed["attempted_windows"] == 1
    assert read_gate(runtime.base)["consecutive_failures"] == 1
    download = Mock()
    assert run_observed(runtime, download)["attempted_windows"] == 0
    download.assert_not_called()
    expire(runtime)
    failed = run_observed(runtime, Mock(side_effect=failed_download))
    value = read_gate(runtime.base)
    assert failed["attempted_windows"] == 1 and value["consecutive_failures"] == 2
    delay = (datetime.fromisoformat(value["next_attempt_at"]) - datetime.now(UTC)).total_seconds()
    assert 3590 < delay <= 3600
    expire(runtime)
    assert run_probe(runtime)["qualified_rows"] == 1
    assert read_gate(runtime.base)["consecutive_failures"] == 0


@pytest.mark.parametrize("cohort", ["observed", "probe"])
@pytest.mark.parametrize("guard", ["pit_failure", "pit_corrupt", "current_active", "disk"])
def test_both_cohorts_yield_before_any_request(runtime, monkeypatch, cohort, guard):
    marker = runtime.base / "pit-repair/next-pass.json"
    if guard.startswith("pit"):
        marker.write_text("{" if guard == "pit_corrupt" else json.dumps({
            "outcome": "FAILED", "returncode": 1, "consecutive_failures": 2,
            "next_attempt_at": (datetime.now(UTC) + timedelta(hours=1)).isoformat()}))
    elif guard == "current_active":
        path = runtime.base / "unified-data/service-state.json"
        value = json.loads(path.read_text())
        path.write_text(json.dumps({**value, "state": "RUNNING"}))
    else:
        monkeypatch.setattr(runner.shutil, "disk_usage", lambda _: SimpleNamespace(
            free=runner.RESERVE - 1))
    download = Mock()
    result = (run_probe if cohort == "probe" else run_observed)(runtime, download)
    assert result["attempted_windows"] == result["stage_counts_this_pass"]["requested"] == 0
    download.assert_not_called()


@pytest.mark.parametrize("outcome", ["SUCCEEDED", "PARTIAL", "RUNNING"])
def test_normal_pit_schedule_does_not_starve_delisted_probe(runtime, outcome):
    marker = runtime.base / "pit-repair/next-pass.json"
    marker.write_text(json.dumps({"outcome": outcome, "returncode": 0,
                                  "receipt_valid": True, "consecutive_failures": 0,
                                  "next_attempt_at": (
                                      datetime.now(UTC) + timedelta(hours=6)).isoformat()}))
    assert run_probe(runtime)["attempted_windows"] == 1


@pytest.mark.parametrize("stage", ["sealed", "finished_capture", "incomplete", "committed_gate"])
def test_another_root_recovers_global_pending_before_issuing_requests(runtime, stage):
    stopped = Event()
    stopped.set()
    run_observed(runtime, stop=stopped)
    observed = HistoryQueue(runtime.observed.root, *runtime.inputs)
    queue = DelistedHistoryQueue(runtime.probe, runtime.bundle)
    store = HistoryStore(queue.root / "lake")
    gate = HistorySourceGate(runtime.base, observed, now=datetime.now(UTC))
    gate.bind(queue, store, now=datetime.now(UTC))
    attempt = queue.claim(now=datetime.now(UTC), revision=REVISION)
    store.begin(attempt)
    gate.begin(queue.root, attempt, now=datetime.now(UTC))
    if stage in {"sealed", "committed_gate"}:
        receipt = store.seal(attempt, delisted_raw(attempt, rows=False, requested=False,
                                                 complete=False), state="LOGIN_FAILED")
        if stage == "committed_gate":
            gate.record(receipt, now=datetime.now(UTC))
    elif stage == "finished_capture":
        store.path(attempt["attempt_id"], "capture.jsonl").write_text(json.dumps({
            "kind": "finished", "observed_at": datetime.now(UTC).isoformat(),
            "state": "LOGIN_FAILED", "body_complete": False,
            "vendor_error_code": None, "error": "VENDOR_LOGIN_FAILED"}) + "\n")
    download = Mock()
    result = run_observed(runtime, download)
    download.assert_not_called()
    assert result["attempted_windows"] == 0
    value = read_gate(runtime.base)
    assert value["pending"] is None and value["consecutive_failures"] == 1
    deadline = value["next_attempt_at"]
    # The probe later replays its task commit without incrementing the source failure again.
    probe = run_probe(runtime, download)
    assert probe["recovery"]["replayed_sealed_receipts"] == 1
    assert read_gate(runtime.base)["consecutive_failures"] == 1
    assert read_gate(runtime.base)["next_attempt_at"] == deadline
    assert store.load(attempt["attempt_id"])["quality"]["qualified_rows"] == 0
    if stage == "committed_gate":
        assert value["last_recovery"] is None
    else:
        assert value["last_recovery"]["untrusted_source_outcome"] == (stage == "incomplete")


def test_crash_after_recovery_seals_untrusted_outcome_cannot_reset_backoff(runtime, monkeypatch):
    stopped = Event()
    stopped.set()
    run_observed(runtime, stop=stopped)
    observed = HistoryQueue(runtime.observed.root, *runtime.inputs)
    gate = HistorySourceGate(runtime.base, observed, now=datetime.now(UTC))
    queue = DelistedHistoryQueue(runtime.probe, runtime.bundle)
    store = HistoryStore(queue.root / "lake")
    attempt = queue.claim(now=datetime.now(UTC), revision=REVISION)
    store.begin(attempt)
    gate.begin(queue.root, attempt, now=datetime.now(UTC))
    with monkeypatch.context() as patch:
        patch.setattr(gate, "save", Mock(side_effect=OSError("simulated crash")))
        with pytest.raises(OSError, match="simulated"):
            gate.recover_pending(now=datetime.now(UTC))
    assert store.load(attempt["attempt_id"])["quality"]["state"] == "INTERRUPTED"
    assert run_observed(runtime)["attempted_windows"] == 0
    assert read_gate(runtime.base)["consecutive_failures"] == 1


def test_pre_upgrade_capture_failure_is_not_lost_when_probe_bootstraps_shared_gate(runtime):
    queue = runtime.observed
    attempt = queue.claim(now=datetime.now(UTC), revision=REVISION)
    store = HistoryStore(queue.root / "lake")
    store.begin(attempt)
    store.path(attempt["attempt_id"], "capture.jsonl").write_text(json.dumps({
        "kind": "finished", "observed_at": datetime.now(UTC).isoformat(),
        "state": "LOGIN_FAILED", "body_complete": False,
        "vendor_error_code": None, "error": "VENDOR_LOGIN_FAILED"}) + "\n")
    download = Mock()
    result = run_probe(runtime, download)
    assert result["attempted_windows"] == 0
    download.assert_not_called()
    assert read_gate(runtime.base)["consecutive_failures"] == 1
    assert store.load(attempt["attempt_id"])["quality"]["state"] == "LOGIN_FAILED"
    active = HistoryQueue(queue.root, *runtime.inputs).tasks[attempt["scope"]["task_id"]]
    assert active["active"] is None


def test_unknown_legacy_intent_stays_uncertain_after_recovery_seal_crash(runtime, monkeypatch):
    queue = runtime.observed
    store = HistoryStore(queue.root / "lake")
    attempt = queue.claim(now=datetime.now(UTC), revision=REVISION)
    store.begin(attempt)
    gate = HistorySourceGate(runtime.base, queue, now=datetime.now(UTC))
    gate.bind(queue, store, now=datetime.now(UTC))
    gate.begin(queue.root, attempt, now=datetime.now(UTC), legacy_untrusted=True)
    store.seal(attempt, raw_for(attempt["scope"]))
    with monkeypatch.context() as patch:
        patch.setattr(gate, "save", Mock(side_effect=OSError("simulated crash")))
        with pytest.raises(OSError, match="simulated"):
            gate.recover_pending(now=datetime.now(UTC))
    download = Mock()
    assert run_probe(runtime, download)["attempted_windows"] == 0
    download.assert_not_called()
    assert read_gate(runtime.base)["consecutive_failures"] == 1


def test_gate_disappearing_while_capture_runs_stops_child_and_reaps_it(runtime, monkeypatch):
    stop = Event()
    child = Mock(returncode=-15)
    child.poll.return_value = None
    def spawn(*args, **kwargs):
        gate_path(runtime.base).unlink()
        return child
    monkeypatch.setattr(runner.subprocess, "Popen", spawn)
    with pytest.raises(ValueError, match="STATE_CHANGED_OR_LOST"):
        run_probe(runtime, runner.download, stop=stop)
    child.terminate.assert_called_once()
    child.wait.assert_called_once_with(timeout=3)
    assert not gate_path(runtime.base).exists()


def test_delisted_stop_reaps_capture_and_preserves_no_source_failure(runtime, monkeypatch):
    stop = Event()
    child = Mock(returncode=-15)
    child.poll.return_value = None
    def spawn(*args, **kwargs):
        assert "--history-gate-required" in args[0]
        stop.set()
        return child
    monkeypatch.setattr(runner.subprocess, "Popen", spawn)
    result = run_probe(runtime, runner.download, stop=stop)
    assert result["state"] == "INTERRUPTED"
    child.terminate.assert_called_once()
    child.wait.assert_called_once_with(timeout=3)
    assert result["qualified_rows"] == 0
    value = read_gate(runtime.base)
    assert value["pending"] is None and value["consecutive_failures"] == 0


@pytest.mark.parametrize("damage", ["corrupt", "deleted", "relocated"])
def test_untrustworthy_shared_state_fails_closed_for_both_cohorts(runtime, damage):
    run_probe(runtime, Mock(side_effect=failed_download))
    if damage == "corrupt":
        gate_path(runtime.base).write_text("{")
    elif damage == "deleted":
        gate_path(runtime.base).unlink()
    else:
        value = read_gate(runtime.base)
        value["base_sha256"] = "0" * 64
        gate_module.write_json_atomic(gate_path(runtime.base), signed({
            k: v for k, v in value.items() if k != "evidence_sha256"}))
    for run in (run_observed, run_probe):
        download = Mock()
        with pytest.raises((OSError, ValueError)):
            run(runtime, download)
        download.assert_not_called()


def test_shared_history_backoff_is_checked_inside_vendor_lease_before_login(runtime, monkeypatch):
    run_probe(runtime, Mock(side_effect=failed_download))
    queue = HistoryQueue(runtime.observed.root, *runtime.inputs)
    queue.meta["global_next_attempt_at"] = None  # Direct test claim, not the runner.
    attempt = queue.claim(now=datetime.now(UTC), revision=REVISION)
    store = HistoryStore(queue.root / "lake")
    store.begin(attempt)
    monkeypatch.setattr(runner, "baostock_session", lambda **_: nullcontext())
    api = Mock()
    runner.capture_vendor(store, attempt, api, before_login=lambda: runner.priority_reason(
        runtime.base, datetime.now(UTC)))
    api.login.assert_not_called()
    api.query_history_k_data_plus.assert_not_called()
    assert runner.read_capture(store, attempt)["state"] == "YIELDED_TO_CURRENT_COLLECTION"


def test_service_alternates_one_bounded_pass_per_cycle_and_keeps_both_receipt_pointers(
    runtime, monkeypatch,
):
    monkeypatch.setenv("QTRADE_BAOSTOCK_FREE_DATA_ACK", "true")
    monkeypatch.setattr(service, "worker_lease", lambda *_: nullcontext())
    stop = Event()
    waits = []
    def wait(seconds):
        waits.append(seconds)
        if len(waits) == 2:
            stop.set()
        return False
    monkeypatch.setattr(stop, "wait", wait)
    calls = []
    def one_pass(base, root, plan, master, **kwargs):
        calls.append((root, plan, master, kwargs))
        assert kwargs["stop"] is stop and kwargs["seconds"] == 240
        return {"state": "PASS_FINISHED", "evidence_sha256": str(len(calls)) * 64}
    monkeypatch.setattr(service, "run_pass", one_pass)
    result = service.serve(runtime.base, runtime.observed.root, *runtime.inputs,
                           revision=REVISION, stop=stop, probe_bundle=runtime.bundle,
                           probe_root=runtime.probe)
    assert len(calls) == 2 and all(290 < seconds <= 300 for seconds in waits)
    assert calls[0][0:3] == (runtime.observed.root, *runtime.inputs)
    assert calls[1][0:3] == (runtime.probe, None, None)
    assert calls[1][3]["observed_root"] == runtime.observed.root
    assert result["last_observed_execution_sha256"] == "1" * 64
    assert result["last_delisted_execution_sha256"] == "2" * 64
    assert result["completed_passes"] == 2 and result["state"] == "STOPPED"


@pytest.mark.skipif(os.name == "nt", reason="actual shared flock requires Linux")
def test_writer_lease_excludes_different_cohort_roots(runtime, monkeypatch):
    lease_dir = runtime.base / "leases"
    monkeypatch.setattr(runner, "worker_lease", REAL_WORKER_LEASE)
    with REAL_WORKER_LEASE(lease_dir), pytest.raises(BlockingIOError):
        run_probe(runtime, Mock(), lease_dir=lease_dir)
