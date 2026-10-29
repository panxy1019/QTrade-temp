"""Synthetic clocks/books, actual local SQL/raw/Parquet/accounting replay only."""

import json
import sqlite3
from copy import deepcopy
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest
from test_collect_unified_data import CALENDAR
from test_forward_transition_audit import books  # noqa:F401
from test_unified_store import store  # noqa:F401

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts import audit_qlib_execution_day_scope as scoped
from scripts import audit_qlib_forward_transition as original

DAY = date(2026, 10, 26)
OPEN = datetime(2026, 10, 26, 1, 30, tzinfo=UTC)
CLOSE = datetime(2026, 10, 26, 7, tzinfo=UTC)


@pytest.fixture
def fixed(books):  # noqa:F811
    for (_strategy, mode), identity in books.identities.items():
        if mode == "PAPER":
            with sqlite3.connect(books.root / identity / "operations.sqlite") as db:
                db.execute(
                    "UPDATE audit_events SET timestamp=?",
                    ((OPEN - timedelta(hours=1)).isoformat(),),
                )
    identity = books.identities[("approved_model_" + books.model, "PAPER")]
    config = original.document(books.root / "roster.json")["books"][identity]
    books.selected = identity
    books.config = config
    return books


def run_book(value, public=None):
    return scoped.audit_execution_book(
        value.root,
        value.base / "unified-data/lake",
        value.selected,
        value.config,
        public,
        DAY,
        CALENDAR,
        value.now,
        {},
    )


def test_post_close_stop_preserves_real_historical_observation(fixed):
    with sqlite3.connect(fixed.root / fixed.selected / "operations.sqlite") as db:
        db.execute(
            "INSERT INTO audit_events VALUES(2,?,?,?)",
            ("SYSTEM_STOP", (CLOSE + timedelta(hours=5)).isoformat(), "control"),
        )
    before = original.audit_book(
        fixed.root,
        fixed.base / "unified-data/lake",
        fixed.selected,
        fixed.config,
        None,
        DAY,
        CALENDAR,
        fixed.now,
        {},
    )
    after = run_book(fixed)
    assert "FORWARD_AUDIT_LOCAL_PAPER_NOT_RUNNING" in before["failures"]
    assert after["accepted"] and after["fill_count"] == 0 and after["blocked_count"] == 1
    assert after["admitted_archive"]["model_id"] == fixed.model


@pytest.mark.parametrize("event", ["SYSTEM_STOP", "KILL_SWITCH", "SYSTEM_HALT"])
def test_intraday_stop_does_not_become_valid_by_ignoring_current_runtime(fixed, event):
    with sqlite3.connect(fixed.root / fixed.selected / "operations.sqlite") as db:
        db.execute(
            "INSERT INTO audit_events VALUES(2,?,?,?)",
            (event, (OPEN + timedelta(hours=1)).isoformat(), "control"),
        )
    assert run_book(fixed)["accepted"] is False


@pytest.mark.parametrize(
    "damage",
    [
        "missing_session",
        "pending",
        "replaced_target",
        "changed_input",
        "blocked_missing",
        "forged_cash",
        "shadow_fill",
        "late_admission",
        "missing_admission",
        "bad_admission_hash",
    ],
)
def test_original_historical_economic_and_prospective_gates_remain(fixed, damage):
    book = fixed.root / fixed.selected
    if damage in {
        "missing_session",
        "pending",
        "replaced_target",
        "changed_input",
        "blocked_missing",
        "forged_cash",
        "shadow_fill",
    }:
        with sqlite3.connect(book / "forward.sqlite") as db:
            if damage == "missing_session":
                db.execute("DELETE FROM sessions")
            elif damage == "pending":
                db.execute("UPDATE sessions SET status='PENDING'")
            elif damage == "replaced_target":
                db.execute("UPDATE targets SET id=?", ("9" * 64,))
            elif damage == "changed_input":
                db.execute("UPDATE sessions SET input_hash=?", ("9" * 64,))
            else:
                value = json.loads(db.execute("SELECT result FROM sessions").fetchone()[0])
                if damage == "blocked_missing":
                    value["blocked"] = {}
                elif damage == "forged_cash":
                    value["cash"] = "1000001"
                elif damage == "shadow_fill":
                    value["fills"] = [{"symbol": "SH600036"}]
                db.execute("UPDATE sessions SET result=?", (json.dumps(value),))
    else:
        path = next((book / "admissions").glob("*.json"))
        value = json.loads(path.read_bytes())
        if damage == "missing_admission":
            path.unlink()
        else:
            if damage == "late_admission":
                value["verified_at"] = OPEN.isoformat()
                value = signed({k: v for k, v in value.items() if k != "evidence_sha256"})
            else:
                value["evidence_sha256"] = "9" * 64
            path.write_text(json.dumps(value))
    assert run_book(fixed)["accepted"] is False


