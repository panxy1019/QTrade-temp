from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from qtrade.domain import CancelCommand, Instrument, OrderRequest, RuntimeMode, Side
from qtrade.execution import FeeSchedule, PaperAction
from qtrade.reconciliation import Reconciler, ReconciliationResult
from qtrade.risk import RiskContext, RiskLimits
from qtrade.runtime import build_paper_runtime

NOW = datetime(2026, 8, 12, tzinfo=UTC)


def attest(bundle):
    bundle.runtime.attest_readiness(
        Reconciler().issue_attestation(
            ReconciliationResult(True, ()),
            gateway_connected=True,
            uncertain_fills=0,
            now=datetime.now(UTC),
            validity=timedelta(minutes=10),
        )
    )


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
    attest(bundle)
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
    attest(bundle)
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
    attest(first)
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
    attest(first)
    first.runtime.start(actor="operator")
    order, _ = submit(first, "open-buy")
    assert order.status.value == "ACCEPTED"
    assert first.gateway.reservations.reserved_cash == Decimal("1000")

    restarted = build(tmp_path)
    assert len(restarted.oms.orders) == 1
    assert restarted.oms.orders[0].internal_order_id == order.internal_order_id
    assert restarted.gateway.reservations.reserved_cash == Decimal("1000")
    assert restarted.risk.state.value == "HALTED"
    restarted.reconcile_and_recover(actor="operator")
    assert restarted.risk.state.value == "NORMAL"
    restarted.runtime.start(actor="operator")


def test_recovery_refuses_uncertain_fill(tmp_path):
    bundle = build(tmp_path)
    bundle.journal.begin_fill("pending", "order", "hash")
    with pytest.raises(ValueError, match="uncertain"):
        bundle.reconcile_and_recover(actor="operator")


def test_crash_after_atomic_fill_commit_restores_exactly_one_effect(tmp_path, monkeypatch):
    bundle = build(tmp_path)
    attest(bundle)
    bundle.runtime.start(actor="operator")
    order, _ = submit(bundle, "buy-crash")

    def crash_after_commit(trade):
        raise RuntimeError("simulated process crash")

    monkeypatch.setattr(bundle.ledger, "book", crash_after_commit)
    with pytest.raises(RuntimeError, match="simulated process crash"):
        bundle.gateway.fill(
            order, fill_key="fill-crash", price=Decimal("10"), quantity=100, now=NOW
        )

    assert bundle.journal.get_fill("fill-crash").state == "APPLIED"  # type: ignore[union-attr]
    assert bundle.journal.uncertain_fills() == ()

    restarted = build(tmp_path)
    assert restarted.ledger.account.cash == Decimal("99000")
    assert restarted.ledger.account.position_quantity("510300") == 100
    assert restarted.oms.orders[0].filled_quantity == 100
    assert restarted.gateway.reservations.reserved_cash == Decimal("0")
    assert restarted.risk.state.value == "HALTED"


def test_cancel_remains_available_after_halt_and_is_idempotent(tmp_path):
    bundle = build(tmp_path)
    attest(bundle)
    bundle.runtime.start(actor="operator")
    order, _ = submit(bundle, "open-before-halt")
    bundle.runtime.kill(actor="operator", reason="test halt")

    first = bundle.service.cancel(CancelCommand(order.internal_order_id, "cancel-key"), now=NOW)
    second = bundle.service.cancel(CancelCommand(order.internal_order_id, "cancel-key"), now=NOW)

    assert first is second
    assert first.status.value == "CANCELLED"
    assert bundle.gateway.reservations.reserved_cash == Decimal("0")


def test_runtime_metrics_count_unique_submissions_rejections_and_fills(tmp_path):
    bundle = build(tmp_path)
    attest(bundle)
    bundle.runtime.start(actor="operator")

    accepted, created = submit(bundle, "metric-order")
    replayed, replay_created = submit(bundle, "metric-order")
    bundle.gateway.fill(
        accepted, fill_key="metric-fill", price=Decimal("10"), quantity=100, now=NOW
    )
    bundle.gateway.fill(
        accepted, fill_key="metric-fill", price=Decimal("10"), quantity=100, now=NOW
    )
    rejected, _ = bundle.service.submit(
        OrderRequest(
            "metric-reject",
            "strategy",
            "portfolio",
            "510300",
            Side.BUY,
            Decimal("10"),
            99,
            NOW,
        ),
        RiskContext(
            bundle.ledger.account,
            {"510300": Instrument("510300", "SSE", "ETF", 100, Decimal("0.001"))},
            {"510300": Decimal("10")},
            NOW,
            True,
            True,
            NOW,
        ),
        now=NOW,
    )

    metrics = bundle.metrics.snapshot(now=NOW)
    assert created is True and replay_created is False and replayed is accepted
    assert rejected.status.value == "RISK_REJECTED"
    assert metrics["orders_total"] == 2
    assert metrics["rejects_total"] == 1
    assert metrics["fills_total"] == 1
    assert metrics["reject_rate"] == "0.5"
    assert metrics["fill_rate"] == "0.5"
    assert metrics["orders_per_minute"] == 2


def test_runtime_state_persistence_failure_latches_halt(tmp_path, monkeypatch):
    bundle = build(tmp_path)
    attest(bundle)
    bundle.runtime.start(actor="operator")

    def fail_snapshot(states):
        raise OSError("simulated database failure")

    monkeypatch.setattr(bundle.journal, "save_states", fail_snapshot)
    with pytest.raises(OSError, match="database failure"):
        submit(bundle, "database-failure")

    assert bundle.risk.state.value == "HALTED"
    assert bundle.metrics.reconciliation_status == "FAILED"
    with pytest.raises(RuntimeError, match="risk state HALTED"):
        submit(bundle, "blocked-after-database-failure")
