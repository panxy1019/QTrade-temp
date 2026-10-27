"""Synthetic local books plus real raw/Parquet replay; never production acceptance."""

import json
import sqlite3
from copy import deepcopy
from dataclasses import asdict
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from types import SimpleNamespace

import pytest
from test_collect_unified_data import CALENDAR, NOW, batch, fake_source
from test_unified_store import store  # noqa: F401

from qtrade.domain import RuntimeMode
from qtrade.execution import FeeSchedule
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.forward_paper import ForwardPaperBook, ForwardTarget
from qtrade.research.forward_retirement import closed_book_snapshot, retirement_event
from scripts import audit_qlib_forward_transition as audit
from scripts.collect_unified_data import collect_batch
from scripts.qlib_forward_paper_adapter import observed_session, public_book_summary


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def resign(value):
    return signed({k: v for k, v in value.items() if k != "evidence_sha256"})


@pytest.fixture
def books(store, tmp_path):  # noqa: F811
    base, root = tmp_path / "base", tmp_path / "books"
    store.root = base / "unified-data/lake"
    source, _ = fake_source()
    manifest = collect_batch(store, batch(), CALENDAR, source=source)
    market, evidence = observed_session(
        store.root,
        [manifest["batch_id"]],
        day=date(2026, 10, 26),
        instruments=["SH600036"],
        calendar=CALENDAR,
        now=NOW,
    )
    roster, public, identities = {}, [], {}
    model = "a" * 64
    for strategy in (*audit.STOCK_STRATEGIES, "approved_model_" + model):
        for mode in ("PAPER", "SHADOW"):
            config = {
                "strategy_id": strategy,
                "mode": mode,
                "cash": "1000000",
                "fee_version": "forward-research-cost-v1",
            }
            identity = signed(config)["evidence_sha256"]
            roster[identity], identities[(strategy, mode)] = config, identity
            book_root = root / identity
            book_root.mkdir(parents=True)
            target = ForwardTarget(
                strategy,
                "b" * 64,
                "c" * 64,
                "d" * 40,
                datetime(2026, 10, 25, 9, tzinfo=UTC),
                market.open_at,
                {"SH600036": Decimal(".5")},
                ("Synthetic fixture; not production forward evidence",),
            )
            archive = {
                "kind": "approved-prediction"
                if strategy.startswith("approved_model_")
                else "signal-family",
                "artifact_id": target.signal_id,
                "manifest_sha256": "e" * 64,
                "strategy_id": strategy,
                "model_id": model if strategy.startswith("approved_model_") else None,
                "data_event_id": "f" * 64,
                "approval_sha256": "1" * 64 if strategy.startswith("approved_model_") else None,
            }
            write(
                book_root / "admissions" / (target.identity + ".json"),
                signed(
                    {
                        **AUTHORITY,
                        "target_id": target.identity,
                        "archive": archive,
                        "verified_at": (target.generated_at + timedelta(seconds=1)).isoformat(),
                    }
                ),
            )
            write(book_root / "archive-link.json", signed({**AUTHORITY, "archive": archive}))
            write(book_root / "market-inputs/2026-10-26.json", evidence)
            result = {
                "schema_version": 1,
                "mode": mode,
                "strategy_id": strategy,
                "target_id": target.identity,
                "signal_id": target.signal_id,
                "data_version": target.data_version,
                "source_revision": target.source_revision,
                "session": "2026-10-26",
                "observed_at": str(market.observed_at),
                "market_evidence": market.market_evidence,
                "calendar_evidence": market.calendar_evidence,
                "target_weights": {"SH600036": "0.5"},
                "target_quantities": {},
                "orders": [],
                "fills": [],
                "holdings": {},
                "cash": "1000000" if mode == "PAPER" else None,
                "net_asset_value": "1000000" if mode == "PAPER" else None,
                "forward_return": "0" if mode == "PAPER" else None,
                "turnover": "0" if mode == "PAPER" else None,
                "blocked": {"SH600036": "UNKNOWN_TRADING_CONSTRAINTS"},
                "LIVE_TRADING_ENABLED": False,
                "execution_kind": "SIMULATED_END_OF_DAY_OPEN_PRICE"
                if mode == "PAPER"
                else "NO_EXECUTION",
                "status": "PAPER_OBSERVED" if mode == "PAPER" else "SHADOW_OBSERVED",
                "limitations": list(target.limitations),
            }
            with sqlite3.connect(book_root / "forward.sqlite") as connection:
                connection.executescript(
                    "CREATE TABLE config(id INTEGER,payload TEXT);"
                    "CREATE TABLE targets(id TEXT,execution_at TEXT,payload TEXT,frozen_at TEXT);"
                    "CREATE TABLE sessions(day TEXT,target_id TEXT,input_hash TEXT,status TEXT,"
                    "result TEXT,input_payload TEXT)"
                )
                connection.execute(
                    "INSERT INTO config VALUES(1,?)",
                    (
                        json.dumps(
                            {
                                "strategy": strategy,
                                "mode": mode,
                                "cash": "1000000",
                                "fees": {"version": "forward-research-cost-v1"},
                            }
                        ),
                    ),
                )
                connection.execute(
                    "INSERT INTO targets VALUES(?,?,?,?)",
                    (
                        target.identity,
                        str(target.execution_at),
                        audit.canonical(asdict(target)),
                        str(target.generated_at),
                    ),
                )
                connection.execute(
                    "INSERT INTO sessions VALUES(?,?,?,'COMPLETE',?,?)",
                    (
                        "2026-10-26",
                        target.identity,
                        sha256(audit.canonical(asdict(market)).encode()).hexdigest(),
                        json.dumps(result),
                        audit.canonical(asdict(market)),
                    ),
                )
            with sqlite3.connect(book_root / "operations.sqlite") as connection:
                connection.executescript(
                    "CREATE TABLE audit_events(sequence INTEGER,event_type TEXT,"
                    "timestamp TEXT,service TEXT); CREATE TABLE fill_journal("
                    "fill_key TEXT,state TEXT,trade_payload TEXT)"
                )
                if mode == "PAPER":
                    connection.execute(
                        "INSERT INTO audit_events VALUES(1,?,?,?)",
                        ("SYSTEM_START", datetime.now(UTC).isoformat(), "control"),
                    )
            view = SimpleNamespace(
                strategy_id=strategy,
                mode=RuntimeMode(mode),
                bundle=SimpleNamespace(
                    runtime=SimpleNamespace(status=SimpleNamespace(running=mode == "PAPER")),
                    risk=SimpleNamespace(
                        state=SimpleNamespace(value="NORMAL" if mode == "PAPER" else "HALTED")
                    ),
                ),
            )
            with sqlite3.connect(book_root / "forward.sqlite") as connection:
                view.db = connection
                public.append(public_book_summary(view, archive_link=archive))
    write(root / "roster.json", signed({**AUTHORITY, "books": roster}))
    now = datetime.now(UTC)
    publication = signed(
        {
            **AUTHORITY,
            "schema_version": "qtrade.forward-service.v1",
            "generated_at": now.isoformat(),
            "heartbeat_at": now.isoformat(),
            "source_revision": "d" * 40,
            "state": "RUNNING",
            "books": public,
            "errors": [],
            "limitations": ["Synthetic fixtures only"],
        }
    )
    return SimpleNamespace(
        base=base,
        root=root,
        model=model,
        publication=publication,
        now=now,
        identities=identities,
        manifest=manifest,
        store=store,
    )


