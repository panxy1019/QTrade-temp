from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from qtrade.monitoring.data_freshness import signed
from qtrade.research.workflow import digest
from scripts import collect_pit_evidence as collector
from scripts import run_pit_repair as scheduler


def test_backfilled_vendor_state_never_invents_historical_availability(tmp_path, monkeypatch):
    import json

    base = tmp_path
    source = base / "qlib-data-20261023"
    source.mkdir()
    (source / "dataset.json").write_text(json.dumps({
        "stocks": [{"symbol": "000001"}], "end": "2026-10-23", "accepted_symbols": 1,
    }))

    class Result:
        error_code = "0"

        def __init__(self, rows):
            self.fields = list(rows[0])
            self.values = iter(rows)

        def next(self):
            self.row = next(self.values, None)
            return self.row is not None

        def get_row_data(self):
            return list(self.row.values())

    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    master_returned = []
    def fetch_master():
        master_returned.append(datetime.now(UTC))
        return Result([{"code": "sz.000001", "status": "0"}])

    fake = SimpleNamespace(
        login=lambda: SimpleNamespace(error_code="0"), logout=lambda: None,
        query_stock_basic=fetch_master,
        query_history_k_data_plus=lambda *a, **kw: Result([{
            "code": "sz.000001", "date": "2020-01-02", "tradestatus": "0", "isST": "1",
        }]),
    )
    monkeypatch.setitem(__import__("sys").modules, "baostock", fake)
    monkeypatch.setattr(collector, "baostock_session", nullcontext)
    monkeypatch.setattr(collector, "write_json_atomic", write)
    result = collector.collect(base, 1)
    assert result["historical_available_at_known"] is False
    assert result["historical_pit_certified"] is False
    assert result["execution_enforcement"] == "NOT_USED_DAILY_PROXY_REMAINS"
    assert result["status_rows"] == 1
    assert result["master_inactive_rows"] == 1
    assert datetime.fromisoformat(result["master_observed_at"]) >= master_returned[0]
    assert datetime.fromisoformat(result["observed_at"]) >= datetime.fromisoformat(
        result["master_observed_at"]
    )
    status = json.loads((base / "pit-repair/status/000001.json").read_text())
    assert status["historical_available_at"] is None
    assert status["certified_at_decision"] is False


@pytest.fixture
def cached_source(tmp_path, monkeypatch):
    import json
    import sys

    source = tmp_path / "qlib-data-20261023"
    source.mkdir()
    (source / "dataset.json").write_text(json.dumps({
        "stocks": [{"symbol": "000001"}], "end": "2026-10-23", "accepted_symbols": 1,
    }))

    def write(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))

    root = tmp_path / "pit-repair"
    observed = datetime.now(UTC).isoformat()

    def cache(at=observed):
        master = {"source": "BaoStock query_stock_basic", "observed_at": at,
                  "historical_available_at": None, "rows": [{"code": "sz.000001"}]}
        identity = digest(master)
        write(root / "master" / (identity + ".json"), master)
        write(root / "coverage.json", signed({"master_sha256": identity}))
        return master, identity

    class Result:
        error_code = "0"
        fields = ["code", "date", "tradestatus", "isST"]

        def __init__(self):
            self.values = iter([["sz.000001", "2020-01-02", "1", "0"]])

        def next(self):
            self.row = next(self.values, None)
            return self.row is not None

        def get_row_data(self):
            return self.row

    calls = []

    def failed_master():
        calls.append("master")
        return SimpleNamespace(error_code="10002007")

    fake = SimpleNamespace(
        login=lambda: SimpleNamespace(error_code="0"), logout=lambda: None,
        query_stock_basic=failed_master, query_history_k_data_plus=lambda *a, **kw: Result(),
    )
    monkeypatch.setitem(sys.modules, "baostock", fake)
    monkeypatch.setattr(collector, "baostock_session", nullcontext)
    monkeypatch.setattr(collector, "write_json_atomic", write)
    return tmp_path, root, cache, calls


def test_recent_verified_master_resumes_status_without_repeating_failed_master(cached_source):
    import json

    base, root, cache, calls = cached_source
    master, identity = cache()
    result = collector.collect(base, 1)
    assert calls == []
    assert result["master_sha256"] == identity
    assert result["master_observed_at"] == master["observed_at"]
    assert result["master_fetch_outcome"] == "REUSED_VERIFIED_SNAPSHOT"
    assert result["status_symbols"] == 1
    assert json.loads((root / "attempt.json").read_text())["state"] == "COMPLETE"
    assert result["historical_pit_certified"] is False


