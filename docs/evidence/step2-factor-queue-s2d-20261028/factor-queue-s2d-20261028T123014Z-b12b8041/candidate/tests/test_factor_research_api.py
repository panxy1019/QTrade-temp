from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from test_qlib_workflow import client as client

from qtrade.api import research_workflow as api
from qtrade.monitoring.data_freshness import AUTHORITY
from qtrade.research.factor_strategy_catalog import preview_from_catalog, validate_catalog
from qtrade.research.workflow import SCHEMA
from qtrade.research.workflow_queue import unseal
from scripts.publish_research_snapshot import factor_strategy_publication


@pytest.fixture
def inputs(monkeypatch):
    fixture = json.loads(Path("tests/fixtures/factor_strategy_register_v1.json").read_bytes())
    catalog = fixture["catalog"]
    catalog["worker"]["heartbeat_at"] = datetime.now(UTC).isoformat()
    # Re-seal through the catalog's existing stable/heartbeat contracts below.
    from qtrade.monitoring.data_freshness import signed

    catalog = signed({k: v for k, v in catalog.items() if k != "evidence_sha256"})
    validate_catalog(catalog)
    workflow = {
        **AUTHORITY,
        "schema_version": SCHEMA,
        "worker": {
            "heartbeat_at": datetime.now(UTC).isoformat(),
            "source_revision": catalog["worker"]["source_revision"],
        },
        "experiments": [],
    }
    reads, writes = [], []

    def published(path, **kwargs):
        reads.append((path, kwargs))
        return catalog if path == "factor-catalog.json" else workflow

    class Queue:
        def append(self, *args):
            writes.append(args)

    monkeypatch.setattr(api, "read_public", published)
    monkeypatch.setattr(api.GitQueue, "from_environment", lambda: Queue())
    return fixture["spec"], catalog, workflow, reads, writes


def login(client):
    http, password = client
    assert (
        http.post(
            "/research/login", json={"user": "research-test", "password": password}
        ).status_code
        == 200
    )
    return http


def body(spec, catalog):
    definition = preview_from_catalog(catalog, spec)
    return {
        "spec": spec,
        "request_id": "e" * 32,
        "expected_strategy_version": definition["strategy_version"],
        "expected_catalog_sha256": catalog["catalog_sha256"],
    }


def test_factor_auth_origin_and_bounded_catalog(client, inputs):
    http, _ = client
    spec, catalog, _, reads, writes = inputs
    assert (
        http.post("/research/factor-strategies/specification", json={"spec": spec}).status_code
        == 401
    )
    assert (
        http.post("/research/factor-strategies/jobs", json=body(spec, catalog)).status_code == 401
    )
    assert not writes
    login(client)
    assert (
        http.post(
            "/research/factor-strategies/specification",
            json={"spec": spec},
            headers={"origin": "https://untrusted.invalid"},
        ).status_code
        == 403
    )
    assert not writes
    response = http.get("/research/factor-strategies/catalog")
    assert response.status_code == 200 and response.headers["cache-control"] == "no-store"
    assert reads[-1] == ("factor-catalog.json", {"maximum": 1_000_000})


def test_preview_and_queue_bind_exact_factor_without_model_authority(client, inputs):
    spec, catalog, _, _, writes = inputs
    http = login(client)
    preview = http.post("/research/factor-strategies/specification", json={"spec": spec})
    assert preview.status_code == 200, preview.text
    expected = preview_from_catalog(catalog, spec)
    assert preview.json()["strategy_definition"] == expected
    assert preview.headers["cache-control"] == "no-store"
    response = http.post("/research/factor-strategies/jobs", json=body(spec, catalog))
    assert response.status_code == 202, response.text
    assert response.json()["registration_only"] is True
    kind, job_id, envelope = writes[0]
    assert (kind, job_id) == ("requests", "e" * 32)
    value = unseal(envelope, __import__("os").environ["QTRADE_RESEARCH_QUEUE_SECRET"])
    assert value["actor"] == "research-test" and value["factor_spec"] == expected["spec"]
    assert value["kind"] == "FACTOR_STRATEGY_REGISTER_V1"
    assert value["source_revision"] == catalog["worker"]["source_revision"]
    assert (
        value["data_version_id"]
        == expected["evaluation_context"]["data_binding"]["data_version_id"]
    )
    assert value["live_trading_enabled"] is False
    assert not {"model", "spec", "activation", "paper_admission"} & value.keys()


@pytest.mark.parametrize("field", ["expected_catalog_sha256", "expected_strategy_version"])
def test_changed_preview_cannot_enqueue(client, inputs, field):
    spec, catalog, _, _, writes = inputs
    request = body(spec, catalog)
    request[field] = "0" * 64
    assert login(client).post("/research/factor-strategies/jobs", json=request).status_code == 409
    assert not writes


