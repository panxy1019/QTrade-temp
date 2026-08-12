from __future__ import annotations

from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from qtrade.accounting import Ledger
from qtrade.audit import AuditLog
from qtrade.config import SafetyConfig, UnsafeConfiguration
from qtrade.domain import Instrument, OrderRequest, OrderStatus, RuntimeMode, Side
from qtrade.execution import FeeSchedule, PaperAction, PaperGateway
from qtrade.oms import DuplicateOrderConflict, InvalidTransition, OrderManager
from qtrade.risk import KillState, RiskContext, RiskEngine, RiskLimits
from qtrade.service import TradingService

NOW = datetime(2026, 8, 12, 2, 0, tzinfo=UTC)


def build_system(opening_cash: Decimal = Decimal("100000")):
    audit = AuditLog()
    ledger = Ledger(opening_cash, audit)
    oms = OrderManager(audit)
    risk = RiskEngine(
        RiskLimits(
            max_order_value=Decimal("50000"),
            max_position_quantity=10_000,
            max_gross_exposure=Decimal("100000"),
            max_price_deviation=Decimal("0.10"),
            cash_reserve=Decimal("1000"),
            estimated_buy_fee_rate=Decimal("0.0003"),
        ),
        audit,
    )
    gateway = PaperGateway(
        oms,
        ledger,
        audit,
        FeeSchedule(
            version="test-v1",
            commission_rate=Decimal("0.0003"),
            sell_tax_rate=Decimal("0.0005"),
        ),
    )
    return TradingService(risk, oms, gateway), risk, oms, gateway, ledger, audit


def request(
    *,
    key: str = "key-1",
    side: Side = Side.BUY,
    price: str = "10.00",
    quantity: int = 100,
) -> OrderRequest:
    return OrderRequest(
        idempotency_key=key,
        strategy_id="strategy-1",
        portfolio_id="portfolio-1",
        symbol="510300",
        side=side,
        price=Decimal(price),
        quantity=quantity,
        created_at=NOW,
    )


def context(ledger: Ledger, **overrides: object) -> RiskContext:
    values: dict[str, object] = {
        "account": ledger.account,
        "instruments": {
            "510300": Instrument(
                symbol="510300",
                exchange="SSE",
                security_type="ETF",
                lot_size=100,
                price_tick=Decimal("0.001"),
            )
        },
        "market_prices": {"510300": Decimal("10.00")},
        "market_timestamp": NOW,
        "gateway_connected": True,
        "reconciliation_ok": True,
        "now": NOW,
        "sellable_quantities": {
            symbol: position.quantity for symbol, position in ledger.account.positions.items()
        },
    }
    values.update(overrides)
    return RiskContext(**values)  # type: ignore[arg-type]


def test_live_mode_is_not_representable_and_environment_default_is_false(monkeypatch):
    assert {mode.value for mode in RuntimeMode} == {"BACKTEST", "PAPER", "SHADOW"}
    monkeypatch.delenv("LIVE_TRADING_ENABLED", raising=False)
    assert "LIVE" not in RuntimeMode.__members__
    assert SafetyConfig.from_environment().live_trading_enabled is False


def test_live_environment_setting_is_rejected(monkeypatch):
    monkeypatch.setenv("LIVE_TRADING_ENABLED", "true")
    with pytest.raises(UnsafeConfiguration):
        SafetyConfig.from_environment()


def test_external_gateway_capability_is_rejected():
    _, risk, oms, _, _, _ = build_system()

    class UnsafeGateway:
        external_order_transmission = True

    with pytest.raises(ValueError, match="external order transmission"):
        TradingService(risk, oms, UnsafeGateway())  # type: ignore[arg-type]


def test_end_to_end_partial_fill_is_idempotent_and_books_cash():
    service, _, _, gateway, ledger, audit = build_system()
    order, created = service.submit(request(), context(ledger), now=NOW)

    assert created is True
    assert order.status is OrderStatus.ACCEPTED
    trade, first = gateway.fill(
        order, fill_key="fill-1", price=Decimal("10.00"), quantity=40, now=NOW
    )
    duplicate, second = gateway.fill(
        order, fill_key="fill-1", price=Decimal("10.00"), quantity=40, now=NOW
    )

    assert first is True and second is False
    assert duplicate == trade
    assert order.status is OrderStatus.PARTIALLY_FILLED
    assert order.filled_quantity == 40
    assert ledger.account.cash == Decimal("99599.88")
    assert ledger.account.position_quantity("510300") == 40
    assert sum(event.event_type == "TRADE_BOOKED" for event in audit.events) == 1


