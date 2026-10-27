"""Synthetic real-book retirement; no production performance acceptance."""

import json
import sqlite3
from dataclasses import replace
from datetime import UTC, datetime, timedelta

import pytest
from test_forward_service import environment, link, target  # noqa: F401

from qtrade.domain import RuntimeMode
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.forward_paper import ForwardPaperBook
from qtrade.research.forward_publication import validate_forward_publication
from qtrade.research.forward_retirement import closed_book_snapshot, retirement_event
from scripts import run_qlib_forward_service as service
from scripts.qlib_forward_paper_adapter import public_book_summary

OLD, NEW = "1" * 64, "2" * 64


def retire(instance):
    now = datetime.now(UTC)
    event = signed(
        {
            **AUTHORITY,
            "model_id": OLD,
            "kind": "RETIRED",
            "at": now.isoformat(),
            "details": {"replacement": NEW},
        }
    )
    with sqlite3.connect(instance.model_database) as db:
        db.execute("UPDATE models SET state='RETIRED' WHERE model_id=?", (OLD,))
        db.execute("UPDATE active_model SET model_id=?", (NEW,))
        db.execute("INSERT OR REPLACE INTO models VALUES(?, 'ACTIVE', ?,1)", (NEW, now.isoformat()))
        db.execute(
            "INSERT INTO model_events(model_id,at,kind,payload) VALUES(?,?,'RETIRED',?)",
            (OLD, now.isoformat(), json.dumps(event)),
        )
    return event


@pytest.fixture
def current_book(environment, monkeypatch):  # noqa: F811
    def create(mode=RuntimeMode.PAPER):
        monkeypatch.setattr(service, "ForwardPaperBook", ForwardPaperBook)
        monkeypatch.setattr(service, "public_book_summary", public_book_summary)
        monkeypatch.setattr(service, "validate_forward_publication", validate_forward_publication)
        instance = environment(modes=(mode,))
        item = replace(target(), strategy_id="approved_model_" + OLD)
        with sqlite3.connect(instance.model_database) as db:
            db.executescript(
                "CREATE TABLE models(model_id TEXT PRIMARY KEY,state TEXT,activated_at TEXT,"
                "activation_count INTEGER); CREATE TABLE active_model(singleton INTEGER,"
                "model_id TEXT); CREATE TABLE model_events(sequence INTEGER PRIMARY KEY,"
                "model_id TEXT,at TEXT,kind TEXT,payload TEXT)"
            )
            db.execute(
                "INSERT INTO models VALUES(?,'ACTIVE',?,1)",
                (OLD, (item.generated_at - timedelta(minutes=1)).isoformat()),
            )
            db.execute("INSERT INTO active_model VALUES(1,?)", (OLD,))
        archive = {
            **link(),
            "strategy_id": item.strategy_id,
            "model_id": OLD,
            "kind": "approved-prediction",
        }
        instance.register(item, archive, now=datetime.now(UTC))
        identity, book = next(iter(instance.books.items()))
        monkeypatch.setattr(instance, "current_targets", lambda _: [])
        observed = []
        monkeypatch.setattr(instance, "observe_book", lambda *args, **kwargs: observed.append(args))
        return instance, identity, book, item, observed

    return create


@pytest.mark.parametrize("mode", [RuntimeMode.PAPER, RuntimeMode.SHADOW])
def test_retired_flat_real_book_stays_visible_stopped_and_byte_immutable(current_book, mode):
    instance, identity, book, item, observed = current_book(mode)
    if mode is RuntimeMode.PAPER:
        book.recover(actor="synthetic test")
    original = book.db.execute("SELECT * FROM targets").fetchall()
    event = retire(instance)
    result = instance.cycle()
    assert result["state"] == "RUNNING" and not result["errors"] and not observed
    assert len(result["books"]) == 1 and result["books"][0]["strategy_id"] == item.strategy_id
    assert result["books"][0]["running"] is False
    assert result["books"][0]["recovery_required"] == (mode is RuntimeMode.PAPER)
    path = instance.root / identity / "retirements" / (event["evidence_sha256"] + ".json")
    raw = path.read_bytes()
    proof = service.load(path)
    assert proof["snapshot"]["position_count"] == 0
    assert proof["snapshot"]["unexecuted_targets_blocked_by_retirement"] == [item.identity]
    assert proof["snapshot"]["cash"] == ("1000000" if mode is RuntimeMode.PAPER else None)
    assert proof["whole_step1_accepted"] is False
    assert instance.cycle()["state"] == "RUNNING"
    assert (
        path.read_bytes() == raw and book.db.execute("SELECT * FROM targets").fetchall() == original
    )
    assert not book.db.execute("SELECT * FROM sessions").fetchall()


