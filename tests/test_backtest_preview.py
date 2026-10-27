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
    metrics = first["metrics"]
    assert Decimal(metrics["excess_return"]) == (  # type: ignore[index]
        Decimal(metrics["total_return"]) - Decimal(metrics["benchmark_return"])
    )
    assert "information_ratio" in metrics  # type: ignore[operator]
    benchmark = first["benchmark"]
    assert benchmark["strategy_id"] == "equal-weight-buy-hold-benchmark-v1"  # type: ignore[index]
    assert benchmark["decision_timing"] == (  # type: ignore[index]
        "first session close, next open execution"
    )
    assert benchmark["first_decision_known_symbols_only"] is True  # type: ignore[index]
    assert benchmark["current_universe_survivorship_bias"] is True  # type: ignore[index]
    assert benchmark["reserve"] == first["parameters"]["reserve"]  # type: ignore[index]
    assert benchmark["transaction_cost_rate"] == "0.001"  # type: ignore[index]
    assert benchmark["slippage_rate"] == "0.0005"  # type: ignore[index]
    assert benchmark["execution_timing"] == first["execution_timing"]  # type: ignore[index]
    curve = first["curve"]
    assert all(  # type: ignore[arg-type]
        set(point) == {
            "session",
            "equity",
            "drawdown",
            "benchmark_equity",
            "benchmark_drawdown",
            "excess_return",
        }
        for point in curve
    )
    assert Decimal(curve[-1]["excess_return"]) == Decimal(  # type: ignore[index]
        metrics["excess_return"]  # type: ignore[index]
    )


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
                "trading_day": [bar.trading_day for bar in records],
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


@pytest.mark.parametrize("route", [
    "/backtest-preview", "/experiment-run-spec-draft", "/experiment-run-spec-backtest",
])
def test_legacy_daily_routes_retire_before_loading_any_dataset(tmp_path, monkeypatch, route):
    from importlib import import_module

    module = import_module("qtrade.api.app")
    manifest = _write_dataset(tmp_path)
    context = bootstrap_context()
    context.historical_manifest_path = manifest

    def forbidden(*args, **kwargs):
        pytest.fail("retired route attempted dataset access or old execution")

    for name in ("_load_verified_market_dataset", "_load_backtest_preview_bars",
                 "run_backtest_preview", "_experiment_run_spec_draft",
                 "_hash_bound_experiment_preview"):
        monkeypatch.setattr(module, name, forbidden)
    client = TestClient(create_app(context, ApiAuthConfig()))
    backtest = {
        "start": "2026-02-11", "end": "2026-03-21", "factor": "momentum",
        "horizons": [2, 3], "top_k": 1, "trend_window": None,
        "volatility_window": None, "target_annual_volatility": None,
    }
    spec = {
        "template_id": "etf-momentum-rotation-v1", "experiment_id": "retired-replay",
        "git_commit": "a" * 40, "seed": 7, "backtest": backtest,
        "split": {
            "train_start": "2026-01-01", "train_end": "2026-01-20",
            "validation_start": "2026-01-21", "validation_end": "2026-02-10",
            "test_start": "2026-02-11", "test_end": "2026-03-21",
        },
    }
    module.BacktestPreviewRequest(**backtest)
    module.ExperimentRunSpecDraftRequest(**spec)
    valid = backtest if route == "/backtest-preview" else spec
    original = manifest.read_bytes()
    for request in ({"json": valid}, {"json": {}}, {"content": b"invalid json"}, {}):
        response = client.post(route, **request)
        assert response.status_code == 410
        assert response.json()["detail"]["code"] == "LEGACY_DAILY_RESEARCH_RETIRED"
        assert response.json()["detail"]["state_mutation"] is False
    manifest.unlink()
    assert client.post(route, json=valid).status_code == 410
    assert original and not manifest.exists()


def test_retired_routes_keep_the_original_read_role_dependency(monkeypatch):
    from importlib import import_module

    from fastapi import HTTPException

    module = import_module("qtrade.api.app")
    calls = []

    def role(required, auth):
        def deny():
            calls.append(required.value)
            raise HTTPException(403, "test-only read authorization denied")
        return deny

    monkeypatch.setattr(module, "_require_role", role)
    client = TestClient(create_app(bootstrap_context(), ApiAuthConfig()))
    for route in ("/backtest-preview", "/experiment-run-spec-draft", "/experiment-run-spec-backtest"):
        assert client.post(route, content=b"invalid").status_code == 403
    assert client.get("/strategy-templates").status_code == 403
    assert calls == ["read"] * 4