def test_duplicate_submission_returns_original_without_second_gateway_order():
    service, _, _, _, ledger, audit = build_system()
    first, created_first = service.submit(request(), context(ledger), now=NOW)
    second, created_second = service.submit(request(), context(ledger), now=NOW)

    assert first is second
    assert created_first is True and created_second is False
    assert sum(event.event_type == "PAPER_ORDER_ACCEPTED" for event in audit.events) == 1


def test_idempotency_key_reuse_with_different_request_fails():
    service, _, _, _, ledger, _ = build_system()
    service.submit(request(), context(ledger), now=NOW)

    with pytest.raises(DuplicateOrderConflict):
        service.submit(request(price="10.01"), context(ledger), now=NOW)


@pytest.mark.parametrize(
    ("change", "expected"),
    [
        ({"gateway_connected": False}, "gateway disconnected"),
        ({"reconciliation_ok": False}, "reconciliation failed"),
        ({"market_timestamp": NOW - timedelta(minutes=1)}, "stale market data"),
    ],
)
def test_operational_risk_fails_closed(change, expected):
    service, _, _, _, ledger, _ = build_system()
    order, _ = service.submit(request(), context(ledger, **change), now=NOW)

    assert order.status is OrderStatus.RISK_REJECTED
    assert expected in (order.rejection_reason or "")


def test_halted_and_paused_reject_new_orders_and_state_change_is_audited():
    for state in (KillState.HALTED, KillState.PAUSED):
        service, risk, _, _, ledger, audit = build_system()
        risk.set_state(state, actor="operator", reason="test", now=NOW)
        order, _ = service.submit(request(key=f"key-{state}"), context(ledger), now=NOW)

        assert order.status is OrderStatus.RISK_REJECTED
        assert risk.state is state
        assert any(event.event_type == "RISK_STATE_CHANGED" for event in audit.events)


def test_reduce_only_allows_only_position_reduction():
    service, risk, _, gateway, ledger, _ = build_system()
    buy, _ = service.submit(request(key="buy"), context(ledger), now=NOW)
    gateway.fill(buy, fill_key="initial", price=Decimal("10"), quantity=100, now=NOW)
    risk.set_state(KillState.REDUCE_ONLY, actor="operator", reason="test", now=NOW)

    rejected, _ = service.submit(request(key="new-buy"), context(ledger), now=NOW)
    sell, _ = service.submit(
        request(key="sell", side=Side.SELL, quantity=100), context(ledger), now=NOW
    )

    assert rejected.status is OrderStatus.RISK_REJECTED
    assert sell.status is OrderStatus.ACCEPTED


@pytest.mark.parametrize(
    ("req", "reason"),
    [
        (request(key="quantity", quantity=1), "invalid quantity"),
        (request(key="price", price="12.00"), "abnormal price"),
        (request(key="cash", price="10", quantity=10_000), "order value limit"),
    ],
)
def test_order_limits_are_enforced(req, reason):
    service, _, _, _, ledger, _ = build_system()
    order, _ = service.submit(req, context(ledger), now=NOW)
    assert order.status is OrderStatus.RISK_REJECTED
    assert reason in (order.rejection_reason or "")


def test_buy_cash_check_includes_estimated_fees():
    service, _, _, _, ledger, _ = build_system(opening_cash=Decimal("1000.20"))
    order, _ = service.submit(request(), context(ledger), now=NOW)
    assert order.status is OrderStatus.RISK_REJECTED
    assert order.rejection_reason == "insufficient cash"


def test_open_buy_order_reserves_cash_against_competing_order():
    service, _, _, _, ledger, _ = build_system(opening_cash=Decimal("2100"))
    first, _ = service.submit(request(key="first"), context(ledger), now=NOW)
    second, _ = service.submit(request(key="second"), context(ledger), now=NOW)
    assert first.status is OrderStatus.ACCEPTED
    assert second.status is OrderStatus.RISK_REJECTED
    assert second.rejection_reason == "insufficient cash"


