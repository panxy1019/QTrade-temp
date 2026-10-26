from copy import deepcopy

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from qtrade.api import research_workflow as api
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.source_constraints import LEGACY_PROTOCOL, PROTOCOL
from qtrade.research.workflow import archived_template, default_spec


def archived_result():
    spec = default_spec("linear")
    spec.update(name="frozen-reference", source_snapshot_sha256="a" * 64,
                topk=40, sell_cost_bps=15, seed=17)
    return signed({
        **AUTHORITY, "schema_version": "qtrade.workflow-result.v1", "engine": "QLIB",
        "state": "COMPLETE", "spec": spec, "experiment_id": "reference",
        "run_id": "reference-run", "source_revision": "b" * 40,
        "source_snapshot_sha256": "a" * 64, "dataset_sha256": "c" * 64,
        "comparison_protocol": PROTOCOL,
    })


def test_template_preserves_complete_archived_inputs_and_cannot_mutate_the_reference():
    reference = archived_result()
    before = deepcopy(reference)
    result = archived_template(reference, "b" * 40)
    assert result["spec"] == reference["spec"]
    assert result["source_snapshot_sha256"] == "a" * 64
    assert result["base_result_sha256"] == reference["evidence_sha256"]
    assert result["dataset_sha256"] == reference["dataset_sha256"]
    assert result["live_trading_enabled"] is False
    assert result["promotion"] == "NOT_ELIGIBLE"
    result["spec"]["segments"]["test"][1] = "2025-01-01"
    assert reference == before


def test_legacy_v2_template_cannot_be_relabelled_for_the_new_policy():
    reference = archived_result()
    reference["comparison_protocol"] = LEGACY_PROTOCOL
    reference = signed({k: v for k, v in reference.items() if k != "evidence_sha256"})
    before = deepcopy(reference)
    with pytest.raises(ValueError, match="当前协议"):
        archived_template(reference, "b" * 40)
    assert reference == before


@pytest.mark.parametrize("kind", [
    "legacy", "missing_source", "source_drift", "missing_cost", "extra_command",
    "changed_worker", "unknown_worker", "tamper", "authority", "numeric_false", "dataset",
    "missing_identity",
])
def test_reuse_cannot_fill_missing_or_incompatible_inputs_with_current_defaults(kind):
    reference = archived_result()
    reference.pop("evidence_sha256")
    worker = "b" * 40
    if kind == "legacy":
        reference["spec"] = None
    elif kind == "missing_source":
        reference["spec"].pop("source_snapshot_sha256")
    elif kind == "source_drift":
        reference["source_snapshot_sha256"] = "d" * 64
    elif kind == "missing_cost":
        reference["spec"].pop("buy_cost_bps")
    elif kind == "extra_command":
        reference["spec"]["command"] = "arbitrary-code"
    elif kind == "changed_worker":
        worker = "e" * 40
    elif kind == "unknown_worker":
        worker = None
    elif kind == "authority":
        reference["point_in_time"] = True
    elif kind == "numeric_false":
        reference["point_in_time"] = 0
    elif kind == "dataset":
        reference["dataset_sha256"] = "missing"
    elif kind == "missing_identity":
        reference.pop("run_id")
    reference = signed(reference)
    if kind == "tamper":
        reference["spec"]["topk"] = 99
    with pytest.raises(ValueError):
        archived_template(reference, worker)


def test_template_endpoint_is_read_only_and_public_but_submission_still_requires_login(monkeypatch):
    result = archived_result()
    listing = {"schema_version": api.SCHEMA, "worker": {"source_revision": "b" * 40},
               "experiments": [{"experiment_id": "reference", "state": "COMPLETE",
                                "detail_file": "workflow-results/" + "d" * 64 + ".json",
                                "detail_sha256": result["evidence_sha256"]}]}
    monkeypatch.setattr(api, "read_public", lambda path: listing if path == "workflow.json"
                        else result)
    def forbidden_queue():
        raise AssertionError("read-only configuration must not open the write queue")
    monkeypatch.setattr(api.GitQueue, "from_environment", forbidden_queue)
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        response = client.get("/research/experiments/reference/template")
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        assert response.json()["spec"] == result["spec"]
        assert client.post("/research/jobs", json={}).status_code == 401
        listing["worker"]["source_revision"] = "e" * 40
        blocked = client.get("/research/experiments/reference/template")
        assert blocked.status_code == 422
        assert "执行器代码与原实验不同" in blocked.json()["detail"]
        assert client.get("/research/experiments/missing/template").status_code == 404
