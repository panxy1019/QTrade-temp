from contextlib import suppress
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from threading import Barrier, BrokenBarrierError, Thread

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
    with pytest.raises(RuntimeError, match="not started"):
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


@pytest.mark.parametrize(
    ("account", "message"),
    (
        (
            {
                "cash": "100000",
                "positions": {
                    "510300": {
                        "quantity": True,
                        "average_cost": "10",
                        "realized_pnl": "0",
                    }
                },
            },
            "position quantity",
        ),
        ({"cash": "NaN", "positions": {}}, "account cash"),
    ),
)
def test_restart_rejects_malformed_account_state(tmp_path, account, message):
    first = build(tmp_path)
    first.journal.save_state("account", account)

    with pytest.raises(ValueError, match=message):
        build(tmp_path)


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
    with pytest.raises(ValueError, match="uncertain"):
        restarted.reconcile_and_recover(actor="operator")
    assert restarted.risk.state.value == "HALTED"


def test_restart_rejects_boolean_order_quantity(tmp_path):
    first = build(tmp_path)
    attest(first)
    first.runtime.start(actor="operator")
    submit(first, "malformed-order")
    runtime = first.journal.load_state("runtime")
    assert runtime is not None
    orders = runtime["orders"]
    assert isinstance(orders, list) and isinstance(orders[0], dict)
    request = orders[0]["request"]
    assert isinstance(request, dict)
    request["quantity"] = True
    first.journal.save_state("runtime", runtime)

    with pytest.raises(ValueError, match="order quantity"):
        build(tmp_path)


@pytest.mark.parametrize(
    ("field", "value", "message"),
    (
        ("remaining_quantity", True, "reservation remaining quantity"),
        ("remaining_cash", "NaN", "reservation remaining cash"),
    ),
)
def test_restart_rejects_malformed_reservation_state(tmp_path, field, value, message):
    first = build(tmp_path)
    attest(first)
    first.runtime.start(actor="operator")
    submit(first, "malformed-reservation")
    runtime = first.journal.load_state("runtime")
    assert runtime is not None
    reservations = runtime["reservations"]
    assert isinstance(reservations, list) and isinstance(reservations[0], dict)
    reservations[0][field] = value
    first.journal.save_state("runtime", runtime)

    with pytest.raises(ValueError, match=message):
        build(tmp_path)


@pytest.mark.parametrize(
    ("field", "message"),
    (
        ("reservations", "durable reservations"),
        ("sellability_lots", "durable sellability lots"),
    ),
)
def test_restart_rejects_non_list_runtime_collection(tmp_path, field, message):
    first = build(tmp_path)
    attest(first)
    first.runtime.start(actor="operator")
    submit(first, "malformed-runtime-collection")
    runtime = first.journal.load_state("runtime")
    assert runtime is not None
    runtime[field] = {}
    first.journal.save_state("runtime", runtime)

    with pytest.raises(ValueError, match=message):
        build(tmp_path)