def run(data, *, publication=None, day=date(2026, 10, 26), now=None, model_database=None):
    return audit.audit_transition(
        data.base,
        data.root,
        publication if publication is not None else data.publication,
        day,
        data.model,
        CALENDAR,
        now=now or data.now,
        model_database=model_database,
    )


def all_failures(result):
    return result["failures"] + [f for book in result["books"] for f in book["failures"]]


@pytest.fixture
def retained_books(books, tmp_path):
    """Two actual flat retired books augment twelve current observation fixtures."""
    old = "1" * 64
    strategy = "approved_model_" + old
    now = datetime.now(UTC)
    opened = []
    roster = audit.document(books.root / "roster.json")["books"]
    for mode in (RuntimeMode.PAPER, RuntimeMode.SHADOW):
        config = {
            "strategy_id": strategy,
            "mode": mode.value,
            "cash": "1000000",
            "fee_version": "forward-research-cost-v1",
        }
        identity = signed(config)["evidence_sha256"]
        roster[identity] = config
        book = ForwardPaperBook(
            books.root / identity,
            strategy_id=strategy,
            mode=mode,
            opening_cash=Decimal("1000000"),
            fees=FeeSchedule("forward-research-cost-v1", Decimal(".0003"), Decimal(".0005")),
        )
        target = ForwardTarget(
            strategy,
            "b" * 64,
            "c" * 64,
            "d" * 40,
            now - timedelta(seconds=5),
            now + timedelta(days=1),
            {"SH600036": Decimal(".5")},
            ("Synthetic retired book; no new-day performance",),
        )
        book.freeze(target, now=datetime.now(UTC))
        link = {
            "kind": "approved-prediction",
            "artifact_id": target.signal_id,
            "manifest_sha256": "e" * 64,
            "strategy_id": strategy,
            "model_id": old,
            "data_event_id": "f" * 64,
            "approval_sha256": "1" * 64,
        }
        write(
            books.root / identity / "admissions" / (target.identity + ".json"),
            signed(
                {
                    **AUTHORITY,
                    "target_id": target.identity,
                    "archive": link,
                    "verified_at": datetime.now(UTC).isoformat(),
                }
            ),
        )
        write(books.root / identity / "archive-link.json", signed({**AUTHORITY, "archive": link}))
        opened.append((identity, config, book, link))
    retired_at = datetime.now(UTC)
    event = signed(
        {
            **AUTHORITY,
            "model_id": old,
            "kind": "RETIRED",
            "at": retired_at.isoformat(),
            "details": {"replacement": books.model},
        }
    )
    models = tmp_path / "retired-models.sqlite"
    with sqlite3.connect(models) as db:
        db.executescript(
            "CREATE TABLE models(model_id TEXT,state TEXT,activated_at TEXT,"
            "activation_count INTEGER); CREATE TABLE active_model(singleton INTEGER,"
            "model_id TEXT); CREATE TABLE model_events(sequence INTEGER PRIMARY KEY,"
            "model_id TEXT,at TEXT,kind TEXT,payload TEXT)"
        )
        db.execute(
            "INSERT INTO models VALUES(?,'RETIRED',?,1)",
            (old, (now - timedelta(minutes=1)).isoformat()),
        )
        db.execute("INSERT INTO active_model VALUES(1,?)", (books.model,))
        db.execute(
            "INSERT INTO model_events VALUES(1,?,?,'RETIRED',?)",
            (old, retired_at.isoformat(), json.dumps(event)),
        )
    resolved = retirement_event(models, strategy, now=datetime.now(UTC))
    for identity, config, book, link in opened:
        book.bundle.runtime.stop(actor="synthetic-retirement")
        snapshot = closed_book_snapshot(
            books.root / identity, config, resolved, now=datetime.now(UTC)
        )
        proof = signed(
            {
                **AUTHORITY,
                "kind": "RETIRED_RESEARCH_BOOK_READ_ONLY",
                "book_id": identity,
                "model_event": resolved,
                "snapshot": snapshot,
                "whole_step1_accepted": False,
                "recorded_at": datetime.now(UTC).isoformat(),
            }
        )
        write(books.root / identity / "retirements" / (event["evidence_sha256"] + ".json"), proof)
        books.publication["books"].append(public_book_summary(book, archive_link=link))
        book.db.close()
    write(books.root / "roster.json", signed({**AUTHORITY, "books": roster}))
    books.now = datetime.now(UTC)
    books.publication.update(generated_at=books.now.isoformat(), heartbeat_at=books.now.isoformat())
    books.publication = resign(books.publication)
    books.model_database, books.retired_strategy, books.retired_id = models, strategy, opened[0][0]
    return books