@pytest.mark.parametrize("damage", ["missing", "hash", "authority", "future", "never_activated"])
def test_invalid_retirement_proof_stops_running_book_without_acceptance(current_book, damage):
    instance, identity, book, _, observed = current_book()
    book.recover(actor="synthetic test")
    event = retire(instance)
    with sqlite3.connect(instance.model_database) as db:
        if damage == "missing":
            db.execute("DELETE FROM model_events")
        elif damage == "never_activated":
            db.execute("UPDATE models SET activation_count=0 WHERE model_id=?", (OLD,))
        else:
            if damage == "hash":
                event["details"]["replacement"] = "3" * 64
            elif damage == "authority":
                event["live_trading_enabled"] = 0
                event = signed({k: v for k, v in event.items() if k != "evidence_sha256"})
            else:
                event["at"] = (datetime.now(UTC) + timedelta(hours=1)).isoformat()
                event = signed({k: v for k, v in event.items() if k != "evidence_sha256"})
            db.execute("UPDATE model_events SET at=?,payload=?", (event["at"], json.dumps(event)))
    result = instance.cycle()
    assert result["state"] == "HUMAN_RECOVERY_REQUIRED" and result["errors"]
    assert not book.bundle.runtime.status.running and not observed
    assert identity not in instance.retired_books
    assert not (instance.root / identity / "retirements").exists()


@pytest.mark.parametrize(
    "damage",
    [
        "pending_session",
        "uncertain_fill",
        "held",
        "cash",
        "orders",
        "late_fill",
        "admission",
        "target",
    ],
)
def test_retirement_cannot_hide_holdings_uncertainty_or_damaged_history(current_book, damage):
    instance, identity, book, item, observed = current_book()
    retire(instance)
    if damage == "pending_session":
        book.db.execute(
            "INSERT INTO sessions VALUES('2026-10-27',?,'x','PENDING',NULL,'{}')", (item.identity,)
        )
    elif damage == "target":
        book.db.execute("UPDATE targets SET id=?", ("9" * 64,))
    elif damage == "admission":
        (instance.root / identity / "admissions" / (item.identity + ".json")).unlink()
    else:
        with sqlite3.connect(instance.root / identity / "operations.sqlite") as db:
            if damage in {"uncertain_fill", "late_fill"}:
                trade = {
                    "instrument": "SH600036",
                    "side": "BUY",
                    "quantity": 100,
                    "price": "10",
                    "fee": "5",
                    "timestamp": datetime.now(UTC).isoformat(),
                }
                db.execute(
                    "INSERT INTO fill_journal VALUES('fixture','fixture','x',?,?,NULL,?)",
                    (
                        "PENDING" if damage == "uncertain_fill" else "APPLIED",
                        datetime.now(UTC).isoformat(),
                        json.dumps(trade),
                    ),
                )
            else:
                value = (
                    {
                        "orders": [{"status": "SUBMISSION_UNKNOWN"}],
                        "reservations": [],
                        "sellability_lots": [],
                    }
                    if damage == "orders"
                    else {
                        "cash": "999999" if damage == "cash" else "1000000",
                        "positions": {"SH600036": {"quantity": 100}} if damage == "held" else {},
                    }
                )
                db.execute(
                    "INSERT INTO runtime_state VALUES(?,?,?)",
                    (
                        "runtime" if damage == "orders" else "account",
                        json.dumps(value),
                        datetime.now(UTC).isoformat(),
                    ),
                )
    book.db.commit()
    result = instance.cycle()
    assert result["state"] == "HUMAN_RECOVERY_REQUIRED" and result["errors"] and not observed
    assert identity not in instance.retired_books and not book.bundle.runtime.status.running
    assert not (instance.root / identity / "retirements").exists()


