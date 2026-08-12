from datetime import UTC, datetime

import pytest

from qtrade.accounting import Ledger
from qtrade.audit import AuditEvent, AuditLog
from qtrade.domain import Instrument, OrderRequest, RuntimeMode, Side
from qtrade.execution import FeeSchedule, PaperGateway
from qtrade.oms import OrderManager
from qtrade.persistence import SQLiteOperationalJournal
from qtrade.risk import RiskContext, RiskEngine, RiskLimits
from qtrade.service import TradingService


def test_sqlite_journal_persists_uncertain_fill_across_restart(tmp_path):
    path = tmp_path / "runtime.db"
    first = SQLiteOperationalJournal(path)
    assert first.begin_fill("fill-1", "order-1", "hash-1") is True

    restarted = SQLiteOperationalJournal(path)
    assert restarted.begin_fill("fill-1", "order-1", "hash-1") is False
    assert restarted.uncertain_fills()[0].state == "PENDING"
    restarted.mark_fill("fill-1", "APPLIED")
    assert restarted.uncertain_fills() == ()
    with pytest.raises(ValueError, match="immutable"):
        restarted.mark_fill("fill-1", "FAILED", "late-error")


def test_sqlite_journal_rejects_fill_key_conflict_and_persists_audit(tmp_path):
    journal = SQLiteOperationalJournal(tmp_path / "runtime.db")
    journal.begin_fill("fill-1", "order-1", "hash-1")
    with pytest.raises(ValueError, match="conflicts"):
        journal.begin_fill("fill-1", "order-2", "hash-2")
    journal.append(
        AuditEvent(
            event_type="TEST",
            service="test",
            message="durable",
            timestamp=datetime(2026, 8, 12, tzinfo=UTC),
        )
    )


def test_paper_gateway_restart_refuses_pending_fill_replay(tmp_path):
    from decimal import Decimal

    now = datetime(2026, 8, 12, tzinfo=UTC)
    audit = AuditLog()
    ledger = Ledger(Decimal("100000"), audit)
    oms = OrderManager(audit)
    fees = FeeSchedule("test", Decimal("0"), Decimal("0"))
    gateway = PaperGateway(oms, ledger, audit, fees)
    risk = RiskEngine(RiskLimits(Decimal("50000"), 10000, Decimal("100000")), audit)
    service = TradingService(risk, oms, gateway, RuntimeMode.BACKTEST)
    order, _ = service.submit(
        OrderRequest("key", "strategy", "portfolio", "510300", Side.BUY, Decimal("10"), 100, now),
        RiskContext(
            ledger.account,
            {"510300": Instrument("510300", "SSE", "ETF", 100, Decimal("0.001"))},
            {"510300": Decimal("10")},
            now,
            True,
            True,
            now,
            sellable_quantities={},
        ),
        now=now,
    )
    journal = SQLiteOperationalJournal(tmp_path / "runtime.db")
    journal.begin_fill("fill-pending", order.internal_order_id, "different-hash")
    restarted_gateway = PaperGateway(oms, ledger, audit, fees, journal=journal)
    with pytest.raises(ValueError, match="conflicts"):
        restarted_gateway.fill(
            order,
            fill_key="fill-pending",
            price=Decimal("10"),
            quantity=100,
            now=now,
        )
