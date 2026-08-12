from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from threading import Lock
from typing import Protocol

from qtrade.domain.models import utc_now


@dataclass(frozen=True, slots=True)
class AuditEvent:
    event_type: str
    message: str
    service: str
    severity: str = "INFO"
    strategy_id: str | None = None
    portfolio_id: str | None = None
    order_id: str | None = None
    timestamp: datetime = field(default_factory=utc_now)


class AuditSink(Protocol):
    def append(self, event: AuditEvent) -> None: ...


class JsonlAuditSink:
    """Append-only tamper-evident bootstrap sink.

    This hash chain detects mutation/reordering but cannot prevent deletion of a
    complete file. Durable/WORM storage remains a later production control.
    """

    def __init__(self, path: Path) -> None:
        self._path = path
        self._lock = Lock()
        self._last_hash = self._load_last_hash()

    def _load_last_hash(self) -> str:
        if not self._path.exists():
            return "0" * 64
        lines = self._path.read_text(encoding="utf-8").splitlines()
        if not lines:
            return "0" * 64
        return str(json.loads(lines[-1])["hash"])

    def append(self, event: AuditEvent) -> None:
        with self._lock:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            payload = asdict(event)
            payload["timestamp"] = event.timestamp.isoformat()
            payload["prev_hash"] = self._last_hash
            canonical = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            payload["hash"] = sha256(canonical.encode("utf-8")).hexdigest()
            with self._path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(payload, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
            self._last_hash = str(payload["hash"])

    def verify_chain(self) -> bool:
        previous = "0" * 64
        if not self._path.exists():
            return True
        for line in self._path.read_text(encoding="utf-8").splitlines():
            payload = json.loads(line)
            stored_hash = payload.pop("hash", None)
            if payload.get("prev_hash") != previous:
                return False
            canonical = json.dumps(
                payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")
            )
            calculated = sha256(canonical.encode("utf-8")).hexdigest()
            if stored_hash != calculated:
                return False
            previous = calculated
        return True


class AuditLog:
    def __init__(self, sink: AuditSink | None = None) -> None:
        self._events: list[AuditEvent] = []
        self._sink = sink

    @property
    def events(self) -> tuple[AuditEvent, ...]:
        return tuple(self._events)

    def record(self, event: AuditEvent) -> None:
        self._events.append(event)
        if self._sink is not None:
            self._sink.append(event)
