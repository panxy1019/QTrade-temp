import json
from contextlib import nullcontext
from copy import deepcopy
from datetime import date, timedelta

import pytest
from test_history_backfill import platform_writers as history_writers  # noqa: F401
from test_public_delisted_plan import NOW, REVISION, compile_bundle, write_inputs
from test_public_delisted_plan import inputs as official_fixture
from test_public_delisted_plan import platform_writers as official_writers  # noqa: F401

from qtrade.monitoring.data_freshness import signed
from scripts import audit_delisted_history as audit_module
from scripts import delisted_history_queue as queue_module
from scripts.delisted_history_queue import SECURITY_STATUS, SOURCE_KIND, DelistedHistoryQueue
from scripts.history_backfill_store import HistoryStore, fields, request_for

official_inputs = official_fixture


@pytest.fixture(autouse=True)
def local_writers(monkeypatch):
    import os

    if os.name == "nt":
        original = queue_module.write_json_immutable
        def write(path, value):
            try:
                original(path, value)
            except PermissionError:
                assert path.is_file() and json.loads(path.read_text(encoding="utf-8")) == value
        monkeypatch.setattr(queue_module, "write_json_immutable", write)
    # Linux flock is verified by the parent; the audit always uses the writer lease.
    monkeypatch.setattr(audit_module, "worker_lease", lambda _: nullcontext())


@pytest.fixture
def runtime(tmp_path, official_inputs):
    path = write_inputs(tmp_path, official_inputs)
    manifest = compile_bundle(tmp_path, path)
    bundle = tmp_path / "pit-repair/delisted-plans" / manifest["plan_sha256"]
    root = tmp_path / "delisted-execution"
    queue = DelistedHistoryQueue(root, bundle, now=NOW)
    return queue, HistoryStore(root / "lake"), bundle, tmp_path


def raw_for(attempt, *, rows=True, price="10", requested=True, complete=True):
    scope = attempt["scope"]
    day = date.fromisoformat(scope["start"])
    while day.weekday() >= 5:
        day += timedelta(days=1)
    return {
        "request": request_for(scope), "fields": list(fields("day")),
        "rows": [[str(day), request_for(scope)["code"], price, "11", "9", price,
                  "1000", str(float(price) * 1000), "3", "1"]] if rows else [],
        "body_complete": complete, "vendor_error_code": "0" if complete else None,
        "transport_error": None, "query_attempted": requested,
    }


def seal(queue, store, attempt, *, state=None, **kwargs):
    store.begin(attempt)
    receipt = store.seal(attempt, raw_for(attempt, **kwargs), state=state)
    queue.finish(store, receipt, now=NOW)
    return receipt


def audit(runtime, **kwargs):
    queue, _, bundle, _ = runtime
    return audit_module.audit_delisted(queue.root, bundle, revision=REVISION,
                                       now=NOW + timedelta(hours=5), **kwargs)


def test_claim_stays_inactive_and_has_no_current_universe_or_historical_availability(runtime):
    queue, store, bundle, _ = runtime
    before = {path: path.read_bytes() for path in bundle.rglob("*") if path.is_file()}
    attempt = queue.claim(now=NOW, revision=REVISION)
    assert attempt is not None
    scope = attempt["scope"]
    assert scope["source_kind"] == SOURCE_KIND
    assert scope["security_status"] == SECURITY_STATUS
    assert scope["historical_available_at"] is None
    assert scope["historical_pit_certified"] is False
    assert "universe_version" not in scope
    assert (date.fromisoformat(scope["end"]) - date.fromisoformat(scope["start"])).days < 31
    receipt = seal(queue, store, attempt)
    assert store.load(attempt["attempt_id"]) == receipt
    snapshot = queue.snapshot(now=NOW)
    assert snapshot["qualified_rows"] == 1
    assert snapshot["current_universe_coverage_delta"] == snapshot["published_qlib_versions"] == 0
    assert "universe_version" not in snapshot
    assert all(path.read_bytes() == payload for path, payload in before.items())


def test_yielded_retry_success_advances_the_same_frontier_without_duplicate_new_request(runtime):
    queue, store, _, _ = runtime
    first = queue.claim(now=NOW, revision=REVISION)
    task_id = first["scope"]["task_id"]
    start = first["scope"]["start"]
    seal(queue, store, first, state="YIELDED_TO_SHARED_VENDOR_SESSION", rows=False,
         requested=False, complete=False)
    assert queue.tasks[task_id]["before"] is None
    retry = queue.claim(now=NOW + timedelta(minutes=2), revision=REVISION, retry=True)
    assert retry["scope"]["start"] == start
    seal(queue, store, retry)
    assert queue.tasks[task_id]["before"] == start
    assert queue.frontier(queue.tasks[task_id])[1] < start