def test_fourteen_book_acceptance_explicitly_separates_retired_closure_from_current_day(
    retained_books,
):
    data = retained_books
    before = {
        p: p.read_bytes()
        for p in data.root.rglob("*")
        if p.is_file() and p.suffix in {".json", ".sqlite"}
    }
    result = run(data, model_database=data.model_database)
    assert result["state"] == "PASSED", all_failures(result)
    assert result["schema_version"] == "qtrade.forward-transition-audit.v2"
    assert result["current_book_count"] == 12 and result["retired_book_count"] == 2
    assert result["expected_book_count"] == len(result["books"]) == 14
    retired = [r for r in result["books"] if r.get("scope")]
    assert len(retired) == 2 and all(r["accepted"] for r in retired)
    assert all("session_sha256" not in r for r in retired)
    assert result["whole_step1_accepted"] is False and not result["unclassified_local_books"]
    assert before == {p: p.read_bytes() for p in before}


@pytest.mark.parametrize(
    "damage",
    ["registry_missing", "reactivated", "pair", "public_running", "proof", "account", "admission"],
)
def test_retained_history_cannot_hide_unclassified_or_unsafe_books(retained_books, damage):
    data = retained_books
    models = data.model_database
    if damage == "registry_missing":
        models = None
    elif damage == "reactivated":
        with sqlite3.connect(models) as db:
            db.execute("UPDATE models SET state='ACTIVE'")
    elif damage == "pair":
        roster = audit.document(data.root / "roster.json")
        roster["books"].pop(data.retired_id)
        write(data.root / "roster.json", resign(roster))
        data.publication["books"] = [
            b
            for b in data.publication["books"]
            if not (b["strategy_id"] == data.retired_strategy and b["mode"] == "PAPER")
        ]
    elif damage == "public_running":
        b = next(
            b
            for b in data.publication["books"]
            if b["strategy_id"] == data.retired_strategy and b["mode"] == "PAPER"
        )
        b.update(running=True, recovery_required=False)
    elif damage == "proof":
        path = next((data.root / data.retired_id / "retirements").glob("*.json"))
        proof = audit.document(path)
        proof["model_event"]["event_sequence"] += 1
        write(path, resign(proof))
    elif damage == "account":
        with sqlite3.connect(data.root / data.retired_id / "operations.sqlite") as db:
            db.execute(
                "INSERT INTO runtime_state VALUES('account',?,'fixture')",
                (json.dumps({"cash": "999999", "positions": {}}),),
            )
    else:
        next((data.root / data.retired_id / "admissions").glob("*.json")).unlink()
    data.publication["books"] = [resign(b) for b in data.publication["books"]]
    result = run(data, model_database=models, publication=resign(data.publication))
    assert result["state"] == "BLOCKED" and not result["forward_book_transition_accepted"]