def test_model_rollback_never_resumes_a_retired_paper_book(current_book):
    instance, identity, book, _, _ = current_book()
    retire(instance)
    assert instance.cycle()["state"] == "RUNNING"
    path = next((instance.root / identity / "retirements").glob("*.json"))
    original = path.read_bytes()
    with sqlite3.connect(instance.model_database) as db:
        db.execute(
            "UPDATE models SET state='ACTIVE',activation_count=2,activated_at=? WHERE model_id=?",
            (datetime.now(UTC).isoformat(), OLD),
        )
        db.execute("UPDATE models SET state='RETIRED' WHERE model_id=?", (NEW,))
        db.execute("UPDATE active_model SET model_id=?", (OLD,))
    assert (
        retirement_event(instance.model_database, book.strategy_id, now=datetime.now(UTC)) is None
    )
    assert instance.cycle()["state"] == "HUMAN_RECOVERY_REQUIRED"
    assert path.read_bytes() == original and not book.bundle.runtime.status.running


def test_signed_but_changed_retirement_receipt_remains_failed(current_book):
    instance, identity, book, _, observed = current_book()
    retire(instance)
    instance.cycle()
    path = next((instance.root / identity / "retirements").glob("*.json"))
    value = service.load(path)
    value["snapshot"]["fill_count"] = 999
    path.write_text(json.dumps(signed({k: v for k, v in value.items() if k != "evidence_sha256"})))
    corrupt = path.read_bytes()
    result = instance.cycle()
    assert result["state"] == "HUMAN_RECOVERY_REQUIRED" and not observed
    assert path.read_bytes() == corrupt and not book.bundle.runtime.status.running
    assert any(
        e["code"] == "FORWARD_SERVICE_RETIRED_BOOK_CHANGED_REVIEW_REQUIRED"
        for e in result["errors"]
    )


def test_retired_book_recovery_is_rejected_before_consuming_a_fresh_request(current_book):
    instance, identity, book, _, _ = current_book()
    retire(instance)
    instance.cycle()
    request = service.prepare_recovery_request(
        instance.root, book_id=identity, actor="synthetic-test"
    )
    assert instance.recover_requested_books() == ["FORWARD_SERVICE_RECOVERY_REQUEST_REJECTED"]
    assert not (instance.root / "recovery/consumed" / (request["request_id"] + ".json")).exists()
    assert not book.bundle.runtime.status.running


def test_closed_past_round_trip_reconciles_real_cash_without_new_day_returns(current_book):
    from decimal import Decimal

    instance, identity, book, item, _ = current_book()
    now = datetime.now(UTC)
    with sqlite3.connect(instance.root / identity / "operations.sqlite") as db:
        for index, side in enumerate(["BUY", "SELL"]):
            trade = {
                "instrument": "SH600036",
                "side": side,
                "quantity": 100,
                "price": "10",
                "fee": "5",
                "timestamp": (item.generated_at - timedelta(seconds=5 - index)).isoformat(),
            }
            db.execute(
                "INSERT INTO fill_journal VALUES(?,?,?,'APPLIED',?,NULL,?)",
                (str(index), str(index), "fixture", now.isoformat(), json.dumps(trade)),
            )
        for key, value in [
            ("account", {"cash": "999990", "positions": {}}),
            ("runtime", {"orders": [], "reservations": [], "sellability_lots": []}),
        ]:
            db.execute(
                "INSERT INTO runtime_state VALUES(?,?,?)", (key, json.dumps(value), now.isoformat())
            )
    book.bundle.ledger.account.cash = Decimal("999990")
    retire(instance)
    result = instance.cycle()
    assert result["state"] == "RUNNING" and not result["errors"]
    proof = service.load(next((instance.root / identity / "retirements").glob("*.json")))
    assert proof["snapshot"]["cash"] == "999990" and proof["snapshot"]["fill_count"] == 2
    assert "forward_return" not in proof["snapshot"] and not result["books"][0]["sessions"]


