"""Independent synthetic archive, READY, protected queue and owned child acceptance."""
from __future__ import annotations

import gzip
import json
import os
import shutil
import sqlite3
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from pathlib import Path
from types import SimpleNamespace

import pytest
from test_factor_strategy_contract import setup as strategy_data  # noqa: F401
from test_step2_factor_contract_independent import contract as factor_data  # noqa: F401

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.factor_strategy_catalog import (
    KIND,
    REQUEST_SCHEMA,
    catalog_identity,
    preview_from_catalog,
    validate_catalog,
)
from qtrade.research.workflow import digest
from qtrade.research.workflow_queue import seal
from scripts import factor_research_contracts as local
from scripts import run_factor_strategy_registration as runner_module
from scripts import run_qlib_worker as worker_module
from scripts.run_factor_strategy_registration import run


@pytest.fixture
def archive(strategy_data, tmp_path):  # noqa: F811
    setup = strategy_data
    base = tmp_path / "worker-base"
    root = base / "unified-data/qlib-versions/versions" / setup.root.name
    shutil.copytree(setup.root, root)
    folder = tmp_path / "approved-factor-archive"
    (folder / "reports").mkdir(parents=True)
    shutil.copyfile(setup.data.registry.database, folder / "factors.sqlite")
    report = setup.report
    compressed = gzip.compress(json.dumps(report, sort_keys=True).encode(), mtime=0)
    path = folder / "reports" / (report["evaluation_id"] + ".json.gz")
    path.write_bytes(compressed)
    specs = []
    for number, ref in enumerate(setup.data.request["ordered_factor_refs"]):
        spec = deepcopy(setup.request)
        spec.update(name=f"approved-factor-{number}", factor_ref=ref,
            signal_kind="FACTOR_COMPOSITE" if number == 3 else "FACTOR_SINGLE")
        specs.append(spec)
    index = signed({**AUTHORITY, "schema_version": local.INDEX_SCHEMA,
        "registry_sha256": sha256((folder / "factors.sqlite").read_bytes()).hexdigest(),
        "evaluations": [{**setup.request["evaluation_ref"], "data_version_id": root.name,
                          "gzip_sha256": sha256(compressed).hexdigest()}], "templates": specs})
    (folder / "index.json").write_text(json.dumps(index))
    resolver = local.FactorResearchContracts(base, setup.data.database, "e" * 40,
                                              setup.registry, folder)
    catalog = resolver.publication()

    def request(spec=None, identity="a" * 32):
        spec = deepcopy(spec or specs[0])
        definition = preview_from_catalog(catalog, spec)
        return {"schema_version": REQUEST_SCHEMA, "kind": KIND, "job_id": identity,
            "actor": "independent-test", "factor_spec": spec, "factor_spec_sha256": digest(spec),
            "expected_strategy_version": definition["strategy_version"],
            "expected_catalog_sha256": catalog["catalog_sha256"], "source_revision": "e" * 40,
            "data_version_id": root.name, "live_trading_enabled": False}

    return SimpleNamespace(setup=setup, base=base, root=root, folder=folder, report_path=path,
        index=index, resolver=resolver, catalog=catalog, request=request, specs=specs,
        output=tmp_path / "child-output", request_path=tmp_path / "request.json")


def reseal(value):
    return signed({key: item for key, item in value.items() if key != "evidence_sha256"})


def test_published_fixture_has_exact_pure_preview_and_bounded_wire_contract():
    fixture = json.loads(Path("tests/fixtures/factor_strategy_register_v1.json").read_bytes())
    assert preview_from_catalog(fixture["catalog"], fixture["spec"]) == fixture["definition"]
    assert len(json.dumps(fixture["catalog"], indent=2).encode()) < 1_000_000


@pytest.mark.skipif(sys.platform == 'win32', reason='POSIX durable directory fsync')
def test_real_archive_preview_and_child_reconcile_existing_atomic_registry(archive):
    request = archive.request()
    before = {p.relative_to(archive.folder): p.read_bytes()
              for p in archive.folder.rglob("*") if p.is_file()}
    archive.request_path.write_text(json.dumps(request))
    receipt = run(base=archive.base, data_database=archive.setup.data.database,
        strategy_database=archive.setup.database, archive_root=archive.folder,
        request_path=archive.request_path, output=archive.output, source_revision="e" * 40)
    assert archive.resolver.completion(request, archive.output) == receipt
    assert receipt["registration_only"] is True
    assert receipt["status"] == "EXPERIMENT" and receipt["role"] == "NONE"
    assert receipt["backtest_implemented"] is receipt["model_activation"] is False
    assert archive.resolver.publication()["catalog_sha256"] != request["expected_catalog_sha256"]
    # New parents change the catalog; completing the committed job remains valid.
    assert archive.resolver.completion(request, archive.output) == receipt
    assert all((archive.folder / p).read_bytes() == raw for p, raw in before.items())
    assert len(archive.setup.registry.list()) == 1
    with pytest.raises(ValueError, match="NEW_OUTPUT"):
        run(base=archive.base, data_database=archive.setup.data.database,
            strategy_database=archive.setup.database, archive_root=archive.folder,
            request_path=archive.request_path, output=archive.output, source_revision="e" * 40)


