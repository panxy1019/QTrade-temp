import json
import sqlite3
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from threading import Event, Thread
from typing import Any

import pyarrow as pa  # type: ignore[import-untyped]
import pyarrow.parquet as pq  # type: ignore[import-untyped]
import pytest
from fastapi.testclient import TestClient

from qtrade.accounting import Ledger
from qtrade.api import ApiAuthConfig, AppContext, context_from_runtime, create_app
from qtrade.api.app import (
    _load_verified_backtest_analytics,
    _load_verified_experiment_catalog,
    _load_verified_factor_search_report,
    _load_verified_robustness_report,
    _minute_overlay_status,
    _paper_snapshot,
    _paper_trade_rows,
    _validated_artifact_manifest,
    _validated_backtest_positions,
    _validated_backtest_trades,
)
from qtrade.audit import AuditEvent, AuditLog
from qtrade.control import PlatformRuntime
from qtrade.domain import Instrument, OrderRequest, RuntimeMode, Side
from qtrade.execution import FeeSchedule
from qtrade.monitoring import MetricsRegistry
from qtrade.oms import OrderManager
from qtrade.reconciliation import Reconciler, ReconciliationResult
from qtrade.reporting import DailyPaperReport, DailyReportStore
from qtrade.risk import RiskContext, RiskEngine, RiskLimits
from qtrade.runtime import build_paper_runtime


def client() -> TestClient:
    audit = AuditLog()
    metrics = MetricsRegistry()
    ledger = Ledger(Decimal("100000"), audit)
    oms = OrderManager(audit)
    risk = RiskEngine(RiskLimits(Decimal("50000"), 10000, Decimal("100000")), audit)
    runtime = PlatformRuntime(RuntimeMode.SHADOW, risk, audit, metrics)
    runtime.attest_readiness(
        Reconciler().issue_attestation(
            ReconciliationResult(True, ()),
            gateway_connected=True,
            uncertain_fills=0,
            now=datetime.now(UTC),
            validity=timedelta(minutes=10),
        )
    )
    context = AppContext(runtime, metrics, oms, ledger, audit)
    return TestClient(create_app(context, ApiAuthConfig(control_token="control-test")))


def test_dashboard_and_health_make_shadow_mode_explicit():
    api = client()
    dashboard = api.get("/")
    assert dashboard.status_code == 200
    assert 'lang="zh-CN"' in dashboard.text
    assert api.get("/web/").status_code == 200
    response = api.get("/health")
    assert response.status_code == 200
    assert response.json()["runtime_mode"] == "SHADOW"


def test_deployment_provenance_exposes_only_allowlisted_vercel_identity(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("VERCEL_TARGET_ENV", "production")
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "a" * 40)
    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "main")
    monkeypatch.setenv("VERCEL_OIDC_TOKEN", "must-never-be-public")

    payload = client().get("/deployment").json()

    assert payload == {
        "classification": "PUBLIC_BUILD_PROVENANCE",
        "provider": "VERCEL",
        "status": "AVAILABLE",
        "target_environment": "production",
        "git_commit_sha": "a" * 40,
        "git_commit_ref": "main",
        "external_order_transmission": False,
    }
    assert "must-never-be-public" not in json.dumps(payload)


def test_deployment_provenance_rejects_invalid_commit_identity(monkeypatch):
    monkeypatch.setenv("VERCEL", "1")
    monkeypatch.setenv("VERCEL_TARGET_ENV", "production")
    monkeypatch.setenv("VERCEL_GIT_COMMIT_SHA", "not-a-git-sha")
    monkeypatch.setenv("VERCEL_GIT_COMMIT_REF", "main\nsecret")

    payload = client().get("/deployment").json()

    assert payload["status"] == "UNAVAILABLE"
    assert payload["git_commit_sha"] is None
    assert payload["git_commit_ref"] is None
    assert payload["external_order_transmission"] is False


def test_prometheus_metrics_export_is_numeric_bounded_and_read_only():
    api = client()
    response = api.get("/metrics/prometheus")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/plain; version=0.0.4")
    assert "# TYPE qtrade_orders_total counter" in response.text
    assert "qtrade_orders_total 0" in response.text
    assert "qtrade_gateway_connected 1" in response.text
    assert "qtrade_runtime_healthy 1" in response.text
    assert (
        'qtrade_runtime_info{mode="SHADOW",risk_state="NORMAL",'
        'reconciliation_status="OK"} 1'
    ) in response.text
    assert "strategy_heartbeats" not in response.text


def test_prometheus_metrics_fail_closed_on_non_finite_values():
    from qtrade.api.app import bootstrap_context

    context = bootstrap_context()
    context.metrics.market_data_lag_seconds = Decimal("NaN")
    response = TestClient(create_app(context, ApiAuthConfig())).get("/metrics/prometheus")

    assert response.status_code == 503
    assert response.json() == {"detail": "runtime metrics unavailable"}


def test_built_dashboard_assets_are_served_at_root_and_web_paths(tmp_path):
    assets = tmp_path / "assets"
    assets.mkdir()
    (assets / "app.js").write_text("export default true", encoding="utf-8")
    (tmp_path / "index.html").write_text(
        '<!doctype html><html lang="zh-CN"><body>'
        '<script type="module" src="/assets/app.js"></script></body></html>',
        encoding="utf-8",
    )
    audit = AuditLog()
    metrics = MetricsRegistry()
    ledger = Ledger(Decimal("100000"), audit)
    oms = OrderManager(audit)
    risk = RiskEngine(RiskLimits(Decimal("50000"), 10000, Decimal("100000")), audit)
    runtime = PlatformRuntime(RuntimeMode.PAPER, risk, audit, metrics)
    api = TestClient(
        create_app(
            AppContext(runtime, metrics, oms, ledger, audit),
            ApiAuthConfig(),
            web_dist_path=tmp_path,
        )
    )

    assert api.get("/").status_code == 200
    assert api.get("/assets/app.js").text == "export default true"
    assert api.get("/web/assets/app.js").text == "export default true"
    assert api.get("/api/health").status_code == 200
    assert api.get("/api/metrics/prometheus").status_code == 200


def test_bootstrap_runtime_fails_closed_until_readiness_is_verified():
    from qtrade.api.app import bootstrap_context

    runtime = bootstrap_context().runtime
    with pytest.raises(ValueError, match="readiness attestation"):
        runtime.start(actor="operator")


def test_read_role_cannot_control_runtime():
    response = client().post("/system/start", json={"reason": "test"})
    assert response.status_code == 403


def test_unconfigured_control_authentication_fails_closed():
    from qtrade.api.app import bootstrap_context

    api = TestClient(create_app(bootstrap_context(), ApiAuthConfig()))
    response = api.post(
        "/system/start",
        headers={"Authorization": "Bearer anything"},
        json={},
    )
    assert response.status_code == 503


def test_control_role_can_pause_and_kill_but_cannot_resume_halt():
    api = client()
    headers = {"Authorization": "Bearer control-test"}
    assert api.post("/system/pause", headers=headers, json={}).json()["risk_state"] == "PAUSED"
    assert api.post("/system/resume", headers=headers, json={}).json()["risk_state"] == "NORMAL"
    assert (
        api.post("/system/kill", headers=headers, json={"reason": "test"}).json()["risk_state"]
        == "HALTED"
    )
    assert api.post("/system/resume", headers=headers, json={}).status_code == 409