def test_source_failure_backoff_survives_restart_and_cannot_be_reset_by_new_scopes(runtime):
    queue, store, bundle, _ = runtime
    attempt = queue.claim(now=NOW, revision=REVISION)
    seal(queue, store, attempt, state="LOGIN_FAILED", rows=False, requested=False, complete=False)
    restarted = DelistedHistoryQueue(queue.root, bundle, now=NOW)
    assert restarted.meta["global_failures"] == 1
    assert restarted.claim(now=NOW + timedelta(minutes=20), revision=REVISION) is None
    assert restarted.claim(now=NOW + timedelta(minutes=20), revision=REVISION, retry=True) is None
    assert restarted.claim(now=NOW + timedelta(minutes=31), revision=REVISION) is not None


def test_sealed_but_uncommitted_result_is_not_coverage_until_recovery(runtime):
    queue, store, bundle, _ = runtime
    attempt = queue.claim(now=NOW, revision=REVISION)
    store.begin(attempt)
    receipt = store.seal(attempt, raw_for(attempt))
    before = audit(runtime)
    assert before["verified_accepted_rows"] == 0
    assert before["state"] == "VERIFIED_WITH_PENDING_RECOVERY"
    restarted = DelistedHistoryQueue(queue.root, bundle, now=NOW)
    result = restarted.recover(store, now=NOW)
    assert result["replayed_sealed_receipts"] == 1
    assert restarted.tasks[attempt["scope"]["task_id"]]["active"] is None
    assert store.load(attempt["attempt_id"]) == receipt
    after = audit((restarted, store, bundle, runtime[3]))
    assert after["state"] == "VERIFIED" and after["verified_accepted_rows"] == 1


def test_interrupted_capture_is_retained_and_never_becomes_price_coverage(runtime):
    queue, store, bundle, _ = runtime
    attempt = queue.claim(now=NOW, revision=REVISION)
    store.begin(attempt)
    restarted = DelistedHistoryQueue(queue.root, bundle, now=NOW)
    recovered = restarted.recover(store, now=NOW)
    assert recovered["interrupted_attempts"] == 1
    assert store.load(attempt["attempt_id"])["quality"]["state"] == "INTERRUPTED"
    report = audit((restarted, store, bundle, runtime[3]))
    assert report["verified_accepted_rows"] == 0
    assert report["gap_examples"][0]["state"] == "INTERRUPTED"


@pytest.mark.parametrize("change", [
    {"security_status": "ACTIVE_OBSERVED"}, {"source_kind": "CURRENT_UNIVERSE"},
    {"historical_available_at": "2020-01-01T00:00:00+00:00"},
    {"universe_version": "a" * 64}, {"source_bundle_sha256": "b" * 64},
])
def test_resigned_scope_cannot_enter_as_active_change_source_or_invent_availability(
    runtime, change,
):
    queue, store, _, _ = runtime
    attempt = queue.claim(now=NOW, revision=REVISION)
    forged = deepcopy(attempt)
    forged["scope"].update(change)
    with pytest.raises(ValueError, match="ATTEMPT_SCOPE"):
        queue.validate_attempt(forged)
    store.begin(attempt)
    receipt = store.seal(attempt, raw_for(attempt))
    assert queue.tasks[attempt["scope"]["task_id"]]["active"] == attempt
    queue.finish(store, receipt, now=NOW)


def test_read_only_planned_audit_counts_zero_and_never_creates_execution_checkpoints(runtime):
    queue, _, bundle, base = runtime
    missing_root = base / "not-started"
    report = audit_module.audit_delisted(missing_root, bundle, revision=REVISION,
                                         now=NOW + timedelta(hours=5))
    assert report["planned_scopes_metadata_only"] == 2
    assert report["verified_accepted_rows"] == report["verified_receipt_attempts"] == 0
    assert report["state"] == "VERIFIED" and not missing_root.exists()
    reader = DelistedHistoryQueue(queue.root, bundle, now=NOW, read_only=True)
    with pytest.raises(ValueError, match="READ_ONLY"):
        reader.claim(now=NOW, revision=REVISION)


def test_audit_rejects_inflated_checkpoint_coverage_and_leaves_every_input_unchanged(runtime):
    queue, store, _, _ = runtime
    attempt = queue.claim(now=NOW, revision=REVISION)
    seal(queue, store, attempt)
    task_id = attempt["scope"]["task_id"]
    path = queue.task_path(task_id)
    task = json.loads(path.read_bytes())
    task["windows"][attempt["scope"]["start"]]["qualified_rows"] = 12345
    path.write_text(json.dumps(signed({k: v for k, v in task.items() if k != "evidence_sha256"})))
    original = {p: p.read_bytes() for p in queue.root.rglob("*") if p.is_file()}
    report = audit(runtime)
    assert report["state"] == "INVALID_EVIDENCE"
    assert report["verified_accepted_rows"] == 0
    assert report["errors"][0]["error_code"] == "DELISTED_AUDIT_ACCEPTED_COVERAGE_MISMATCH"
    assert all(p.read_bytes() == raw for p, raw in original.items())