@pytest.mark.parametrize("change", ["source", "catalog", "strategy", "dataset", "factor",
    "evaluation", "portfolio", "unknown", "live", "schema", "kind", "job", "actor"])
def test_request_or_actual_resolution_rejects_rehashed_tampering(archive, change):
    request = archive.request()
    if change in {"source", "catalog", "strategy", "dataset"}:
        key = {"source": "source_revision", "catalog": "expected_catalog_sha256",
               "strategy": "expected_strategy_version", "dataset": "data_version_id"}[change]
        request[key] = "f" * len(request[key])
    elif change == "factor":
        request["factor_spec"]["factor_ref"]["factor_version"] = "f" * 64
    elif change == "evaluation":
        request["factor_spec"]["evaluation_ref"]["report_manifest_sha256"] = "f" * 64
    elif change == "portfolio":
        request["factor_spec"]["portfolio"]["topk"] = True
    elif change == "unknown":
        request["factor_spec"]["module_path"] = "untrusted"
    elif change == "live":
        request["live_trading_enabled"] = True
    elif change == "schema":
        request["schema_version"] = "qlib"
    elif change == "kind":
        request["kind"] = "QLIB_RESEARCH_JOB"
    elif change == "job":
        request["job_id"] = "../" + "a" * 32
    elif change == "actor":
        request["actor"] = "contains\nnewline"
    request["factor_spec_sha256"] = digest(request["factor_spec"])
    with pytest.raises(ValueError):
        archive.resolver.verify(request, register=True)
    assert archive.setup.registry.list() == ()


@pytest.mark.parametrize("change", ["report", "registry", "ready_file", "admission", "manifest"])
def test_cached_preview_never_substitutes_for_actual_content_admission(archive, change):
    request = archive.request()
    if change in {"report", "registry", "ready_file", "manifest"}:
        path = {"report": archive.report_path, "registry": archive.folder / "factors.sqlite",
            "ready_file": archive.root / "provider/calendars/day.txt",
            "manifest": archive.root / "version.json"}[change]
        stamp = path.stat()
        raw = bytearray(path.read_bytes())
        raw[-4] ^= 1
        path.write_bytes(raw)
        os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    else:
        with sqlite3.connect(archive.setup.data.database) as connection:
            connection.execute("UPDATE data_versions SET actual_end='2099-01-01'")
    with pytest.raises((ValueError, UnicodeDecodeError, json.JSONDecodeError)):
        archive.resolver.verify(request, register=True)
    assert archive.setup.registry.list() == ()


@pytest.mark.parametrize("change", ["heartbeat_old", "heartbeat_naive", "heartbeat_future",
    "calendar", "source", "template", "unknown", "authority_type", "hash"])
def test_catalog_clock_hash_and_pure_preview_constraints(archive, change):
    catalog = deepcopy(archive.catalog)
    if change == "heartbeat_old":
        catalog["worker"]["heartbeat_at"] = (datetime.now(UTC) - timedelta(days=3)).isoformat()
    elif change == "heartbeat_naive":
        catalog["worker"]["heartbeat_at"] = "2020-01-01T00:00:00"
    elif change == "heartbeat_future":
        catalog["worker"]["heartbeat_at"] = (datetime.now(UTC) + timedelta(days=1)).isoformat()
    elif change == "calendar":
        catalog["datasets"][0]["calendar"].reverse()
    elif change == "source":
        catalog["worker"]["implementation_sources"].pop("scripts/run_qlib_worker.py")
    elif change == "template":
        catalog["templates"][0]["spec"]["portfolio"]["topk"] = 7
    elif change == "unknown":
        catalog["extra"] = True
    elif change == "authority_type":
        catalog["live_trading_enabled"] = 0
    catalog["catalog_sha256"] = catalog_identity(catalog)
    catalog = reseal(catalog)
    if change == "heartbeat_old":
        assert validate_catalog(catalog)["catalog_sha256"] == archive.catalog["catalog_sha256"]
    else:
        if change == "hash":
            catalog["evidence_sha256"] = "f" * 64
        with pytest.raises(ValueError):
            validate_catalog(catalog)


