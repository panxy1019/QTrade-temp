from decimal import Decimal

import pytest
from fastapi.testclient import TestClient

from qtrade.accounting import Ledger
from qtrade.api import ApiAuthConfig, AppContext, create_app
from qtrade.audit import AuditLog
from qtrade.control import PlatformRuntime
from qtrade.domain import RuntimeMode
from qtrade.monitoring import MetricsRegistry
from qtrade.oms import OrderManager
from qtrade.risk import RiskEngine, RiskLimits


def client() -> TestClient:
    audit = AuditLog()
    metrics = MetricsRegistry()
    metrics.gateway_connected = True
    metrics.reconciliation_status = "OK"
    ledger = Ledger(Decimal("100000"), audit)
    oms = OrderManager(audit)
    risk = RiskEngine(RiskLimits(Decimal("50000"), 10000, Decimal("100000")), audit)
    context = AppContext(
        PlatformRuntime(RuntimeMode.SHADOW, risk, audit, metrics), metrics, oms, ledger, audit
    )
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
    with pytest.raises(ValueError, match="verified gateway"):
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
