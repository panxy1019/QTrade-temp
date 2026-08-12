from datetime import UTC, datetime
from decimal import Decimal

import pytest

from qtrade.domain import Instrument, OrderRequest, RuntimeMode, Side
from qtrade.execution import FeeSchedule
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