def test_failed_refresh_preserves_old_master_clock_and_records_cache_fallback(cached_source):
    base, _, cache, calls = cached_source
    master, identity = cache((datetime.now(UTC) - timedelta(days=2)).isoformat())
    result = collector.collect(base, 1)
    assert calls == ["master"]
    assert result["master_sha256"] == identity
    assert result["master_observed_at"] == master["observed_at"]
    assert result["master_fetch_outcome"] == "REFRESH_FAILED_USING_VERIFIED_CACHE"
    assert result["master_refresh_error"] == {
        "type": "VendorQueryError", "vendor_code": "10002007",
    }
    assert result["historical_pit_certified"] is False


def test_first_master_failure_writes_attempt_without_inventing_coverage(cached_source):
    import json

    base, root, _, _ = cached_source
    with pytest.raises(collector.VendorQueryError):
        collector.collect(base, 1)
    assert not (root / "coverage.json").exists()
    attempt = json.loads((root / "attempt.json").read_text())
    assert attempt["state"] == "FAILED"
    assert attempt["vendor_code"] == "10002007"
    assert attempt["historical_pit_certified"] is False


@pytest.mark.parametrize("tamper", ["coverage", "master"])
def test_corrupt_cached_evidence_cannot_replace_accepted_coverage(cached_source, tamper):
    import json

    base, root, cache, _ = cached_source
    _, identity = cache()
    if tamper == "coverage":
        value = json.loads((root / "coverage.json").read_text())
        value["master_sha256"] = "0" * 64
        (root / "coverage.json").write_text(json.dumps(value))
    elif tamper == "master":
        path = root / "master" / (identity + ".json")
        path.write_text("{}")
    accepted = (root / "coverage.json").read_bytes()
    with pytest.raises(ValueError, match="CACHED_"):
        collector.collect(base, 1)
    assert (root / "coverage.json").read_bytes() == accepted
    assert json.loads((root / "attempt.json").read_text())["state"] == "FAILED"


def test_failed_pass_retries_same_scope_after_backoff_and_success_advances():
    now = datetime.now(UTC)
    failed = scheduler.finish_pass(40, 1, now, 0)
    assert scheduler.plan_pass(failed, now) == (1800, 40)
    assert scheduler.plan_pass(failed, now + timedelta(minutes=31)) == (0, 40)
    success = scheduler.finish_pass(40, 0, now, 2)
    assert success["consecutive_failures"] == 0
    assert scheduler.plan_pass(success, now) == (21600, 60)
    assert scheduler.plan_pass({"maximum": 40, "at": now.isoformat()}, now) == (1800, 40)
    assert scheduler.plan_pass({}, now) == (0, 20)
    assert scheduler.plan_pass(scheduler.finish_pass(990, 0, now, 0), now)[1] == 990
    assert scheduler.plan_pass(scheduler.finish_pass(40, 1, now, 10), now) == (21600, 40)


def test_partial_source_pass_retries_same_scope_despite_zero_process_exit():
    now = datetime.now(UTC)
    partial = scheduler.finish_pass(40, 0, now, 0, partial=True)
    assert partial["outcome"] == "PARTIAL"
    assert partial["returncode"] == 0
    assert scheduler.plan_pass(partial, now) == (1800, 40)
    assert scheduler.plan_pass(partial, now + timedelta(minutes=31)) == (0, 40)


def test_worker_cleanup_reaps_its_running_collector():
    import subprocess
    import sys

    child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"])
    try:
        scheduler.stop_child(child)
        assert child.poll() is not None
        scheduler.stop_child(child)
    finally:
        scheduler.stop_child(child)


def test_batched_collector_moves_past_a_persistent_vendor_failure(cached_source, monkeypatch):
    import json
    import sys

    base, root, cache, _ = cached_source
    cache()
    dataset_path = base / "qlib-data-20261023/dataset.json"
    dataset_path.write_text(json.dumps({
        "stocks": [{"symbol": f"{i:06d}"} for i in range(1, 4)],
        "end": "2026-10-23", "accepted_symbols": 3,
    }))
    queried = []

    class Result:
        error_code = "0"
        fields = ["code", "date", "tradestatus", "isST"]

        def __init__(self, code):
            self.values = iter([[code, "2020-01-02", "1", "0"]])

        def next(self):
            self.row = next(self.values, None)
            return self.row is not None

        def get_row_data(self):
            return self.row

    def fetch(code, *args, **kwargs):
        queried.append(code)
        return SimpleNamespace(error_code="10002007") if code == "sz.000001" else Result(code)

    monkeypatch.setattr(sys.modules["baostock"], "query_history_k_data_plus", fetch)
    from scripts import pit_collection_queue
    monkeypatch.setattr(pit_collection_queue, "write_json_atomic", collector.write_json_atomic)
    first = collector.collect(base, 3, batch_size=1)
    assert first["status_symbols"] == 0
    assert first["collection_progress"]["unattempted_symbols"] == 2
    second = collector.collect(base, 3, batch_size=1)
    third = collector.collect(base, 3, batch_size=1)
    assert queried == ["sz.000001", "sz.000002", "sz.000003"]
    assert second["status_symbols"] == 1 and third["status_symbols"] == 2
    assert third["collection_progress"]["waiting_retry_symbols"] == 1
    assert third["collection_progress"]["unattempted_symbols"] == 0
    assert third["collection_progress"]["unresolved_symbols"] == 1
    assert [f["symbol"] for f in third["failures"]] == ["000001"]
    assert third["historical_pit_certified"] is False
    assert json.loads((root / "attempt.json").read_text())["state"] == "PARTIAL"
    fourth = collector.collect(base, 3, batch_size=1)
    assert queried == ["sz.000001", "sz.000002", "sz.000003"]
    assert fourth["status_symbols"] == 2