@pytest.mark.parametrize(
    "failure", ["workflow-stale", "factor-stale", "future", "naive", "source", "unsupported"]
)
def test_worker_identity_and_both_clocks_close_mutations(client, inputs, failure):
    spec, catalog, workflow, _, writes = inputs
    if failure == "workflow-stale":
        workflow["worker"]["heartbeat_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    elif failure in {"factor-stale", "future", "naive"}:
        clock = (
            (datetime.now(UTC) - timedelta(hours=1)).isoformat()
            if failure == "factor-stale"
            else (
                (datetime.now(UTC) + timedelta(hours=1)).isoformat()
                if failure == "future"
                else "2026-10-28T01:00:00"
            )
        )
        catalog["worker"]["heartbeat_at"] = clock
    elif failure == "source":
        workflow["worker"]["source_revision"] = "0" * 40
    else:
        catalog["worker"]["supported_kinds"] = []
    # Even a fresh transport with a malformed/resigned catalog is not authority.
    assert (
        login(client)
        .post("/research/factor-strategies/specification", json={"spec": spec})
        .status_code
        == 503
    )
    assert not writes


def test_shared_capacity_unknown_fields_and_failure_idempotency(client, inputs, monkeypatch):
    spec, catalog, workflow, _, writes = inputs
    http = login(client)
    request = body(spec, catalog)
    assert (
        http.post(
            "/research/factor-strategies/jobs", json={**request, "actor": "forged"}
        ).status_code
        == 422
    )
    assert (
        http.post(
            "/research/factor-strategies/specification",
            json={"spec": {**spec, "command": "invalid"}},
        ).status_code
        == 422
    )
    assert (
        http.post(
            "/research/factor-strategies/specification", json={"spec": spec, "padding": "x" * 17000}
        ).status_code
        == 413
    )
    workflow["experiments"] = [
        {"state": value} for value in ["RUNNING", "QUEUED", "CANCEL_REQUESTED"]
    ]
    assert http.post("/research/factor-strategies/jobs", json=request).status_code == 429
    assert not writes
    workflow["experiments"] = [{"state": "COMPLETE"}]

    class Conflict:
        def append(self, *args):
            raise ValueError("IMMUTABLE_JOB_ID_CONFLICT")

    monkeypatch.setattr(api.GitQueue, "from_environment", lambda: Conflict())
    assert http.post("/research/factor-strategies/jobs", json=request).status_code == 409


def test_original_qlib_entrance_does_not_accept_factor_spec(client, inputs):
    spec, _, _, _, writes = inputs
    assert (
        login(client)
        .post("/research/jobs", json={"spec": deepcopy(spec), "request_id": "e" * 32})
        .status_code
        == 422
    )
    assert not writes


def test_optional_factor_catalog_transport_is_bounded_and_fail_closed(inputs, tmp_path):
    _, catalog, _, _, _ = inputs
    source = tmp_path / "factor-catalog.json"
    source.write_text(json.dumps(catalog))
    assert factor_strategy_publication(source) == catalog
    source.write_text('{"untrusted":true}')
    assert factor_strategy_publication(source)["state"] == "NO_VERIFIED_FACTOR_STRATEGY_CATALOG"
    source.write_bytes(b" " * 1_000_001)
    assert factor_strategy_publication(source)["state"] == "NO_VERIFIED_FACTOR_STRATEGY_CATALOG"


def test_factor_registration_details_require_complete_bound_receipt(client, inputs, monkeypatch):
    http, _ = client
    _, _, workflow, _, _ = inputs
    from qtrade.monitoring.data_freshness import signed

    value = signed(
        {
            **AUTHORITY,
            "schema_version": "qtrade.factor-strategy-registration.v1",
            "kind": "FACTOR_STRATEGY_REGISTER_V1",
            "job_id": "e" * 32,
            "state": "COMPLETE",
            "registration_only": True,
            "status": "EXPERIMENT",
            "role": "NONE",
            "backtest_implemented": False,
            "paper_admission": False,
            "model_activation": False,
        }
    )
    relative = "workflow-results/" + "a" * 64 + ".json"
    row = {
        "experiment_id": "e" * 32,
        "engine": "FACTOR_REGISTRATION",
        "state": "COMPLETE",
        "detail_file": relative,
        "detail_sha256": value["evidence_sha256"],
    }
    workflow["experiments"] = [row]
    monkeypatch.setattr(
        api, "read_public", lambda path, **_: workflow if path == "workflow.json" else value
    )
    response = http.get("/research/factor-strategies/jobs/" + "e" * 32)
    assert response.status_code == 200 and response.json() == value
    assert response.headers["cache-control"] == "no-store"
    row["engine"] = "QLIB"
    assert http.get("/research/factor-strategies/jobs/" + "e" * 32).status_code == 404
    row["engine"] = "FACTOR_REGISTRATION"
    value["paper_admission"] = True
    assert http.get("/research/factor-strategies/jobs/" + "e" * 32).status_code == 503
    assert http.get("/research/factor-strategies/jobs/invalid").status_code == 422
