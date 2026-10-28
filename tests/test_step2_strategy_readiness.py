"""Isolated STEP2 reuse checks; no Qlib training, remote queue or production writes."""

from __future__ import annotations

import json
import sys
from copy import deepcopy
from datetime import UTC, datetime

import pytest
from test_qlib_strategy_contract import make_version
from test_qlib_workflow import client as client

from qtrade.api import research_workflow as api
from qtrade.monitoring.data_freshness import AUTHORITY
from qtrade.research.data_versions import DataVersionCatalog
from qtrade.research.strategy_contract import bound_default_spec, freeze_strategy, ready_dataset
from qtrade.research.workflow import SCHEMA, canonical
from qtrade.research.workflow_queue import unseal
from qtrade.strategies.registry import StrategyRole, StrategyStatus
from scripts import run_qlib_worker as worker_module

REVISION = "a" * 40


@pytest.fixture
def isolated_pipeline(tmp_path, monkeypatch):
    """Use the real HTTP/worker adapters with an in-memory queue and temporary stores."""
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "false")
    monkeypatch.setenv("QTRADE_RESEARCH_QUEUE_SECRET", "step2-test-only-signing-key" * 2)
    items = {}

    class Queue:
        def read(self):
            return deepcopy(items)

        def append(self, section, identity, envelope):
            key = section + "/" + identity
            if key in items and canonical(items[key]) != canonical(envelope):
                raise ValueError("IMMUTABLE_JOB_ID_CONFLICT")
            items[key] = deepcopy(envelope)

    queue = Queue()
    monkeypatch.setattr(worker_module.GitQueue, "from_environment", lambda: queue)
    worker = worker_module.Worker(
        tmp_path, tmp_path / "workflow.sqlite", sys.executable, REVISION
    )
    archive = tmp_path / "unified-data/qlib-versions"
    root = make_version(archive)
    catalog = DataVersionCatalog(worker.online.data_database, archive)
    catalog.admit(root.name, now=datetime.now(UTC))

    def listing():
        return {
            **AUTHORITY,
            "schema_version": SCHEMA,
            "worker": {
                "heartbeat_at": datetime.now(UTC).isoformat(),
                "source_revision": REVISION,
            },
            "experiments": [],
            **worker.research_contracts.publication(),
        }

    monkeypatch.setattr(api, "read_public", lambda *_: listing())
    yield worker, queue, root, catalog
    worker.db.close()


@pytest.mark.parametrize("model", ["linear", "lightgbm"])
def test_builder_selection_reaches_same_queue_and_registry_without_promotion(
    client, isolated_pipeline, model
):
    http, password = client
    worker, queue, root, _ = isolated_pipeline
    binding = ready_dataset(root, worker.online.data_database)
    spec = bound_default_spec(binding, model)
    spec.update(
        topk=31, n_drop=7, hold_thresh=5, risk_degree=0.6,
        buy_cost_bps=11, sell_cost_bps=19, min_cost=7, initial_cash=300_000, seed=20261028,
    )
    with worker.online.registry.connect() as connection:
        models_before = tuple(connection.iterdump())
    files_before = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    assert http.post(
        "/research/login", json={"user": "research-test", "password": password}
    ).status_code == 200
    preview = http.post("/research/specification", json={"spec": spec})
    assert preview.status_code == 200, preview.text
    definition = preview.json()["strategy_definition"]
    assert definition == freeze_strategy(spec, REVISION)
    body = {
        "spec": spec, "request_id": "1" * 32,
        "expected_strategy_version": definition["strategy_version"],
    }
    first = http.post("/research/jobs", json=body)
    replay = http.post("/research/jobs", json=body)
    assert first.status_code == replay.status_code == 202
    assert first.json() == replay.json()
    envelope, = queue.read().values()
    request = unseal(envelope, worker.secret)
    assert request["spec"] == spec
    assert request["expected_strategy_version"] == definition["strategy_version"]
    worker.ingest()
    worker.ingest()
    stored, = worker.db.execute("SELECT request,state FROM jobs").fetchall()
    assert json.loads(stored[0]) == request and stored[1] == "QUEUED"
    assert worker.db.execute("SELECT state FROM events").fetchall() == [("QUEUED",)]
    registry = worker.research_contracts.registry
    record, = registry.list()
    assert record.status is StrategyStatus.EXPERIMENT and record.role is StrategyRole.NONE
    assert record.spec.universe == ("SH600036",)
    assert registry.research_contract(record.spec.strategy_id) == definition
    assert len(registry.research_jobs(record.spec.strategy_id)) == 1
    assert len(registry.transitions(record.spec.strategy_id)) == 1
    assert {key: definition["portfolio"][key] for key in (
        "topk", "n_drop", "hold_thresh", "risk_degree"
    )} == {"topk": 31, "n_drop": 7, "hold_thresh": 5, "risk_degree": 0.6}
    assert definition["training_config"]["exchange"]["open_cost"] == 0.0011
    assert definition["training_config"]["exchange"]["close_cost"] == 0.0019
    assert definition["training_config"]["account"] == 300_000
    assert definition["execution"]["timing"] == "PREVIOUS_SIGNAL_NEXT_SESSION_OPEN"
    assert definition["paper_admission"] is definition["model_activation"] is False
    with worker.online.registry.connect() as connection:
        assert tuple(connection.iterdump()) == models_before
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == (
        files_before
    )