def test_invalid_success_checkpoint_waits_for_retry_while_new_symbols_continue(
    cached_source, monkeypatch,
):
    import base64
    import json
    import sys

    from scripts import pit_collection_queue as queue

    base, root, cache, _ = cached_source
    cache()
    dataset_path = base / "qlib-data-20261023/dataset.json"
    dataset_path.write_text(json.dumps({"stocks": [{"symbol": "000001"}, {"symbol": "000002"}],
                                        "end": "2026-10-23", "accepted_symbols": 2}))
    dataset = json.loads(dataset_path.read_bytes())
    checkpoint = root / "status/000001.json"
    checkpoint.parent.mkdir()
    original = b'{"invalid-original":true}\n'
    checkpoint.write_bytes(original)
    monkeypatch.setattr(collector, "write_json_immutable", collector.write_json_atomic)
    monkeypatch.setattr(queue, "write_json_atomic", collector.write_json_atomic)
    attempts = {}
    queue.begin_attempt(attempts, "000001", datetime.now(UTC))
    queue.end_attempt(attempts, "000001", None)
    queue.save_attempts(root, dataset, attempts)
    queried = []

    class Result:
        fields = ["date", "code", "tradestatus", "isST"]
        error_code = "0"

        def __init__(self, code):
            self.values = iter([["2020-01-02", code, "1", "0"]])

        def next(self):
            self.row = next(self.values, None)
            return self.row is not None

        def get_row_data(self):
            return self.row

    def fetch(code, *args, **kwargs):
        queried.append(code)
        return Result(code)
    monkeypatch.setattr(sys.modules["baostock"], "query_history_k_data_plus", fetch)
    first = collector.collect(base, 2, batch_size=1)
    assert queried == ["sz.000002"]
    assert first["status_symbols"] == 1
    assert first["collection_progress"]["waiting_retry_symbols"] == 1
    assert first["quarantined_checkpoints"][0]["symbol"] == "000001"
    assert checkpoint.read_bytes() == original
    archived = json.loads(next((root / "quarantine/000001").glob("*.json")).read_bytes())
    assert base64.b64decode(archived["raw_bytes_base64"]) == original
    attempts = queue.read_attempts(root, dataset, datetime.now(UTC))
    attempts["000001"]["attempted_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    attempts["000001"]["next_attempt_at"] = (datetime.now(UTC) - timedelta(minutes=30)).isoformat()
    queue.save_attempts(root, dataset, attempts)
    second = collector.collect(base, 2, batch_size=1)
    assert queried == ["sz.000002", "sz.000001"]
    assert second["status_symbols"] == 2
    assert second["quarantined_checkpoints"] == []
    assert json.loads(checkpoint.read_bytes())["symbol"] == "000001"
    assert base64.b64decode(archived["raw_bytes_base64"]) == original


def test_rejected_source_content_is_preserved_and_does_not_become_a_successful_checkpoint(
    cached_source, monkeypatch,
):
    import json
    import sys

    from scripts import pit_collection_queue as queue

    base, root, cache, _ = cached_source
    cache()
    monkeypatch.setattr(collector, "write_json_immutable", collector.write_json_atomic)
    monkeypatch.setattr(queue, "write_json_atomic", collector.write_json_atomic)

    class Result:
        fields = ["date", "code", "tradestatus", "isST", "amount"]
        error_code = "0"

        def __init__(self):
            self.values = iter([["2020-01-02", "sz.000001", "1", "0", "NaN"]])

        def next(self):
            self.row = next(self.values, None)
            return self.row is not None

        def get_row_data(self):
            return self.row
    monkeypatch.setattr(sys.modules["baostock"], "query_history_k_data_plus",
                        lambda *a, **kw: Result())
    result = collector.collect(base, 1, batch_size=1)
    assert result["status_symbols"] == 0
    assert result["collection_progress"]["waiting_retry_symbols"] == 1
    assert not (root / "status/000001.json").exists()
    rejected = json.loads(next((root / "rejected-responses/000001").glob("*.json")).read_bytes())
    assert rejected["rows"][0]["amount"] == "NaN"
    assert result["historical_pit_certified"] is False
