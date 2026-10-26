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


def test_recovery_does_not_signal_an_unrelated_reused_pid(tmp_path):
    (tmp_path / "child.json").write_text(json.dumps({"pid": 1, "start_ticks": "impossible"}))
    if not Path("/proc/1").exists():
        pytest.skip("Linux process identity check")
    with pytest.raises(ValueError, match="UNCERTAIN_CHILD_IDENTITY"):
        worker_module.recover_child(tmp_path)