def test_restart_rejects_malformed_sellability_lot_quantity(tmp_path):
    first = build(tmp_path)
    attest(first)
    first.runtime.start(actor="operator")
    request = OrderRequest(
        "malformed-lot",
        "strategy",
        "portfolio",
        "510300",
        Side.BUY,
        Decimal("10"),
        100,
        NOW,
        trading_day=date(2026, 8, 12),
        sellable_on=date(2026, 8, 13),
    )
    order, _ = first.service.submit(
        request,
        RiskContext(
            first.ledger.account,
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
    first.gateway.fill(
        order, fill_key="malformed-lot-fill", price=Decimal("10"), quantity=100, now=NOW
    )
    runtime = first.journal.load_state("runtime")
    assert runtime is not None
    lots = runtime["sellability_lots"]
    assert isinstance(lots, list) and isinstance(lots[0], dict)
    lots[0]["quantity"] = True
    first.journal.save_state("runtime", runtime)

    with pytest.raises(ValueError, match="sellability lot quantity"):
        build(tmp_path)


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

    def crash_after_commit(projected, trade):
        raise RuntimeError("simulated process crash")

    monkeypatch.setattr(bundle.ledger, "commit_projection", crash_after_commit)
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


def test_audit_failure_after_applied_commit_converges_and_halts(tmp_path, monkeypatch):
    bundle = build(tmp_path)
    attest(bundle)
    bundle.runtime.start(actor="operator")
    order, _ = submit(bundle, "audit-after-commit")

    monkeypatch.setattr(
        bundle.journal,
        "append",
        lambda _event: (_ for _ in ()).throw(OSError("audit sink failure")),
    )
    with pytest.raises(OSError, match="audit sink failure"):
        bundle.gateway.fill(
            order,
            fill_key="audit-after-commit-fill",
            price=Decimal("10"),
            quantity=100,
            now=NOW,
        )

    marker = bundle.journal.get_fill("audit-after-commit-fill")
    assert marker is not None and marker.state == "APPLIED"
    assert bundle.ledger.account.cash == Decimal("99000")
    assert bundle.ledger.account.position_quantity("510300") == 100
    assert order.status.value == "FILLED" and order.filled_quantity == 100
    assert bundle.gateway.reservations.reserved_cash == Decimal("0")
    assert bundle.risk.state.value == "HALTED"
    assert bundle.metrics.reconciliation_status == "FAILED"

    restarted = build(tmp_path)
    assert restarted.ledger.account.cash == Decimal("99000")
    assert restarted.ledger.account.position_quantity("510300") == 100
    assert restarted.oms.orders[0].status.value == "FILLED"
    assert restarted.gateway.reservations.reserved_cash == Decimal("0")


def test_concurrent_multi_symbol_fills_commit_one_serial_account_state(
    tmp_path, monkeypatch
):
    bundle = build(tmp_path)
    attest(bundle)
    bundle.runtime.start(actor="operator")

    def submit_symbol(key: str, symbol: str, quantity: int):
        return bundle.service.submit(
            OrderRequest(
                key,
                "strategy",
                "portfolio",
                symbol,
                Side.BUY,
                Decimal("10"),
                quantity,
                NOW,
            ),
            RiskContext(
                bundle.ledger.account,
                {symbol: Instrument(symbol, "SSE", "ETF", 100, Decimal("0.001"))},
                {symbol: Decimal("10")},
                NOW,
                True,
                True,
                NOW,
                sellable_quantities={},
            ),
            now=NOW,
        )[0]

    first = submit_symbol("multi-fill-1", "510300", 100)
    second = submit_symbol("multi-fill-2", "510500", 200)
    original_project = bundle.ledger.project
    projection_barrier = Barrier(2)

    def synchronized_project(trade):
        projected = original_project(trade)
        with suppress(BrokenBarrierError):
            projection_barrier.wait(timeout=0.2)
        return projected

    monkeypatch.setattr(bundle.ledger, "project", synchronized_project)
    errors: list[BaseException] = []

    def fill(order, key: str, quantity: int) -> None:
        try:
            bundle.gateway.fill(
                order,
                fill_key=key,
                price=Decimal("10"),
                quantity=quantity,
                now=NOW,
            )
        except BaseException as exc:
            errors.append(exc)

    threads = [
        Thread(target=fill, args=(first, "multi-fill-event-1", 100)),
        Thread(target=fill, args=(second, "multi-fill-event-2", 200)),
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=3)

    assert all(not thread.is_alive() for thread in threads)
    assert errors == []
    assert bundle.ledger.account.cash == Decimal("97000")
    assert bundle.ledger.account.position_quantity("510300") == 100
    assert bundle.ledger.account.position_quantity("510500") == 200
    assert {order.status.value for order in bundle.oms.orders} == {"FILLED"}
    assert bundle.gateway.reservations.reserved_cash == Decimal("0")
    assert bundle.journal.get_fill("multi-fill-event-1").state == "APPLIED"  # type: ignore[union-attr]
    assert bundle.journal.get_fill("multi-fill-event-2").state == "APPLIED"  # type: ignore[union-attr]

    restarted = build(tmp_path)
    assert restarted.ledger.account.cash == Decimal("97000")
    assert restarted.ledger.account.position_quantity("510300") == 100
    assert restarted.ledger.account.position_quantity("510500") == 200
    assert {order.status.value for order in restarted.oms.orders} == {"FILLED"}
    assert restarted.gateway.reservations.reserved_cash == Decimal("0")


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
    assert bundle.metrics.cancels_total == 1


def test_cancel_persistence_failure_halts_and_retry_can_persist(tmp_path, monkeypatch):
    bundle = build(tmp_path)
    attest(bundle)
    bundle.runtime.start(actor="operator")
    order, _ = submit(bundle, "cancel-persistence")
    original_save = bundle.journal.save_states

    monkeypatch.setattr(
        bundle.journal,
        "save_states",
        lambda _states: (_ for _ in ()).throw(OSError("cancel database failure")),
    )
    command = CancelCommand(order.internal_order_id, "cancel-persistence-key")
    with pytest.raises(OSError, match="cancel database failure"):
        bundle.service.cancel(command, now=NOW)
    assert bundle.risk.state.value == "HALTED"

    monkeypatch.setattr(bundle.journal, "save_states", original_save)
    retried = bundle.service.cancel(command, now=NOW)
    assert retried.status.value == "CANCELLED"
    assert bundle.metrics.cancels_total == 1

    restarted = build(tmp_path)
    assert restarted.oms.orders[0].status.value == "CANCELLED"
    assert restarted.gateway.reservations.reserved_cash == Decimal("0")


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
    with pytest.raises(RuntimeError, match="not started"):
        submit(bundle, "blocked-after-database-failure")


def test_invalid_adverse_fill_never_becomes_durable_applied_state(tmp_path):
    bundle = build_paper_runtime(
        mode=RuntimeMode.PAPER,
        opening_cash=Decimal("1000"),
        limits=RiskLimits(Decimal("5000"), 10000, Decimal("5000")),
        fees=FeeSchedule("test", Decimal("0"), Decimal("0")),
        journal_path=tmp_path / "runtime.db",
    )
    attest(bundle)
    bundle.runtime.start(actor="operator")
    order, _ = submit(bundle, "adverse-fill")

    with pytest.raises(ValueError, match="cash negative"):
        bundle.gateway.fill(
            order,
            fill_key="adverse-fill",
            price=Decimal("20"),
            quantity=100,
            now=NOW,
        )

    marker = bundle.journal.get_fill("adverse-fill")
    assert marker is not None and marker.state == "FAILED"
    restarted = build_paper_runtime(
        mode=RuntimeMode.PAPER,
        opening_cash=Decimal("1000"),
        limits=RiskLimits(Decimal("5000"), 10000, Decimal("5000")),
        fees=FeeSchedule("test", Decimal("0"), Decimal("0")),
        journal_path=tmp_path / "runtime.db",
    )
    assert restarted.ledger.account.cash == Decimal("1000")
    assert restarted.ledger.account.position_quantity("510300") == 0
    assert restarted.risk.state.value == "HALTED"


def test_monitoring_failure_cannot_turn_successful_effect_into_failure(tmp_path, monkeypatch):
    bundle = build(tmp_path)
    attest(bundle)
    bundle.runtime.start(actor="operator")

    metrics_type = type(bundle.metrics)
    original_record_order = metrics_type.record_order
    monkeypatch.setattr(
        metrics_type,
        "record_order",
        lambda _self, **_kwargs: (_ for _ in ()).throw(RuntimeError("metrics down")),
    )
    order, created = submit(bundle, "metrics-submit-failure")
    assert created is True and order.status.value == "ACCEPTED"
    assert bundle.metrics.monitoring_degraded is True

    monkeypatch.setattr(metrics_type, "record_order", original_record_order)
    monkeypatch.setattr(
        metrics_type,
        "record_fill",
        lambda _self: (_ for _ in ()).throw(RuntimeError("metrics down")),
    )
    trade, fill_created = bundle.gateway.fill(
        order,
        fill_key="metrics-fill-failure",
        price=Decimal("10"),
        quantity=100,
        now=NOW,
    )
    assert fill_created is True and trade.internal_order_id == order.internal_order_id
    assert order.status.value == "FILLED"
    assert bundle.ledger.account.position_quantity("510300") == 100
