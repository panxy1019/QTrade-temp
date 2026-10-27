from __future__ import annotations

import io
import json
from datetime import UTC, date, datetime, timedelta
from hashlib import sha256
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from qtrade.api import current_datasets as current
from qtrade.data.storage import _BAR_PARQUET_SCHEMA
from qtrade.market_data.akshare import SHANGHAI
from qtrade.market_data.sina_minute import SINA_FIVE_MINUTE_SESSION_TIMES
from qtrade.monitoring.data_freshness import AUTHORITY, signed


def test_mutable_pointer_refreshes_upstream_cache_but_parquet_is_immutable(monkeypatch):
    requested = []

    def open_request(request, timeout):
        requested.append(request.full_url)
        return io.BytesIO(b"{}")

    monkeypatch.setattr(current, "urlopen", open_request)
    assert current.download("current.json", 100) == b"{}"
    assert requested[-1].startswith(current.ROOT + "current.json?v=")
    current.download("datasets/" + "a" * 64 + ".parquet", 100)
    assert requested[-1] == current.ROOT + "datasets/" + "a" * 64 + ".parquet"


@pytest.fixture
def publication(monkeypatch):
    version = "current-minute-test"
    rows = []
    for day in (date(2026, 10, 22), date(2026, 10, 23)):
        for clock in SINA_FIVE_MINUTE_SESSION_TIMES:
            rows.append(
                {
                    "symbol": "600036",
                    "exchange": "SSE",
                    "timestamp": datetime.combine(day, clock, SHANGHAI).astimezone(UTC).isoformat(),
                    "trading_day": day,
                    "open": "10",
                    "high": "10.1",
                    "low": "9.9",
                    "close": "10",
                    "volume": "100000",
                    "data_version": version,
                }
            )
    sink = pa.BufferOutputStream()
    pq.write_table(pa.Table.from_pylist(rows, schema=_BAR_PARQUET_SCHEMA), sink)
    content = sink.getvalue().to_pybytes()
    checksum = sha256(content).hexdigest()
    row = {
        "data_version": version,
        "checksum": checksum,
        "bar_count": 96,
        "universe": {"symbols": ["600036"], "point_in_time": False},
        "latest_by_symbol": {"600036": "2026-10-23"},
        "minimum_latest_day": "2026-10-23",
        "quality_status": "PASSED",
        "snapshot_readiness": False,
        "real_data_paper_readiness": False,
        "trading_days": ["2026-10-22", "2026-10-23"],
        "interval": "5m",
        "instruments": [
            {"symbol": "600036", "name": "招商银行", "sector_code": "bank", "sector_name": "银行"}
        ],
    }
    value = signed(
        {
            **AUTHORITY,
            "schema_version": "qtrade.current-datasets.v1",
            "published_at": datetime.now(UTC).isoformat(),
            "calendar": json.loads(Path("configs/observer_calendar_2026.json").read_text()),
            "daily": {**row},
            "minute": {**row},
        }
    )
    current.parquet.cache_clear()
    monkeypatch.setattr(
        current,
        "download",
        lambda name, limit: json.dumps(value).encode() if name == "current.json" else content,
    )
    app = FastAPI()
    app.include_router(current.router)
    yield TestClient(app), value, content
    current.parquet.cache_clear()


def test_chart_uses_current_checksum_and_latest_session(publication):
    client, value, _ = publication
    response = client.get(
        "/current/bars/minute/600036", params={"version": value["minute"]["data_version"]}
    )
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    body = response.json()
    assert body["dataset_checksum"] == value["minute"]["checksum"]
    assert len(body["bars"]) == 48
    assert {b["trading_day"] for b in body["bars"]} == {"2026-10-23"}


def test_minute_preview_computes_through_latest_day_and_rejects_version_change(publication):
    client, value, _ = publication
    payload = {"symbol": "600036", "start": "2026-10-22", "end": "2026-10-23"}
    response = client.post(
        "/current/single-stock-five-minute-backtest",
        json=payload,
        headers={"X-QTrade-Data-Version": value["minute"]["data_version"]},
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["selected_trading_days"][-1] == "2026-10-23"
    assert result["curve"][-1]["session"].startswith("2026-10-23")
    assert result["external_order_transmission"] is False
    assert "SOURCE_NATIVE_ADJUSTMENT_NOT_CERTIFIED" in result["promotion_reasons"]
    assert (
        client.post(
            "/current/single-stock-five-minute-backtest",
            json=payload,
            headers={"X-QTrade-Data-Version": "old-august"},
        ).status_code
        == 409
    )


def test_corrupt_publication_and_parquet_never_fall_back(publication, monkeypatch):
    client, value, content = publication
    value["minute"]["bar_count"] = 95
    assert client.get("/current/datasets").status_code == 503
    value["minute"]["bar_count"] = 96
    monkeypatch.setattr(
        current,
        "download",
        lambda name, limit: (
            json.dumps(value).encode() if name == "current.json" else content + b"corrupt"
        ),
    )
    assert client.get("/current/bars/minute/600036").status_code == 503


def test_stale_publication_cannot_remain_green(publication):
    _, value, _ = publication
    value["published_at"] = (datetime.now(UTC) - timedelta(hours=1)).isoformat()
    value = signed({k: v for k, v in value.items() if k != "evidence_sha256"})
    assert current.validate_catalog(value)["minute"]["freshness"] == "STALE"


@pytest.mark.parametrize("route", [
    "/backtest-preview", "/experiment-run-spec-draft", "/experiment-run-spec-backtest",
])
def test_current_daily_execution_is_retired_even_without_available_data(monkeypatch, route):
    def forbidden(*args, **kwargs):
        pytest.fail("retired daily route attempted source resolution or download")

    monkeypatch.setattr(current, "resolve", forbidden)
    monkeypatch.setattr(current, "catalog", forbidden)
    monkeypatch.setattr(current, "download", forbidden)
    app = FastAPI()
    app.include_router(current.router)
    client = TestClient(app)
    for payload in ({}, {"start": "2026-01-01", "end": "2026-10-23"}):
        for headers in ({}, {"X-QTrade-Data-Version": "old-or-unknown-version"}):
            response = client.post("/current" + route, json=payload, headers=headers)
            assert response.status_code == 410
            assert response.json()["detail"]["code"] == "LEGACY_DAILY_RESEARCH_RETIRED"
    assert client.post("/current" + route, content=b"not-json").status_code == 410
