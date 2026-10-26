"""Persistent per-scope monthly cursors, independent retries and immutable attempt recovery."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any
from uuid import uuid4

from qtrade.market_data.backfill_plan import build_backfill_plan, month_window
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts.atomic_json import write_json_atomic, write_json_immutable
from scripts.history_backfill_store import HistoryStore


def read_signed(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if (signed({k: v for k, v in value.items() if k != "evidence_sha256"}) != value
            or any(value.get(k) != v for k, v in AUTHORITY.items())):
        raise ValueError("HISTORY_CHECKPOINT_HASH_OR_AUTHORITY_INVALID")
    return value


class HistoryQueue:
    """All mutation requires the worker's process lease; no SQLite WAL on NFS."""

    def __init__(self, root: Path, plan: dict[str, Any], master: dict[str, Any]):
        from datetime import date

        expected = build_backfill_plan(
            master, requested_end=date.fromisoformat(plan["requested_end"]),
            observed_at=datetime.fromisoformat(plan["observed_at"]),
            code_revision=plan["code_revision"], years=plan["years"],
        )
        if expected != plan:
            raise ValueError("HISTORY_FROZEN_PLAN_OR_MASTER_INVALID")
        self.root, self.plan, self.master = root, plan, master
        self.identity = plan["evidence_sha256"]
        self.definitions = {t["task_id"]: t for t in plan["tasks"]}
        self.securities = {s["instrument_id"]: s for s in master["securities"]}
        for name, value in (("plan.json", plan), ("master.json", master)):
            path = root / name
            if path.exists():
                if read_signed(path) != value:
                    raise ValueError("HISTORY_FROZEN_INPUT_CONFLICT")
            else:
                write_json_immutable(path, value)
        self.meta = (read_signed(root / "scheduler.json") if (root / "scheduler.json").exists()
                     else {**AUTHORITY, "plan_sha256": self.identity,
                           "global_failures": 0, "global_next_attempt_at": None})
        if self.meta["plan_sha256"] != self.identity:
            raise ValueError("HISTORY_SCHEDULER_PLAN_MISMATCH")
        self.tasks = {}
        for task_id in self.definitions:
            path = self.task_path(task_id)
            task = read_signed(path) if path.exists() else {
                **AUTHORITY, "plan_sha256": self.identity, "task_id": task_id,
                "before": None, "new_windows_exhausted": False, "windows": {},
                "active": None, "last_attempt_at": None,
            }
            if task["plan_sha256"] != self.identity or task["task_id"] != task_id:
                raise ValueError("HISTORY_SCOPE_CHECKPOINT_MISMATCH")
            self.tasks[task_id] = task

    def task_path(self, task_id: str) -> Path:
        if re.fullmatch(r"[a-f0-9]{64}", task_id) is None:
            raise ValueError("HISTORY_TASK_ID_INVALID")
        return self.root / "tasks" / (task_id + ".json")

    def save_task(self, task: dict[str, Any]) -> None:
        value = signed({k: v for k, v in task.items() if k != "evidence_sha256"})
        write_json_atomic(self.task_path(task["task_id"]), value)
        self.tasks[task["task_id"]] = value

    def save_meta(self) -> None:
        self.meta = signed({k: v for k, v in self.meta.items() if k != "evidence_sha256"})
        write_json_atomic(self.root / "scheduler.json", self.meta)

    def claim(self, *, now: datetime, revision: str, retry: bool = False) -> dict[str, Any] | None:
        from datetime import date

        if now.tzinfo is None or re.fullmatch(r"[a-f0-9]{40}", revision) is None:
            raise ValueError("HISTORY_CLAIM_CLOCK_OR_REVISION_INVALID")
        deadline = self.meta["global_next_attempt_at"]
        if deadline and now < datetime.fromisoformat(deadline):
            return None
        for task in sorted(self.tasks.values(), key=lambda t: (
                t["last_attempt_at"] or "", t["task_id"])):
            if task["active"] is not None:
                continue
            definition = self.definitions[task["task_id"]]
            if retry:
                due = sorted((w for w in task["windows"].values() if w["next_attempt_at"]
                              and datetime.fromisoformat(w["next_attempt_at"]) <= now),
                             key=lambda w: w["next_attempt_at"])
                if not due:
                    continue
                previous = due[0]
                start, end = previous["start"], previous["end"]
            else:
                if task["new_windows_exhausted"]:
                    continue
                window = month_window(
                    date.fromisoformat(definition["requested_start"]),
                    date.fromisoformat(definition["requested_end"]),
                    before=date.fromisoformat(task["before"]) if task["before"] else None,
                )
                if window is None:
                    task["new_windows_exhausted"] = True
                    self.save_task(task)
                    continue
                start, end = map(str, window)
                previous = task["windows"].get(start)
                if previous and previous["next_attempt_at"] and datetime.fromisoformat(
                        previous["next_attempt_at"]) > now:
                    continue
            if date.fromisoformat(end) >= now.date():
                raise ValueError("HISTORY_SCOPE_NOT_CLOSED")
            security = self.securities[definition["instrument_id"]]
            attempt = {"attempt_id": uuid4().hex, "started_at": now.isoformat(),
                       "retry": retry, "attempt_number": previous["attempts"] + 1
                       if previous else 1,
                       "scope": {"plan_sha256": self.identity, "task_id": task["task_id"],
                                 "master_sha256": self.plan["master_sha256"],
                                 "universe_version": self.plan["universe_version"],
                                 "instrument_id": definition["instrument_id"],
                                 "symbol": security["symbol"],
                                 "security_type": security["security_type"],
                                 "frequency": definition["frequency"], "start": start, "end": end,
                                 "code_revision": revision,
                                 "original_candidate_source": definition["candidate_source"],
                                 "execution_source": "BAOSTOCK_RAW_ADJUSTFLAG_3",
                                 "vendor_listing_date_claim": definition[
                                     "vendor_listing_date_claim"],
                                 "listing_claim_used_as_authoritative_boundary": False}}
            task["active"], task["last_attempt_at"] = attempt, now.isoformat()
            self.save_task(task)  # A process kill after this line is a durable interrupted attempt.
            return attempt
        return None

    def finish(self, store: HistoryStore, receipt: dict[str, Any], *, now: datetime) -> None:
        attempt = receipt["attempt"]
        task = self.tasks[attempt["scope"]["task_id"]]
        start = attempt["scope"]["start"]
        old = task["windows"].get(start)
        if task["active"] is None and old and old["latest_attempt_id"] == attempt["attempt_id"]:
            return
        if task["active"] != attempt or attempt["scope"]["plan_sha256"] != self.identity:
            raise ValueError("HISTORY_COMPLETION_CLAIM_MISMATCH")
        checked = store.load(attempt["attempt_id"])
        if checked != receipt:
            raise ValueError("HISTORY_COMPLETION_RECEIPT_MISMATCH")
        quality, state = receipt["quality"], receipt["quality"]["state"]
        accepted = old["accepted_attempt_id"] if old else None
        conflict = None
        if quality["qualified_rows"]:
            if accepted:
                prior_rows = store.qualified_rows(accepted)
                new_rows = store.qualified_rows(attempt["attempt_id"])
                if any(prior_rows[k] != new_rows[k] for k in prior_rows.keys() & new_rows.keys()):
                    conflict = "QUALIFIED_OVERLAP_CHANGED_QUARANTINED"
                elif prior_rows.keys() <= new_rows.keys():
                    accepted = attempt["attempt_id"]
                else:
                    conflict = "QUALIFIED_COVERAGE_REGRESSION_PRIOR_RETAINED"
            else:
                accepted = attempt["attempt_id"]
        failures = (old["failures"] if old else 0) + 1
        yielded = state in {"YIELDED_TO_SHARED_VENDOR_SESSION", "YIELDED_TO_CURRENT_COLLECTION"}
        complete = state == "CONTENT_QUALIFIED_WITH_UNRESOLVED_GAPS" and not quality.get(
            "partial_sessions") and conflict is None
        delay = (60 if yielded else min(21600, 1800 * 2 ** min(failures - 1, 4)))
        next_at = None if complete else (now + timedelta(seconds=delay)).isoformat()
        # A cooperative yield did not examine this window and must not advance its cursor.
        if not yielded and not attempt["retry"]:
            task["before"] = start
        task["windows"][start] = {
            "start": start, "end": attempt["scope"]["end"], "state": state,
            "attempts": attempt["attempt_number"], "failures": 0 if complete else failures,
            "latest_attempt_id": attempt["attempt_id"],
            "receipt_sha256": receipt["evidence_sha256"], "accepted_attempt_id": accepted,
            "next_attempt_at": next_at, "source_conflict": conflict,
            "qualified_rows": store.load(accepted)["quality"]["qualified_rows"] if accepted else 0,
            "quality": quality,
            "accepted_quality": store.load(accepted)["quality"] if accepted else None,
            "stage_counts": receipt["stage_counts"], "downloaded_rows": receipt["downloaded_rows"],
        }
        task["active"] = None
        # The immutable receipt and active claim already exist. Persist vendor-wide
        # backoff before completing the claim; recovery replays this receipt once.
        unseen_source_attempt = self.meta.get("last_source_attempt_id") != attempt["attempt_id"]
        if unseen_source_attempt and state in {
                "LOGIN_FAILED", "SOURCE_QUERY_FAILED", "QUERY_TIMEOUT"}:
            self.meta["global_failures"] += 1
            seconds = min(21600, 1800 * 2 ** min(self.meta["global_failures"] - 1, 4))
            self.meta["global_next_attempt_at"] = (now + timedelta(seconds=seconds)).isoformat()
            self.meta["last_source_attempt_id"] = attempt["attempt_id"]
            self.save_meta()
        elif unseen_source_attempt and receipt["raw"] and state not in {
                "YIELDED_TO_SHARED_VENDOR_SESSION", "YIELDED_TO_CURRENT_COLLECTION", "INTERRUPTED",
                "STORAGE_RESERVE_PAUSED"}:
            self.meta.update(global_failures=0, global_next_attempt_at=None,
                             last_source_attempt_id=attempt["attempt_id"])
            self.save_meta()
        self.save_task(task)

    def recover(self, store: HistoryStore, *, now: datetime) -> dict[str, int]:
        from scripts.run_history_backfill import read_capture

        replayed = interrupted = 0
        for task in self.tasks.values():
            attempt = task["active"]
            if attempt is None:
                continue
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
        return {"replayed_sealed_receipts": replayed, "interrupted_attempts": interrupted}

    def snapshot(self, *, now: datetime) -> dict[str, Any]:
        from datetime import date

        details = []
        states: Counter[str] = Counter()
        for task in self.tasks.values():
            definition = self.definitions[task["task_id"]]
            windows = list(task["windows"].values())
            for window in windows:
                states[window["state"]] += 1
            unexamined_end = (date.fromisoformat(task["before"]) - timedelta(days=1)
                              if task["before"] else date.fromisoformat(
                                  definition["requested_end"]))
            start = date.fromisoformat(definition["requested_start"])
            sessions = sorted(d for w in windows for d in (
                w["accepted_quality"] or {}).get("qualified_observed_sessions", []))
            details.append({
                "task_id": task["task_id"], "instrument_id": definition["instrument_id"],
                "frequency": definition["frequency"],
                "requested_start": definition["requested_start"],
                "requested_end": definition["requested_end"],
                "vendor_listing_date_claim": definition["vendor_listing_date_claim"],
                "listing_claim_used_as_authoritative_boundary": False,
                "new_windows_exhausted": task["new_windows_exhausted"],
                "next_window_before": task["before"], "active_attempt": task["active"],
                "unexamined_range": [str(start), str(unexamined_end)]
                if start <= unexamined_end else None,
                "qualified_rows": sum(w["qualified_rows"] for w in windows),
                "qualified_observed_start": sessions[0] if sessions else None,
                "qualified_observed_end": sessions[-1] if sessions else None,
                "qualified_observed_session_count": len(sessions),
                "windows": windows,
            })
        return signed({
            **AUTHORITY, "schema_version": "qtrade.history-progress.v1",
            "plan_sha256": self.identity, "universe_version": self.plan["universe_version"],
            "updated_at": now.isoformat(), "frozen_scope_count": len(self.tasks),
            "target_securities": self.plan["target_securities"], "window_states": dict(states),
            "qualified_rows": sum(t["qualified_rows"] for t in details),
            "frequency_summary": {f: {
                "planned_scopes": sum(t["frequency"] == f for t in details),
                "content_qualified_scopes": sum(t["frequency"] == f and t["qualified_rows"] > 0
                                                for t in details),
                "qualified_rows": sum(t["qualified_rows"] for t in details if t["frequency"] == f),
                "full_history_certified_scopes": 0, "independent_source_reconciled_scopes": 0,
            } for f in ("day", "5min")},
            "coverage_basis": "SEALED_RECEIPT_ACCOUNTING_NOT_A_NEW_BULK_REVALIDATION",
            "global_next_attempt_at": self.meta["global_next_attempt_at"],
            "all_requested_windows_examined": all(t["new_windows_exhausted"] for t in details),
            "historical_pit_certified": False, "ten_year_history_certified": False,
            "independent_source_reconciled": False, "historical_available_at": None,
            "tasks": details,
        })
