from __future__ import annotations

import json
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from threading import Lock

from qtrade.audit import AuditEvent


@dataclass(frozen=True, slots=True)
class FillJournalRecord:
    fill_key: str
    order_id: str
    payload_hash: str
    state: str
    updated_at: datetime
    error: str | None


class SQLiteOperationalJournal:
    """Transactional local journal; PostgreSQL remains the multi-process target."""

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._initialize()

    @contextmanager
    def _connect(self) -> Iterator[sqlite3.Connection]:
        connection = sqlite3.connect(self._path)
        try:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA synchronous=FULL")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _initialize(self) -> None:
        with self._connect() as connection:
            connection.executescript(
                """
                CREATE TABLE IF NOT EXISTS fill_journal (
                    fill_key TEXT PRIMARY KEY,
                    order_id TEXT NOT NULL,
                    payload_hash TEXT NOT NULL,
                    state TEXT NOT NULL CHECK (state IN ('PENDING','APPLIED','FAILED')),
                    updated_at TEXT NOT NULL,
                    error TEXT
                );
                CREATE TABLE IF NOT EXISTS audit_events (
                    sequence INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    service TEXT NOT NULL,
                    severity TEXT NOT NULL,
                    order_id TEXT,
                    payload TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_state (
                    state_key TEXT PRIMARY KEY,
                    payload TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                """
            )

    def begin_fill(self, fill_key: str, order_id: str, payload_hash: str) -> bool:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as connection:
            existing = connection.execute(
                "SELECT order_id, payload_hash FROM fill_journal WHERE fill_key = ?",
                (fill_key,),
            ).fetchone()
            if existing is not None:
                if existing != (order_id, payload_hash):
                    raise ValueError("fill key conflicts with durable journal")
                return False
            connection.execute(
                "INSERT INTO fill_journal VALUES (?, ?, ?, 'PENDING', ?, NULL)",
                (fill_key, order_id, payload_hash, now),
            )
            return True

    def mark_fill(self, fill_key: str, state: str, error: str | None = None) -> None:
        if state not in {"APPLIED", "FAILED"}:
            raise ValueError("fill terminal journal state must be APPLIED or FAILED")
        with self._lock, self._connect() as connection:
            cursor = connection.execute(
                "UPDATE fill_journal SET state=?, updated_at=?, error=? WHERE fill_key=?",
                (state, datetime.now(UTC).isoformat(), error, fill_key),
            )
            if cursor.rowcount != 1:
                raise KeyError(fill_key)

    def commit_fill_effect(
        self,
        fill_key: str,
        *,
        account_state: dict[str, object],
        runtime_state: dict[str, object],
    ) -> None:
        """Atomically mark a fill APPLIED with recoverable economic/OMS state."""
        now = datetime.now(UTC).isoformat()
        account_json = json.dumps(account_state, sort_keys=True)
        runtime_json = json.dumps(runtime_state, sort_keys=True)
        with self._lock, self._connect() as connection:
            row = connection.execute(
                "SELECT state FROM fill_journal WHERE fill_key=?", (fill_key,)
            ).fetchone()
            if row is None or row[0] != "PENDING":
                raise ValueError("fill must be PENDING before atomic effect commit")
            connection.execute(
                "UPDATE fill_journal SET state='APPLIED', updated_at=?, error=NULL "
                "WHERE fill_key=?",
                (now, fill_key),
            )
            for key, payload in (("account", account_json), ("runtime", runtime_json)):
                connection.execute(
                    "INSERT INTO runtime_state(state_key,payload,updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(state_key) DO UPDATE SET payload=excluded.payload, "
                    "updated_at=excluded.updated_at",
                    (key, payload, now),
                )

    def get_fill(self, fill_key: str) -> FillJournalRecord | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT fill_key, order_id, payload_hash, state, updated_at, error "
                "FROM fill_journal WHERE fill_key=?",
                (fill_key,),
            ).fetchone()
        if row is None:
            return None
        return FillJournalRecord(
            row[0], row[1], row[2], row[3], datetime.fromisoformat(row[4]), row[5]
        )

    def uncertain_fills(self) -> tuple[FillJournalRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT fill_key, order_id, payload_hash, state, updated_at, error "
                "FROM fill_journal WHERE state != 'APPLIED' ORDER BY fill_key"
            ).fetchall()
        return tuple(
            FillJournalRecord(
                row[0], row[1], row[2], row[3], datetime.fromisoformat(row[4]), row[5]
            )
            for row in rows
        )

    def append(self, event: AuditEvent) -> None:
        payload = json.dumps(
            {
                "message": event.message,
                "strategy_id": event.strategy_id,
                "portfolio_id": event.portfolio_id,
            },
            sort_keys=True,
        )
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO audit_events(timestamp,event_type,service,severity,order_id,payload) "
                "VALUES(?,?,?,?,?,?)",
                (
                    event.timestamp.isoformat(),
                    event.event_type,
                    event.service,
                    event.severity,
                    event.order_id,
                    payload,
                ),
            )

    def save_state(self, state_key: str, payload: dict[str, object]) -> None:
        serialized = json.dumps(payload, sort_keys=True)
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO runtime_state(state_key,payload,updated_at) VALUES(?,?,?) "
                "ON CONFLICT(state_key) DO UPDATE SET payload=excluded.payload, "
                "updated_at=excluded.updated_at",
                (state_key, serialized, datetime.now(UTC).isoformat()),
            )

    def load_state(self, state_key: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM runtime_state WHERE state_key=?", (state_key,)
            ).fetchone()
        return None if row is None else dict(json.loads(row[0]))