@pytest.mark.parametrize("unsupported", [
    {"factor_refs": [{"factor_id": "qtrade.price.momentum20", "factor_version": "f" * 64}]},
    {"evaluation_ref": {"evaluation_id": "e" * 64}},
    {"rebalance": "weekly"},
    {"weight_method": "risk_parity"},
    {"module_path": "caller.strategy"},
])
def test_unimplemented_step2_choices_do_not_silently_fall_back(
    client, isolated_pipeline, unsupported
):
    http, password = client
    worker, queue, root, _ = isolated_pipeline
    spec = bound_default_spec(ready_dataset(root, worker.online.data_database))
    spec.update(unsupported)
    http.post("/research/login", json={"user": "research-test", "password": password})
    assert http.post("/research/specification", json={"spec": spec}).status_code == 422
    assert http.post("/research/jobs", json={
        "spec": spec, "request_id": "1" * 32, "expected_strategy_version": "e" * 64,
    }).status_code == 422
    assert queue.read() == {}
    worker.ingest()
    assert worker.db.execute("SELECT count(*) FROM jobs").fetchone()[0] == 0
    assert worker.research_contracts.registry.list() == ()


def test_new_admitted_data_appends_research_identity_and_preserves_original(
    client, isolated_pipeline
):
    """New version mechanics only: fixture admission does not certify historical PIT."""
    http, password = client
    worker, queue, root, catalog = isolated_pipeline
    http.post("/research/login", json={"user": "research-test", "password": password})
    original_files = {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()}
    old_definition = None
    original_envelope = None
    for index, selected in enumerate((root, None), start=1):
        if selected is None:
            selected = make_version(root.parent.parent, end="2026-10-27",
                                    boundary="2026-10-28", nonce="b")
            catalog.admit(selected.name, now=datetime.now(UTC))
        spec = bound_default_spec(ready_dataset(selected, worker.online.data_database))
        preview = http.post("/research/specification", json={"spec": spec})
        assert preview.status_code == 200, preview.text
        definition = preview.json()["strategy_definition"]
        response = http.post("/research/jobs", json={
            "spec": spec, "request_id": str(index) * 32,
            "expected_strategy_version": definition["strategy_version"],
        })
        assert response.status_code == 202, response.text
        worker.ingest()
        if old_definition is None:
            old_definition = definition
            original_envelope = queue.read()["requests/" + "1" * 32]
        else:
            assert definition["strategy_version"] != old_definition["strategy_version"]
            assert definition["spec"]["data_binding"] != old_definition["spec"]["data_binding"]
    registry = worker.research_contracts.registry
    assert len(registry.list()) == 2
    assert registry.research_contract(old_definition["strategy_id"]) == old_definition
    assert queue.read()["requests/" + "1" * 32] == original_envelope
    assert worker.db.execute("SELECT count(*) FROM jobs WHERE state='QUEUED'").fetchone()[0] == 2
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob("*") if p.is_file()} == (
        original_files
    )
    for row in worker.research_contracts.publication()["datasets"]:
        assert row["audit"]["universe"]["historical_pit_certified"] is False
        assert row["audit"]["promotion"] == "NOT_ELIGIBLE"


def test_worker_revision_drift_after_http_acceptance_rejects_before_registration(
    client, isolated_pipeline
):
    http, password = client
    worker, _, root, _ = isolated_pipeline
    spec = bound_default_spec(ready_dataset(root, worker.online.data_database))
    http.post("/research/login", json={"user": "research-test", "password": password})
    preview = http.post("/research/specification", json={"spec": spec})
    assert preview.status_code == 200
    response = http.post("/research/jobs", json={
        "spec": spec, "request_id": "1" * 32,
        "expected_strategy_version": preview.json()["expected_strategy_version"],
    })
    assert response.status_code == 202
    worker.research_contracts.revision = "b" * 40
    worker.ingest()
    worker.ingest()
    assert worker.db.execute("SELECT state,error FROM jobs").fetchall() == [
        ("REJECTED", "IMMUTABLE_RESEARCH_INPUT_REJECTED")
    ]
    assert worker.db.execute("SELECT state FROM events ORDER BY rowid").fetchall() == [
        ("QUEUED",), ("REJECTED",)
    ]
    assert worker.research_contracts.registry.list() == ()