def test_stale_or_stopped_current_public_does_not_replace_frozen_session(fixed):
    public = deepcopy(
        next(
            b
            for b in fixed.publication["books"]
            if b["strategy_id"] == fixed.config["strategy_id"] and b["mode"] == "PAPER"
        )
    )
    public.update(running=False, risk_state="HALTED", recovery_required=True)
    # This function compares only the preserved historical session and target.
    assert run_book(fixed, public)["accepted"] is True
    public["sessions"] = []
    assert run_book(fixed, public)["accepted"] is False


def model_fixture(path):
    old = "a" * 64
    new = "b" * 64

    def receipt(model, at):
        return signed(
            {**AUTHORITY, "state": "APPLIED", "model_id": model, "active_model": model, "at": at}
        )

    previous = receipt(old, "2026-10-25T12:00:00+00:00")
    current = receipt(new, "2026-10-26T12:00:00+00:00")
    with sqlite3.connect(path) as db:
        db.executescript(
            "CREATE TABLE models(model_id TEXT,state TEXT,activated_at TEXT,"
            "activation_count INTEGER);"
            "CREATE TABLE active_model(singleton INTEGER,model_id TEXT);"
            "CREATE TABLE model_events(sequence INTEGER,model_id TEXT,kind TEXT,"
            "at TEXT,payload TEXT);"
        )
        db.executemany(
            "INSERT INTO models VALUES(?,?,?,1)",
            [(old, "RETIRED", previous["at"]), (new, "ACTIVE", current["at"])],
        )
        db.execute("INSERT INTO active_model VALUES(1,?)", (new,))
        for i, value in enumerate([previous, current], 1):
            event = signed(
                {
                    **AUTHORITY,
                    "kind": "CONTROL_APPLIED",
                    "model_id": value["model_id"],
                    "at": value["at"],
                    "details": value,
                }
            )
            db.execute(
                "INSERT INTO model_events VALUES(?,?,?,?,?)",
                (i, event["model_id"], event["kind"], event["at"], json.dumps(event)),
            )
    return old, new, previous, current


def test_day_model_and_post_close_model_have_distinct_approvals(tmp_path):
    path = tmp_path / "models.sqlite"
    old, new, previous, current = model_fixture(path)
    result = scoped.approval_scope(
        path,
        DAY,
        old,
        previous["evidence_sha256"],
        new,
        current["evidence_sha256"],
        datetime.now(UTC),
    )
    assert result["execution_approval"] == previous
    assert result["current_approval"] == current


@pytest.mark.parametrize(
    "damage",
    [
        "old_relabelled_as_new",
        "wrong_old_approval",
        "wrong_new_approval",
        "intraday_switch",
        "future_switch",
        "naive_switch",
        "changed_current_pointer",
    ],
)
def test_approval_scope_fails_closed_under_resealed_changes(tmp_path, damage):
    path = tmp_path / "models.sqlite"
    old, new, previous, current = model_fixture(path)
    if damage == "old_relabelled_as_new":
        old = new
    elif damage == "wrong_old_approval":
        previous["evidence_sha256"] = "f" * 64
    elif damage == "wrong_new_approval":
        current["evidence_sha256"] = "f" * 64
    elif damage == "changed_current_pointer":
        with sqlite3.connect(path) as db:
            db.execute("UPDATE active_model SET model_id=?", ("f" * 64,))
    else:
        at = {
            "intraday_switch": "2026-10-26T03:00:00+00:00",
            "future_switch": "2099-10-26T12:00:00+00:00",
            "naive_switch": "2026-10-26T12:00:00",
        }[damage]
        current["at"] = at
        current = signed({k: v for k, v in current.items() if k != "evidence_sha256"})
        event = signed(
            {**AUTHORITY, "kind": "CONTROL_APPLIED", "model_id": new, "at": at, "details": current}
        )
        with sqlite3.connect(path) as db:
            db.execute(
                "UPDATE model_events SET at=?,payload=? WHERE sequence=2", (at, json.dumps(event))
            )
    with pytest.raises((ValueError, TypeError)):
        scoped.approval_scope(
            path,
            DAY,
            old,
            previous["evidence_sha256"],
            new,
            current["evidence_sha256"],
            datetime.now(UTC),
        )


