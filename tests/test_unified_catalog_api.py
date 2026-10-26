from copy import deepcopy
from datetime import UTC, datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from qtrade.api import unified_data as api
from qtrade.instruments.observed import build_observed_master
from qtrade.monitoring.data_freshness import AUTHORITY, signed


@pytest.fixture
def publication():
    now = datetime.now(UTC)
    master = build_observed_master(
        vendor_rows=[{"code": "sh.600036", "code_name": "招商银行", "type": "1", "status": "1"},
                     {"code": "sz.000002", "type": "1", "status": "0"}],
        vendor_sha256="a" * 64, source_groups=[{
            "source_id": "daily1000", "role": "legacy", "sha256": "b" * 64,
            "rows": [{"symbol": "600036", "security_type": "STOCK"}],
        }], observed_at=now, target_count=1,
    )
    for row in master["securities"]:
        row["frequencies"] = {freq: {"planned": row["in_target"],
                                     "state": "PLANNED" if row["in_target"] else "NOT_PLANNED"}
                              for freq in ("day", "5min")}
    master = signed({**{k: v for k, v in master.items() if k != "evidence_sha256"},
                     "frequency_counts": {freq: {"PLANNED": 1} for freq in ("day", "5min")}})
    return signed({**AUTHORITY, "schema_version": "qtrade.unified-catalog.v1",
                   "built_at": now.isoformat(), "master": master})


def test_unified_query_is_bounded_filtered_readonly_and_preserves_inactive(
    publication, monkeypatch,
):
    api._load.cache_clear()
    monkeypatch.setattr(api, "download", lambda name, limit: __import__("json").dumps(
        publication).encode())
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        summary = client.get('/unified/summary')
        assert summary.status_code == 200 and summary.headers["cache-control"] == "no-store"
        assert "securities" not in summary.json()["master"]
        rows = client.get('/unified/securities?query=招商&frequency=5min').json()
        assert rows["total"] == 1 and rows["rows"][0]["instrument_id"] == "SSE.600036"
        assert rows["read_only"] and rows["point_in_time"] is False
        assert client.get('/unified/securities?target_only=false').json()["total"] == 2
        assert client.get('/unified/securities?quality=QUARANTINED').json()["total"] == 0
        assert client.get('/unified/securities?limit=101').status_code == 422
        assert client.get('/unified/securities?offset=-1').status_code == 422
        assert client.get('/unified/securities?frequency=1min').status_code == 422
        assert client.post('/unified/securities', json={}).status_code == 405
    api._load.cache_clear()


@pytest.mark.parametrize("change", ["hash", "authority", "identity", "count", "available", "clock"])
def test_unified_catalog_rejects_inconsistent_evidence_even_if_rehashed(publication, change):
    value = deepcopy(publication)
    row = value["master"]["securities"][0]
    if change == "hash":
        value["master"]["target_actual"] += 1
        with pytest.raises(ValueError):
            api.validate_publication(value)
        return
    if change == "authority":
        value["point_in_time"] = True
    if change == "identity":
        row["exchange"] = "BAD"
    if change == "count":
        value["master"]["frequency_counts"]["day"]["PLANNED"] = 2
    if change == "available":
        row["available_at"] = "2016-01-01"
    if change == "clock":
        value["built_at"] = "2099-01-01T00:00:00+00:00"
    value["master"] = signed({k: v for k, v in value["master"].items() if k != "evidence_sha256"})
    value = signed({k: v for k, v in value.items() if k != "evidence_sha256"})
    with pytest.raises(ValueError):
        api.validate_publication(value)
