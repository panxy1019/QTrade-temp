"""Local durable collection plans, bounded retries and transactional data-ready events."""

from __future__ import annotations

import json
import re
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from qtrade.market_data.akshare import SHANGHAI
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.persistence.sqlite import _claim_single_process_owner
from scripts.unified_store import SCHEMA, UnifiedBatchStore


def clock(value: datetime) -> str:
    if value.tzinfo is None:
        raise ValueError("COLLECTION_CLOCK_REQUIRES_TIMEZONE")
    return value.astimezone(UTC).isoformat(timespec="seconds")


def required_ends(calendar: Calendar, now: datetime) -> dict[str, str]:
    ends = {freq: calendar.expected(now, "daily" if freq == "day" else "minute")
            for freq in ("day", "5min")}
    local = now.astimezone(SHANGHAI)
    # Before today's close a quote cannot supply yesterday's cash amount.
    if calendar.is_open(local.date()) and ends["day"].date() < local.date():
        del ends["day"]
    return {freq: clock(end) for freq, end in ends.items()}


class UnifiedCollectionQueue:
    def __init__(self, database: Path):
        self.database = database.resolve()
        self.database.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as c:
            c.executescript("""
                CREATE TABLE IF NOT EXISTS process_owner (
                    singleton INTEGER PRIMARY KEY, process_id INTEGER, owner_token TEXT,
                    claimed_at TEXT
                );
                CREATE TABLE IF NOT EXISTS collection_tasks (
                    universe_version TEXT, frequency TEXT, symbol TEXT, trading_day TEXT,
                    security_type TEXT, desired_end TEXT, completed_end TEXT,
                    state TEXT, attempts INTEGER DEFAULT 0, next_attempt_at TEXT,
                    active_batch TEXT, last_success_batch TEXT, last_error TEXT,
                    PRIMARY KEY(universe_version,frequency,symbol,trading_day)
                );
                CREATE TABLE IF NOT EXISTS collection_batches (
                    batch_id TEXT PRIMARY KEY, spec TEXT, state TEXT, attempted_at TEXT,
                    completed_at TEXT, manifest_sha256 TEXT
                );
                CREATE TABLE IF NOT EXISTS collection_chunks (
                    batch_id TEXT, universe_version TEXT, frequency TEXT, symbol TEXT,
                    start_time TEXT, end_time TEXT, bar_count INTEGER,
                    PRIMARY KEY(batch_id,symbol)
                );
                CREATE TABLE IF NOT EXISTS data_ready_events (
                    event_id TEXT PRIMARY KEY, frequency TEXT, batch_id TEXT UNIQUE,
                    payload TEXT, created_at TEXT
                );
                CREATE TABLE IF NOT EXISTS excluded_collection_chunks (
                    batch_id TEXT, symbol TEXT, reason TEXT, observed_at TEXT,
                    PRIMARY KEY(batch_id,symbol)
                );
                CREATE TABLE IF NOT EXISTS collection_metadata (
                    key TEXT PRIMARY KEY, value TEXT
                );
                CREATE INDEX IF NOT EXISTS collection_due ON collection_tasks (
                    universe_version,frequency,trading_day,state,next_attempt_at
                );
                CREATE INDEX IF NOT EXISTS collection_symbol_chunks ON collection_chunks (
                    universe_version,frequency,symbol,end_time
                );
            """)
        _claim_single_process_owner(self.database)

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        c = sqlite3.connect(self.database, timeout=10)
        c.row_factory = sqlite3.Row
        try:
            c.execute("PRAGMA journal_mode=WAL")
            c.execute("PRAGMA synchronous=FULL")
            c.execute("BEGIN IMMEDIATE")
            yield c
            c.commit()
        except Exception:
            c.rollback()
            raise
        finally:
            c.close()

    def plan(self, master: dict[str, Any], calendar: Calendar, now: datetime) -> dict[str, str]:
        if (signed({k: v for k, v in master.items() if k != "evidence_sha256"}) != master
                or any(master.get(k) != v for k, v in AUTHORITY.items())
                or re.fullmatch(r"[a-f0-9]{64}", master["universe_version"]) is None):
            raise ValueError("COLLECTION_UNIVERSE_IDENTITY_INVALID")
        targets = [r for r in master["securities"] if r["in_target"]]
        if len(targets) != master["target_actual"]:
            raise ValueError("COLLECTION_UNIVERSE_TARGET_COUNT_MISMATCH")
        ends = required_ends(calendar, now)
        with self.connect() as c:
            for freq, end in ends.items():
                trading_day = datetime.fromisoformat(end).astimezone(SHANGHAI).date()
                for row in targets:
                    if row["status"] != "ACTIVE_OBSERVED" or row["issues"]:
                        raise ValueError("UNSAFE_OBSERVED_COLLECTION_IDENTITY")
                    c.execute("""
                        INSERT INTO collection_tasks (
                            universe_version,frequency,symbol,trading_day,security_type,
                            desired_end,state,next_attempt_at
                        ) VALUES(?,?,?,?,?,?,'PLANNED',?)
                        ON CONFLICT(universe_version,frequency,symbol,trading_day) DO UPDATE SET
                            state=CASE WHEN collection_tasks.state='SUCCEEDED'
                                AND collection_tasks.completed_end < excluded.desired_end
                                THEN 'PLANNED' ELSE collection_tasks.state END,
                            next_attempt_at=CASE WHEN collection_tasks.state='SUCCEEDED'
                                AND collection_tasks.completed_end < excluded.desired_end
                                THEN excluded.next_attempt_at
                                ELSE collection_tasks.next_attempt_at END,
                            desired_end=MAX(collection_tasks.desired_end,excluded.desired_end)
                    """, (master["universe_version"], freq, row["symbol"], str(trading_day),
                          row["security_type"], end, clock(now)))
        return ends

    def claim(
        self, universe_version: str, frequency: str, day: date, *, now: datetime,
        code_revision: str, limit: int = 25,
    ) -> dict[str, Any] | None:
        if not 1 <= limit <= 50 or frequency not in {"day", "5min"}:
            raise ValueError("INVALID_COLLECTION_CLAIM")
        with self.connect() as c:
            rows = [dict(r) for r in c.execute("""
                SELECT * FROM collection_tasks WHERE universe_version=? AND frequency=?
                    AND trading_day=? AND state IN ('PLANNED','FAILED','INTERRUPTED')
                    AND next_attempt_at<=? AND (completed_end IS NULL OR completed_end<desired_end)
                ORDER BY completed_end IS NOT NULL,completed_end,next_attempt_at,symbol
            """, (universe_version, frequency, str(day), clock(now)))]
            fresh = [r for r in rows if r["state"] == "PLANNED"]
            retry = [r for r in rows if r["state"] != "PLANNED"]
            retry_budget = min(len(retry), max(1, limit // 5))
            selected = fresh[:limit - retry_budget]
            selected += retry[:limit - len(selected)]
            # If retries were fewer than reserved, fill with remaining untouched tasks.
            selected += [r for r in fresh if r not in selected][:limit - len(selected)]
            if not selected:
                return None
            end = max(r["desired_end"] for r in selected)
            selected = [r for r in selected if r["desired_end"] == end]
            spec = {"frequency": frequency, "target_end": end, "universe_version": universe_version,
                    "symbols": [r["symbol"] for r in selected], "code_revision": code_revision,
                    "after_by_symbol": {r["symbol"]: r["completed_end"] for r in selected}}
            batch_id = uuid4().hex
            c.execute("INSERT INTO collection_batches VALUES(?,?,'RUNNING',?,NULL,NULL)",
                      (batch_id, json.dumps(spec, sort_keys=True), clock(now)))
            for row in selected:
                c.execute("""
                    UPDATE collection_tasks SET state='RUNNING',active_batch=?,attempts=attempts+1
                    WHERE universe_version=? AND frequency=? AND symbol=? AND trading_day=?
                """, (batch_id, universe_version, frequency, row["symbol"], str(day)))
        return {"batch_id": batch_id, "spec": spec,
                "security_types": {r["symbol"]: r["security_type"] for r in selected}}

    def finish(self, manifest: dict[str, Any], *, now: datetime) -> None:
        batch_id, spec = manifest["batch_id"], manifest["spec"]
        if (signed({k: v for k, v in manifest.items() if k != "evidence_sha256"}) != manifest
                or manifest.get("schema_version") != SCHEMA
                or any(manifest.get(k) != v for k, v in AUTHORITY.items())
                or set(manifest["outcomes"]) != set(spec["symbols"])):
            raise ValueError("COLLECTION_RECEIPT_HASH_MISMATCH")
        with self.connect() as c:
            row = c.execute("SELECT * FROM collection_batches WHERE batch_id=?",
                            (batch_id,)).fetchone()
            if row is None or json.loads(row["spec"]) != spec:
                raise ValueError("COLLECTION_BATCH_SPEC_CHANGED")
            if row["state"] == "COMPLETE":
                if row["manifest_sha256"] != manifest["evidence_sha256"]:
                    raise ValueError("COLLECTION_COMMIT_CONFLICT")
                return
            if row["state"] != "RUNNING":
                raise ValueError("COLLECTION_BATCH_NOT_RUNNING")
            for symbol, outcome in manifest["outcomes"].items():
                task = c.execute("SELECT * FROM collection_tasks WHERE active_batch=? AND symbol=?",
                                 (batch_id, symbol)).fetchone()
                if task is None or task["state"] != "RUNNING":
                    raise ValueError("COLLECTION_TASK_NOT_OWNED")
                success = outcome["state"] == "SUCCEEDED"
                end = (clock(datetime.fromisoformat(outcome["end"]))
                       if success else task["completed_end"])
                ready = success and end >= task["desired_end"]
                delay = min(21600, 60 * 2 ** min(task["attempts"] - 1, 8))
                state = "SUCCEEDED" if ready else "PLANNED" if success else "FAILED"
                error = outcome.get("error_code")
                if error is not None and re.fullmatch(r"[A-Z_]{1,100}", error) is None:
                    raise ValueError("UNSAFE_COLLECTION_ERROR_CODE")
                retry_at = now if ready else now + timedelta(seconds=60 if success else delay)
                c.execute("""
                    UPDATE collection_tasks SET state=?,completed_end=?,next_attempt_at=?,
                        active_batch=NULL,last_success_batch=?,last_error=?
                    WHERE universe_version=? AND frequency=? AND symbol=? AND trading_day=?
                """, (state, end, clock(retry_at),
                      batch_id if success else task["last_success_batch"], error,
                      task["universe_version"], task["frequency"], symbol, task["trading_day"]))
                if success:
                    c.execute("INSERT INTO collection_chunks VALUES(?,?,?,?,?,?,?)",
                              (batch_id, spec["universe_version"], spec["frequency"], symbol,
                               clock(datetime.fromisoformat(outcome["start"])), end,
                               outcome["bar_count"]))
            c.execute("UPDATE collection_batches SET state='COMPLETE',completed_at=?,"
                      "manifest_sha256=? "
                      "WHERE batch_id=?", (clock(now), manifest["evidence_sha256"], batch_id))
            if manifest["bar_count"]:
                event = signed({**AUTHORITY, "schema_version": "qtrade.data-ready-event.v1",
                                "batch_id": batch_id, "frequency": spec["frequency"],
                                "universe_version": spec["universe_version"],
                                "manifest_sha256": manifest["evidence_sha256"],
                                "target_end": spec["target_end"], "created_at": clock(now),
                                "scope": (
                                    "ACCEPTED_INCREMENTAL_ROWS_NOT_FULL_UNIVERSE_OR_QLIB_VERSION")})
                c.execute("INSERT INTO data_ready_events VALUES(?,?,?,?,?)",
                          (event["evidence_sha256"], spec["frequency"], batch_id,
                           json.dumps(event, sort_keys=True), clock(now)))

    def recover(self, store: UnifiedBatchStore, *, now: datetime) -> dict[str, int]:
        with self.connect() as c:
            rows = [dict(r) for r in c.execute(
                "SELECT * FROM collection_batches WHERE state='RUNNING'")]
        replayed = interrupted = 0
        for batch in rows:
            if store.batch_path(batch["batch_id"]).is_file():
                self.finish(store.load(batch["batch_id"]), now=now)
                replayed += 1
            else:
                with self.connect() as c:
                    c.execute("UPDATE collection_batches SET state='INTERRUPTED',completed_at=? "
                              "WHERE batch_id=?", (clock(now), batch["batch_id"]))
                    c.execute("UPDATE collection_tasks SET state='INTERRUPTED',active_batch=NULL,"
                              "last_error='PROCESS_INTERRUPTED',next_attempt_at=? "
                              "WHERE active_batch=?",
                              (clock(now + timedelta(seconds=60)), batch["batch_id"]))
                interrupted += 1
        return {"replayed": replayed, "interrupted": interrupted}

    def recheck_daily_amounts(self, store: UnifiedBatchStore, *, now: datetime) -> int:
        """Retain legacy price-only archives; they cannot satisfy the full daily contract."""
        with self.connect() as c:
            if c.execute("SELECT 1 FROM collection_metadata "
                         "WHERE key='strict_daily_amount_v1'").fetchone():
                return 0
            tasks = [dict(r) for r in c.execute(
                "SELECT * FROM collection_tasks WHERE frequency='day' "
                "AND last_success_batch IS NOT NULL")]
        cache = {}
        repaired = 0
        for task in tasks:
            batch_id = task["last_success_batch"]
            if batch_id not in cache:
                cache[batch_id] = store.load(batch_id)
            if not cache[batch_id]["outcomes"][task["symbol"]].get("missing_amount_bars", 0):
                continue
            with self.connect() as c:
                c.execute("INSERT OR IGNORE INTO excluded_collection_chunks VALUES(?,?,?,?)",
                          (batch_id, task["symbol"], "DAILY_AMOUNT_UNVERIFIED", clock(now)))
                c.execute("UPDATE collection_tasks SET state='FAILED',completed_end=NULL,"
                          "last_success_batch=NULL,last_error='DAILY_AMOUNT_UNVERIFIED',"
                          "next_attempt_at=? WHERE universe_version=? AND frequency='day' "
                          "AND symbol=? AND trading_day=?",
                          (clock(now), task["universe_version"], task["symbol"],
                           task["trading_day"]))
            repaired += 1
        with self.connect() as c:
            c.execute("INSERT INTO collection_metadata VALUES('strict_daily_amount_v1',?)",
                      (clock(now),))
        return repaired

    def snapshot(self, universe_version: str) -> dict[str, Any]:
        with self.connect() as c:
            tasks = [dict(r) for r in c.execute(
                "SELECT * FROM collection_tasks t WHERE universe_version=? AND trading_day="
                "(SELECT MAX(trading_day) FROM collection_tasks s WHERE "
                "s.universe_version=t.universe_version AND s.frequency=t.frequency)",
                (universe_version,))]
            batches = [dict(r) for r in c.execute(
                "SELECT batch_id,state,attempted_at,completed_at,manifest_sha256 "
                "FROM collection_batches ORDER BY attempted_at DESC LIMIT 12")]
            events = c.execute("SELECT COUNT(*) FROM data_ready_events").fetchone()[0]
            counts = {(r["frequency"], r["symbol"], r["trading_day"]): dict(r)
                      for r in c.execute(
                          "SELECT frequency,symbol,SUBSTR(end_time,1,10) trading_day,"
                          "MIN(start_time) accepted_start,SUM(bar_count) bar_count "
                          "FROM collection_chunks c WHERE universe_version=? AND NOT EXISTS "
                          "(SELECT 1 FROM excluded_collection_chunks e WHERE e.batch_id=c.batch_id "
                          "AND e.symbol=c.symbol) "
                          "GROUP BY frequency,symbol,SUBSTR(end_time,1,10)", (universe_version,))}
        for task in tasks:
            total = counts.get((task["frequency"], task["symbol"], task["trading_day"]), {})
            task["accepted_bar_count"] = total.get("bar_count", 0)
            task["accepted_start"] = total.get("accepted_start")
        return {"tasks": tasks, "recent_batches": batches, "data_ready_events": events}
