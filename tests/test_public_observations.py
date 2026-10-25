import copy
import json
from datetime import datetime

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from qtrade.api import public_observations as public
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts.run_data_observer import REPO


def at(value):
    return datetime.fromisoformat(value)


@pytest.fixture
def snapshot():
    latest = "2026-10-23T15:00:00+08:00"
    datasets = {}
    for kind, count in (("daily", 30), ("minute", 75)):
        symbols = [f"{index:06d}" for index in range(count)]
        report = signed({
            **AUTHORITY, "kind": kind, "completed_at": "2026-10-25T08:00:00+00:00",
            "required_symbol_count": count, "bar_count": count,
            "latest_by_symbol": dict.fromkeys(symbols, latest),
            "manifest": {"data_version": kind, "checksum": "a" * 64},
        })
        datasets[kind] = {"report": report, "latest_bars": [{
            "symbol": symbol, "exchange": "SSE", "timestamp": latest,
            "trading_day": "2026-10-23", "open": "1", "high": "1",
            "low": "1", "close": "1", "volume": "100",
        } for symbol in symbols]}
    return signed({
        **AUTHORITY, "schema_version": public.SCHEMA,
        "published_at": "2026-10-25T08:00:10+00:00",
        "calendar": json.loads((REPO / "configs/observer_calendar_2026.json").read_text()),
        "observer": {"heartbeat_at": "2026-10-25T08:00:05+00:00", "worker_alive": True,
                     "streams": {kind: {"last_attempt": {"outcome": "SUCCEEDED"}}
                                 for kind in datasets}},
        "datasets": datasets, "experiments": [],
    })


def resign(value):
    return signed({k: v for k, v in value.items() if k != "evidence_sha256"})


def test_publication_recomputes_health_and_keeps_execution_disabled(snapshot):
    result = public.validate_snapshot(snapshot, now=at("2026-10-25T08:01:00+00:00"))
    assert result["healthy"] is True
    assert result["read_only"] is True
    assert result["external_order_transmission"] is False
    assert result["streams"]["minute"]["observed_symbols"] == 75


def test_old_green_snapshot_cannot_stay_green(snapshot):
    result = public.validate_snapshot(snapshot, now=at("2026-10-25T08:21:00+00:00"))
    assert result["healthy"] is False
    assert result["publication_status"] == "STALE"


def test_current_clock_requires_new_market_data(snapshot):
    value = copy.deepcopy(snapshot)
    value["published_at"] = "2026-10-26T07:30:10+00:00"
    value["observer"]["heartbeat_at"] = "2026-10-26T07:30:05+00:00"
    result = public.validate_snapshot(resign(value), now=at("2026-10-26T07:31:00+00:00"))
    assert result["healthy"] is False
    assert result["streams"]["minute"]["status"] == "STALE_OR_INCOMPLETE"


@pytest.mark.parametrize("change", ["checksum", "authority", "missing_bar", "clock", "price"])
def test_invalid_publication_fails_closed(snapshot, change):
    value = copy.deepcopy(snapshot)
    if change == "checksum":
        value["datasets"]["daily"]["latest_bars"][0]["close"] = "2"
    elif change == "authority":
        value["external_order_transmission"] = True
        value = resign(value)
    elif change == "missing_bar":
        value["datasets"]["daily"]["latest_bars"].pop()
        value = resign(value)
    elif change == "clock":
        value["published_at"] = "2026-10-26T08:00:00+00:00"
        value = resign(value)
    else:
        value["datasets"]["daily"]["latest_bars"][0]["close"] = "NaN"
        value = resign(value)
    with pytest.raises(ValueError):
        public.validate_snapshot(value, now=at("2026-10-25T08:01:00+00:00"))


def test_dead_collector_is_not_hidden_by_running_publisher(snapshot):
    snapshot["observer"]["worker_alive"] = False
    result = public.validate_snapshot(resign(snapshot), now=at("2026-10-25T08:01:00+00:00"))
    assert result["healthy"] is False


def test_failed_attempt_remains_visible_even_with_fresh_data(snapshot):
    snapshot["observer"]["streams"]["daily"]["last_attempt"]["outcome"] = "FAILED"
    result = public.validate_snapshot(resign(snapshot), now=at("2026-10-25T08:01:00+00:00"))
    assert result["healthy"] is False
    assert result["streams"]["daily"]["status"] == "COLLECTION_FAILED"


def test_api_is_read_only_and_missing_source_is_not_success(monkeypatch):
    app = FastAPI()
    app.include_router(public.router)
    client = TestClient(app)

    def unavailable():
        raise TimeoutError("private upstream response")

    monkeypatch.setattr(public, "fetch_snapshot", unavailable)
    assert client.get("/data-freshness").status_code == 503
    assert "private" not in client.get("/data-freshness").text
    assert client.post("/persisted-experiments", json={}).status_code == 405


def test_vercel_does_not_cache_health(monkeypatch, snapshot):
    from qtrade.api.vercel import app

    result = public.validate_snapshot(snapshot, now=at("2026-10-25T08:01:00+00:00"))
    monkeypatch.setattr(public, "fetch_snapshot", lambda: result)
    response = TestClient(app).get("/?route=/observations/health")
    assert response.status_code == 200
    assert response.headers["cache-control"] == "no-store"
    assert "vercel-cdn-cache-control" not in response.headers