def test_live_ledger_drift_cannot_be_closed_from_a_valid_durable_snapshot(current_book):
    from decimal import Decimal

    instance, identity, book, _, _ = current_book()
    retire(instance)
    book.bundle.ledger.account.cash = Decimal("1")
    result = instance.cycle()
    assert result["state"] == "HUMAN_RECOVERY_REQUIRED"
    assert any(
        r["code"] == "FORWARD_SERVICE_RETIRED_LIVE_ACCOUNT_UNRECONCILED" for r in result["errors"]
    )
    assert not (instance.root / identity / "retirements").exists()


def test_missed_pre_retirement_session_cannot_disappear_into_read_only_history(current_book):
    instance, identity, book, item, _ = current_book()
    past = replace(
        item,
        generated_at=item.generated_at - timedelta(days=2),
        execution_at=item.execution_at - timedelta(days=2),
    )
    book.freeze(past, now=past.generated_at + timedelta(seconds=1))
    archive = {
        **link(),
        "strategy_id": past.strategy_id,
        "model_id": OLD,
        "kind": "approved-prediction",
    }
    service.write_json_immutable(
        instance.root / identity / "admissions" / (past.identity + ".json"),
        signed(
            {
                **AUTHORITY,
                "target_id": past.identity,
                "archive": archive,
                "verified_at": (past.generated_at + timedelta(seconds=2)).isoformat(),
            }
        ),
    )
    retire(instance)
    result = instance.cycle()
    assert result["state"] == "HUMAN_RECOVERY_REQUIRED"
    assert any(
        r["code"] == "FORWARD_RETIREMENT_PRIOR_SESSION_REQUIRES_RECONCILIATION"
        for r in result["errors"]
    )
    assert book.db.execute("SELECT COUNT(*) FROM targets").fetchone()[0] == 2
    assert not (instance.root / identity / "retirements").exists()


def test_retirement_blocks_recovery_even_when_it_changes_after_consumption(
    current_book, monkeypatch
):
    instance, identity, book, _, _ = current_book()
    instance.cycle()  # Publishes the real current instance for the explicit fixture request.
    request = service.prepare_recovery_request(
        instance.root, book_id=identity, actor="synthetic-test"
    )
    original = service.write_json_immutable

    def change_after_claim(path, value):
        original(path, value)
        if path.parent.name == "consumed":
            retire(instance)

    monkeypatch.setattr(service, "write_json_immutable", change_after_claim)
    assert instance.recover_requested_books() == ["FORWARD_SERVICE_RECOVERY_RECONCILIATION_FAILED"]
    result = service.load(instance.root / "recovery/receipts" / (request["request_id"] + ".json"))
    assert result["state"] == "FAILED" and not book.bundle.runtime.status.running
    assert instance.recover_requested_books() == []


def test_closed_snapshot_is_read_only_and_no_new_day_acceptance(current_book):
    instance, identity, book, _, _ = current_book()
    retire(instance)
    instance.cycle()
    event = retirement_event(instance.model_database, book.strategy_id, now=datetime.now(UTC))
    before = {p: p.read_bytes() for p in (instance.root / identity).rglob("*.json")}
    snapshot = closed_book_snapshot(
        instance.root / identity, instance.roster[identity], event, now=datetime.now(UTC)
    )
    assert snapshot["scope"] == "READ_ONLY_RETIRED_BOOK_NOT_A_NEW_DAY_OBSERVATION"
    assert before == {p: p.read_bytes() for p in before}