@pytest.mark.parametrize("change", ["duplicate_json", "extra", "wal", "bad_gzip", "gzip_bomb",
    "oversize_index", "bad_index_hash", "wrong_report_ref", "source_drift"])
def test_archive_bounds_and_actual_sources_fail_closed(archive, monkeypatch, change):
    index = deepcopy(archive.index)
    if change == "duplicate_json":
        (archive.folder / "index.json").write_text('{"x":1,"x":2}')
    elif change == "extra":
        index["arbitrary_path"] = "../another.sqlite"
    elif change == "wal":
        (archive.folder / "factors.sqlite-wal").write_bytes(b"uncheckpointed")
    elif change in {"bad_gzip", "gzip_bomb"}:
        compressed = b"garbage" if change == "bad_gzip" else gzip.compress(b"x" * 100)
        archive.report_path.write_bytes(compressed)
        index["evaluations"][0]["gzip_sha256"] = sha256(compressed).hexdigest()
        monkeypatch.setattr(local, "MAX_REPORT_BYTES", 50)
    elif change == "oversize_index":
        (archive.folder / "index.json").write_bytes(b" " * 100_001)
    elif change == "bad_index_hash":
        index["evidence_sha256"] = "f" * 64
    elif change == "wrong_report_ref":
        index["evaluations"][0]["report_manifest_sha256"] = "f" * 64
    elif change == "source_drift":
        sources = local._sources()
        sources["scripts/run_qlib_worker.py"] = "f" * 64
        monkeypatch.setattr(local, "_sources", lambda: sources)
    if change not in {"duplicate_json", "oversize_index", "wal", "source_drift"}:
        value = index if change == "bad_index_hash" else reseal(index)
        (archive.folder / "index.json").write_text(json.dumps(value))
    with pytest.raises((ValueError, OSError)):
        archive.resolver.verify(archive.request(), register=True)
    assert archive.setup.registry.list() == ()


@pytest.fixture
def worker(archive, monkeypatch, tmp_path):
    monkeypatch.setenv("QTRADE_RESEARCH_QUEUE_SECRET", "test-only-key" * 4)
    queue = SimpleNamespace(read=lambda: {})
    monkeypatch.setattr(worker_module.GitQueue, "from_environment", lambda: queue)
    value = worker_module.Worker(archive.base, tmp_path / "workflow.sqlite", sys.executable,
                                  "e" * 40)
    value.factor_contracts = archive.resolver
    value.strategies_database = archive.setup.database
    value.online.data_database = archive.setup.data.database
    value.predictor.tick = lambda **_: None
    monkeypatch.setattr(value, "publish", lambda *args: None)
    yield value, queue
    value.db.close()


def test_factor_ingest_reuses_hmac_shared_capacity_idempotence_and_cancellation(worker, archive):
    value, queue = worker
    requests = [archive.request(identity=str(i) * 32) for i in range(1, 5)]
    queue.read = lambda: {"requests/" + row["job_id"]: seal(row, value.secret) for row in requests}
    value.ingest()
    value.ingest()
    assert value.db.execute("SELECT state FROM jobs ORDER BY rowid").fetchall() == [
        ("QUEUED",)] * 3 + [("REJECTED",)]
    # Ingest validates bounded types; expensive archive/READY reads are child-only.
    assert archive.setup.registry.list() == ()
    request = requests[0]
    altered = deepcopy(request)
    altered["actor"] = "different"
    queue.read = lambda: {"requests/" + request["job_id"]: seal(altered, value.secret),
        "cancellations/" + request["job_id"]: seal({"job_id": request["job_id"],
        "kind": "CANCEL_RESEARCH_JOB"}, value.secret)}
    value.ingest()
    value.execute(request["job_id"], request)
    row = value.db.execute("SELECT state,request FROM jobs WHERE id=?", (request["job_id"],)
                           ).fetchone()
    assert row[0] == "CANCELLED" and json.loads(row[1])["actor"] == request["actor"]