def test_closed_retired_books_never_replace_a_future_current_day_observation(retained_books):
    data = retained_books
    future = (data.now + timedelta(days=3)).date()
    result = run(data, model_database=data.model_database, day=future)
    assert result["state"] == "BLOCKED"
    assert "FORWARD_AUDIT_REAL_SESSION_NOT_COMPLETE" in result["failures"]


def database(data, mode="PAPER", strategy="momentum20"):
    return data.root / data.identities[(strategy, mode)] / "forward.sqlite"


def test_full_twelve_book_raw_receipt_acceptance_is_read_only_and_not_whole_step1(books):
    before = {
        str(p.relative_to(books.base.parent)): sha256(p.read_bytes()).hexdigest()
        for directory in (books.base, books.root)
        for p in directory.rglob("*")
        if p.is_file()
    }
    result = run(books)
    assert result["state"] == "PASSED", all_failures(result)
    assert result["expected_book_count"] == 12 and len(result["books"]) == 12
    assert result["whole_step1_accepted"] is False and result["historical_pit_certified"] is False
    assert all(book["fill_count"] == 0 and book["blocked_count"] == 1 for book in result["books"])
    after = {
        str(p.relative_to(books.base.parent)): sha256(p.read_bytes()).hexdigest()
        for directory in (books.base, books.root)
        for p in directory.rglob("*")
        if p.is_file()
    }
    assert before == after


