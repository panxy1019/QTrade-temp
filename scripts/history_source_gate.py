"""One durable anonymous-source failure clock shared by both history cohorts.

All mutation requires run_history_backfill.worker_lease. This module performs no
vendor calls. A pending intent survives a crash between capture and queue commit.
"""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts.atomic_json import write_json_atomic
from scripts.history_backfill_queue import HistoryQueue, read_signed
from scripts.history_backfill_store import HistoryStore

FAILURES = {"LOGIN_FAILED", "SOURCE_QUERY_FAILED", "QUERY_TIMEOUT"}
NO_SOURCE_RESULT = {"YIELDED_TO_SHARED_VENDOR_SESSION", "YIELDED_TO_CURRENT_COLLECTION",
                    "INTERRUPTED", "STORAGE_RESERVE_PAUSED"}
SCHEMA = "qtrade.history-source-backoff.v1"


def gate_path(base: Path) -> Path:
    return base / "unified-data/backfill/source-backoff.json"


def clock(value: Any) -> datetime:
    if not isinstance(value, str):
        raise ValueError("HISTORY_SOURCE_CLOCK_REQUIRED")
    result = datetime.fromisoformat(value)
    if result.tzinfo is None:
        raise ValueError("HISTORY_SOURCE_TIMEZONE_REQUIRED")
    return result


def read_gate(base: Path) -> dict[str, Any] | None:
    path = gate_path(base)
    try:
        with path.open("rb") as handle:
            payload = handle.read(256_001)
    except FileNotFoundError:
        return None
    if len(payload) > 256_000:
        raise ValueError("HISTORY_SOURCE_STATE_SIZE_LIMIT")
    value = json.loads(payload)
    if (not isinstance(value, dict)
            or signed({k: v for k, v in value.items() if k != "evidence_sha256"}) != value
            or any(value.get(k) != v for k, v in AUTHORITY.items())
            or value.get("schema_version") != SCHEMA
            or value.get("base_sha256") != sha256(str(base.resolve()).encode()).hexdigest()
            or type(value.get("consecutive_failures")) is not int
            or not 0 <= value["consecutive_failures"] <= 1_000_000
            or re.fullmatch(r"[a-f0-9]{64}", value.get("observed_plan_sha256", "")) is None
            or not isinstance(value.get("observed_root"), str)):
        raise ValueError("HISTORY_SOURCE_STATE_INVALID")
    clock(value["updated_at"])
    deadline = value["next_attempt_at"]
    if deadline is not None:
        clock(deadline)
    if bool(value["consecutive_failures"]) != bool(deadline):
        raise ValueError("HISTORY_SOURCE_FAILURE_DEADLINE_INVALID")
    last = value["last_attempt_id"]
    if value["consecutive_failures"] and last is None:
        raise ValueError("HISTORY_SOURCE_FAILURE_RECEIPT_REQUIRED")
    if last is not None:
        if re.fullmatch(r"[a-f0-9]{32}", last) is None:
            raise ValueError("HISTORY_SOURCE_ATTEMPT_INVALID")
        clock(value["last_receipt_sealed_at"])
        if re.fullmatch(r"[a-f0-9]{64}", value["last_receipt_sha256"]) is None:
            raise ValueError("HISTORY_SOURCE_RECEIPT_IDENTITY_INVALID")
    pending = value["pending"]
    if pending is not None:
        attempt = pending["attempt"]
        if (not isinstance(pending["root"], str)
                or type(pending.get("legacy_untrusted", False)) is not bool
                or re.fullmatch(r"[a-f0-9]{32}", attempt["attempt_id"]) is None
                or re.fullmatch(r"[a-f0-9]{64}", attempt["scope"]["plan_sha256"]) is None):
            raise ValueError("HISTORY_SOURCE_PENDING_INVALID")
        clock(attempt["started_at"])
    return value


def history_backoff_reason(base: Path, now: datetime, required: bool = False) -> str | None:
    try:
        value = read_gate(base)
        if value is None and required:
            return "HISTORY_SOURCE_BACKOFF_INVALID"
        if (value is not None and value["next_attempt_at"] is not None
                and now < clock(value["next_attempt_at"])):
            return "HISTORY_SOURCE_BACKOFF_ACTIVE"
    except (OSError, ValueError, TypeError, KeyError):
        return "HISTORY_SOURCE_BACKOFF_INVALID"
    return None


