from importlib import import_module
from urllib.parse import quote

from fastapi.testclient import TestClient

from qtrade.api.vercel import app


def _api(path: str) -> str:
    return f"/api?route={quote(path, safe='')}"


def test_minute_research_snapshot_reuses_one_verified_long_dataset(
    monkeypatch,
) -> None:
    api_module = import_module("qtrade.api.app")
    original = api_module._open_verified_market_parquet
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(api_module, "_open_verified_market_parquet", counted)
    payload = TestClient(app).get(_api("/minute-research-snapshot")).json()

    assert calls == 1
    assert payload["classification"] == "VERIFIED_MINUTE_RESEARCH_READ_SNAPSHOT"
    assert payload["unavailable_sections"] == []
    assert payload["longFiveMinuteResearch"]["minute_dataset"]["bar_count"] == (
        1_324_800
    )
    assert payload["reservedTailDiagnostic"]["test_session_count"] == 8
    assert payload["developmentPolicySearch"]["candidate_count"] == 4
    assert payload["forwardObservation"]["future_session_count"] == 0
    assert payload["forwardSourceCoverage"]["ready_symbol_count"] == 74
    assert payload["forwardSourceCoverage"]["incomplete_symbol_count"] == 1
    assert payload["forwardSourceCoverage"]["enters_forward_metrics"] is False
    assert payload["forwardSourceCoverageHistory"]["checkpoint_count"] == 5
    assert payload["forwardSourceCoverageHistory"]["all_checkpoints_monotonic"] is True
    assert payload["intradayObservation"]["symbol_count"] == 15
    assert payload["intradayBreadth"]["symbol_count"] == 75
    assert payload["intradayContinuity"]["all_checkpoints_monotonic"] is True
    assert payload["publicMinuteAudit"]["all_valid"] is True
    assert payload["realtime_readiness"] is False
    assert payload["real_data_paper_readiness"] is False
    assert payload["external_order_transmission"] is False
