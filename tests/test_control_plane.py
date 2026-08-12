import json
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256

import pytest
from fastapi.testclient import TestClient

from qtrade.accounting import Ledger
from qtrade.api import ApiAuthConfig, AppContext, context_from_runtime, create_app
from qtrade.audit import AuditLog
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
    ):
        assert api.get(path).status_code == 200


def test_verified_research_results_are_exposed_and_tampering_fails_closed(tmp_path):
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
    assert api.get("/experiments").json()["data_version"] == "akshare-bars-v1"
    assert api.get("/strategies").json()[0]["metrics"]["sharpe"] == "0.44"
    target.write_text(target.read_text().replace("0.44", "9.99"), encoding="utf-8")
    assert api.get("/strategies").json() == []


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