def test_control_events_are_auditable():
    api = client()
    headers = {"Authorization": "Bearer control-test"}
    api.post("/system/start", headers=headers, json={})
    events = api.get("/audit").json()
    assert events[-1]["event_type"] == "SYSTEM_START"


def test_audit_endpoint_reads_bounded_durable_history_after_restart(tmp_path):
    journal_path = tmp_path / "runtime.db"
    first = build_paper_runtime(
        mode=RuntimeMode.PAPER,
        opening_cash=Decimal("100000"),
        limits=RiskLimits(Decimal("50000"), 10000, Decimal("100000")),
        fees=FeeSchedule("test", Decimal("0"), Decimal("0")),
        journal_path=journal_path,
    )
    first.audit.record(
        AuditEvent(
            event_type="DURABLE_TEST",
            service="test",
            message="persisted",
        )
    )
    restarted = build_paper_runtime(
        mode=RuntimeMode.PAPER,
        opening_cash=Decimal("100000"),
        limits=RiskLimits(Decimal("50000"), 10000, Decimal("100000")),
        fees=FeeSchedule("test", Decimal("0"), Decimal("0")),
        journal_path=journal_path,
    )
    response = TestClient(create_app(context_from_runtime(restarted))).get("/audit")
    assert response.status_code == 200
    assert response.json()[-1]["event_type"] == "DURABLE_TEST"


def test_audit_endpoint_fails_closed_on_corrupt_durable_row(tmp_path):
    journal_path = tmp_path / "runtime.db"
    bundle = build_paper_runtime(
        mode=RuntimeMode.PAPER,
        opening_cash=Decimal("100000"),
        limits=RiskLimits(Decimal("50000"), 10000, Decimal("100000")),
        fees=FeeSchedule("test", Decimal("0"), Decimal("0")),
        journal_path=journal_path,
    )
    bundle.audit.record(AuditEvent(event_type="TEST", service="test", message="valid"))
    with sqlite3.connect(journal_path) as connection:
        connection.execute("UPDATE audit_events SET severity='FORGED'")
    response = TestClient(create_app(context_from_runtime(bundle))).get("/audit")
    assert response.status_code == 503


def test_required_read_surfaces_exist():
    api = client()
    for path in (
        "/market",
        "/instruments",
        "/strategies",
        "/experiments",
        "/portfolio",
        "/positions",
        "/orders",
        "/trades",
        "/account",
        "/risk",
        "/pnl",
        "/system",
        "/minute-overlay",
    ):
        assert api.get(path).status_code == 200


def test_minute_overlay_status_is_unconfigured_and_fails_closed() -> None:
    unconfigured = client().get("/minute-overlay").json()
    assert unconfigured["readiness"] == "UNCONFIGURED"
    assert unconfigured["external_order_transmission"] is False
    assert unconfigured["promotion"] == "NOT_ELIGIBLE"

    valid: dict[str, object] = {
        "classification": "CAUSAL_MINUTE_RISK_OVERLAY_PAPER_SHADOW_ONLY",
        "readiness": "READY",
        "provider": "mock",
        "provider_connected": True,
        "bar_interval": "1m",
        "symbol_count": 1,
        "ready_symbol_count": 1,
        "external_order_transmission": False,
        "promotion": "NOT_ELIGIBLE",
        "reasons": [],
        "symbols": [
            {
                "symbol": "510300",
                "ready": True,
                "last_bar_timestamp": "2026-08-12T09:31:00+08:00",
                "base_target_quantity": 500,
                "corrected_target_quantity": 200,
                "multiplier": "0.5",
                "reasons": ["MINUTE_TREND_WEAK"],
            }
        ],
    }
    assert _minute_overlay_status(lambda: valid) == valid
    symbol = valid["symbols"]
    assert isinstance(symbol, list) and isinstance(symbol[0], dict)
    symbol[0]["corrected_target_quantity"] = 600
    assert _minute_overlay_status(lambda: valid)["readiness"] == "STATUS_UNAVAILABLE"
    assert (
        _minute_overlay_status(lambda: (_ for _ in ()).throw(OSError("offline")))[
            "readiness"
        ]
        == "STATUS_UNAVAILABLE"
    )


