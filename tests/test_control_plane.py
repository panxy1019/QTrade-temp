from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from qtrade.accounting import Ledger
from qtrade.api import ApiAuthConfig, AppContext, context_from_runtime, create_app
from qtrade.audit import AuditLog
from qtrade.control import PlatformRuntime
from qtrade.domain import RuntimeMode
from qtrade.execution import FeeSchedule
from qtrade.monitoring import MetricsRegistry
from qtrade.oms import OrderManager
from qtrade.reconciliation import Reconciler, ReconciliationResult
from qtrade.risk import RiskEngine, RiskLimits
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
    assert 'id="mode"' in api.get("/").text
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
