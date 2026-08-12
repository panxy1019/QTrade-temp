from datetime import UTC, datetime

from qtrade.audit import AuditEvent, JsonlAuditSink


def test_jsonl_audit_sink_appends_complete_records(tmp_path):
    target = tmp_path / "audit.jsonl"
    sink = JsonlAuditSink(target)
    event = AuditEvent(
        event_type="KILL_SWITCH",
        message="manual halt",
        service="risk",
        severity="WARNING",
        timestamp=datetime(2026, 8, 12, tzinfo=UTC),
    )

    sink.append(event)
    sink.append(event)

    lines = target.read_text(encoding="utf-8").splitlines()
    assert len(lines) == 2
    assert all('"event_type": "KILL_SWITCH"' in line for line in lines)
    assert sink.verify_chain() is True


def test_jsonl_audit_chain_detects_mutation(tmp_path):
    target = tmp_path / "audit.jsonl"
    sink = JsonlAuditSink(target)
    event = AuditEvent(
        event_type="KILL_SWITCH",
        message="manual halt",
        service="risk",
        timestamp=datetime(2026, 8, 12, tzinfo=UTC),
    )
    sink.append(event)
    target.write_text(
        target.read_text(encoding="utf-8").replace("manual halt", "silently changed"),
        encoding="utf-8",
    )
    assert sink.verify_chain() is False