@pytest.mark.parametrize(
    "damage", ["pending", "missing", "backdated", "admission", "hash", "return"]
)
def test_local_receipt_failures_cannot_be_hidden_by_a_valid_publication(books, damage):
    with sqlite3.connect(database(books)) as connection:
        if damage == "pending":
            connection.execute("UPDATE sessions SET status='PENDING',result=NULL")
        elif damage == "missing":
            connection.execute("DELETE FROM sessions")
        elif damage == "backdated":
            connection.execute("UPDATE targets SET frozen_at='2026-10-26T09:30:00+08:00'")
        elif damage == "hash":
            connection.execute("UPDATE sessions SET input_hash='changed'")
        elif damage == "return":
            row = json.loads(connection.execute("SELECT result FROM sessions").fetchone()[0])
            row["forward_return"] = "0.3"
            connection.execute("UPDATE sessions SET result=?", (json.dumps(row),))
        elif damage == "admission":
            identity = connection.execute("SELECT id FROM targets").fetchone()[0]
            (database(books).parent / "admissions" / (identity + ".json")).unlink()
    result = run(books)
    assert result["state"] == "BLOCKED"
    failed = [book for book in result["books"] if not book["accepted"]]
    assert len(failed) == 1 and failed[0]["strategy_id"] == "momentum20"


@pytest.mark.parametrize("mode", ["PAPER", "SHADOW"])
def test_fabricated_fills_or_shadow_economics_fail_even_if_publication_is_signed(books, mode):
    with sqlite3.connect(database(books, mode)) as connection:
        row = json.loads(connection.execute("SELECT result FROM sessions").fetchone()[0])
        if mode == "PAPER":
            row["fills"] = [{"symbol": "SH600036", "side": "BUY", "quantity": 100}]
        else:
            row["forward_return"] = "0"
        connection.execute("UPDATE sessions SET result=?", (json.dumps(row),))
    result = run(books)
    expected = (
        "EXECUTION_WITH_UNKNOWN_OR_BLOCKED_CONSTRAINTS"
        if mode == "PAPER"
        else ("SHADOW_EXECUTION_FORBIDDEN")
    )
    assert "FORWARD_AUDIT_" + expected in all_failures(result)


@pytest.mark.parametrize("damage", ["stale", "coverage", "target", "receipt", "paused_model"])
def test_publication_failures_and_paused_model_book_are_explicit(books, damage):
    publication = deepcopy(books.publication)
    if damage == "stale":
        publication["heartbeat_at"] = (books.now - timedelta(minutes=15)).isoformat()
    elif damage == "coverage":
        publication["books"].pop()
    elif damage == "target":
        publication["books"][0]["frozen_targets"][0]["signal_id"] = "2" * 64
    elif damage == "receipt":
        publication["books"][0]["sessions"][0]["data_version"] = "2" * 64
    else:
        model = next(
            b
            for b in publication["books"]
            if b["mode"] == "PAPER" and b["strategy_id"].startswith("approved_model_")
        )
        model["risk_state"] = "PAUSED"
    publication["books"] = [resign(book) for book in publication["books"]]
    result = run(books, publication=resign(publication))
    assert result["state"] == "BLOCKED"
    if damage == "paused_model":
        assert "FORWARD_AUDIT_PAPER_RUNTIME_NOT_READY" in all_failures(result)


def test_local_pause_is_detected_when_published_book_still_appears_normal(books):
    identity = books.identities[("approved_model_" + books.model, "PAPER")]
    with sqlite3.connect(books.root / identity / "operations.sqlite") as connection:
        connection.execute(
            "INSERT INTO audit_events VALUES(2,?,?,?)",
            ("SYSTEM_PAUSE", books.now.isoformat(), "control"),
        )
    result = run(books)
    assert "FORWARD_AUDIT_LOCAL_PAPER_NOT_RUNNING" in all_failures(result)


