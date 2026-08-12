from datetime import UTC, datetime

import pytest

from qtrade.audit import AuditEvent
from qtrade.persistence import SQLiteOperationalJournal


def test_sqlite_journal_persists_uncertain_fill_across_restart(tmp_path):
    path = tmp_path / "runtime.db"
    first = SQLiteOperationalJournal(path)
    assert first.begin_fill("fill-1", "order-1", "hash-1") is True

    restarted = SQLiteOperationalJournal(path)
    assert restarted.begin_fill("fill-1", "order-1", "hash-1") is False
    assert restarted.uncertain_fills()[0].state == "PENDING"
    restarted.mark_fill("fill-1", "APPLIED")
    assert restarted.uncertain_fills() == ()


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