def test_market_dataset_requires_matching_parquet_checksum(tmp_path):
    audit = AuditLog()
    metrics = MetricsRegistry()
    ledger = Ledger(Decimal("100000"), audit)
    oms = OrderManager(audit)
    risk = RiskEngine(RiskLimits(Decimal("50000"), 10000, Decimal("100000")), audit)
    runtime = PlatformRuntime(RuntimeMode.PAPER, risk, audit, metrics)
    bars = tmp_path / "bars.parquet"
    bars.write_bytes(b"verified-bars")
    evidence = {
        "classification": "noncommercial_research_historical_only",
        "bar_count": 1,
        "observed_start": "2026-08-12T15:00:00+08:00",
        "observed_end": "2026-08-12T15:00:00+08:00",
        "snapshot_readiness": False,
        "real_data_paper_readiness": False,
        "manifest": {
            "data_version": "akshare-test",
            "checksum": sha256(bars.read_bytes()).hexdigest(),
            "quality_status": "PASSED",
        },
        "universe": {
            "provider": "AKShare",
            "provider_version": "1.18.84",
            "point_in_time": False,
            "symbols": ["510300"],
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
    target = tmp_path / "manifest.json"
    target.write_text(json.dumps(evidence), encoding="utf-8")
    api = TestClient(
        create_app(
            AppContext(
                runtime,
                metrics,
                oms,
                ledger,
                audit,
                historical_manifest_path=target,
            ),
            ApiAuthConfig(),
        )
    )
    market = api.get("/market").json()
    assert market["provider_connected"] is False
    assert market["execution_gateway_connected"] is False
    assert market["snapshot_readiness"] is False
    assert market["historical"]["data_version"] == "akshare-test"
    assert api.get("/instruments").json()[0]["point_in_time"] is False
    evidence["universe"]["symbols"] = ["510500"]
    target.write_text(json.dumps(evidence), encoding="utf-8")
    assert api.get("/instruments").json() == []
    evidence["universe"]["symbols"] = ["510300"]
    evidence["evidence_sha256"] = sha256(
        json.dumps(
            {key: value for key, value in evidence.items() if key != "evidence_sha256"},
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    target.write_text(json.dumps(evidence), encoding="utf-8")
    bars.write_bytes(b"tampered")
    assert api.get("/market").json()["historical"] is None
    assert api.get("/instruments").json() == []
    target.write_bytes(b"\xff\xfe\xfd")
    assert api.get("/market").status_code == 200
    assert api.get("/market").json()["historical"] is None
    assert api.get("/instruments").status_code == 200
    assert api.get("/instruments").json() == []


def test_latest_historical_bars_are_verified_and_never_labeled_realtime(tmp_path):
    audit = AuditLog()
    metrics = MetricsRegistry()
    ledger = Ledger(Decimal("100000"), audit)
    oms = OrderManager(audit)
    risk = RiskEngine(RiskLimits(Decimal("50000"), 10000, Decimal("100000")), audit)
    runtime = PlatformRuntime(RuntimeMode.PAPER, risk, audit, metrics)
    bars = tmp_path / "bars.parquet"
    pq.write_table(
        pa.table(
            {
                "symbol": ["510300", "510300"],
                "exchange": ["SSE", "SSE"],
                "timestamp": ["2026-08-12T03:30:00+12:00", "2026-08-11T08:30:00-08:00"],
                "trading_day": [date(2026, 8, 11), date(2026, 8, 12)],
                "open": ["4.0", "4.1"],
                "high": ["4.2", "4.3"],
                "low": ["3.9", "4.0"],
                "close": ["4.1", "4.2"],
                "volume": ["100", "200"],
                "data_version": ["akshare-test", "akshare-test"],
            }
        ),
        bars,
    )
    evidence = {
        "classification": "noncommercial_research_historical_only",
        "bar_count": 2,
        "observed_start": "2026-08-11T15:00:00+08:00",
        "observed_end": "2026-08-12T15:00:00+08:00",
        "snapshot_readiness": False,
        "real_data_paper_readiness": False,
        "manifest": {
            "data_version": "akshare-test",
            "checksum": sha256(bars.read_bytes()).hexdigest(),
            "quality_status": "PASSED",
        },
        "universe": {
            "provider": "AKShare",
            "provider_version": "1.18.84",
            "point_in_time": False,
            "symbols": ["510300"],
        },
    }
    evidence["evidence_sha256"] = sha256(
        json.dumps(evidence, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()
    target = tmp_path / "manifest.json"
    target.write_text(json.dumps(evidence), encoding="utf-8")
    api = TestClient(
        create_app(
            AppContext(runtime, metrics, oms, ledger, audit, historical_manifest_path=target),
            ApiAuthConfig(),
        )
    )
    latest = api.get("/historical-bars/latest").json()
    assert latest == [
        {
            "symbol": "510300",
            "exchange": "SSE",
            "timestamp": "2026-08-11T08:30:00-08:00",
            "trading_day": "2026-08-12",
            "open": "4.1",
            "high": "4.3",
            "low": "4.0",
            "close": "4.2",
            "volume": "200",
            "data_version": "akshare-test",
            "observation_type": "HISTORICAL_DAILY_CLOSE",
        }
    ]
    series = api.get("/historical-bars/510300").json()
    assert series["symbol"] == "510300"
    assert series["data_version"] == "akshare-test"
    assert series["dataset_checksum"] == evidence["manifest"]["checksum"]
    assert series["total_count"] == 2
    assert series["truncated"] is False
    assert [bar["close"] for bar in series["bars"]] == ["4.1", "4.2"]
    assert all(
        bar["observation_type"] == "HISTORICAL_DAILY_BAR" for bar in series["bars"]
    )
    assert api.get("/historical-bars/510500").json()["bars"] == []
    evidence["universe"]["symbols"] = ["510300", "510500"]
    evidence["evidence_sha256"] = sha256(
        json.dumps(
            {key: value for key, value in evidence.items() if key != "evidence_sha256"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    target.write_text(json.dumps(evidence), encoding="utf-8")
    assert api.get("/historical-bars/latest").json() == []
    evidence["universe"]["symbols"] = ["510300"]
    evidence["bar_count"] = 3
    evidence["evidence_sha256"] = sha256(
        json.dumps(
            {key: value for key, value in evidence.items() if key != "evidence_sha256"},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()
    target.write_text(json.dumps(evidence), encoding="utf-8")
    assert api.get("/historical-bars/latest").json() == []


def test_historical_series_rejects_physical_source_disorder(tmp_path):
    from qtrade.api.app import _load_historical_bar_series

    bars = tmp_path / "bars.parquet"
    pq.write_table(
        pa.table(
            {
                "symbol": ["510300", "510300"],
                "exchange": ["SSE", "SSE"],
                "timestamp": ["2026-08-12T15:00:00+08:00", "2026-08-11T15:00:00+08:00"],
                "trading_day": ["2026-08-12", "2026-08-11"],
                "open": ["4.1", "4.0"],
                "high": ["4.3", "4.2"],
                "low": ["4.0", "3.9"],
                "close": ["4.2", "4.1"],
                "volume": ["200", "100"],
                "data_version": ["akshare-test", "akshare-test"],
            }
        ),
        bars,
    )
    assert _load_historical_bar_series(
        tmp_path / "manifest.json",
        symbol="510300",
        data_version="akshare-test",
        expected_checksum=sha256(bars.read_bytes()).hexdigest(),
        expected_universe={"symbols": ["510300"]},
        expected_bar_count=2,
    ) is None


def test_latest_historical_bars_rechecks_the_bytes_it_decodes(tmp_path, monkeypatch):
    from qtrade.api import app as api_app

    verified = b"verified"
    replaced = b"replaced"
    monkeypatch.setattr(api_app, "_read_bounded_file", lambda _path, _limit: replaced)
    monkeypatch.setattr(api_app.pq, "ParquetFile", lambda _source: pytest.fail("must not decode"))
    assert api_app._load_latest_historical_bars(
        tmp_path / "manifest.json",
        data_version="akshare-test",
        expected_checksum=sha256(verified).hexdigest(),
        expected_universe={"symbols": ["510300"]},
        expected_bar_count=1,
        expected_observed_end="2026-08-12T15:00:00+08:00",
    ) == []


def test_unbound_research_summary_is_not_exposed_as_verified_catalog(tmp_path):
    audit = AuditLog()
    metrics = MetricsRegistry()
    ledger = Ledger(Decimal("100000"), audit)
    oms = OrderManager(audit)
    risk = RiskEngine(RiskLimits(Decimal("50000"), 10000, Decimal("100000")), audit)
    runtime = PlatformRuntime(RuntimeMode.PAPER, risk, audit, metrics)
    report = {
        "classification": "exploratory_noncommercial_current_universe_not_pit",
        "data_version": "akshare-bars-v1",
        "promotion": "NOT_ELIGIBLE",
        "promotion_reasons": ["NO_PAPER_OR_SHADOW_SESSIONS"],
        "experiments": [
            {
                "experiment_id": "momentum-v1",
                "metrics": {"sharpe": "0.44"},
                "scenario_type": "PRIMARY",
            }
        ],
    }
    canonical = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    target = tmp_path / "comparison.json"
    target.write_text(
        json.dumps({"sha256": sha256(canonical.encode()).hexdigest(), "report": report}),
        encoding="utf-8",
    )
    api = TestClient(
        create_app(
            AppContext(
                runtime, metrics, oms, ledger, audit, research_comparison_path=target
            ),
            ApiAuthConfig(),
        )
    )
    assert api.get("/strategies").json() == []
    assert api.get("/experiments").json()["classification"] == (
        "NO_VERIFIED_RESEARCH_RESULT"
    )
    target.write_text(target.read_text().replace("0.44", "9.99"), encoding="utf-8")
    assert api.get("/strategies").json() == []
    for malformed in ("[]", "null", '"scalar"'):
        target.write_text(malformed, encoding="utf-8")
        assert api.get("/strategies").json() == []
        assert api.get("/experiments").json()["classification"] == (
            "NO_VERIFIED_RESEARCH_RESULT"
        )


def test_walk_forward_endpoint_exposes_only_verified_evidence(tmp_path):
    audit = AuditLog()
    metrics = MetricsRegistry()
    ledger = Ledger(Decimal("100000"), audit)
    oms = OrderManager(audit)
    risk = RiskEngine(RiskLimits(Decimal("50000"), 10000, Decimal("100000")), audit)
    runtime = PlatformRuntime(RuntimeMode.PAPER, risk, audit, metrics)
    report = {
        "classification": "exploratory_noncommercial_current_universe_not_pit",
        "promotion": "NOT_ELIGIBLE",
        "promotion_reasons": ["CURRENT_UNIVERSE_SURVIVORSHIP_BIAS"],
        "result": {
            "fold_pass_ratio": "0",
            "aggregate_metrics": {"sharpe": "-1.1"},
            "folds": [{"fold": 1, "selected_candidate_id": "buy-hold"}],
        },
    }
    canonical = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    target = tmp_path / "walk-forward.json"
    target.write_text(
        json.dumps({"sha256": sha256(canonical.encode()).hexdigest(), "report": report}),
        encoding="utf-8",
    )
    api = TestClient(
        create_app(
            AppContext(
                runtime,
                metrics,
                oms,
                ledger,
                audit,
                walk_forward_report_path=target,
            ),
            ApiAuthConfig(),
        )
    )
    assert api.get("/walk-forward").json()["result"]["aggregate_metrics"]["sharpe"] == "-1.1"
    target.write_text(target.read_text().replace("-1.1", "9.9"), encoding="utf-8")
    response = api.get("/walk-forward").json()
    assert response["classification"] == "NO_VERIFIED_WALK_FORWARD_RESULT"
    assert response["result"]["folds"] == []


def test_factor_search_endpoint_verifies_report_and_bound_artifacts(tmp_path):
    diagnostics = [
        {
            "factor_id": "reversal-5",
            "periods": 12,
            "instruments_minimum": 30,
            "metrics": {
                "rank_ic": "0.2",
                "icir": "0.7",
                "long_short_spread": "0.01",
                "factor_turnover": "0.5",
            },
        }
    ]
    validation = {
        "selected_candidate_id": "reversal-20-top3-20",
        "selection_metric": "validation_sharpe",
        "scores": [["reversal-20-top3-20", "0.7"]],
        "candidate_count": 1,
        "candidates": [
            {"candidate_id": "reversal-20-top3-20", "parameters": []}
        ],
    }
    test = {
        "selected_candidate_id": "reversal-20-top3-20",
        "selected_metrics": {
            "total_return": "-0.12",
            "sharpe": "-0.5",
            "maximum_drawdown": "-0.24",
            "turnover": "11",
            "transaction_costs": "0.01",
        },
        "buy_hold_metrics": {
            "total_return": "-0.04",
            "sharpe": "-0.3",
            "maximum_drawdown": "-0.15",
        },
        "terminal_excess_return": "-0.08",
        "walk_forward_result_hash": "a" * 64,
    }
    artifact_values = {
        "factor_diagnostics.json": diagnostics,
        "validation_scores.json": validation,
        "test_metrics.json": test,
    }
    artifact_hashes = {}
    for filename, payload in artifact_values.items():
        encoded = json.dumps(payload, sort_keys=True, indent=2).encode()
        (tmp_path / filename).write_bytes(encoded)
        artifact_hashes[filename] = sha256(encoded).hexdigest()
    report = {
        "classification": "bounded_factor_search_current_universe_non_pit",
        "promotion": "NOT_ELIGIBLE",
        "promotion_reasons": ["CURRENT_UNIVERSE_SURVIVORSHIP_BIAS"],
        "artifacts": artifact_hashes,
        "research_budget": {
            "validation_candidate_cap": 15,
            "validation_candidates_run": 1,
        },
        "portfolio_selection": {
            "selected_candidate_id": "reversal-20-top3-20",
            "benchmark_excluded_from_active_selection": True,
        },
    }
    canonical = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    target = tmp_path / "report.json"
    target.write_text(
        json.dumps({"sha256": sha256(canonical.encode()).hexdigest(), "report": report}),
        encoding="utf-8",
    )
    assert _load_verified_factor_search_report(target) is not None

    audit = AuditLog()
    metrics = MetricsRegistry()
    ledger = Ledger(Decimal("100000"), audit)
    oms = OrderManager(audit)
    runtime = PlatformRuntime(
        RuntimeMode.PAPER,
        RiskEngine(RiskLimits(Decimal("50000"), 10000, Decimal("100000")), audit),
        audit,
        metrics,
    )
    api = TestClient(
        create_app(
            AppContext(
                runtime,
                metrics,
                oms,
                ledger,
                audit,
                factor_search_report_path=target,
            ),
            ApiAuthConfig(),
        )
    )
    assert api.get("/factor-search").json()["test"]["terminal_excess_return"] == "-0.08"
    (tmp_path / "test_metrics.json").write_text("{}", encoding="utf-8")
    assert api.get("/factor-search").json()["classification"] == (
        "NO_VERIFIED_FACTOR_SEARCH_RESULT"
    )


def test_robustness_endpoint_exposes_only_verified_diagnostic(tmp_path):
    audit = AuditLog()
    metrics = MetricsRegistry()
    ledger = Ledger(Decimal("100000"), audit)
    oms = OrderManager(audit)
    risk = RiskEngine(RiskLimits(Decimal("50000"), 10000, Decimal("100000")), audit)
    runtime = PlatformRuntime(RuntimeMode.PAPER, risk, audit, metrics)
    report = {
        "classification": "post_rejection_diagnostic_reuses_observations_not_new_heldout",
        "data_checksum": "a" * 64,
        "data_version": "bars-v1",
        "git_commit": "abcdef0",
        "known_biases": [
            "CURRENT_UNIVERSE_SURVIVORSHIP_BIAS",
            "POST_REJECTION_OBSERVATIONS_REUSED_FOR_DIAGNOSTICS",
        ],
        "promotion": "NOT_ELIGIBLE",
        "promotion_reasons": ["NEGATIVE_HELD_OUT_WALK_FORWARD"],
        "scenarios": [
            {
                "scenario_id": "base",
                "parameter_overrides": [],
                "cost_multiplier": "1",
                "regime": "ALL",
                "universe_override": None,
            }
        ],
        "result": {
            "worst_sharpe": "-0.10",
            "dispersion": "0.00",
            "any_negative_sharpe": True,
            "scenario_metrics": [["base", [["sharpe", "-0.10"]]]],
        },
    }
    result_payload = {
        "scenarios": report["scenarios"],
        "results": report["result"]["scenario_metrics"],
        "worst_sharpe": "-0.10",
        "dispersion": "0.00",
        "any_negative_sharpe": True,
    }
    report["result"]["result_hash"] = sha256(
        json.dumps(result_payload, sort_keys=True, default=str).encode()
    ).hexdigest()
    canonical = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    target = tmp_path / "robustness.json"
    target.write_text(
        json.dumps({"sha256": sha256(canonical.encode()).hexdigest(), "report": report}),
        encoding="utf-8",
    )
    api = TestClient(
        create_app(
            AppContext(
                runtime,
                metrics,
                oms,
                ledger,
                audit,
                robustness_report_path=target,
            ),
            ApiAuthConfig(),
        )
    )
    assert api.get("/robustness").json()["result"]["any_negative_sharpe"] is True
    target.write_text(target.read_text().replace("-0.10", "9.99"), encoding="utf-8")
    response = api.get("/robustness").json()
    assert response["classification"] == "NO_VERIFIED_ROBUSTNESS_RESULT"
    assert response["result"]["scenario_metrics"] == []


def test_robustness_endpoint_rejects_hash_valid_malformed_or_promoted_report(tmp_path):
    audit = AuditLog()
    metrics = MetricsRegistry()
    runtime = PlatformRuntime(
        RuntimeMode.PAPER,
        RiskEngine(RiskLimits(Decimal("50000"), 10000, Decimal("100000")), audit),
        audit,
        metrics,
    )
    target = tmp_path / "robustness.json"
    api = TestClient(
        create_app(
            AppContext(
                runtime,
                metrics,
                OrderManager(audit),
                Ledger(Decimal("100000"), audit),
                audit,
                robustness_report_path=target,
            ),
            ApiAuthConfig(),
        )
    )
    for report in (
        {"unexpected": "shape"},
        {"classification": "forged", "promotion": "APPROVED", "result": "bad"},
    ):
        canonical = json.dumps(report, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        target.write_text(
            json.dumps({"sha256": sha256(canonical.encode()).hexdigest(), "report": report}),
            encoding="utf-8",
        )
        assert api.get("/robustness").json()["classification"] == (
            "NO_VERIFIED_ROBUSTNESS_RESULT"
        )


def test_robustness_endpoint_rejects_malformed_parameter_overrides(tmp_path):
    malformed = {
        "classification": "post_rejection_diagnostic_reuses_observations_not_new_heldout",
        "data_checksum": "a" * 64,
        "data_version": "bars-v1",
        "git_commit": "abcdef0",
        "known_biases": [
            "CURRENT_UNIVERSE_SURVIVORSHIP_BIAS",
            "POST_REJECTION_OBSERVATIONS_REUSED_FOR_DIAGNOSTICS",
        ],
        "promotion": "NOT_ELIGIBLE",
        "promotion_reasons": ["NEGATIVE_HELD_OUT_WALK_FORWARD"],
        "scenarios": [{
            "scenario_id": "base",
            "parameter_overrides": [["only-one"]],
            "cost_multiplier": "1",
            "regime": "ALL",
            "universe_override": None,
        }],
        "result": {
            "worst_sharpe": "-0.1",
            "dispersion": "0",
            "any_negative_sharpe": True,
            "scenario_metrics": [["base", [["sharpe", "-0.1"]]]],
        },
    }
    payload = {
        "scenarios": malformed["scenarios"],
        "results": malformed["result"]["scenario_metrics"],
        "worst_sharpe": "-0.1",
        "dispersion": "0",
        "any_negative_sharpe": True,
    }
    malformed["result"]["result_hash"] = sha256(
        json.dumps(payload, sort_keys=True, default=str).encode()
    ).hexdigest()
    canonical = json.dumps(malformed, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    target = tmp_path / "malformed.json"
    target.write_text(
        json.dumps({"sha256": sha256(canonical.encode()).hexdigest(), "report": malformed}),
        encoding="utf-8",
    )
    assert _load_verified_robustness_report(target) is None


def test_backtest_analytics_verifies_comparison_record_and_curve_hashes(tmp_path):
    experiment_id = "verified-leader"
    root = tmp_path / "experiments"
    artifacts = root / f"{experiment_id}-artifacts"
    artifacts.mkdir(parents=True)
    equity = [
        {"session": "2026-01-02", "equity": "100000"},
        {"session": "2026-01-05", "equity": "99000"},
    ]
    drawdown = [
        {"session": "2026-01-02", "drawdown": "0"},
        {"session": "2026-01-05", "drawdown": "-0.01"},
    ]
    artifact_hashes = {}
    for name, payload in (
        ("equity", equity),
        ("drawdown", drawdown),
        (
            "trades",
            [{
                "trading_day": "2026-01-05",
                "symbol": "510300",
                "side": "BUY",
                "price": "3.5",
                "quantity": 100,
                "cost": "0.5",
            }],
        ),
        ("positions", {"510300": 100}),
    ):
        target = artifacts / f"{name}.json"
        target.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        artifact_hashes[name] = sha256(target.read_bytes()).hexdigest()
    metrics = {"sharpe": "0.2", "total_return": "0.01"}
    record = {
        "experiment_id": experiment_id,
        "model": "buy-hold",
        "data_version": "bars-v1",
        "git_commit": "abc123",
        "universe": ["510300"],
        "metrics": metrics,
        "hyperparameters": {},
        "factor_set": [],
        "transaction_cost": "0.001428571428571428571428571429",
        "split": {
            "train_start": "2026-01-02",
            "train_end": "2026-01-02",
            "validation_start": "2026-01-03",
            "validation_end": "2026-01-03",
            "test_start": "2026-01-04",
            "test_end": "2026-01-05",
        },
        "artifacts": [f"{name}:{digest}" for name, digest in artifact_hashes.items()],
    }
    canonical_record = json.dumps(record, sort_keys=True, separators=(",", ":"))
    record_hash = sha256(canonical_record.encode()).hexdigest()
    (root / f"{experiment_id}.json").write_text(
        json.dumps({"spec_hash": record_hash, "record": record}), encoding="utf-8"
    )
    result_payload = {
        "artifact_manifest": tuple(sorted(artifact_hashes.items())),
        "metrics": tuple(sorted((key, Decimal(value)) for key, value in metrics.items())),
        "record_hash": record_hash,
        "strategy_id": experiment_id,
    }
    result_hash = sha256(
        json.dumps(
            result_payload, sort_keys=True, separators=(",", ":"), default=str
        ).encode()
    ).hexdigest()
    report = {
        "classification": "exploratory_noncommercial_current_universe_not_pit",
        "data_version": "bars-v1",
        "git_commit": "abc123",
        "split": {
            "train_start": "2026-01-02",
            "train_end": "2026-01-02",
            "validation_start": "2026-01-03",
            "validation_end": "2026-01-03",
            "test_start": "2026-01-04",
            "test_end": "2026-01-05",
        },
        "promotion": "NOT_ELIGIBLE",
        "promotion_reasons": ["CURRENT_UNIVERSE_SURVIVORSHIP_BIAS"],
        "experiments": [{
            "experiment_id": experiment_id,
            "spec_hash": "f" * 64,
            "result_hash": result_hash,
            "metrics": metrics,
            "scenario_type": "SENSITIVITY",
        }],
    }
    canonical_report = json.dumps(report, sort_keys=True, separators=(",", ":"))
    comparison = tmp_path / "comparison.json"
    comparison.write_text(
        json.dumps({"sha256": sha256(canonical_report.encode()).hexdigest(), "report": report}),
        encoding="utf-8",
    )
    verified = _load_verified_backtest_analytics(comparison, root)
    assert verified is not None
    assert verified["leader"]["experiment_id"] == experiment_id
    assert verified["curve"][-1]["drawdown"] == "-0.01"
    assert verified["monthly_returns"] == [{"month": "2026-01", "return": "-0.01"}]
    assert verified["trades"][0]["notional"] == "350.0"
    assert verified["positions"] == [{"symbol": "510300", "quantity": 100}]
    assert set(verified["artifact_hashes"]) == {
        "equity", "drawdown", "trades", "positions"
    }
    catalog = _load_verified_experiment_catalog(comparison, root)
    assert catalog is not None
    assert catalog["strategies"] == [{
        "strategy_id": experiment_id,
        "name": "buy-hold",
        "version": record_hash[:12],
        "family": "BUY_AND_HOLD_BENCHMARK",
        "status": "NOT_ELIGIBLE",
        "role": "NONE",
        "metrics": metrics,
        "scenario_type": "SENSITIVITY",
        "data_version": "bars-v1",
        "git_commit": "abc123",
        "evidence_status": "VERIFIED_NOT_ELIGIBLE",
        "lineage_status": "NOT_REGISTERED",
    }]
    catalog_report = catalog["report"]
    assert isinstance(catalog_report, dict)
    assert catalog_report["lineage"] == []
    assert catalog_report["experiments"][0]["model"] == "buy-hold"
    assert catalog_report["experiments"][0]["universe_size"] == 1
    duplicated_report = {**report, "experiments": [report["experiments"][0]] * 2}
    duplicated = json.dumps(duplicated_report, sort_keys=True, separators=(",", ":"))
    comparison.write_text(
        json.dumps(
            {
                "sha256": sha256(duplicated.encode()).hexdigest(),
                "report": duplicated_report,
            }
        ),
        encoding="utf-8",
    )
    assert _load_verified_experiment_catalog(comparison, root) is None
    comparison.write_text(
        json.dumps({"sha256": sha256(canonical_report.encode()).hexdigest(), "report": report}),
        encoding="utf-8",
    )
    (artifacts / "equity.json").write_text("[]", encoding="utf-8")
    assert _load_verified_backtest_analytics(comparison, root) is None
    assert _load_verified_experiment_catalog(comparison, root) is None


def test_backtest_analytics_aligns_verified_buy_hold_benchmark(tmp_path):
    root = tmp_path / "experiments"

    def record_experiment(experiment_id, model, equities, sharpe):
        artifacts = root / f"{experiment_id}-artifacts"
        artifacts.mkdir(parents=True)
        equity = [
            {"session": "2026-01-02", "equity": str(equities[0])},
            {"session": "2026-01-05", "equity": str(equities[1])},
        ]
        drawdown = [
            {"session": "2026-01-02", "drawdown": "0"},
            {"session": "2026-01-05", "drawdown": "0"},
        ]
        hashes = {}
        for name, payload in (
            ("equity", equity),
            ("drawdown", drawdown),
            ("trades", []),
            ("positions", {}),
        ):
            target = artifacts / f"{name}.json"
            target.write_text(json.dumps(payload), encoding="utf-8")
            hashes[name] = sha256(target.read_bytes()).hexdigest()
        metrics = {"sharpe": sharpe, "total_return": "0.1"}
        record = {
            "experiment_id": experiment_id,
            "model": model,
            "data_version": "bars-v1",
            "git_commit": "abc123",
            "universe": ["510300"],
            "metrics": metrics,
            "split": {"test_start": "2026-01-01", "test_end": "2026-01-31"},
            "artifacts": [f"{name}:{digest}" for name, digest in hashes.items()],
        }
        record_hash = sha256(
            json.dumps(record, sort_keys=True, separators=(",", ":")).encode()
        ).hexdigest()
        (root / f"{experiment_id}.json").write_text(
            json.dumps({"spec_hash": record_hash, "record": record}), encoding="utf-8"
        )
        result_payload = {
            "artifact_manifest": tuple(sorted(hashes.items())),
            "metrics": tuple(sorted((key, Decimal(value)) for key, value in metrics.items())),
            "record_hash": record_hash,
            "strategy_id": experiment_id,
        }
        result_hash = sha256(
            json.dumps(result_payload, sort_keys=True, separators=(",", ":"), default=str).encode()
        ).hexdigest()
        return {
            "experiment_id": experiment_id,
            "spec_hash": "f" * 64,
            "result_hash": result_hash,
            "metrics": metrics,
            "scenario_type": "PRIMARY",
        }

    benchmark = record_experiment("plain-baseline", "buy-hold", (100, 105), "0.1")
    leader = record_experiment("verified-leader", "momentum", (100, 110), "0.2")
    report = {
        "classification": "exploratory_noncommercial_current_universe_not_pit",
        "data_version": "bars-v1",
        "git_commit": "abc123",
        "promotion": "NOT_ELIGIBLE",
        "experiments": [benchmark, leader],
    }
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"))
    comparison = tmp_path / "comparison.json"
    comparison.write_text(
        json.dumps({"sha256": sha256(canonical.encode()).hexdigest(), "report": report}),
        encoding="utf-8",
    )
    verified = _load_verified_backtest_analytics(comparison, root)
    assert verified is not None
    assert verified["benchmark"]["experiment_id"] == "plain-baseline"
    assert verified["curve"][-1]["benchmark_equity"] == "105"
    assert verified["curve"][-1]["excess_return"] == "0.05"
    (root / "plain-baseline-artifacts" / "equity.json").write_text("[]", encoding="utf-8")
    assert _load_verified_backtest_analytics(comparison, root) is None


def test_backtest_artifact_manifest_rejects_unsigned_extra_files():
    core = [
        f"{name}:{'a' * 64}"
        for name in ("equity", "drawdown", "trades", "positions")
    ]
    assert set(_validated_artifact_manifest(core)) == {
        "equity", "drawdown", "trades", "positions"
    }
    with pytest.raises(ValueError, match="missing analytics artifacts"):
        _validated_artifact_manifest([*core, f"unverified:{'b' * 64}"])


def test_backtest_trade_and_position_artifacts_require_experiment_universe():
    allowed = frozenset({"510300"})
    with pytest.raises(ValueError, match="invalid backtest trades"):
        _validated_backtest_trades(
            [{
                "trading_day": "2026-01-05",
                "symbol": "510500",
                "side": "BUY",
                "price": "3.5",
                "quantity": 100,
                "cost": "0.5",
            }],
            allowed,
            frozenset({"2026-01-05"}),
        )
    with pytest.raises(ValueError, match="invalid backtest positions"):
        _validated_backtest_positions({"510500": 100}, allowed)


def test_backtest_artifacts_reject_off_curve_trades_and_non_iso_sessions():
    from qtrade.api.app import (
        _reconcile_backtest_trades_and_positions,
        _validated_equity_drawdown_curve,
    )

    with pytest.raises(ValueError, match="invalid backtest trades"):
        _validated_backtest_trades(
            [{
                "trading_day": "2030-01-01",
                "symbol": "510300",
                "side": "BUY",
                "price": "10",
                "quantity": 1,
                "cost": "0",
            }],
            frozenset({"510300"}),
            frozenset({"2026-01-05"}),
        )
    with pytest.raises(ValueError, match="do not reconcile"):
        _reconcile_backtest_trades_and_positions(
            [{"symbol": "510300", "side": "BUY", "quantity": 1}],
            [{"symbol": "510300", "quantity": 999}],
        )
    with pytest.raises(ValueError, match="invalid analytics curve"):
        _validated_equity_drawdown_curve(
            [{"session": "not-a-date", "equity": "100"}],
            [{"session": "not-a-date", "drawdown": "0"}],
        )


def test_backtest_curve_rejects_drawdown_inconsistent_with_equity():
    from qtrade.api.app import _validated_equity_drawdown_curve

    with pytest.raises(ValueError, match="invalid analytics curve"):
        _validated_equity_drawdown_curve(
            [
                {"session": "2026-01-02", "equity": "100"},
                {"session": "2026-01-05", "equity": "90"},
            ],
            [
                {"session": "2026-01-02", "drawdown": "0"},
                {"session": "2026-01-05", "drawdown": "0"},
            ],
        )


def test_daily_report_history_exposes_verified_summary(tmp_path):
    audit = AuditLog()
    metrics = MetricsRegistry()
    ledger = Ledger(Decimal("100000"), audit)
    oms = OrderManager(audit)
    risk = RiskEngine(RiskLimits(Decimal("50000"), 10000, Decimal("100000")), audit)
    runtime = PlatformRuntime(RuntimeMode.PAPER, risk, audit, metrics)
    report_root = tmp_path / "reports"
    report = DailyPaperReport(
        "paper-20260812",
        datetime(2026, 8, 12, tzinfo=UTC).date(),
        datetime(2026, 8, 12, 16, tzinfo=UTC),
        "ticks-v1",
        ("trend@1.0.0",),
        (),
        (),
        (),
        (),
        (),
        (),
        (),
        (),
        (),
    )
    _, digest = DailyReportStore(report_root).save(report)
    api = TestClient(
        create_app(
            AppContext(runtime, metrics, oms, ledger, audit, daily_report_root=report_root),
            ApiAuthConfig(),
        )
    )
    response = api.get("/reports")
    assert response.status_code == 200
    assert response.json() == [
        {
            "report_id": "paper-20260812",
            "trading_day": "2026-08-12",
            "generated_at": "2026-08-12T16:00:00+00:00",
            "data_version": "ticks-v1",
            "strategy_versions": ["trend@1.0.0"],
            "sha256": digest,
        }
    ]
    artifact = next(report_root.glob("*.json"))
    artifact.write_text(
        artifact.read_text(encoding="utf-8").replace("ticks-v1", "tampered"),
        encoding="utf-8",
    )
    assert api.get("/reports").json() == []
    artifact.write_bytes(b"\xff\xfe")
    assert api.get("/reports").json() == []
    artifact.write_text("[]", encoding="utf-8")
    assert api.get("/reports").json() == []


def test_daily_report_scheduler_status_is_explicit_and_read_only():
    api = client()
    unconfigured = api.get("/reporting-status")
    assert unconfigured.status_code == 200
    assert unconfigured.json() == {
        "classification": "TRUSTED_PAPER_EOD_REPORTING",
        "readiness": "UNCONFIGURED",
        "automatic": False,
        "state": "UNCONFIGURED",
        "venue": None,
        "timezone": "Asia/Shanghai",
        "cutoff": None,
        "data_version": None,
        "strategy_versions": [],
        "last_attempt_day": None,
        "last_generated_day": None,
        "last_report_id": None,
        "last_sha256": None,
        "error_code": "TRUSTED_RUNTIME_IDENTITY_NOT_CONFIGURED",
        "caller_supplied_identity": False,
        "external_order_transmission": False,
    }


def test_daily_report_history_rejects_hash_valid_schema_and_identity_mismatch(tmp_path):
    report_root = tmp_path / "reports"
    report_root.mkdir()
    report = {
        "report_id": "paper-20260812",
        "trading_day": "not-a-date",
        "generated_at": "2026-08-12T16:00:00+00:00",
        "data_version": "ticks-v1",
        "strategy_versions": ["trend@1.0.0"],
    }
    canonical = json.dumps(report, sort_keys=True, separators=(",", ":"))
    (report_root / "different-name.json").write_text(
        json.dumps({"report": report, "sha256": sha256(canonical.encode()).hexdigest()}),
        encoding="utf-8",
    )
    audit = AuditLog()
    metrics = MetricsRegistry()
    ledger = Ledger(Decimal("0"), audit)
    oms = OrderManager(audit)
    risk = RiskEngine(RiskLimits(Decimal("0"), 0, Decimal("0")), audit)
    runtime = PlatformRuntime(RuntimeMode.PAPER, risk, audit, metrics)
    api = TestClient(
        create_app(
            AppContext(runtime, metrics, oms, ledger, audit, daily_report_root=report_root),
            ApiAuthConfig(),
        )
    )
    assert api.get("/reports").json() == []


def test_daily_report_history_has_bounded_files_and_directory(tmp_path):
    report_root = tmp_path / "reports"
    report_root.mkdir()
    oversized = report_root / "oversized.json"
    oversized.write_bytes(b" " * (4 * 1024 * 1024 + 1))
    audit = AuditLog()
    metrics = MetricsRegistry()
    ledger = Ledger(Decimal("0"), audit)
    oms = OrderManager(audit)
    risk = RiskEngine(RiskLimits(Decimal("0"), 0, Decimal("0")), audit)
    runtime = PlatformRuntime(RuntimeMode.PAPER, risk, audit, metrics)
    api = TestClient(
        create_app(
            AppContext(runtime, metrics, oms, ledger, audit, daily_report_root=report_root),
            ApiAuthConfig(),
        )
    )
    assert api.get("/reports").json() == []
    oversized.unlink()
    for index in range(367):
        (report_root / f"invalid-{index:03}.json").write_text("{}", encoding="utf-8")
    assert api.get("/reports").json() == []


def test_api_context_uses_canonical_paper_runtime(tmp_path):
    bundle = build_paper_runtime(
        mode=RuntimeMode.PAPER,
        opening_cash=Decimal("100"),
        limits=RiskLimits(Decimal("10"), 100, Decimal("100")),
        fees=FeeSchedule("test", Decimal("0"), Decimal("0")),
        journal_path=tmp_path / "runtime.db",
    )
    context = context_from_runtime(bundle)
    assert context.runtime is bundle.runtime
    assert context.ledger is bundle.ledger
    api = TestClient(create_app(context, ApiAuthConfig(control_token="control-test")))
    response = api.post(
        "/system/recover",
        headers={"Authorization": "Bearer control-test"},
        json={},
    )
    assert response.status_code == 200


def test_paper_account_endpoints_are_traceable_and_restart_consistent(tmp_path):
    journal_path = tmp_path / "paper-account.db"
    now = datetime(2026, 8, 12, 1, 31, tzinfo=UTC)
    limits = RiskLimits(Decimal("50000"), 10000, Decimal("100000"))
    fees = FeeSchedule("test", Decimal("0"), Decimal("0"))
    instruments = {
        symbol: Instrument(symbol, "SSE", "ETF", 100, Decimal("0.001"))
        for symbol in ("510300", "510500")
    }

    def build():
        return build_paper_runtime(
            mode=RuntimeMode.PAPER,
            opening_cash=Decimal("100000"),
            limits=limits,
            fees=fees,
            journal_path=journal_path,
        )

    bundle = build()
    bundle.reconcile_and_recover(actor="operator")
    bundle.runtime.start(actor="operator")
    context = RiskContext(
        bundle.ledger.account,
        instruments,
        {"510300": Decimal("10"), "510500": Decimal("5")},
        now,
        True,
        True,
        now,
    )
    filled_order, _ = bundle.service.submit(
        OrderRequest(
            "paper-buy-filled",
            "trend@1.0.0",
            "paper-etf",
            "510300",
            Side.BUY,
            Decimal("10"),
            100,
            now,
            trading_day=date(2026, 8, 12),
            sellable_on=date(2026, 8, 13),
        ),
        context,
        now=now,
    )
    bundle.gateway.fill(
        filled_order,
        fill_key="paper-fill-1",
        price=Decimal("10"),
        quantity=100,
        now=now + timedelta(seconds=1),
    )
    open_order, _ = bundle.service.submit(
        OrderRequest(
            "paper-buy-open",
            "rotation@1.0.0",
            "paper-etf",
            "510500",
            Side.BUY,
            Decimal("5"),
            100,
            now + timedelta(seconds=2),
            trading_day=date(2026, 8, 12),
            sellable_on=date(2026, 8, 13),
        ),
        context,
        now=now + timedelta(seconds=2),
    )

    def assertions(runtime_bundle) -> None:
        app_context = context_from_runtime(runtime_bundle)
        app_context.operational_date = date(2026, 8, 12)
        api = TestClient(create_app(app_context, ApiAuthConfig()))

        orders = api.get("/orders").json()
        assert [(row["strategy_id"], row["side"], row["price"]) for row in orders] == [
            ("trend@1.0.0", "BUY", "10"),
            ("rotation@1.0.0", "BUY", "5"),
        ]
        assert orders[0]["internal_order_id"] == filled_order.internal_order_id
        assert orders[1]["internal_order_id"] == open_order.internal_order_id

        trades = api.get("/trades").json()
        assert trades == [
            {
                "trade_id": trades[0]["trade_id"],
                "internal_order_id": filled_order.internal_order_id,
                "strategy_id": "trend@1.0.0",
                "portfolio_id": "paper-etf",
                "symbol": "510300",
                "side": "BUY",
                "price": "10",
                "quantity": 100,
                "fee": "0.00",
                "timestamp": "2026-08-12T01:31:01+00:00",
                "execution_type": "PAPER_SIMULATED_FILL",
            }
        ]
        positions = api.get("/positions").json()
        assert len(positions) == 1
        assert positions[0]["symbol"] == "510300"
        assert positions[0]["quantity"] == 100
        assert Decimal(positions[0]["average_cost"]) == Decimal("10")
        assert positions[0]["sellable_quantity"] == 0
        assert positions[0]["as_of_date"] == "2026-08-12"
        assert Decimal(positions[0]["cost_basis_value"]) == Decimal("1000")
        assert positions[0]["market_price"] is None
        assert positions[0]["market_value"] is None
        assert positions[0]["unrealized_pnl"] is None
        assert positions[0]["valuation_status"] == "MARKET_PRICE_UNAVAILABLE"
        account = api.get("/account").json()
        assert Decimal(account["cash"]) == Decimal("99000")
        assert account["equity"] is None
        assert account["valuation_status"] == "MARKET_PRICE_UNAVAILABLE"
        risk = api.get("/risk").json()
        assert Decimal(risk["position_cost_basis"]) == Decimal("1000")
        assert Decimal(risk["open_buy_notional"]) == Decimal("500")
        assert Decimal(risk["gross_exposure"]) == Decimal("1500")
        assert Decimal(risk["largest_position_concentration"]) == Decimal("1")
        assert risk["open_order_count"] == 1
        assert risk["drawdown"] is None
        assert risk["valuation_status"] == "MARKET_PRICE_UNAVAILABLE"

    assertions(bundle)
    assertions(build())

    corrupt_context = context_from_runtime(bundle)
    valid_payload = dict(bundle.journal.applied_trade_payloads()[0])
    corrupt_context.applied_trade_reader = lambda: (
        {**valid_payload, "quantity": 1000},
    )
    assert _paper_trade_rows(corrupt_context) == []
    corrupt_context.applied_trade_reader = lambda: (
        {
            **valid_payload,
            "timestamp": (filled_order.created_at - timedelta(seconds=1)).isoformat(),
        },
    )
    assert _paper_trade_rows(corrupt_context) == []
    original_filled = filled_order.filled_quantity
    filled_order.filled_quantity = 0
    corrupt_context.applied_trade_reader = lambda: (valid_payload,)
    assert _paper_trade_rows(corrupt_context) == []
    filled_order.filled_quantity = original_filled


def test_paper_snapshot_waits_for_atomic_fill_projection(tmp_path, monkeypatch):
    bundle = build_paper_runtime(
        mode=RuntimeMode.PAPER,
        opening_cash=Decimal("100000"),
        limits=RiskLimits(Decimal("50000"), 10000, Decimal("100000")),
        fees=FeeSchedule("test", Decimal("0"), Decimal("0")),
        journal_path=tmp_path / "atomic-read.db",
    )
    bundle.reconcile_and_recover(actor="operator")
    bundle.runtime.start(actor="operator")
    now = datetime(2026, 8, 12, 1, 31, tzinfo=UTC)
    order, _ = bundle.service.submit(
        OrderRequest(
            "atomic-read",
            "strategy",
            "portfolio",
            "510300",
            Side.BUY,
            Decimal("10"),
            100,
            now,
        ),
        RiskContext(
            bundle.ledger.account,
            {"510300": Instrument("510300", "SSE", "ETF", 100, Decimal("0.001"))},
            {"510300": Decimal("10")},
            now,
            True,
            True,
            now,
        ),
        now=now,
    )
    entered = Event()
    release = Event()
    read_done = Event()
    original_commit = bundle.oms.commit_projection

    def blocked_commit(current, projected):
        entered.set()
        assert release.wait(timeout=5)
        original_commit(current, projected)

    monkeypatch.setattr(bundle.oms, "commit_projection", blocked_commit)
    fill_errors: list[BaseException] = []

    def fill() -> None:
        try:
            bundle.gateway.fill(
                order,
                fill_key="atomic-fill",
                price=Decimal("12"),
                quantity=100,
                now=now + timedelta(seconds=1),
            )
        except BaseException as exc:  # pragma: no cover - assertion reports details
            fill_errors.append(exc)

    context = context_from_runtime(bundle)
    snapshots: list[dict[str, Any]] = []

    def read() -> None:
        snapshots.append(_paper_snapshot(context))
        read_done.set()

    fill_thread = Thread(target=fill)
    read_thread = Thread(target=read)
    fill_thread.start()
    assert entered.wait(timeout=5)
    read_thread.start()
    assert not read_done.wait(timeout=0.1)
    release.set()
    fill_thread.join(timeout=5)
    read_thread.join(timeout=5)
    assert not fill_errors
    assert read_done.is_set()
    snapshot = snapshots[0]
    assert snapshot["account"]["cash"] == "98800.00"
    assert snapshot["positions"][0]["quantity"] == 100
    assert snapshot["orders"][0]["filled_quantity"] == 100
    assert len(snapshot["trades"]) == 1
    assert Decimal(snapshot["risk"]["position_cost_basis"]) == Decimal("1200")
    assert Decimal(snapshot["risk"]["open_buy_notional"]) == Decimal("0")
    assert Decimal(snapshot["risk"]["gross_exposure"]) == Decimal("1200")


def test_trade_role_can_cancel_but_control_role_cannot(tmp_path):
    bundle = build_paper_runtime(
        mode=RuntimeMode.PAPER,
        opening_cash=Decimal("100000"),
        limits=RiskLimits(Decimal("50000"), 10000, Decimal("100000")),
        fees=FeeSchedule("test", Decimal("0"), Decimal("0")),
        journal_path=tmp_path / "runtime.db",
    )
    bundle.reconcile_and_recover(actor="operator")
    bundle.runtime.start(actor="operator")
    now = datetime.now(UTC)
    order, _ = bundle.service.submit(
        OrderRequest(
            "submit-key",
            "strategy",
            "portfolio",
            "510300",
            Side.BUY,
            Decimal("10"),
            100,
            now,
        ),
        RiskContext(
            bundle.ledger.account,
            {"510300": Instrument("510300", "SSE", "ETF", 100, Decimal("0.001"))},
            {"510300": Decimal("10")},
            now,
            True,
            True,
            now,
        ),
        now=now,
    )
    api = TestClient(
        create_app(
            context_from_runtime(bundle),
            ApiAuthConfig(control_token="control-test", trade_token="trade-test"),
        )
    )
    payload = {"internal_order_id": order.internal_order_id, "idempotency_key": "cancel-1"}
    control_response = api.post(
        "/orders/cancel",
        headers={"Authorization": "Bearer control-test"},
        json=payload,
    )
    assert control_response.status_code == 403
    trade_response = api.post(
        "/orders/cancel",
        headers={"Authorization": "Bearer trade-test"},
        json=payload,
    )
    assert trade_response.status_code == 200
    assert trade_response.json()["status"] == "CANCELLED"
    retry = api.post(
        "/orders/cancel",
        headers={"Authorization": "Bearer trade-test"},
        json=payload,
    )
    assert retry.status_code == 200
    assert bundle.gateway.reservations.reserved_cash == Decimal("0")