@pytest.mark.parametrize("change", ["unsealed", "tampered", "malformed", "source", "disabled"])
def test_factor_worker_ingress_rejects_untrusted_or_unconfigured_jobs(worker, archive, change):
    value, queue = worker
    request = archive.request()
    if change == "malformed":
        request["factor_spec"]["execution"]["cost_callable"] = "arbitrary"
        request["factor_spec_sha256"] = digest(request["factor_spec"])
    if change == "source":
        request["source_revision"] = "f" * 40
    if change == "disabled":
        value.factor_contracts = None
    envelope = request if change == "unsealed" else seal(request, value.secret)
    if change == "tampered":
        envelope["live_trading_enabled"] = True
    queue.read = lambda: {"requests/" + request["job_id"]: envelope}
    value.ingest()
    rows = value.db.execute("SELECT state FROM jobs").fetchall()
    assert rows == ([("REJECTED",)] if change in {"source", "disabled"} else [])
    assert archive.setup.registry.list() == ()


def test_restart_never_replays_factor_registration_uncertainty(worker, archive, monkeypatch):
    value, queue = worker
    request = archive.request()
    queue.read = lambda: {"requests/" + request["job_id"]: seal(request, value.secret)}
    value.ingest()
    value.update(request["job_id"], "RUNNING")
    monkeypatch.setattr(worker_module, "recover_child", lambda _: None)
    resumed = worker_module.Worker(archive.base, Path(value.db.execute(
        "PRAGMA database_list").fetchone()[2]), sys.executable, "e" * 40)
    try:
        resumed.ingest()
        assert resumed.db.execute("SELECT state FROM jobs").fetchone()[0] == "INTERRUPTED"
    finally:
        resumed.db.close()


def test_registry_commit_receipt_failure_is_preserved_and_explicit_resubmit_is_idempotent(
        worker, archive, monkeypatch):
    value, queue = worker
    request = archive.request()
    queue.read = lambda: {"requests/" + request["job_id"]: seal(request, value.secret)}
    value.ingest()
    value.update(request["job_id"], "RUNNING")
    archive.request_path.write_text(json.dumps(request))

    def fail_after_commit(*args):
        raise OSError("injected receipt persistence failure")

    monkeypatch.setattr(runner_module, "write_json_immutable", fail_after_commit)
    with pytest.raises(OSError, match="persistence failure"):
        run(base=archive.base, data_database=archive.setup.data.database,
            strategy_database=archive.setup.database, archive_root=archive.folder,
            request_path=archive.request_path, output=archive.output, source_revision="e" * 40)
    record = archive.setup.registry.list()[0]
    assert len(archive.setup.registry.research_jobs(record.spec.strategy_id)) == 1
    assert not (archive.output / "registration.json").exists()
    monkeypatch.setattr(worker_module, "recover_child", lambda _: None)
    resumed = worker_module.Worker(archive.base, Path(value.db.execute(
        "PRAGMA database_list").fetchone()[2]), sys.executable, "e" * 40)
    try:
        resumed.ingest()
        assert resumed.db.execute("SELECT state FROM jobs").fetchone()[0] == "INTERRUPTED"
        assert len(archive.setup.registry.research_jobs(record.spec.strategy_id)) == 1
    finally:
        resumed.db.close()
    # An explicit new authenticated request observes the changed catalog. It links
    # to the same immutable definition, without another lifecycle transition.
    catalog = archive.resolver.publication()
    request.update(job_id="b" * 32, expected_catalog_sha256=catalog["catalog_sha256"])
    definition = archive.resolver.verify(request, register=True)
    assert definition["strategy_id"] == record.spec.strategy_id
    assert len(archive.setup.registry.research_jobs(record.spec.strategy_id)) == 2
    assert len(archive.setup.registry.transitions(record.spec.strategy_id)) == 1


def test_actual_ready_verification_finishes_before_atomic_registry_write(archive, monkeypatch):
    calls = 0
    original = archive.resolver.inputs

    def drift():
        nonlocal calls
        result = original()
        calls += 1
        if calls == 3:
            return result[0], "f" * 64
        return result

    monkeypatch.setattr(archive.resolver, "inputs", drift)
    with pytest.raises(ValueError, match="ARCHIVE_CHANGED_BEFORE_REGISTRATION"):
        archive.resolver.verify(archive.request(), register=True)
    assert archive.setup.registry.list() == ()


def test_factor_and_qlib_jobs_share_the_same_three_slot_capacity(worker, archive):
    from qtrade.research.workflow import default_spec

    value, queue = worker
    spec = default_spec()
    items = {"requests/" + str(i) * 32: seal({"job_id": str(i) * 32,
        "kind": "QLIB_RESEARCH_JOB", "actor": "independent-test", "spec": spec,
        "spec_sha256": digest(spec), "live_trading_enabled": False}, value.secret)
        for i in range(1, 4)}
    factor = archive.request(identity="4" * 32)
    items["requests/" + factor["job_id"]] = seal(factor, value.secret)
    queue.read = lambda: items
    value.ingest()
    assert value.db.execute("SELECT state,error FROM jobs WHERE id=?", (factor["job_id"],)
                           ).fetchone() == ("REJECTED", "QUEUE_CAPACITY_EXCEEDED")


