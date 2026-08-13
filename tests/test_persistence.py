import sqlite3
from datetime import UTC, date, datetime

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
    events = SQLiteOperationalJournal(tmp_path / "runtime.db").recent_audit_events()
    assert [(event.event_type, event.message) for event in events] == [("TEST", "durable")]


def test_sqlite_audit_reader_is_bounded_and_chronological(tmp_path):
    journal = SQLiteOperationalJournal(tmp_path / "runtime.db")
    for index in range(3):
        journal.append(
            AuditEvent(
                event_type=f"TEST_{index}",
                service="test",
                message=str(index),
                timestamp=datetime(2026, 8, 12, index, tzinfo=UTC),
            )
        )
    assert [event.message for event in journal.recent_audit_events(2)] == ["1", "2"]
    with pytest.raises(ValueError, match="between 1 and 1000"):
        journal.recent_audit_events(1001)


def test_sqlite_daily_incidents_are_filtered_before_the_bound(tmp_path):
    journal = SQLiteOperationalJournal(tmp_path / "runtime.db")
    for index in range(4):
        journal.append(
            AuditEvent(
                event_type=f"INFO_{index}",
                service="test",
                message="noise",
                timestamp=datetime(2026, 8, 12, index, tzinfo=UTC),
            )
        )
    journal.append(
        AuditEvent(
            event_type="INCIDENT",
            service="test",
            severity="ERROR",
            message="kept",
            timestamp=datetime(2026, 8, 12, 5, tzinfo=UTC),
        )
    )
    assert [
        event.message for event in journal.audit_incidents_for_day(date(2026, 8, 12), 1)
    ] == ["kept"]


def test_sqlite_daily_incidents_use_shanghai_day_boundary(tmp_path):
    journal = SQLiteOperationalJournal(tmp_path / "runtime.db")
    journal.append(
        AuditEvent(
            event_type="INCIDENT",
            service="test",
            severity="ERROR",
            message="shanghai-day",
            timestamp=datetime(2026, 8, 11, 16, 30, tzinfo=UTC),
        )
    )
    assert [
        event.message for event in journal.audit_incidents_for_day(date(2026, 8, 12))
    ] == ["shanghai-day"]


def test_sqlite_audit_reader_rejects_corrupt_payload_schema(tmp_path):
    path = tmp_path / "runtime.db"
    journal = SQLiteOperationalJournal(path)
    journal.append(
        AuditEvent(event_type="TEST", service="test", message="valid")
    )
    with sqlite3.connect(path) as connection:
        connection.execute(
            "UPDATE audit_events SET payload=?",
            ('{"message":{"forged":1},"portfolio_id":null,"strategy_id":null}',),
        )
    with pytest.raises(ValueError, match="message is invalid"):
        journal.recent_audit_events()


def test_sqlite_runtime_snapshots_commit_as_one_transaction(tmp_path):
    journal = SQLiteOperationalJournal(tmp_path / "runtime.db")
    journal.save_states({"account": {"cash": "10"}, "runtime": {"orders": []}})
    assert journal.load_state("account") == {"cash": "10"}
    assert journal.load_state("runtime") == {"orders": []}


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
