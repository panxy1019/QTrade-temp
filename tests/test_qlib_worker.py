import json
import sys
from pathlib import Path

import pytest

from qtrade.research.workflow import default_spec, digest
from qtrade.research.workflow_queue import seal
from scripts import run_qlib_worker as worker_module


@pytest.fixture
def worker(tmp_path, monkeypatch):
    monkeypatch.setenv("QTRADE_RESEARCH_QUEUE_SECRET", "test-only-queue-signing-key" * 2)
    queue = type("Queue", (), {"read": lambda self: {}})()
    monkeypatch.setattr(worker_module.GitQueue, "from_environment", lambda: queue)
    value = worker_module.Worker(tmp_path, tmp_path / "catalog.sqlite", sys.executable, "a" * 40)
    yield value, queue
    value.db.close()


def request(value, identity):
    spec = default_spec()
    return seal(
        {
            "job_id": identity,
            "kind": "QLIB_RESEARCH_JOB",
            "actor": "test-only",
            "spec": spec,
            "spec_sha256": digest(spec),
            "live_trading_enabled": False,
        },
        value.secret,
    )


def test_worker_deduplicates_and_records_history_and_capacity(worker):
    value, queue = worker
    items = {"requests/" + str(n) * 32: request(value, str(n) * 32) for n in range(1, 5)}
    queue.read = lambda: items
    value.ingest()
    value.ingest()
    assert value.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 4
    assert value.db.execute("SELECT COUNT(*) FROM jobs WHERE state='REJECTED'").fetchone()[0] == 1
    value.update("1" * 32, "COMPUTING_ALPHA158")
    assert value.db.execute(
        "SELECT state FROM events WHERE id=? ORDER BY rowid", ("1" * 32,)
    ).fetchall() == [("QUEUED",), ("COMPUTING_ALPHA158",)]


def test_worker_restart_never_replays_an_uncertain_running_job(worker):
    value, queue = worker
    identity = "a" * 32
    queue.read = lambda: {"requests/" + identity: request(value, identity)}
    value.ingest()
    value.update(identity, "TRAINING_MODEL")
    resumed = worker_module.Worker(
        value.base, value.base / "catalog.sqlite", sys.executable, "a" * 40
    )
    try:
        assert resumed.db.execute("SELECT state FROM jobs").fetchone()[0] == "INTERRUPTED"
        resumed.ingest()
        assert resumed.db.execute("SELECT state FROM jobs").fetchone()[0] == "INTERRUPTED"
    finally:
        resumed.db.close()


def test_worker_rejects_tampered_requests_and_honors_queued_cancel(worker):
    value, queue = worker
    identity = "b" * 32
    envelope = request(value, identity)
    envelope["spec"]["model"] = "arbitrary.command"
    queue.read = lambda: {"requests/" + identity: envelope}
    value.ingest()
    assert value.db.execute("SELECT COUNT(*) FROM jobs").fetchone()[0] == 0
    queue.read = lambda: {
        "requests/" + identity: request(value, identity),
        "cancellations/" + identity: seal(
            {"job_id": identity, "kind": "CANCEL_RESEARCH_JOB"}, value.secret
        ),
    }
    value.ingest()
    value.execute(identity, {})
    assert value.db.execute("SELECT state FROM jobs").fetchone()[0] == "CANCELLED"


@pytest.mark.parametrize("policy,protocol", [
    ("RECONCILED_BACKFILL_ADDITIONAL_BLOCKS_V1",
     "ALIGNED_SUPPORTED_UNIVERSE_SOURCE_BOUND_CONSTRAINTS_V2"),
    ("RECONCILED_BACKFILL_ADDITIONAL_BLOCKS_V2",
     "ALIGNED_SUPPORTED_UNIVERSE_SOURCE_BOUND_CONSTRAINTS_V3"),
])
def test_archive_publication_keeps_the_original_constraint_protocol(worker, monkeypatch,
                                                                  policy, protocol):
    from copy import deepcopy

    from qtrade.monitoring.data_freshness import signed

    value, _ = worker
    monkeypatch.setattr(worker_module, "write_json_atomic", lambda path, detail: None)
    summary = signed({
        "workflow_spec": default_spec(), "run_id": "old-frozen-reference",
        "source_revision": "b" * 40, "source_snapshot_sha256": "a" * 64,
        "execution_constraints": {"policy": policy}, "model": "LinearModel",
        "segments": {}, "data_end": "2026-10-23", "dataset_sha256": "c" * 64,
        "symbols": 990, "data_rows": 1000, "feature_count": 158, "metrics": {},
        "curve": [], "signal_evaluation": {}, "portfolio_evaluation": {},
        "cost_assumptions": {}, "portfolio": {}, "limitations": [],
        "completed_at": "2026-10-26T00:00:00+00:00",
    })
    before = deepcopy(summary)
    result = value.archive_detail(summary, "reference")
    assert result["comparison_protocol"] == protocol
    assert summary == before


def test_recovery_does_not_signal_an_unrelated_reused_pid(tmp_path):
    (tmp_path / "child.json").write_text(json.dumps({"pid": 1, "start_ticks": "impossible"}))
    if not Path("/proc/1").exists():
        pytest.skip("Linux process identity check")
    with pytest.raises(ValueError, match="UNCERTAIN_CHILD_IDENTITY"):
        worker_module.recover_child(tmp_path)


def test_model_control_uses_only_verified_request_channel_and_never_creates_training_job(worker):
    value, queue = worker
    calls = []
    value.online.execute = lambda command, **kw: calls.append(command)
    identity = 'c'*32
    payload = {'job_id': identity, 'kind': 'QLIB_MODEL_CONTROL',
               'live_trading_enabled': False, 'command': {'request_id': identity}}
    good = seal(payload, value.secret)
    altered = {**good, 'live_trading_enabled': True}
    queue.read = lambda: {'requests/'+identity: altered}
    value.ingest()
    queue.read = lambda: {'cancellations/'+identity: good}
    value.ingest()
    queue.read = lambda: {'requests/'+identity: seal({**payload, 'command': {
        'request_id': 'd'*32}}, value.secret)}
    value.ingest()
    assert calls == []
    queue.read = lambda: {'requests/'+identity: good}
    value.ingest()
    assert calls == [payload['command']]
    assert value.db.execute('SELECT COUNT(*) FROM jobs').fetchone()[0] == 0