def test_open_buy_orders_count_toward_position_and_gross_limits():
    audit = AuditLog()
    ledger = Ledger(Decimal("100000"), audit)
    oms = OrderManager(audit)
    risk = RiskEngine(RiskLimits(Decimal("10000"), 150, Decimal("1500")), audit)
    gateway = PaperGateway(oms, ledger, audit, FeeSchedule("v1", Decimal("0"), Decimal("0")))
    service = TradingService(risk, oms, gateway)
    first, _ = service.submit(request(key="one"), context(ledger), now=NOW)
    second, _ = service.submit(request(key="two"), context(ledger), now=NOW)
    assert first.status is OrderStatus.ACCEPTED
    assert second.status is OrderStatus.RISK_REJECTED
    assert second.rejection_reason in {"position limit exceeded", "gross exposure limit exceeded"}


def test_open_sell_order_reserves_position_against_competing_order():
    service, _, _, gateway, ledger, _ = build_system()
    buy, _ = service.submit(request(key="buy"), context(ledger), now=NOW)
    gateway.fill(buy, fill_key="buy", price=Decimal("10"), quantity=100, now=NOW)
    first, _ = service.submit(request(key="sell-1", side=Side.SELL), context(ledger), now=NOW)
    second, _ = service.submit(request(key="sell-2", side=Side.SELL), context(ledger), now=NOW)
    assert first.status is OrderStatus.ACCEPTED
    assert second.status is OrderStatus.RISK_REJECTED
    assert second.rejection_reason == "insufficient position"


def test_sell_fails_closed_without_verified_sellability():
    service, _, _, gateway, ledger, _ = build_system()
    buy, _ = service.submit(request(key="buy"), context(ledger), now=NOW)
    gateway.fill(buy, fill_key="buy", price=Decimal("10"), quantity=100, now=NOW)
    unavailable = context(ledger, sellable_quantities=None)
    sell, _ = service.submit(request(key="sell", side=Side.SELL), unavailable, now=NOW)
    assert sell.status is OrderStatus.RISK_REJECTED
    assert sell.rejection_reason == "sellability data unavailable"


@pytest.mark.parametrize("invalid", [Decimal("-1"), Decimal("NaN"), Decimal("Infinity")])
def test_risk_and_fee_configuration_reject_unsafe_numbers(invalid):
    with pytest.raises(ValueError):
        RiskLimits(invalid, 1, Decimal("1"))
    with pytest.raises(ValueError):
        FeeSchedule("v1", invalid, Decimal("0"))


def test_cancel_and_invalid_transition_are_enforced():
    service, _, oms, gateway, ledger, _ = build_system()
    order, _ = service.submit(request(), context(ledger), now=NOW)
    gateway.cancel(order, now=NOW)

    assert order.status is OrderStatus.CANCELLED
    with pytest.raises(InvalidTransition):
        oms.transition(order, OrderStatus.FILLED, now=NOW)


def test_cancel_is_idempotent_and_gateway_lookup_recovers_submission():
    service, _, _, gateway, ledger, audit = build_system()
    order, _ = service.submit(request(), context(ledger), now=NOW)
    assert gateway.lookup(order.internal_order_id) is order
    first = gateway.cancel(order, idempotency_key="cancel-1", now=NOW)
    second = gateway.cancel(order, idempotency_key="cancel-1", now=NOW)
    assert first is second
    assert (
        sum(
            event.event_type == "ORDER_TRANSITION" and "CANCELLED" in event.message
            for event in audit.events
        )
        == 1
    )


def test_disconnected_paper_gateway_does_not_accept_submission():
    _, _, oms, gateway, _, _ = build_system()
    order, _ = oms.create(request(), now=NOW)
    oms.transition(order, OrderStatus.RISK_PENDING, now=NOW)
    oms.transition(order, OrderStatus.RISK_PASSED, now=NOW)
    gateway.disconnect()
    with pytest.raises(ConnectionError, match="disconnected"):
        gateway.submit(order, now=NOW)
    assert gateway.lookup(order.internal_order_id) is None
    gateway.reconnect()
    assert gateway.submit(order, now=NOW).status is OrderStatus.ACCEPTED