class HistorySourceGate:
    def __init__(self, base: Path, observed: HistoryQueue, *, now: datetime):
        self.base = base
        loaded = read_gate(base)
        self.existed = loaded is not None
        self.expected_sha256 = loaded["evidence_sha256"] if loaded is not None else None
        self.value = loaded or {
            **AUTHORITY, "schema_version": SCHEMA,
            "base_sha256": sha256(str(base.resolve()).encode()).hexdigest(),
            "observed_root": str(observed.root.resolve()),
            "observed_plan_sha256": observed.identity,
            "updated_at": now.isoformat(), "consecutive_failures": 0,
            "next_attempt_at": None, "last_attempt_id": None,
            "last_receipt_sha256": None, "last_receipt_sealed_at": None,
            "pending": None, "last_recovery": None,
        }
        if (self.value["observed_root"] != str(observed.root.resolve())
                or self.value["observed_plan_sha256"] != observed.identity):
            raise ValueError("HISTORY_SOURCE_OBSERVED_ROOT_CONFLICT")

    def save(self, *, now: datetime) -> None:
        previous = read_gate(self.base)
        identity = previous["evidence_sha256"] if previous is not None else None
        if identity != self.expected_sha256:
            raise ValueError("HISTORY_SHARED_SOURCE_STATE_CHANGED_OR_LOST")
        self.value["updated_at"] = now.isoformat()
        self.value = signed({k: v for k, v in self.value.items() if k != "evidence_sha256"})
        write_json_atomic(gate_path(self.base), self.value)
        self.expected_sha256 = self.value["evidence_sha256"]

    def sync(self, queue: HistoryQueue) -> None:
        queue.meta.update(global_failures=self.value["consecutive_failures"],
                          global_next_attempt_at=self.value["next_attempt_at"],
                          last_source_attempt_id=self.value["last_attempt_id"],
                          shared_source_base_sha256=self.value["base_sha256"])
        queue.save_meta()

    def bind(self, queue: HistoryQueue, store: HistoryStore, *, now: datetime) -> None:
        """Import a pre-upgrade failure receipt before enabling the second cohort."""
        managed = queue.meta.get("shared_source_base_sha256")
        if managed is not None and (not self.existed or managed != self.value["base_sha256"]):
            raise ValueError("HISTORY_SHARED_SOURCE_STATE_MISSING_OR_RELOCATED")
        failures, deadline = queue.meta["global_failures"], queue.meta["global_next_attempt_at"]
        if (type(failures) is not int or not 0 <= failures <= 1_000_000
                or bool(failures) != bool(deadline)):
            raise ValueError("HISTORY_LEGACY_SOURCE_STATE_INVALID")
        if deadline is not None:
            clock(deadline)
        if managed is None and failures:
            receipt = store.load(queue.meta["last_source_attempt_id"])
            attempt = receipt["attempt"]
            if (attempt["scope"]["plan_sha256"] != queue.identity
                    or receipt["quality"]["state"] not in FAILURES):
                raise ValueError("HISTORY_LEGACY_FAILURE_RECEIPT_INVALID")
            previous = self.value["last_receipt_sealed_at"]
            if previous is None or clock(receipt["sealed_at"]) > clock(previous):
                self.value.update(consecutive_failures=failures, next_attempt_at=deadline,
                                  last_attempt_id=attempt["attempt_id"],
                                  last_receipt_sha256=receipt["evidence_sha256"],
                                  last_receipt_sealed_at=receipt["sealed_at"])
        self.save(now=now)  # Shared state must precede the local migration marker.
        self.sync(queue)

        def commit(receipt: dict[str, Any], *, now: datetime) -> None:
            self.record(receipt, now=now)
            self.sync(queue)

        queue.source_receipt_committer = commit

    def begin(self, root: Path, attempt: dict[str, Any], *, now: datetime,
              legacy_untrusted: bool = False) -> None:
        if self.value["pending"] is not None:
            raise ValueError("HISTORY_SOURCE_PENDING_RECOVERY_REQUIRED")
        self.value["pending"] = {"root": str(root.resolve()), "attempt": attempt,
                                 "legacy_untrusted": legacy_untrusted}
        self.save(now=now)

    def prepare_queue_recovery(self, queue: HistoryQueue, store: HistoryStore, *,
                               now: datetime) -> None:
        """Close the upgrade gap for attempts created before the shared intent existed."""
        for task in queue.tasks.values():
            attempt = task["active"]
            if attempt is None:
                continue
            if store.path(attempt["attempt_id"], "receipt.json").exists():
                receipt = store.load(attempt["attempt_id"])
                if receipt["attempt"] != attempt:
                    raise ValueError("HISTORY_LEGACY_ACTIVE_RECEIPT_MISMATCH")
                self.record(receipt, now=now)
            else:
                if not store.path(attempt["attempt_id"], "request.json").exists():
                    store.begin(attempt)
                self.begin(queue.root, attempt, now=now, legacy_untrusted=True)
                # No shared intent was present. Preserve actual capture content,
                # but do not let an untracked older response reset endpoint health.
                self.recover_pending(now=now)
        self.sync(queue)

    def record(self, receipt: dict[str, Any], *, now: datetime,
               untrusted_recovery: bool = False) -> None:
        attempt = receipt["attempt"]
        pending = self.value["pending"]
        if pending is not None and pending["attempt"] != attempt:
            raise ValueError("HISTORY_SOURCE_PENDING_RECEIPT_MISMATCH")
        state = receipt["quality"]["state"]
        previous = self.value["last_receipt_sealed_at"]
        fresh = (attempt["attempt_id"] != self.value["last_attempt_id"]
                 and (pending is not None or previous is None
                      or clock(receipt["sealed_at"]) > clock(previous)))
        if fresh and (state in FAILURES or untrusted_recovery):
            failures = self.value["consecutive_failures"] + 1
            delay = min(21600, 1800 * 2 ** min(failures - 1, 4))
            self.value.update(consecutive_failures=failures,
                              next_attempt_at=(now + timedelta(seconds=delay)).isoformat())
        elif fresh and state not in NO_SOURCE_RESULT:
            self.value.update(consecutive_failures=0, next_attempt_at=None)
        if fresh and (state not in NO_SOURCE_RESULT or untrusted_recovery):
            self.value.update(last_attempt_id=attempt["attempt_id"],
                              last_receipt_sha256=receipt["evidence_sha256"],
                              last_receipt_sealed_at=receipt["sealed_at"])
        self.value["pending"] = None
        self.save(now=now)

    def recover_pending(self, *, now: datetime) -> dict[str, Any] | None:
        """Any cohort can replay the one shared in-flight request without a vendor call."""
        pending = self.value["pending"]
        if pending is None:
            return None
        from scripts.run_history_backfill import read_capture

        store = HistoryStore(Path(pending["root"]) / "lake")
        attempt = pending["attempt"]
        if read_signed(store.path(attempt["attempt_id"], "request.json")) != signed({
                **AUTHORITY, **attempt}):
            raise ValueError("HISTORY_SOURCE_RECOVERY_REQUEST_MISMATCH")
        untrusted = pending.get("legacy_untrusted", False)
        if store.path(attempt["attempt_id"], "receipt.json").exists():
            receipt = store.load(attempt["attempt_id"])
            untrusted = untrusted or receipt["quality"]["state"] == "INTERRUPTED"
        else:
            raw = read_capture(store, attempt)
            untrusted = untrusted or raw["state"] == "INTERRUPTED"
            receipt = store.seal(attempt, raw,
                                 state=None if raw["state"] == "CAPTURED" else raw["state"],
                                 error="RECOVERED_UNTRUSTED_SOURCE_OUTCOME" if untrusted else None)
            receipt = store.load(attempt["attempt_id"])
        if receipt["attempt"] != attempt:
            raise ValueError("HISTORY_SOURCE_RECOVERY_RECEIPT_MISMATCH")
        self.value["last_recovery"] = {
            "root": pending["root"], "attempt_id": attempt["attempt_id"],
            "receipt_sha256": receipt["evidence_sha256"],
            "untrusted_source_outcome": untrusted, "recovered_at": now.isoformat(),
        }
        self.record(receipt, now=now, untrusted_recovery=untrusted)
        result: dict[str, Any] = self.value["last_recovery"]
        return result
