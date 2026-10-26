import json
from datetime import UTC, datetime, timedelta

import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts import pit_collection_queue as queue
from scripts import run_pit_repair as scheduler

NOW = datetime(2026, 10, 26, 5, tzinfo=UTC)


def test_failed_prefix_does_not_starve_unattempted_symbols_or_due_retries():
    symbols = [f"{i:06d}" for i in range(1, 100)]
    attempts = {}
    for s in symbols[:8]:
        queue.begin_attempt(attempts, s, NOW)
        queue.end_attempt(attempts, s, ValueError("DO_NOT_STORE_MESSAGE"))
    selected = queue.select_batch(symbols, set(), attempts, NOW, 20)
    assert selected == symbols[8:28]
    selected = queue.select_batch(symbols, set(), attempts, NOW + timedelta(minutes=31), 20)
    assert selected == symbols[8:28] + symbols[:5]
    assert not any("DO_NOT_STORE" in str(v) for v in attempts.values())


def test_crashed_attempt_retains_retry_deadline_without_claiming_source_success():
    attempts = {}
    queue.begin_attempt(attempts, "000001", NOW)
    assert attempts["000001"]["state"] == "RUNNING"
    assert queue.select_batch(["000001", "000002"], set(), attempts, NOW, 20) == ["000002"]
    progress = queue.queue_progress(["000001", "000002"], set(), attempts, NOW)
    assert progress["completed_symbols"] == 0
    assert progress["waiting_retry_symbols"] == 1
    assert progress["unattempted_symbols"] == 1
    assert progress["historical_pit_certified"] is False


def test_retry_backoff_is_per_symbol_bounded_and_cannot_invent_success():
    attempts = {}
    delays = []
    for _ in range(9):
        queue.begin_attempt(attempts, "000001", NOW)
        queue.end_attempt(attempts, "000001", ValueError("vendor error"))
        delays.append((datetime.fromisoformat(attempts["000001"]["next_attempt_at"])
                       - NOW).total_seconds())
    assert delays == [1800, 3600, 7200, 14400, 21600, 21600, 21600, 21600, 21600]
    queue.end_attempt(attempts, "000001", None)
    with pytest.raises(ValueError, match="CHECKPOINT_MISSING"):
        queue.select_batch(["000001"], set(), attempts, NOW, 20)
    assert queue.select_batch(["000001"], {"000001"}, attempts, NOW, 20) == []


def test_retry_ledger_rejects_tamper_changed_dataset_future_and_naive_clocks(tmp_path, monkeypatch):
    def write(path, value):
        path.write_text(json.dumps(value))
    monkeypatch.setattr(queue, "write_json_atomic", write)
    dataset = {"stocks": [{"symbol": "000001"}], "end": "2026-10-23"}
    attempts = {}
    queue.begin_attempt(attempts, "000001", NOW)
    queue.save_attempts(tmp_path, dataset, attempts)
    path = tmp_path / "symbol-attempts.json"
    original = path.read_text()
    assert queue.read_attempts(tmp_path, dataset, NOW) == attempts
    with pytest.raises(ValueError, match="IDENTITY"):
        queue.read_attempts(tmp_path, {**dataset, "end": "2026-10-24"}, NOW)
    v = json.loads(original)
    v["entries"]["000001"]["state"] = "SUCCEEDED"
    path.write_text(json.dumps(v))
    with pytest.raises(ValueError, match="IDENTITY"):
        queue.read_attempts(tmp_path, dataset, NOW)
    for clock in [(NOW + timedelta(minutes=1)).isoformat(), "2026-10-26T05:00:00"]:
        v = json.loads(original)
        v.pop("evidence_sha256")
        v["entries"]["000001"]["attempted_at"] = clock
        path.write_text(json.dumps(signed(v)))
        with pytest.raises(ValueError, match="CLOCK"):
            queue.read_attempts(tmp_path, dataset, NOW)


def receipt(progress, *, state="PARTIAL", updated=NOW):
    return signed({**AUTHORITY, "schema_version": "qtrade.pit-source-attempt.v1",
                   "requested_maximum": 990, "historical_pit_certified": False,
                   "updated_at": updated.isoformat(), "state": state,
                   "collection_progress": progress})


def test_partial_batch_keeps_scanning_without_relabelling_missing_evidence_successful():
    progress = queue.queue_progress([f"{i:06d}" for i in range(990)], set(), {}, NOW)
    result = scheduler.finish_batch(990, 0, NOW, 3, receipt(progress), NOW)
    assert result["outcome"] == "PARTIAL"
    assert result["receipt_valid"] is True
    assert scheduler.plan_pass(result, NOW) == (60, 990)


def test_only_failed_symbols_remaining_waits_for_due_retry_and_never_certifies():
    symbols = [f"{i:06d}" for i in range(990)]
    attempts = {}
    queue.begin_attempt(attempts, symbols[0], NOW)
    queue.end_attempt(attempts, symbols[0], ValueError("error"))
    progress = queue.queue_progress(symbols, set(symbols[1:]), attempts, NOW)
    result = scheduler.finish_batch(990, 0, NOW, 0, receipt(progress), NOW)
    assert result["outcome"] == "PARTIAL"
    assert scheduler.plan_pass(result, NOW) == (1800, 990)
    assert result["collection_progress"]["historical_pit_certified"] is False


@pytest.mark.parametrize("kind", ["stale", "unsigned", "false_complete", "exit_failure"])
def test_bad_or_failed_current_receipt_cannot_skip_global_backoff(kind):
    progress = queue.queue_progress([f"{i:06d}" for i in range(990)], set(), {}, NOW)
    at = NOW - timedelta(seconds=1) if kind == "stale" else NOW
    r = receipt(progress, state="COMPLETE" if kind == "false_complete" else "PARTIAL", updated=at)
    if kind == "unsigned":
        r.pop("evidence_sha256")
    result = scheduler.finish_batch(990, 1 if kind == "exit_failure" else 0, NOW, 0, r, NOW)
    assert result["outcome"] == "FAILED"
    assert scheduler.plan_pass(result, NOW) == (1800, 990)


def test_complete_coverage_retains_six_hour_schedule_without_expanding_beyond_universe():
    symbols = [f"{i:06d}" for i in range(990)]
    progress = queue.queue_progress(symbols, set(symbols), {}, NOW)
    result = scheduler.finish_batch(990, 0, NOW, 3, receipt(progress, state="COMPLETE"), NOW)
    assert result["outcome"] == "SUCCEEDED"
    assert scheduler.plan_pass(result, NOW) == (21600, 990)


def test_migration_preserves_old_partial_schedule_as_an_immutable_record(tmp_path, monkeypatch):
    source = tmp_path / "qlib-data-20261023"
    source.mkdir()
    (source / "dataset.json").write_text(json.dumps({
        "stocks": [{"symbol": "000001"}, {"symbol": "000002"}], "accepted_symbols": 2,
    }))
    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    monkeypatch.setattr(scheduler, "write_json_immutable", write)
    old = {"maximum": 40, "outcome": "PARTIAL", "next_attempt_at": NOW.isoformat()}
    new = scheduler.migrate_schedule(tmp_path, old, NOW)
    assert new["maximum"] == 2
    assert new["mode"] == scheduler.QUEUE_MODE
    saved = next((tmp_path / "pit-repair").glob("legacy-next-pass-*.json"))
    assert json.loads(saved.read_text()) == old
