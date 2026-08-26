import json
from datetime import date
from hashlib import sha256
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
from fastapi.testclient import TestClient

from qtrade.api.app import (
    _read_verified_market_parquet_content,
    bootstrap_context,
    create_app,
)

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MANIFEST_PATH = (
    PROJECT_ROOT / "deployment/evidence/long-five-minute-market-data/manifest.json"
)


def _client() -> TestClient:
    context = bootstrap_context()
    context.long_five_minute_manifest_path = MANIFEST_PATH
    return TestClient(create_app(context))


def test_single_stock_five_minute_backtest_is_causal_and_read_only() -> None:
    response = _client().post(
        "/single-stock-five-minute-backtest",
        json={
            "symbol": "600036",
            "start": "2026-08-21",
            "end": "2026-08-25",
        },
    )

    assert response.status_code == 200
    payload = response.json()
    assert payload["classification"] == (
        "AD_HOC_SINGLE_STOCK_FIVE_MINUTE_BACKTEST_CURRENT_NON_PIT"
    )
    assert payload["promotion"] == "NOT_ELIGIBLE"
    assert payload["symbol"] == "600036"
    assert payload["instrument"]["name"] == "招商银行"
    assert payload["verified_universe_symbol_count"] == 75
    assert payload["available_session_count"] == 368
    assert payload["available_start"] == "2025-01-02"
    assert payload["available_end"] == "2026-08-25"
    assert payload["maximum_selectable_session_count"] == 120
    assert payload["selected_trading_days"] == [
        "2026-08-21",
        "2026-08-24",
        "2026-08-25",
    ]
    assert payload["selected_session_count"] == 3
    assert payload["selected_bar_count"] == 144
    assert payload["warmup_session_count"] == 365
    assert payload["warmup_bar_count"] == 17_520
    assert payload["metrics"]["base"]["point_count"] == 144
    assert payload["metrics"]["overlay"]["point_count"] == 144
    assert payload["execution"]["observation_count"] == 144
    assert len(payload["daily"]) == 3
    assert len(payload["curve"]) == 144
    assert payload["curve"][0]["session"].startswith("2026-08-21T09:35:00")
    assert payload["curve"][-1]["session"].startswith("2026-08-25T15:00:00")
    assert payload["causal_contract"] == {
        "signal_observed_at": "FIVE_MINUTE_BAR_CLOSE_T",
        "exposure_change_at": "NEXT_FIVE_MINUTE_BAR_OPEN",
        "same_bar_signal_execution": False,
        "reduction_only": True,
        "maximum_exposure": "1",
        "prior_history_used_only_as_causal_warmup": True,
    }
    assert payload["point_in_time"] is False
    assert payload["realtime_readiness"] is False
    assert payload["real_data_paper_readiness"] is False
    assert payload["state_mutation"] is False
    assert payload["external_order_transmission"] is False
    preview_hash = payload.pop("preview_hash")
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    assert preview_hash == sha256(canonical.encode("utf-8")).hexdigest()


def test_single_stock_five_minute_backtest_rejects_invalid_scope() -> None:
    client = _client()
    cases = [
        (
            {"symbol": "510300", "start": "2026-08-21", "end": "2026-08-25"},
            "outside the verified 75-stock universe",
        ),
        (
            {"symbol": "600036", "start": "2026-08-22", "end": "2026-08-25"},
            "must be verified trading days",
        ),
        (
            {"symbol": "600036", "start": "2026-08-25", "end": "2026-08-25"},
            "start must precede end",
        ),
        (
            {"symbol": "600036", "start": "2025-01-02", "end": "2025-07-30"},
            "limited to 120 sessions",
        ),
        (
            {
                "symbol": "600036",
                "start": "2026-08-21",
                "end": "2026-08-25",
                "short_window": 48,
                "long_window": 12,
            },
            "must satisfy 1 < short < long",
        ),
    ]
    for request, detail in cases:
        response = client.post("/single-stock-five-minute-backtest", json=request)
        assert response.status_code == 422
        assert detail in response.json()["detail"]


def test_single_stock_five_minute_backtest_fails_closed_without_data() -> None:
    response = TestClient(create_app(bootstrap_context())).post(
        "/single-stock-five-minute-backtest",
        json={
            "symbol": "600036",
            "start": "2026-08-21",
            "end": "2026-08-25",
        },
    )

    assert response.status_code == 409
    assert response.json()["detail"] == (
        "verified long five-minute dataset unavailable"
    )


def test_market_parquet_reader_rejects_resigned_schema_drift(tmp_path: Path) -> None:
    row = {
        "symbol": ["600036"],
        "exchange": ["SSE"],
        "timestamp": ["2026-08-25T01:35:00+00:00"],
        "trading_day": [date(2026, 8, 25)],
        "open": ["1"],
        "high": ["1"],
        "low": ["1"],
        "close": ["1"],
        "volume": ["1"],
        "data_version": ["test-v1"],
    }
    target = tmp_path / "bars.parquet"
    valid = pa.table(row)
    pq.write_table(valid, target)
    valid_content = target.read_bytes()
    assert _read_verified_market_parquet_content(
        tmp_path / "manifest.json",
        expected_checksum=sha256(valid_content).hexdigest(),
        expected_bar_count=1,
    ) == valid_content

    drifted = valid.append_column("unexpected", pa.array(["resigned-drift"]))
    pq.write_table(drifted, target)
    drifted_content = target.read_bytes()
    assert _read_verified_market_parquet_content(
        tmp_path / "manifest.json",
        expected_checksum=sha256(drifted_content).hexdigest(),
        expected_bar_count=1,
    ) is None
