from datetime import UTC, datetime
from decimal import Decimal

import pytest

from qtrade.domain import Instrument, OrderRequest, RuntimeMode, Side
from qtrade.execution import FeeSchedule, PaperAction
from qtrade.risk import RiskContext, RiskLimits
from qtrade.runtime import build_paper_runtime

NOW = datetime(2026, 8, 12, tzinfo=UTC)


def build(tmp_path, mode=RuntimeMode.PAPER):
    return build_paper_runtime(
        mode=mode,
        opening_cash=Decimal("100000"),
        limits=RiskLimits(Decimal("50000"), 10000, Decimal("100000")),
        fees=FeeSchedule("test", Decimal("0"), Decimal("0")),
        journal_path=tmp_path / "runtime.db",
    )


def submit(bundle, key="key"):
    return bundle.service.submit(
        OrderRequest(key, "strategy", "portfolio", "510300", Side.BUY, Decimal("10"), 100, NOW),
        RiskContext(
            bundle.ledger.account,
            {"510300": Instrument("510300", "SSE", "ETF", 100, Decimal("0.001"))},
            {"510300": Decimal("10")},
            NOW,
            True,
            True,
            NOW,
            sellable_quantities={},
        ),
        now=NOW,
    )


def test_runtime_rejects_orders_before_readiness_and_after_stop(tmp_path):
    bundle = build(tmp_path)
    with pytest.raises(RuntimeError, match="not started"):
        submit(bundle)
    bundle.metrics.gateway_connected = True
    bundle.metrics.reconciliation_status = "OK"
    bundle.runtime.start(actor="operator")
    order, _ = submit(bundle)
    assert order.broker_order_id is not None
    bundle.runtime.stop(actor="operator")
    with pytest.raises(RuntimeError, match="not started"):
        submit(bundle, "after-stop")


def test_shadow_runtime_has_simulated_gateway_only(tmp_path):
    bundle = build(tmp_path, RuntimeMode.SHADOW)
    assert bundle.runtime.status.mode is RuntimeMode.SHADOW
    assert bundle.gateway.external_order_transmission is False


def test_gateway_incident_halts_runtime_and_blocks_next_submit(tmp_path):
    bundle = build(tmp_path)
    bundle.metrics.gateway_connected = True
    bundle.metrics.reconciliation_status = "OK"
    bundle.runtime.start(actor="operator")
    bundle.gateway.inject_next_action(PaperAction.DISCONNECT)
    with pytest.raises(ConnectionError):
        submit(bundle, "uncertain")
    assert bundle.risk.state.value == "HALTED"
    assert bundle.metrics.reconciliation_status == "FAILED"
    with pytest.raises(RuntimeError, match="risk state HALTED"):
        submit(bundle, "blocked")


def test_restart_restores_account_and_stays_halted_for_reconciliation(tmp_path):
    first = build(tmp_path)
    first.metrics.gateway_connected = True
    first.metrics.reconciliation_status = "OK"
    first.runtime.start(actor="operator")
    order, _ = submit(first, "buy")
    first.gateway.fill(order, fill_key="fill", price=Decimal("10"), quantity=100, now=NOW)
    assert first.ledger.account.cash == Decimal("99000")

    restarted = build(tmp_path)
    assert restarted.ledger.account.cash == Decimal("99000")
    assert restarted.ledger.account.position_quantity("510300") == 100
    assert restarted.risk.state.value == "HALTED"
    with pytest.raises(ValueError, match="halted"):
        restarted.runtime.start(actor="operator")


def test_restart_restores_open_order_and_reservation(tmp_path):
    first = build(tmp_path)
    first.metrics.gateway_connected = True
    first.metrics.reconciliation_status = "OK"
    first.runtime.start(actor="operator")
    order, _ = submit(first, "open-buy")
    assert order.status.value == "ACCEPTED"
    assert first.gateway.reservations.reserved_cash == Decimal("1000")

    restarted = build(tmp_path)
    assert len(restarted.oms.orders) == 1
    assert restarted.oms.orders[0].internal_order_id == order.internal_order_id
    assert restarted.gateway.reservations.reserved_cash == Decimal("1000")
    assert restarted.risk.state.value == "HALTED"