def test_raw_content_tampering_rejects_all_affected_books(books):
    ref = books.manifest["raw"]
    path = books.store.root / "objects" / (ref["sha256"] + ref["suffix"])
    path.write_bytes(b"X" * path.stat().st_size)
    result = run(books)
    assert result["state"] == "BLOCKED"
    assert all(not book["accepted"] for book in result["books"])


def test_real_future_session_remains_blocked_and_cli_has_no_clock_override(books, monkeypatch):
    result = run(books, day=date(2026, 10, 27), now=NOW)
    assert "FORWARD_AUDIT_REAL_SESSION_NOT_COMPLETE" in result["failures"]
    monkeypatch.setattr(
        audit, "audit_transition", lambda *args, **kwargs: pytest.fail("clock override")
    )
    monkeypatch.setattr(audit, "__name__", "test")
    import sys

    monkeypatch.setattr(sys, "argv", ["forward-audit", "--now", "2100-01-01"])
    with pytest.raises(SystemExit):
        audit.main()


def test_shadow_hidden_durable_fill_and_resume_after_stop_are_rejected(books):
    shadow = books.root / books.identities[("momentum20", "SHADOW")] / "operations.sqlite"
    with sqlite3.connect(shadow) as connection:
        connection.execute("INSERT INTO fill_journal VALUES('fake','APPLIED','{}')")
    paper = books.root / books.identities[("momentum20", "PAPER")] / "operations.sqlite"
    with sqlite3.connect(paper) as connection:
        for index, event in enumerate(("SYSTEM_STOP", "SYSTEM_RESUME"), 2):
            connection.execute(
                "INSERT INTO audit_events VALUES(?,?,?,?)",
                (index, event, books.now.isoformat(), "control"),
            )
    failures = all_failures(run(books))
    assert "FORWARD_AUDIT_SHADOW_DURABLE_FILL_FORBIDDEN" in failures
    assert "FORWARD_AUDIT_LOCAL_PAPER_NOT_RUNNING" in failures


def test_missing_local_book_and_numeric_authority_do_not_pass(books):
    path = books.root / "roster.json"
    roster = audit.document(path)
    roster["books"].pop(next(iter(roster["books"])))
    write(path, resign(roster))
    publication = deepcopy(books.publication)
    publication["live_trading_enabled"] = 0
    result = run(books, publication=resign(publication))
    assert "FORWARD_AUDIT_LOCAL_BOOK_COVERAGE_MISMATCH" in result["failures"]
    assert "FORWARD_AUDIT_PUBLICATION_INVALID_STALE_OR_DEGRADED" in result["failures"]


def test_cli_writes_separate_immutable_audit_only(books, tmp_path, monkeypatch, capsys):
    import sys

    publication = tmp_path / "forward.json"
    monkeypatch.setattr(audit, "Calendar", lambda _: CALENDAR)
    write(publication, books.publication)
    output = tmp_path / "acceptance.json"
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "forward-audit",
            "--base",
            str(books.base),
            "--local-root",
            str(books.root),
            "--published-forward",
            str(publication),
            "--day",
            "2026-10-26",
            "--approved-model-id",
            books.model,
            "--output",
            str(output),
        ],
    )
    real_write = audit.write_json_immutable

    def immutable(path, value):
        try:
            real_write(path, value)
        except PermissionError:
            # Windows lacks directory fsync; Linux integration checks the original helper.
            assert path.is_file() and json.loads(path.read_text()) == value

    monkeypatch.setattr(audit, "write_json_immutable", immutable)
    audit.main()
    result = json.loads(capsys.readouterr().out)
    assert result["state"] == "PASSED" and audit.document(output) == result
    before = output.read_bytes()
    with pytest.raises(FileExistsError):
        audit.main()
    assert output.read_bytes() == before
