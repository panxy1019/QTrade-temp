import json
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
from fastapi.testclient import TestClient

from qtrade.api import ApiAuthConfig, create_app
from qtrade.api.app import bootstrap_context
from qtrade.data import Bar
from qtrade.research import BacktestPreviewSpec, run_backtest_preview


def _bars() -> list[Bar]:
    records: list[Bar] = []
    start = date(2026, 1, 1)
    for index in range(80):
        trading_day = start + timedelta(days=index)
        for symbol, close in (
            ("510300", Decimal("10") + Decimal(index) / 10),
            ("510500", Decimal("20") - Decimal(index) / 20),
        ):
            records.append(
                Bar(
                    symbol=symbol,
                    exchange="SSE",
                    timestamp=datetime.combine(
                        trading_day, datetime.min.time(), tzinfo=UTC
                    ),
                    trading_day=trading_day,
                    open=close,
                    high=close,
                    low=close,
                    close=close,
                    volume=Decimal("100000"),
                )
            )
    return records


def _spec(
    *, reserve: str = "0.05", factor: str = "momentum", horizons: tuple[int, ...] = (2, 3)
) -> BacktestPreviewSpec:
    return BacktestPreviewSpec(
        start=date(2026, 1, 1),
        end=date(2026, 3, 21),
        factor=factor,
        horizons=horizons,
        top_k=1,
        rebalance_every=5,
        reserve=Decimal(reserve),
        trend_window=3,
        volatility_window=3,
        target_annual_volatility=Decimal("0.15"),
        transaction_cost_rate=Decimal("0.001"),
        slippage_rate=Decimal("0.0005"),
    )


def test_preview_is_deterministic_and_parameter_changes_are_distinct() -> None:
    checksum = "a" * 64
    first = run_backtest_preview(
        _bars(), data_version="test-bars-v1", data_checksum=checksum, spec=_spec()
    )
    repeated = run_backtest_preview(
        _bars(), data_version="test-bars-v1", data_checksum=checksum, spec=_spec()
    )
    changed = run_backtest_preview(
        _bars(),
        data_version="test-bars-v1",
        data_checksum=checksum,
        spec=_spec(reserve="0.20"),
    )

    assert first == repeated
    assert first["preview_hash"] != changed["preview_hash"]
    assert first["classification"] == "AD_HOC_PREVIEW_CURRENT_UNIVERSE_NOT_PIT"
    assert first["promotion"] == "NOT_ELIGIBLE"
    assert first["external_order_transmission"] is False
    assert first["data_checksum"] == checksum
    assert int(first["metrics"]["number_of_trades"]) > 0  # type: ignore[index]
    assert len(first["curve"]) == 80  # type: ignore[arg-type]


def test_preview_supports_only_bounded_factor_families() -> None:
    checksum = "a" * 64
    previews = {
        factor: run_backtest_preview(
            _bars(),
            data_version="test-bars-v1",
            data_checksum=checksum,
            spec=_spec(factor=factor),
        )
        for factor in ("momentum", "reversal", "low_volatility")
    }

    assert {result["parameters"]["factor"] for result in previews.values()} == {  # type: ignore[index]
        "momentum",
        "reversal",
        "low_volatility",
    }
    assert len({result["preview_hash"] for result in previews.values()}) == 3
    with pytest.raises(ValueError, match="unsupported preview factor"):
        _spec(factor="future_leak")
    with pytest.raises(ValueError, match="low-volatility preview horizons"):
        _spec(factor="low_volatility", horizons=(1,))


def _write_dataset(tmp_path: Path) -> Path:
    records = _bars()
    target = tmp_path / "bars.parquet"
    pq.write_table(
        pa.table(
            {
                "symbol": [bar.symbol for bar in records],
                "exchange": [bar.exchange for bar in records],
                "timestamp": [bar.timestamp.isoformat() for bar in records],
                "trading_day": [bar.trading_day.isoformat() for bar in records],
                "open": [str(bar.open) for bar in records],
                "high": [str(bar.high) for bar in records],
                "low": [str(bar.low) for bar in records],
                "close": [str(bar.close) for bar in records],
                "volume": [str(bar.volume) for bar in records],
                "data_version": ["test-bars-v1"] * len(records),
            }
        ),
        target,
    )
    evidence = {
        "classification": "noncommercial_research_historical_only",
        "bar_count": len(records),
        "observed_start": records[0].timestamp.isoformat(),
        "observed_end": records[-1].timestamp.isoformat(),
        "snapshot_readiness": False,
        "real_data_paper_readiness": False,
        "manifest": {
            "data_version": "test-bars-v1",
            "checksum": sha256(target.read_bytes()).hexdigest(),
            "quality_status": "PASSED",
        },
        "universe": {
            "provider": "fixture",
            "provider_version": "1",
            "point_in_time": False,
            "symbols": ["510300", "510500"],
        },
    }
    evidence["evidence_sha256"] = sha256(
        json.dumps(
            evidence,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    manifest = tmp_path / "manifest.json"
    manifest.write_text(json.dumps(evidence), encoding="utf-8")
    return manifest


def test_preview_endpoint_uses_only_verified_bounded_history(tmp_path) -> None:
    manifest = _write_dataset(tmp_path)
    context = bootstrap_context()
    context.historical_manifest_path = manifest
    api = TestClient(create_app(context, ApiAuthConfig()))
    payload = {
        "start": "2026-01-01",
        "end": "2026-03-21",
        "factor": "reversal",
        "horizons": [2, 3],
        "top_k": 1,
        "rebalance_every": 5,
        "trend_window": 3,
        "volatility_window": 3,
        "target_annual_volatility": "0.15",
    }

    response = api.post("/backtest-preview", json=payload)
    assert response.status_code == 200
    assert response.json()["data_version"] == "test-bars-v1"
    assert response.json()["parameters"]["factor"] == "reversal"
    assert response.json()["execution_timing"] == "close decision, next open execution"

    invalid = api.post("/backtest-preview", json={**payload, "top_k": 11})
    assert invalid.status_code == 422
    unsupported = api.post("/backtest-preview", json={**payload, "factor": "future_leak"})
    assert unsupported.status_code == 422

    (tmp_path / "bars.parquet").write_bytes(b"tampered")
    assert api.post("/backtest-preview", json=payload).status_code == 409