def test_deterministic_paper_reject_and_disconnect_injection():
    service, _, _, gateway, ledger, _ = build_system()
    gateway.inject_next_action(PaperAction.REJECT)
    rejected, _ = service.submit(request(key="reject"), context(ledger), now=NOW)
    assert rejected.status is OrderStatus.REJECTED
    assert rejected.rejection_reason == "injected paper rejection"
    assert gateway.reservations.reserved_cash == Decimal("0")

    gateway.inject_next_action(PaperAction.DISCONNECT)
    with pytest.raises(ConnectionError, match="injected"):
        uncertain, _ = service.submit(request(key="disconnect"), context(ledger), now=NOW)
    assert gateway.connected is False


def test_invalid_overfill_does_not_mutate_accounting():
    service, _, _, gateway, ledger, _ = build_system()
    order, _ = service.submit(request(), context(ledger), now=NOW)
    original_cash = ledger.account.cash

    with pytest.raises(ValueError, match="invalid fill quantity"):
        gateway.fill(order, fill_key="bad", price=Decimal("10"), quantity=101, now=NOW)

    assert ledger.account.cash == original_cash
    assert ledger.account.position_quantity("510300") == 0
    assert order.filled_quantity == 0


def test_failed_fill_replay_is_blocked_pending_reconciliation(monkeypatch):
    service, _, oms, gateway, ledger, _ = build_system()
    order, _ = service.submit(request(), context(ledger), now=NOW)

    def fail_apply(*args, **kwargs):
        raise RuntimeError("injected OMS failure")

    monkeypatch.setattr(oms, "apply_fill", fail_apply)
    with pytest.raises(RuntimeError, match="injected OMS failure"):
        gateway.fill(order, fill_key="uncertain", price=Decimal("10"), quantity=100, now=NOW)
    cash_after_partial_failure = ledger.account.cash
    with pytest.raises(RuntimeError, match="reconciliation is required"):
        gateway.fill(order, fill_key="uncertain", price=Decimal("10"), quantity=100, now=NOW)
    assert ledger.account.cash == cash_after_partial_failure


def test_halted_recovery_requires_explicit_reconciliation():
    _, risk, _, _, _, _ = build_system()
    risk.set_state(KillState.HALTED, actor="operator", reason="incident", now=NOW)

    with pytest.raises(ValueError, match="successful reconciliation"):
        risk.set_state(KillState.NORMAL, actor="operator", reason="resume", now=NOW)
    risk.set_state(
        KillState.NORMAL,
        actor="operator",
        reason="manual verified resume",
        reconciliation_ok=True,
        now=NOW,
    )
    assert risk.state is KillState.NORMAL


def test_halt_latches_on_audit_failure_and_resume_remains_blocked():
    class FailingSink:
        def append(self, event):
            raise OSError("disk failure")

    audit = AuditLog(FailingSink())
    risk = RiskEngine(RiskLimits(Decimal("1"), 1, Decimal("1")), audit)
    with pytest.raises(OSError, match="disk failure"):
        risk.set_state(KillState.HALTED, actor="operator", reason="incident", now=NOW)
    assert risk.state is KillState.HALTED
    assert audit.degraded is True
    with pytest.raises(ValueError, match="audit is degraded"):
        risk.set_state(
            KillState.NORMAL,
            actor="operator",
            reason="resume",
            reconciliation_ok=True,
            now=NOW,
        )


def test_buy_then_sell_accounting_realizes_pnl_and_fees():
    service, _, _, gateway, ledger, _ = build_system()
    buy, _ = service.submit(request(key="buy"), context(ledger), now=NOW)
    gateway.fill(buy, fill_key="buy-fill", price=Decimal("10"), quantity=100, now=NOW)
    sell_request = request(key="sell", side=Side.SELL, price="11")
    sell_context = context(
        ledger,
        market_prices={"510300": Decimal("11")},
    )
    sell, _ = service.submit(sell_request, sell_context, now=NOW)
    gateway.fill(sell, fill_key="sell-fill", price=Decimal("11"), quantity=100, now=NOW)

    position = ledger.account.positions["510300"]
    assert position.quantity == 0
    assert position.realized_pnl == Decimal("98.820")
    assert ledger.account.cash == Decimal("100098.82")
