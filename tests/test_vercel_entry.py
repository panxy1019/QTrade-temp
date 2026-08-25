from urllib.parse import quote

from fastapi.testclient import TestClient

from qtrade.api.vercel import app


def _api(path: str) -> str:
    return f"/api?route={quote(path, safe='')}"


def test_vercel_entry_is_read_only_and_loads_packaged_evidence() -> None:
    client = TestClient(app)

    assert client.get(_api("/health")).status_code == 200
    market = client.get(_api("/market"))
    assert market.status_code == 200
    assert market.json()["historical"]["bar_count"] == 28_770
    assert len(client.get(_api("/historical-bars/latest")).json()) == 30
    assert client.get(_api("/factor-search")).json()["promotion"] == "NOT_ELIGIBLE"
    assert client.get(_api("/factor-robustness")).json()["promotion"] == "NOT_ELIGIBLE"
    assert client.get(_api("/stock-sector-transfer")).json()["promotion"] == "NOT_ELIGIBLE"
    assert len(client.get(_api("/strategies")).json()) == 11
    assert len(client.get(_api("/experiments")).json()["experiments"]) == 11
    assert len(client.get(_api("/walk-forward")).json()["result"]["folds"]) == 3
    assert len(client.get(_api("/robustness")).json()["result"]["scenario_metrics"]) == 14
    analytics = client.get(_api("/backtest-analytics")).json()
    assert analytics["leader"]["experiment_id"].endswith("momentum-vol-slow-20260812-v4")
    assert len(analytics["curve"]) == 632
    preview = client.post(
        _api("/backtest-preview"),
        json={"start": "2025-01-01", "end": "2025-12-31"},
    )
    assert preview.status_code == 200
    assert preview.json()["promotion"] == "NOT_ELIGIBLE"

    blocked = client.post(_api("/system/kill"), json={"reason": "must stay disabled"})
    assert blocked.status_code == 503
    assert client.get(_api("/system")).json()["mode"] == "PAPER"


def test_vercel_entry_caches_only_anonymous_successful_gets() -> None:
    client = TestClient(app)

    public_get = client.get(_api("/market"))
    assert public_get.headers["cache-control"] == "public, max-age=15"
    assert public_get.headers["vercel-cdn-cache-control"] == (
        "public, max-age=60, stale-while-revalidate=300"
    )

    authorized_get = client.get(
        _api("/market"), headers={"Authorization": "Bearer ignored-read-token"}
    )
    assert "cache-control" not in authorized_get.headers
    preview = client.post(
        _api("/backtest-preview"),
        json={"start": "2025-01-01", "end": "2025-12-31"},
    )
    assert "cache-control" not in preview.headers
    blocked = client.post(_api("/system/kill"), json={"reason": "must stay disabled"})
    assert blocked.status_code == 503
    assert "cache-control" not in blocked.headers
