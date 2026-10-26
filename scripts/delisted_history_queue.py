"""Separate inactive-source scopes using the existing history receipt and cursor engine."""

from __future__ import annotations

import calendar
import re
from collections import Counter
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.pit_quality import SHANGHAI
from qtrade.research.workflow import digest
from scripts.atomic_json import write_json_immutable
from scripts.history_backfill_queue import HistoryQueue, read_signed
from scripts.history_backfill_store import HistoryStore
from scripts.plan_public_delisted_history import read_json, verify_bundle

SOURCE_KIND = "OFFICIAL_DELISTED_UNCERTIFIED"
SECURITY_STATUS = "INACTIVE_SOURCE_CLAIM_UNCERTIFIED"
YIELDED = {"YIELDED_TO_SHARED_VENDOR_SESSION", "YIELDED_TO_CURRENT_COLLECTION"}


class DelistedHistoryQueue(HistoryQueue):
    """Mutation requires the single history writer lease held by the existing runner.

    This adapter does not acquire a vendor session, authorize execution or publish
    current-universe coverage. Shared global failure gates belong to that runner.
    """

    def __init__(self, root: Path, bundle: Path, *, now: datetime | None = None,
                 read_only: bool = False):
        now = now or datetime.now(UTC)
        manifest = verify_bundle(bundle, now=now)
        if (root.resolve().is_relative_to(bundle.resolve())
                or bundle.resolve().is_relative_to(root.resolve())):
            raise ValueError("DELISTED_QUEUE_CANNOT_OVERLAP_FROZEN_BUNDLE")
        self.root, self.bundle, self.read_only = root, bundle, read_only
        self.plan = read_json(bundle / "plan.json")
        self.master = read_json(bundle / "master.json")
        self.identity = self.plan["evidence_sha256"]
        self.bundle_identity = manifest["evidence_sha256"]
        self.definitions = {t["task_id"]: t for t in self.plan["tasks"]}
        self.scope_version = digest({"source_kind": SOURCE_KIND, "plan_sha256": self.identity,
                                     "task_ids": sorted(self.definitions)})
        self.securities = {t["instrument_id"]: {
            "symbol": t["symbol"], "security_type": "STOCK", "status": SECURITY_STATUS,
        } for t in self.definitions.values()}
        has_execution_state = ((root / "scheduler.json").exists()
                               or any((root / "tasks").glob("*.json"))
                               or any((root / "lake/attempts").glob("*")))
        if has_execution_state and any(not (root / name).exists()
                                       for name in ("plan.json", "source-bundle.json")):
            raise ValueError("DELISTED_QUEUE_FROZEN_INPUT_MISSING")
        binding = signed({**AUTHORITY, "schema_version": "qtrade.delisted-history-input.v1",
                          "plan_sha256": self.identity,
                          "source_bundle_sha256": self.bundle_identity,
                          "scope_version": self.scope_version, "source_kind": SOURCE_KIND,
                          "historical_available_at": None, "historical_pit_certified": False})
        for name, value in (("plan.json", self.plan), ("source-bundle.json", binding)):
            path = root / name
            if path.exists():
                if read_signed(path) != value:
                    raise ValueError("DELISTED_QUEUE_FROZEN_INPUT_CONFLICT")
            elif not read_only:
                write_json_immutable(path, value)
        self.meta = self._read(root / "scheduler.json") if (root / "scheduler.json").exists() else {
            **AUTHORITY, "plan_sha256": self.identity, "global_failures": 0,
            "global_next_attempt_at": None,
        }
        if (self.meta.get("plan_sha256") != self.identity
                or type(self.meta.get("global_failures")) is not int
                or not 0 <= self.meta["global_failures"] <= 1_000_000):
            raise ValueError("DELISTED_QUEUE_SCHEDULER_INVALID")
        if self.meta["global_next_attempt_at"] is not None:
            self._clock(self.meta["global_next_attempt_at"])
        paths = list((root / "tasks").glob("*.json"))
        if {path.stem for path in paths} - set(self.definitions):
            raise ValueError("DELISTED_QUEUE_CHECKPOINT_OUTSIDE_PLAN")
        self.tasks = {}
        for task_id in self.definitions:
            path = self.task_path(task_id)
            task = self._read(path) if path.exists() else {
                **AUTHORITY, "plan_sha256": self.identity, "task_id": task_id,
                "before": None, "new_windows_exhausted": False, "windows": {},
                "active": None, "last_attempt_at": None,
            }
            if task.get("plan_sha256") != self.identity or task.get("task_id") != task_id:
                raise ValueError("DELISTED_QUEUE_CHECKPOINT_IDENTITY_INVALID")
            if task["before"] is not None:
                self.window(task_id, task["before"])
            for start, window in task["windows"].items():
                if window["start"] != start or self.window(task_id, start)[1] != window["end"]:
                    raise ValueError("DELISTED_QUEUE_SAVED_WINDOW_INVALID")
            if task["active"] is not None:
                self.validate_attempt(task["active"])
            self.tasks[task_id] = task

    @staticmethod
    def _clock(value: str) -> datetime:
        clock = datetime.fromisoformat(value)
        if clock.tzinfo is None:
            raise ValueError("DELISTED_QUEUE_TIMEZONE_REQUIRED")
        return clock

    @staticmethod
    def _read(path: Path) -> dict[str, Any]:
        value = read_signed(path)
        if any(value.get(k) is not False for k, v in AUTHORITY.items() if v is False):
            raise ValueError("DELISTED_QUEUE_AUTHORITY_INVALID")
        return value

    def save_task(self, task: dict[str, Any]) -> None:
        if self.read_only:
            raise ValueError("DELISTED_QUEUE_READ_ONLY")
        super().save_task(task)

    def save_meta(self) -> None:
        if self.read_only:
            raise ValueError("DELISTED_QUEUE_READ_ONLY")
        super().save_meta()

    def window(self, task_id: str, start: str) -> tuple[str, str]:
        definition = self.definitions[task_id]
        day = date.fromisoformat(start)
        first = date.fromisoformat(definition["requested_start"])
        last = date.fromisoformat(definition["requested_end"])
        if day.isoformat() != start or not first <= day <= last or day != max(
            first, day.replace(day=1)
        ):
            raise ValueError("DELISTED_QUEUE_WINDOW_OUTSIDE_SCOPE")
        end = min(last, day.replace(day=calendar.monthrange(day.year, day.month)[1]))
        return start, str(end)

    def scope_binding(self, task_id: str) -> dict[str, Any]:
        task = self.definitions[task_id]
        return {
            "plan_sha256": self.identity, "task_id": task_id,
            "source_bundle_sha256": self.bundle_identity, "scope_version": self.scope_version,
            "source_kind": SOURCE_KIND, "security_status": SECURITY_STATUS,
            "source_reconciliation_sha256": task["source_reconciliation_sha256"],
            "source_record_sha256": task["source_record_sha256"],
            "master_sha256": task["source_master_sha256"],
            "source_dataset_sha256": task["source_dataset_sha256"],
            "instrument_id": task["instrument_id"], "symbol": task["symbol"],
            "security_type": "STOCK", "frequency": "day",
            "original_candidate_source": task["execution_source"],
            "execution_source": task["execution_source"],
            "vendor_listing_date_claim": task["vendor_listing_date_claim"],
            "official_reported_delisting_date_claim": task[
                "official_reported_delisting_date_claim"],
            "listing_claim_used_as_authoritative_boundary": False,
            "delisting_claim_used_as_last_trade_boundary": False,
            "historical_available_at": None, "historical_pit_certified": False,
        }

    def validate_attempt(self, attempt: dict[str, Any]) -> None:
        scope = attempt["scope"]
        if (re.fullmatch(r"[a-f0-9]{32}", attempt["attempt_id"]) is None
                or type(attempt["attempt_number"]) is not int
                or not 1 <= attempt["attempt_number"] <= 1_000_000
                or type(attempt["retry"]) is not bool
                or re.fullmatch(r"[a-f0-9]{40}", scope["code_revision"]) is None):
            raise ValueError("DELISTED_QUEUE_ATTEMPT_INVALID")
        started = self._clock(attempt["started_at"])
        binding = self.scope_binding(scope["task_id"])
        expected = {**binding, "start": scope["start"], "end": scope["end"],
                    "code_revision": scope["code_revision"]}
        if (scope != expected or self.window(scope["task_id"], scope["start"])[1] != scope["end"]
                or date.fromisoformat(scope["end"]) >= started.astimezone(SHANGHAI).date()
                or started < self._clock(self.plan["source_observed_at"])):
            raise ValueError("DELISTED_QUEUE_ATTEMPT_SCOPE_INVALID")

    def claim(self, *, now: datetime, revision: str, retry: bool = False) -> dict[str, Any] | None:
        if self.read_only:
            raise ValueError("DELISTED_QUEUE_READ_ONLY")
        if now.tzinfo is None or re.fullmatch(r"[a-f0-9]{40}", revision) is None:
            raise ValueError("DELISTED_QUEUE_CLAIM_CLOCK_OR_REVISION_INVALID")
        deadline = self.meta["global_next_attempt_at"]
        if deadline and now < self._clock(deadline):
            return None
        for task in sorted(self.tasks.values(), key=lambda t: (
            t["last_attempt_at"] or "", t["task_id"]
        )):
            if task["active"] is not None:
                continue
            if retry:
                due = sorted((window for window in task["windows"].values()
                              if window["next_attempt_at"] is not None
                              and self._clock(window["next_attempt_at"]) <= now),
                             key=lambda window: self._clock(window["next_attempt_at"]))
                if not due:
                    continue
                previous = due[0]
                start, end = previous["start"], previous["end"]
            else:
                frontier = self.frontier(task)
                if frontier is None:
                    task["new_windows_exhausted"] = True
                    self.save_task(task)
                    continue
                start, end = frontier
                previous = task["windows"].get(start)
                if previous and previous["next_attempt_at"] and self._clock(
                    previous["next_attempt_at"]
                ) > now:
                    continue
            attempt = {"attempt_id": uuid4().hex, "started_at": now.isoformat(), "retry": retry,
                       "attempt_number": previous["attempts"] + 1 if previous else 1,
                       "scope": {**self.scope_binding(task["task_id"]), "start": start, "end": end,
                                 "code_revision": revision}}
            self.validate_attempt(attempt)
            task["active"], task["last_attempt_at"] = attempt, now.isoformat()
            self.save_task(task)
            return attempt
        return None

    def finish(self, store: HistoryStore, receipt: dict[str, Any], *, now: datetime) -> None:
        self.validate_attempt(receipt["attempt"])
        if store.load(receipt["attempt"]["attempt_id"]) != receipt:
            raise ValueError("DELISTED_QUEUE_RECEIPT_REPLAY_MISMATCH")
        super().finish(store, receipt, now=now)

    def recover(self, store: HistoryStore, *, now: datetime) -> dict[str, int]:
        from scripts.run_history_backfill import read_capture

        replayed = interrupted = repaired = 0
        for task_id in self.tasks:
            task = self.tasks[task_id]
            attempt = task["active"]
            if attempt is not None:
                self.validate_attempt(attempt)
                if store.path(attempt["attempt_id"], "receipt.json").exists():
                    receipt = store.load(attempt["attempt_id"])
                    replayed += 1
                else:
                    if not store.path(attempt["attempt_id"], "request.json").exists():
                        store.begin(attempt)
                    receipt = store.seal(attempt, read_capture(store, attempt), state="INTERRUPTED",
                                         error="PREVIOUS_PROCESS_INTERRUPTED")
                    interrupted += 1
                self.finish(store, receipt, now=now)
            task = self.tasks[task_id]
            changed = False
            while (frontier := self.frontier(task)) is not None:
                saved = task["windows"].get(frontier[0])
                if saved is None or saved["state"] in YIELDED:
                    break
                receipt = store.load(saved["latest_attempt_id"])
                self.validate_attempt(receipt["attempt"])
                scope = receipt["attempt"]["scope"]
                if (receipt["evidence_sha256"] != saved["receipt_sha256"]
                        or scope["task_id"] != task_id or (scope["start"], scope["end"]) != frontier
                        or receipt["quality"]["state"] != saved["state"]):
                    raise ValueError("DELISTED_QUEUE_FRONTIER_RECEIPT_INVALID")
                task["before"] = frontier[0]
                changed = True
            exhausted = self.frontier(task) is None
            if changed or task["new_windows_exhausted"] != exhausted:
                task["new_windows_exhausted"] = exhausted
                self.save_task(task)
                repaired += 1
        return {"replayed_sealed_receipts": replayed, "interrupted_attempts": interrupted,
                "repaired_frontiers": repaired}

    def snapshot(self, *, now: datetime) -> dict[str, Any]:
        details: list[dict[str, Any]] = []
        states: Counter[str] = Counter()
        for task_id, task in self.tasks.items():
            definition = self.definitions[task_id]
            windows = list(task["windows"].values())
            states.update(window["state"] for window in windows)
            frontier = self.frontier(task)
            details.append({"task_id": task_id, "instrument_id": definition["instrument_id"],
                            "security_status": SECURITY_STATUS, "frequency": "day",
                            "requested_start": definition["requested_start"],
                            "requested_end": definition["requested_end"],
                            "next_window_before": task["before"],
                            "new_windows_exhausted": frontier is None,
                            "unexamined_range": [definition["requested_start"], frontier[1]]
                            if frontier else None, "active_attempt": task["active"],
                            "qualified_rows": sum(window["qualified_rows"] for window in windows),
                            "windows": windows})
        return signed({
            **AUTHORITY, "schema_version": "qtrade.delisted-history-progress.v1",
            "source_kind": SOURCE_KIND, "source_bundle_sha256": self.bundle_identity,
            "plan_sha256": self.identity, "scope_version": self.scope_version,
            "updated_at": now.isoformat(), "frozen_scope_count": len(self.tasks),
            "planned_delisted_securities": len(self.tasks), "window_states": dict(states),
            "qualified_rows": sum(task["qualified_rows"] for task in details),
            "coverage_basis": "SEALED_RECEIPT_ACCOUNTING_NOT_A_NEW_BULK_REVALIDATION",
            "current_universe_coverage_delta": 0, "published_qlib_versions": 0,
            "global_next_attempt_at": self.meta["global_next_attempt_at"],
            "all_requested_windows_examined": all(task["new_windows_exhausted"]
                                                  for task in details),
            "historical_pit_certified": False, "complete_historical_pit_symbols": 0,
            "ten_year_history_certified": False, "independent_source_reconciled": False,
            "historical_available_at": None, "tasks": details,
        })