def test_factor_parent_must_be_published_and_current_experiment_none(archive):
    definition = archive.resolver.verify(archive.request(), register=True)
    catalog = archive.resolver.publication()
    request = archive.request()
    request["factor_spec"]["parent"] = {"strategy_id": definition["strategy_id"],
        "strategy_version": definition["strategy_version"],
        "contract_sha256": definition["evidence_sha256"], "reason": "independent-test",
        "hypothesis": "same factor, explicit cost assumption", "change_summary": "buy cost 9"}
    request["factor_spec"]["execution"]["buy_cost_bps"] = "9"
    request["factor_spec_sha256"] = digest(request["factor_spec"])
    request.update(job_id="b" * 32, expected_catalog_sha256=catalog["catalog_sha256"],
        expected_strategy_version=preview_from_catalog(catalog, request["factor_spec"])[
            "strategy_version"])
    # Simulate another authorized lifecycle operation after preview. The resolver
    # publishes actual parents afresh and refuses this old catalog before writing.
    with sqlite3.connect(archive.setup.database) as connection:
        connection.execute("UPDATE strategies SET status='REJECTED'")
    with pytest.raises(ValueError, match="CATALOG_CHANGED"):
        archive.resolver.verify(request, register=True)
    assert len(archive.setup.registry.list()) == 1


@pytest.mark.parametrize('args', [
    [b'python', b'-c', b'sleep', b'scripts.run_factor_strategy_registration'],
    [b'python', b'-m', b'scripts.run_factor_strategy_registration', b'--output'],
    [b'python', b'-m', b'untrusted', b'scripts.run_factor_strategy_registration'],
])
def test_factor_child_identity_requires_exact_module_and_output_tokens(tmp_path, args):
    assert not worker_module._factor_child_command(args, tmp_path)
    assert worker_module._factor_child_command([b'python', b'-B', b'-m',
        b'scripts.run_factor_strategy_registration', b'--output',
        str(tmp_path / 'output').encode()], tmp_path)


@pytest.mark.skipif(not Path("/proc/self/stat").exists(), reason="Linux owned process identity")
def test_actual_owned_registration_child_never_imports_model_or_backtest(worker, archive,
                                                                       monkeypatch):
    value, queue = worker
    request = archive.request()
    queue.read = lambda: {"requests/" + request["job_id"]: seal(request, value.secret)}
    value.ingest()
    monkeypatch.setattr(shutil, "disk_usage", lambda _: SimpleNamespace(free=101 * 1024**3))
    real_sleep = worker_module.time.sleep
    monkeypatch.setattr(worker_module.time, "sleep", lambda _: real_sleep(0.02))
    value.execute(request["job_id"], request)
    state, result = value.db.execute("SELECT state,result FROM jobs").fetchone()
    assert state == "COMPLETE"
    detail = json.loads((value.public / json.loads(result)["detail_file"]).read_bytes())
    assert detail["schema_version"] == "qtrade.factor-strategy-registration.v1"
    assert detail["registration_only"] is True and detail["model_activation"] is False
    assert not (value.root / "jobs" / request["job_id"] / "output/runs").exists()
    assert len(archive.setup.registry.list()) == 1


@pytest.mark.skipif(not Path('/proc/self/stat').exists(), reason='Linux owned process identity')
def test_actual_child_rejects_changed_catalog_before_any_registration(worker, archive, monkeypatch):
    value, queue = worker
    request = archive.request()
    request['expected_catalog_sha256'] = 'f' * 64
    queue.read = lambda: {'requests/' + request['job_id']: seal(request, value.secret)}
    value.ingest()
    monkeypatch.setattr(shutil, 'disk_usage', lambda _: SimpleNamespace(free=101 * 1024**3))
    real_sleep = worker_module.time.sleep
    monkeypatch.setattr(worker_module.time, 'sleep', lambda _: real_sleep(0.02))
    value.execute(request['job_id'], request)
    assert value.db.execute('SELECT state,error FROM jobs').fetchone() == (
        'REJECTED', 'IMMUTABLE_FACTOR_INPUT_REJECTED')
    assert archive.setup.registry.list() == ()
