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


def test_experiment_run_spec_draft_binds_template_dataset_split_and_parameters(
    tmp_path,
) -> None:
    manifest = _write_dataset(tmp_path)
    context = bootstrap_context()
    context.historical_manifest_path = manifest
    api = TestClient(create_app(context, ApiAuthConfig()))
    backtest = {
        "start": "2026-02-11",
        "end": "2026-03-21",
        "factor": "momentum",
        "horizons": [2, 3],
        "top_k": 1,
        "rebalance_every": 5,
        "reserve": "0.05",
        "trend_window": None,
        "volatility_window": None,
        "target_annual_volatility": None,
        "initial_cash": "1000000",
        "transaction_cost_rate": "0.001",
        "slippage_rate": "0.0005",
    }
    payload = {
        "template_id": "etf-momentum-rotation-v1",
        "experiment_id": "etf-momentum-rotation-replay-001",
        "git_commit": "a" * 40,
        "split": {
            "train_start": "2026-01-01",
            "train_end": "2026-01-20",
            "validation_start": "2026-01-21",
            "validation_end": "2026-02-10",
            "test_start": "2026-02-11",
            "test_end": "2026-03-21",
        },
        "backtest": backtest,
        "seed": 7,
    }

    response = api.post("/experiment-run-spec-draft", json=payload)

    assert response.status_code == 200
    draft = response.json()
    assert draft["classification"] == (
        "READ_ONLY_COMPLETE_EXPERIMENT_RUN_SPEC_DRAFT_NOT_PERSISTED"
    )
    assert draft["draft_status"] == "HASHED_COMPLETE_FIELDS_NOT_IMMUTABLY_STORED"
    assert len(draft["spec_hash"]) == 64
    assert set(draft["spec"]) == {
        "experiment_id",
        "strategy_id",
        "git_commit",
        "data_version",
        "universe",
        "factor_set",
        "label",
        "split",
        "model",
        "parameters",
        "cost_model_id",
        "transaction_cost",
        "seed",
    }
    assert draft["spec"]["strategy_id"] == "etf-momentum-rotation-v1"
    assert draft["spec"]["model"] == (
        "qtrade.research.multi_asset.MomentumAllocation"
    )
    assert draft["spec"]["universe"] == ["510300", "510500"]
    assert draft["spec"]["factor_set"] == ["momentum_2", "momentum_3"]
    assert draft["data_checksum"] == json.loads(manifest.read_text())["manifest"][
        "checksum"
    ]
    assert draft["gates"] == {
        "complete_experiment_run_spec_fields": True,
        "verified_dataset_identity_bound": True,
        "implemented_template_bound": True,
        "chronological_non_overlapping_split": True,
        "immutable_artifact_persisted": False,
        "independent_leakage_review_passed": False,
        "fresh_unseen_test_evidence": False,
    }
    assert draft["creates_immutable_experiment"] is False
    assert draft["registry_write"] is False
    assert draft["paper_runtime_start"] is False
    assert draft["live_runtime_start"] is False
    assert draft["state_mutation"] is False
    assert draft["external_order_transmission"] is False

    repeated = api.post("/experiment-run-spec-draft", json=payload).json()
    changed = api.post(
        "/experiment-run-spec-draft", json={**payload, "seed": 8}
    ).json()
    assert repeated["spec_hash"] == draft["spec_hash"]
    assert changed["spec_hash"] != draft["spec_hash"]

    bound_response = api.post("/experiment-run-spec-backtest", json=payload)
    assert bound_response.status_code == 200
    bound = bound_response.json()
    assert bound["classification"] == (
        "READ_ONLY_HASH_BOUND_EXPERIMENT_PREVIEW_NOT_PERSISTED"
    )
    assert bound["spec_hash"] == draft["spec_hash"]
    assert bound["preview_hash"] == bound["result"]["preview_hash"]
    assert len(bound["binding_hash"]) == 64
    assert bound["result"]["data_version"] == draft["spec"]["data_version"]
    assert bound["result"]["data_checksum"] == draft["data_checksum"]
    assert bound["result"]["parameters"]["factor"] == "momentum"
    assert bound["result"]["parameters"]["trend_window"] is None
    assert bound["gates"] == {
        "single_request_spec_result_binding": True,
        "spec_hash_recomputed_before_run": True,
        "verified_dataset_reloaded_before_run": True,
        "preview_hash_bound_to_spec_hash": True,
        "immutable_artifact_persisted": False,
        "independent_leakage_review_passed": False,
        "fresh_unseen_test_evidence": False,
    }
    assert bound["creates_immutable_experiment"] is False
    assert bound["registry_write"] is False
    assert bound["paper_runtime_start"] is False
    assert bound["live_runtime_start"] is False
    assert bound["state_mutation"] is False
    assert bound["external_order_transmission"] is False
    repeated_bound = api.post("/experiment-run-spec-backtest", json=payload).json()
    changed_bound = api.post(
        "/experiment-run-spec-backtest", json={**payload, "seed": 8}
    ).json()
    assert repeated_bound["binding_hash"] == bound["binding_hash"]
    assert changed_bound["preview_hash"] == bound["preview_hash"]
    assert changed_bound["spec_hash"] != bound["spec_hash"]
    assert changed_bound["binding_hash"] != bound["binding_hash"]

    mismatched_window = {
        **payload,
        "backtest": {**backtest, "start": "2026-02-12"},
    }
    assert api.post(
        "/experiment-run-spec-draft", json=mismatched_window
    ).status_code == 422
    filtered_rotation = {
        **payload,
        "backtest": {
            **backtest,
            "trend_window": 3,
        },
    }
    assert api.post(
        "/experiment-run-spec-draft", json=filtered_rotation
    ).status_code == 422
