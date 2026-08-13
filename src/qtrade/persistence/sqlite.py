from __future__ import annotations

import ctypes
import json
import os
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from threading import Lock
from uuid import uuid4
from zoneinfo import ZoneInfo

from qtrade.audit import AuditEvent

_PROCESS_OWNER_TOKEN = str(uuid4())
_AUDIT_SEVERITIES = {"INFO", "WARNING", "ERROR", "CRITICAL"}
_MAX_AUDIT_FIELD_LENGTH = 1024
_CHINA_ZONE = ZoneInfo("Asia/Shanghai")


def _process_is_alive(process_id: int) -> bool:
    if process_id <= 0:
        return False
    if os.name != "nt":
        try:
            os.kill(process_id, 0)
        except OSError:
            return False
        return True
    kernel32 = ctypes.windll.kernel32
    handle = kernel32.OpenProcess(0x1000, False, process_id)
    if not handle:
        return False
    try:
        exit_code = ctypes.c_ulong()
        return bool(kernel32.GetExitCodeProcess(handle, ctypes.byref(exit_code))) and (
            exit_code.value == 259
        )
    finally:
        kernel32.CloseHandle(handle)


def _claim_single_process_owner(database_path: Path) -> None:
    """Claim the SQLite runtime for one process, allowing same-process adapters."""
    current_pid = os.getpid()
    connection = sqlite3.connect(database_path, timeout=0)
    try:
        connection.execute("BEGIN IMMEDIATE")
        row = connection.execute(
            "SELECT process_id, owner_token FROM process_owner WHERE singleton=1"
        ).fetchone()
        if row is not None and row != (current_pid, _PROCESS_OWNER_TOKEN):
            existing_pid = int(row[0])
            if _process_is_alive(existing_pid):
                raise RuntimeError(
                    f"operational journal is already owned by process {existing_pid}"
                )
            connection.execute("DELETE FROM process_owner WHERE singleton=1")
        connection.execute(
            "INSERT INTO process_owner(singleton,process_id,owner_token,claimed_at) "
            "VALUES(1,?,?,?) ON CONFLICT(singleton) DO UPDATE SET "
            "process_id=excluded.process_id,owner_token=excluded.owner_token,"
            "claimed_at=excluded.claimed_at",
            (current_pid, _PROCESS_OWNER_TOKEN, datetime.now(UTC).isoformat()),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


@dataclass(frozen=True, slots=True)
class FillJournalRecord:
    fill_key: str
    order_id: str
    payload_hash: str
    state: str
    updated_at: datetime
    error: str | None
    trade_payload: dict[str, object] | None


class SQLiteOperationalJournal:
    """Transactional local journal; PostgreSQL remains the multi-process target."""

    def __init__(self, path: Path) -> None:
        self._path = path.resolve()
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = Lock()
        self._initialize()
        _claim_single_process_owner(self._path)
        self._lease_token = str(uuid4())
        self._claim_runtime_lease()

    @property
    def path(self) -> Path:
        return self._path

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
                CREATE TABLE IF NOT EXISTS process_owner (
                    singleton INTEGER PRIMARY KEY CHECK (singleton=1),
                    process_id INTEGER NOT NULL,
                    owner_token TEXT NOT NULL,
                    claimed_at TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS runtime_lease (
                    singleton INTEGER PRIMARY KEY CHECK (singleton=1),
                    lease_token TEXT NOT NULL,
                    claimed_at TEXT NOT NULL
                );
                """
            )
            columns = {
                str(row[1]) for row in connection.execute("PRAGMA table_info(fill_journal)")
            }
            if "trade_payload" not in columns:
                connection.execute("ALTER TABLE fill_journal ADD COLUMN trade_payload TEXT")

    def _claim_runtime_lease(self) -> None:
        with self._lock, self._connect() as connection:
            connection.execute(
                "INSERT INTO runtime_lease(singleton,lease_token,claimed_at) VALUES(1,?,?) "
                "ON CONFLICT(singleton) DO UPDATE SET "
                "lease_token=excluded.lease_token,claimed_at=excluded.claimed_at",
                (self._lease_token, datetime.now(UTC).isoformat()),
            )

    def _assert_active_lease(self, connection: sqlite3.Connection) -> None:
        row = connection.execute(
            "SELECT lease_token FROM runtime_lease WHERE singleton=1"
        ).fetchone()
        if row != (self._lease_token,):
            raise RuntimeError("operational journal instance has been fenced by a newer runtime")

    def begin_fill(self, fill_key: str, order_id: str, payload_hash: str) -> bool:
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as connection:
            self._assert_active_lease(connection)
            existing = connection.execute(
                "SELECT order_id, payload_hash FROM fill_journal WHERE fill_key = ?",
                (fill_key,),
            ).fetchone()
            if existing is not None:
                if existing != (order_id, payload_hash):
                    raise ValueError("fill key conflicts with durable journal")
                return False
            connection.execute(
                "INSERT INTO fill_journal"
                "(fill_key,order_id,payload_hash,state,updated_at,error,trade_payload) "
                "VALUES (?, ?, ?, 'PENDING', ?, NULL, NULL)",
                (fill_key, order_id, payload_hash, now),
            )
            return True

    def mark_fill(self, fill_key: str, state: str, error: str | None = None) -> None:
        if state not in {"APPLIED", "FAILED"}:
            raise ValueError("fill terminal journal state must be APPLIED or FAILED")
        with self._lock, self._connect() as connection:
            self._assert_active_lease(connection)
            row = connection.execute(
                "SELECT state FROM fill_journal WHERE fill_key=?", (fill_key,)
            ).fetchone()
            if row is None:
                raise KeyError(fill_key)
            if row[0] == state:
                return
            if row[0] != "PENDING":
                raise ValueError(f"fill terminal state is immutable: {row[0]}")
            cursor = connection.execute(
                "UPDATE fill_journal SET state=?, updated_at=?, error=? "
                "WHERE fill_key=? AND state='PENDING'",
                (state, datetime.now(UTC).isoformat(), error, fill_key),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("fill state changed concurrently")

    def commit_fill_effect(
        self,
        fill_key: str,
        *,
        account_state: dict[str, object],
        runtime_state: dict[str, object],
        trade_payload: dict[str, object],
    ) -> None:
        """Atomically mark a fill APPLIED with recoverable economic/OMS state."""
        now = datetime.now(UTC).isoformat()
        account_json = json.dumps(account_state, sort_keys=True)
        runtime_json = json.dumps(runtime_state, sort_keys=True)
        trade_json = json.dumps(trade_payload, sort_keys=True)
        with self._lock, self._connect() as connection:
            self._assert_active_lease(connection)
            row = connection.execute(
                "SELECT state FROM fill_journal WHERE fill_key=?", (fill_key,)
            ).fetchone()
            if row is None or row[0] != "PENDING":
                raise ValueError("fill must be PENDING before atomic effect commit")
            connection.execute(
                "UPDATE fill_journal SET state='APPLIED', updated_at=?, error=NULL, "
                "trade_payload=? "
                "WHERE fill_key=?",
                (now, trade_json, fill_key),
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
                "SELECT fill_key, order_id, payload_hash, state, updated_at, error, "
                "trade_payload "
                "FROM fill_journal WHERE fill_key=?",
                (fill_key,),
            ).fetchone()
        if row is None:
            return None
        return FillJournalRecord(
            row[0], row[1], row[2], row[3], datetime.fromisoformat(row[4]), row[5],
            None if row[6] is None else dict(json.loads(row[6]))
        )

    def uncertain_fills(self) -> tuple[FillJournalRecord, ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT fill_key, order_id, payload_hash, state, updated_at, error, "
                "trade_payload "
                "FROM fill_journal WHERE state != 'APPLIED' ORDER BY fill_key"
            ).fetchall()
        return tuple(
            FillJournalRecord(
                row[0], row[1], row[2], row[3], datetime.fromisoformat(row[4]), row[5],
                None if row[6] is None else dict(json.loads(row[6]))
            )
            for row in rows
        )

    def applied_trade_payloads(self) -> tuple[dict[str, object], ...]:
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT trade_payload FROM fill_journal "
                "WHERE state='APPLIED' AND trade_payload IS NOT NULL ORDER BY fill_key"
            ).fetchall()
        return tuple(dict(json.loads(row[0])) for row in rows)

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
            self._assert_active_lease(connection)
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

    def recent_audit_events(self, limit: int = 500) -> tuple[AuditEvent, ...]:
        """Return a bounded chronological view of the durable audit journal."""
        if isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ValueError("audit read limit must be between 1 and 1000")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT timestamp,event_type,service,severity,order_id,payload "
                "FROM (SELECT sequence,timestamp,event_type,service,severity,order_id,payload "
                "FROM audit_events ORDER BY sequence DESC LIMIT ?) "
                "ORDER BY sequence",
                (limit,),
            ).fetchall()
        return self._deserialize_audit_rows(rows)

    def audit_incidents_for_day(
        self, trading_day: date, limit: int = 1000
    ) -> tuple[AuditEvent, ...]:
        """Return bounded ERROR/CRITICAL events for one Asia/Shanghai report day."""
        if isinstance(limit, bool) or not 1 <= limit <= 1000:
            raise ValueError("audit incident limit must be between 1 and 1000")
        start = datetime.combine(trading_day, datetime.min.time(), tzinfo=_CHINA_ZONE)
        end = start + timedelta(days=1)
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT timestamp,event_type,service,severity,order_id,payload "
                "FROM audit_events WHERE julianday(timestamp) IS NULL OR "
                "(julianday(timestamp)>=julianday(?) "
                "AND julianday(timestamp)<julianday(?)) "
                "ORDER BY sequence LIMIT 10001",
                (
                    start.astimezone(UTC).isoformat(),
                    end.astimezone(UTC).isoformat(),
                ),
            ).fetchall()
        if len(rows) > 10000:
            raise ValueError("daily audit event scan limit exceeded")
        events = self._deserialize_audit_rows(rows)
        incidents = tuple(
            event for event in events if event.severity in {"ERROR", "CRITICAL"}
        )
        if len(incidents) > limit:
            raise ValueError("daily audit incident limit exceeded")
        return incidents

    @staticmethod
    def _deserialize_audit_rows(rows: list[tuple[object, ...]]) -> tuple[AuditEvent, ...]:
        events: list[AuditEvent] = []
        for timestamp, event_type, service, severity, order_id, payload_json in rows:
            if not (
                isinstance(timestamp, str)
                and timestamp
                and isinstance(event_type, str)
                and event_type
                and isinstance(service, str)
                and service
                and isinstance(severity, str)
                and severity
            ):
                raise ValueError("durable audit identity fields must be nonempty strings")
            required = (timestamp, event_type, service, severity)
            if not all(len(value) <= _MAX_AUDIT_FIELD_LENGTH for value in required):
                raise ValueError("durable audit identity field is too long")
            if severity not in _AUDIT_SEVERITIES:
                raise ValueError("durable audit severity is invalid")
            timestamp_text = timestamp
            event_type_text = event_type
            service_text = service
            severity_text = severity
            parsed_timestamp = datetime.fromisoformat(timestamp_text)
            if parsed_timestamp.tzinfo is None:
                raise ValueError("durable audit timestamp must be timezone-aware")
            if order_id is not None and (
                not isinstance(order_id, str)
                or not order_id
                or len(order_id) > _MAX_AUDIT_FIELD_LENGTH
            ):
                raise ValueError("durable audit order id is invalid")
            if not isinstance(payload_json, str):
                raise ValueError("durable audit payload must be JSON text")
            payload = json.loads(payload_json)
            if not isinstance(payload, dict):
                raise ValueError("durable audit payload must be an object")
            if set(payload) != {"message", "strategy_id", "portfolio_id"}:
                raise ValueError("durable audit payload schema is invalid")
            message = payload["message"]
            if (
                not isinstance(message, str)
                or not message
                or len(message) > _MAX_AUDIT_FIELD_LENGTH
            ):
                raise ValueError("durable audit message is invalid")
            for field in ("strategy_id", "portfolio_id"):
                value = payload[field]
                if value is not None and (
                    not isinstance(value, str)
                    or not value
                    or len(value) > _MAX_AUDIT_FIELD_LENGTH
                ):
                    raise ValueError(f"durable audit {field} is invalid")
            events.append(
                AuditEvent(
                    event_type=event_type_text,
                    message=message,
                    service=service_text,
                    severity=severity_text,
                    strategy_id=payload["strategy_id"],
                    portfolio_id=payload["portfolio_id"],
                    order_id=order_id,
                    timestamp=parsed_timestamp,
                )
            )
        return tuple(events)

    def save_state(self, state_key: str, payload: dict[str, object]) -> None:
        self.save_states({state_key: payload})

    def save_states(self, states: dict[str, dict[str, object]]) -> None:
        if not states:
            raise ValueError("at least one runtime state is required")
        serialized = {key: json.dumps(payload, sort_keys=True) for key, payload in states.items()}
        now = datetime.now(UTC).isoformat()
        with self._lock, self._connect() as connection:
            self._assert_active_lease(connection)
            for state_key, payload in serialized.items():
                connection.execute(
                    "INSERT INTO runtime_state(state_key,payload,updated_at) VALUES(?,?,?) "
                    "ON CONFLICT(state_key) DO UPDATE SET payload=excluded.payload, "
                    "updated_at=excluded.updated_at",
                    (state_key, payload, now),
                )

    def load_state(self, state_key: str) -> dict[str, object] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT payload FROM runtime_state WHERE state_key=?", (state_key,)
            ).fetchone()
        return None if row is None else dict(json.loads(row[0]))

    def states_with_prefix(
        self, prefix: str
    ) -> tuple[tuple[str, dict[str, object]], ...]:
        if not prefix:
            raise ValueError("runtime state prefix is required")
        with self._connect() as connection:
            rows = connection.execute(
                "SELECT state_key,payload FROM runtime_state "
                "WHERE state_key LIKE ? ORDER BY state_key",
                (f"{prefix}%",),
            ).fetchall()
        return tuple((row[0], dict(json.loads(row[1]))) for row in rows)
