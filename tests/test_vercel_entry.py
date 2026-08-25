from fastapi.testclient import TestClient

from qtrade.api.vercel import app


def test_vercel_entry_is_read_only_and_loads_packaged_evidence() -> None:
    client = TestClient(app)

    assert client.get("/api/health").status_code == 200
    market = client.get("/api/market")
    assert market.status_code == 200
    assert market.json()["historical"]["bar_count"] == 28_770
    assert len(client.get("/api/historical-bars/latest").json()) == 30
    assert client.get("/api/factor-search").json()["promotion"] == "NOT_ELIGIBLE"
    assert client.get("/api/factor-robustness").json()["promotion"] == "NOT_ELIGIBLE"
    assert client.get("/api/stock-sector-transfer").json()["promotion"] == "NOT_ELIGIBLE"
    preview = client.post(
        "/api/backtest-preview",
        json={"start": "2025-01-01", "end": "2025-12-31"},
    )
    assert preview.status_code == 200
    assert preview.json()["promotion"] == "NOT_ELIGIBLE"

    blocked = client.post("/api/system/kill", json={"reason": "must stay disabled"})
    assert blocked.status_code == 503
    assert client.get("/api/system").json()["mode"] == "PAPER"
