import copy
import io
import json
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from qtrade.api import qlib_baseline as api
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.qlib_publication import SCHEMA, validate_publication, verify_archive


def resign(value):
    return signed({k: v for k, v in value.items() if k != "evidence_sha256"})


@pytest.fixture
def publication():
    run = signed(
        {
            **AUTHORITY,
            "schema_version": "qtrade.qlib-run.v1",
            "run_id": "baseline-v1",
            "persisted": True,
            "feature_count": 158,
            "symbols": 900,
            "persistence_kind": "FORMAL_IMMUTABLE_QLIB_RUN",
            "source_revision": "a" * 40,
            "data_end": "2026-10-23",
            "metrics": {"ic": 0.01, "rank_ic": None},
            "segments": {
                "train": ["2017-01-01", "2021-12-28"],
                "valid": ["2022-01-04", "2023-12-26"],
                "test": ["2024-01-02", "2026-10-23"],
            },
            "portfolio_evaluation": {"start": "2026-10-22", "end": "2026-10-23", "days": 2},
            "curve": [
                {
                    "date": day,
                    "equity": 1.0,
                    "benchmark_equity": 1.0,
                    "drawdown": 0,
                    "turnover": 0,
                    "cost": 0,
                }
                for day in ["2026-10-22", "2026-10-23"]
            ],
        }
    )
    return signed(
        {
            **AUTHORITY,
            "schema_version": SCHEMA,
            "published_at": "2026-10-25T13:00:00+00:00",
            "calendar": json.loads(Path("configs/observer_calendar_2026.json").read_text()),
            "state": "COMPLETE",
            "run": run,
            "artifact_manifest_sha256": "b" * 64,
        }
    )


def test_result_freshness_and_publication_age_are_independent(publication):
    now = datetime(2026, 10, 25, 13, 1, tzinfo=UTC)
    value = validate_publication(publication, now=now)
    assert value["publication_status"] == "CURRENT"
    assert value["result_data_status"] == "CURRENT_THROUGH_COMPLETE_DAY"
    later = validate_publication(publication, now=datetime(2026, 10, 26, 9, tzinfo=UTC))
    assert later["publication_status"] == "STALE"
    assert later["result_data_status"] == "HISTORICAL"
    assert later["external_order_transmission"] is False


@pytest.mark.parametrize(
    "mutation",
    [
        "checksum",
        "authority",
        "nested_authority",
        "nan",
        "curve",
        "split",
        "no_run",
        "not_persisted",
    ],
)
def test_tampered_or_inconsistent_result_is_rejected(publication, mutation):
    value = copy.deepcopy(publication)
    if mutation == "checksum":
        value["run"]["metrics"]["ic"] = 0.9
    elif mutation == "authority":
        value["live_trading_enabled"] = True
    elif mutation == "nested_authority":
        value["run"]["external_order_transmission"] = True
    elif mutation == "nan":
        value["run"]["metrics"]["ic"] = float("nan")
    elif mutation == "curve":
        value["run"]["curve"].reverse()
    elif mutation == "split":
        value["run"]["segments"]["train"][1] = "2026-01-01"
    elif mutation == "no_run":
        value["run"] = None
    else:
        value["run"]["persisted"] = False
    if mutation != "checksum":
        if value["run"]:
            value["run"] = resign(value["run"])
        value = resign(value)
    with pytest.raises(ValueError):
        validate_publication(value, now=datetime(2026, 10, 25, 13, 1, tzinfo=UTC))


def test_archive_rechecks_every_artifact_and_rejects_traversal(tmp_path):
    summary = signed({"run_id": "baseline-v1"})
    (tmp_path / "summary.json").write_text(json.dumps(summary))
    (tmp_path / "model.pkl").write_bytes(b"not loaded by publication")
    files = {
        name: sha256((tmp_path / name).read_bytes()).hexdigest()
        for name in ("summary.json", "model.pkl")
    }
    (tmp_path / "checksums.json").write_text(
        json.dumps(signed({"run_id": "baseline-v1", "files": files}))
    )
    assert verify_archive(tmp_path) == summary
    (tmp_path / "model.pkl").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="CHECKSUM"):
        verify_archive(tmp_path)
    files["../escape"] = "a" * 64
    del files["model.pkl"]
    (tmp_path / "checksums.json").write_text(
        json.dumps(signed({"run_id": "baseline-v1", "files": files}))
    )
    with pytest.raises(ValueError, match="OUTSIDE"):
        verify_archive(tmp_path)


def test_api_is_bounded_no_store_and_read_only(monkeypatch, publication):
    document = resign({**publication, "published_at": datetime.now(UTC).isoformat()})
    monkeypatch.setattr(api, "urlopen", lambda *a, **k: io.BytesIO(json.dumps(document).encode()))
    app = FastAPI()
    app.include_router(api.router)
    client = TestClient(app)
    response = client.get("/qlib/baseline")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert client.post("/qlib/baseline").status_code == 405
    monkeypatch.setattr(api, "urlopen", lambda *a, **k: io.BytesIO(b"x" * (api.MAX_BYTES + 1)))
    assert client.get("/qlib/baseline").status_code == 503