def test_corrupt_accepted_raw_is_rejected_and_maximum_receipts_is_an_explicit_lower_bound(runtime):
    queue, store, _, _ = runtime
    first = queue.claim(now=NOW, revision=REVISION)
    receipt = seal(queue, store, first)
    second = queue.claim(now=NOW, revision=REVISION)
    seal(queue, store, second)
    limited = audit(runtime, maximum_receipts=1)
    assert limited["state"] == "PARTIAL_AUDIT_LIMIT" and limited["limit_reached"] is True
    assert limited["verified_accepted_rows"] == 1
    path = store.blobs._blob_path(receipt["raw"]["sha256"], ".jsonl.gz")
    path.write_bytes(b"corrupt original source bytes")
    report = audit(runtime)
    assert report["state"] == "INVALID_EVIDENCE"
    assert report["verified_accepted_rows"] == 1


def test_wrong_frozen_bundle_cannot_reuse_an_existing_execution_root(runtime):
    queue, _, bundle, _ = runtime
    with pytest.raises(ValueError, match="FROZEN_BUNDLE"):
        DelistedHistoryQueue(bundle / "queue", bundle, now=NOW)
    with pytest.raises(ValueError, match="FROZEN_BUNDLE"):
        DelistedHistoryQueue(bundle.parent, bundle, now=NOW)
    saved = (queue.root / "source-bundle.json").read_bytes()
    value = json.loads(saved)
    value["source_bundle_sha256"] = "a" * 64
    (queue.root / "source-bundle.json").write_text(json.dumps(signed({
        k: v for k, v in value.items() if k != "evidence_sha256"
    })))
    with pytest.raises(ValueError, match="FROZEN_INPUT_CONFLICT"):
        DelistedHistoryQueue(queue.root, bundle, now=NOW)


def test_existing_execution_cannot_reconstruct_a_missing_frozen_input_silently(runtime):
    queue, _, bundle, _ = runtime
    queue.claim(now=NOW, revision=REVISION)
    (queue.root / "source-bundle.json").unlink()
    with pytest.raises(ValueError, match="FROZEN_INPUT_MISSING"):
        DelistedHistoryQueue(queue.root, bundle, now=NOW)
    with pytest.raises(ValueError, match="FROZEN_INPUT_MISSING"):
        audit(runtime)
    assert not (queue.root / "source-bundle.json").exists()


def test_empty_window_keeps_unknown_reason_and_adds_zero_price_coverage(runtime):
    queue, store, _, _ = runtime
    attempt = queue.claim(now=NOW, revision=REVISION)
    receipt = seal(queue, store, attempt, rows=False)
    assert receipt["quality"]["state"] == "EMPTY_WINDOW_UNEXPLAINED"
    report = audit(runtime)
    assert report["verified_accepted_rows"] == 0
    assert report["gap_examples"][0]["state"] == "EMPTY_WINDOW_UNEXPLAINED"
    assert report["historical_available_at"] is None
    assert report["historical_pit_certified"] is False


def test_changed_overlap_preserves_original_accepted_bytes_and_does_not_double_count(runtime):
    queue, store, bundle, _ = runtime
    first = queue.claim(now=NOW, revision=REVISION)
    original = seal(queue, store, first)
    task_id, start = first["scope"]["task_id"], first["scope"]["start"]
    task = queue.tasks[task_id]
    # Schedule an explicit due re-observation without changing its original source bytes.
    task["windows"][start]["next_attempt_at"] = NOW.isoformat()
    queue.save_task(task)
    second = queue.claim(now=NOW, revision=REVISION, retry=True)
    seal(queue, store, second, price="10.5")
    saved = queue.tasks[task_id]["windows"][start]
    assert saved["accepted_attempt_id"] == first["attempt_id"]
    assert saved["source_conflict"] == "QUALIFIED_OVERLAP_CHANGED_QUARANTINED"
    assert store.load(first["attempt_id"]) == original
    report = audit(runtime)
    assert report["verified_accepted_rows"] == report["verified_instrument_count"] == 1
    assert report["downloaded_rows_including_reobservations"] == 2
    assert report["state"] == "VERIFIED"
    restarted = DelistedHistoryQueue(queue.root, bundle, now=NOW)
    assert restarted.snapshot(now=NOW)["qualified_rows"] == 1