def test_sqlite_reader_rejects_write_without_creating_missing_database(tmp_path):
    missing = tmp_path / "absent.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        scoped.readonly(missing)
    assert not missing.exists()
    path = tmp_path / "existing.sqlite"
    with sqlite3.connect(path) as db:
        db.execute("CREATE TABLE t(v INTEGER)")
    db = scoped.readonly(path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            db.execute("INSERT INTO t VALUES(1)")
    finally:
        db.close()


def test_cli_has_no_clock_or_mutation_options():
    source = Path(scoped.__file__).read_text()
    assert "--now" not in source and "ForwardPaperBook(" not in source
    assert "ModelRegistry(" not in source and "activate(" not in source and "recover(" not in source


@pytest.fixture
def rollback():
    command = {"kind": "QLIB_DATA_ROLLBACK", "request_id": "r" * 32, "version_id": "c" * 64}
    event = signed(
        {
            **AUTHORITY,
            "kind": "QLIB_DATA_ROLLBACK",
            "version_id": "c" * 64,
            "created_at": (OPEN - timedelta(days=1)).isoformat(),
            "request_id": command["request_id"],
        }
    )
    receipt = signed(
        {
            **AUTHORITY,
            "state": "APPLIED",
            "event_id": event["evidence_sha256"],
            "version_id": "c" * 64,
            "request_id": command["request_id"],
            "command_sha256": signed(command)["evidence_sha256"],
        }
    )
    commands = [{"command": json.dumps(command), "receipt": json.dumps(receipt)}]
    return event, commands


def test_frozen_rollback_epoch_is_not_silently_replaced_with_ready(rollback):
    event, commands = rollback
    scoped.verify_frozen_epoch(
        event,
        event["evidence_sha256"],
        "c" * 64,
        OPEN - timedelta(hours=1),
        datetime.now(UTC),
        commands,
    )


@pytest.mark.parametrize(
    "damage",
    [
        "different_epoch",
        "wrong_version",
        "future_epoch",
        "naive_epoch",
        "missing_command",
        "duplicate_command",
        "wrong_receipt_event",
        "wrong_command_hash",
        "authority_type",
    ],
)
def test_frozen_rollback_requires_exact_recorded_command_and_epoch(rollback, damage):
    event, commands = deepcopy(rollback)
    expected = "f" * 64 if damage == "different_epoch" else event["evidence_sha256"]
    if damage == "wrong_version":
        event["version_id"] = "f" * 64
    elif damage == "future_epoch":
        event["created_at"] = "2099-01-01T00:00:00+00:00"
    elif damage == "naive_epoch":
        event["created_at"] = "2026-10-25T01:00:00"
    elif damage == "missing_command":
        commands = []
    elif damage == "duplicate_command":
        commands *= 2
    elif damage in {"wrong_receipt_event", "wrong_command_hash"}:
        receipt = json.loads(commands[0]["receipt"])
        receipt["event_id" if damage == "wrong_receipt_event" else "command_sha256"] = "f" * 64
        commands[0]["receipt"] = json.dumps(
            signed({k: v for k, v in receipt.items() if k != "evidence_sha256"})
        )
    elif damage == "authority_type":
        event["live_trading_enabled"] = 0
    if damage in {"wrong_version", "future_epoch", "naive_epoch", "authority_type"}:
        event = signed({k: v for k, v in event.items() if k != "evidence_sha256"})
        expected = event["evidence_sha256"]
    with pytest.raises((ValueError, TypeError)):
        scoped.verify_frozen_epoch(
            event, expected, "c" * 64, OPEN - timedelta(hours=1), datetime.now(UTC), commands
        )
