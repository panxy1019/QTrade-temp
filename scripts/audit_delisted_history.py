"""Read-only replay of delisted observations; compiled tasks never count as coverage."""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.workflow import digest
from scripts.atomic_json import write_json_immutable
from scripts.delisted_history_queue import SOURCE_KIND, YIELDED, DelistedHistoryQueue
from scripts.history_backfill_store import PRICE_BASIS, HistoryStore
from scripts.run_history_backfill import worker_lease


class AuditLimit(Exception):
    pass


def audit_delisted(
    root: Path, bundle: Path, *, revision: str, now: datetime, seconds: float = 60,
    maximum_receipts: int = 10000, maximum_details: int = 50,
    lease_dir: Path = Path("/root/qtrade-local"),
) -> dict[str, Any]:
    if (now.tzinfo is None or re.fullmatch(r"[a-f0-9]{40}", revision) is None
            or not 1 <= seconds <= 1800 or not 1 <= maximum_receipts <= 100000
            or not 0 <= maximum_details <= 1000):
        raise ValueError("DELISTED_AUDIT_BUDGET_OR_IDENTITY_INVALID")
    started = time.monotonic()
    with worker_lease(lease_dir):
        queue = DelistedHistoryQueue(root, bundle, now=now, read_only=True)
        store = HistoryStore(root / "lake")
        receipts: dict[str, dict[str, Any]] = {}
        attempted: set[str] = set()
        referenced: set[str] = set()
        hashes, checkpoints = {}, {}
        rows = 0
        instruments: set[str] = set()
        sessions: set[tuple[str, str]] = set()
        states: Counter[str] = Counter()
        orphan_states: Counter[str] = Counter()
        superseded_states: Counter[str] = Counter()
        latest_numbers: dict[tuple[str, str], int] = {}
        counts = Counter({key: 0 for key in (
            "persisted_scope_checkpoints", "verified_latest_windows", "verified_accepted_windows",
            "active_claims", "unsealed_attempt_directories", "integrity_errors",
            "cursor_repairs_needed",
        )})
        errors: list[dict[str, Any]] = []
        gaps: list[dict[str, Any]] = []
        limited = False

        def budget() -> None:
            if time.monotonic() - started >= seconds:
                raise AuditLimit

        def issue(task_id: str | None, error: Exception, attempt_id: str | None = None) -> None:
            counts["integrity_errors"] += 1
            if len(errors) < maximum_details:
                code = str(error)
                errors.append({"task_id": task_id, "attempt_id": attempt_id,
                               "error_code": code if re.fullmatch(r"[A-Z_]{1,100}", code)
                               else type(error).__name__})

        def load(attempt_id: str) -> dict[str, Any]:
            budget()
            if attempt_id in receipts:
                return receipts[attempt_id]
            if attempt_id in attempted:
                raise ValueError("DELISTED_AUDIT_PREVIOUSLY_REJECTED_RECEIPT")
            if len(attempted) >= maximum_receipts:
                raise AuditLimit
            attempted.add(attempt_id)
            value = store.load(attempt_id)
            queue.validate_attempt(value["attempt"])
            stamp = queue._clock(value["sealed_at"])
            if (stamp > now or stamp < queue._clock(value["attempt"]["started_at"])
                    or value["price_basis"] != PRICE_BASIS):
                raise ValueError("DELISTED_AUDIT_RECEIPT_CLOCK_OR_BASIS_INVALID")
            receipts[attempt_id] = value
            return value

        try:
            for task_id, task in queue.tasks.items():
                budget()
                if queue.task_path(task_id).exists():
                    counts["persisted_scope_checkpoints"] += 1
                    checkpoints[task_id] = task["evidence_sha256"]
                if task["active"] is not None:
                    counts["active_claims"] += 1
                frontier = queue.frontier(task)
                if task["new_windows_exhausted"] != (frontier is None) or (
                    frontier is not None and frontier[0] in task["windows"]
                    and task["windows"][frontier[0]]["state"] not in YIELDED
                ):
                    counts["cursor_repairs_needed"] += 1
                # An advancing cursor must retain every examined newer month.
                cursor = task["before"]
                if cursor is not None:
                    virtual = {**task, "before": None}
                    while ((position := queue.frontier(virtual)) is not None
                           and position[0] >= cursor):
                        saved = task["windows"].get(position[0])
                        if saved is None or saved["state"] in YIELDED:
                            issue(task_id, ValueError(
                                "DELISTED_AUDIT_CURSOR_SKIPS_UNEXAMINED_WINDOW"))
                            break
                        virtual["before"] = position[0]
                for start, saved in task["windows"].items():
                    budget()
                    latest_id = saved["latest_attempt_id"]
                    referenced.add(latest_id)
                    head_id = saved["accepted_attempt_id"]
                    if head_id is not None:
                        referenced.add(head_id)
                    try:
                        latest = load(latest_id)
                        scope = latest["attempt"]["scope"]
                        if (scope["task_id"] != task_id or scope["start"] != start
                                or scope["end"] != saved["end"]
                                or saved["receipt_sha256"] != latest["evidence_sha256"]
                                or saved["quality"] != latest["quality"]
                                or saved["state"] != latest["quality"]["state"]
                                or saved["stage_counts"] != latest["stage_counts"]):
                            raise ValueError("DELISTED_AUDIT_LATEST_WINDOW_MISMATCH")
                        head = load(head_id) if head_id is not None else None
                        quality = head["quality"] if head is not None else None
                        expected_rows = quality["qualified_rows"] if quality else 0
                        if ((head is not None and (
                            head["attempt"]["scope"]["task_id"] != task_id
                            or head["attempt"]["scope"]["start"] != start or expected_rows <= 0
                        )) or saved["qualified_rows"] != expected_rows
                                or saved["accepted_quality"] != quality):
                            raise ValueError("DELISTED_AUDIT_ACCEPTED_COVERAGE_MISMATCH")
                        counts["verified_latest_windows"] += 1
                        latest_numbers[(task_id, start)] = latest["attempt"]["attempt_number"]
                        states[latest["quality"]["state"]] += 1
                        if head is not None:
                            hashes[task_id + "." + start] = head["evidence_sha256"]
                            counts["verified_accepted_windows"] += 1
                            rows += expected_rows
                            instruments.add(scope["instrument_id"])
                            sessions.update((scope["instrument_id"], day) for day in quality[
                                "qualified_observed_sessions"])
                        if len(gaps) < maximum_details and (head is None or saved[
                            "next_attempt_at"] is not None or saved["source_conflict"] is not None
                        ):
                            gaps.append({"instrument_id": scope["instrument_id"], "start": start,
                                         "end": saved["end"], "state": saved["state"],
                                         "verified_accepted_rows": expected_rows,
                                         "next_attempt_at": saved["next_attempt_at"],
                                         "source_conflict": saved["source_conflict"]})
                    except (OSError, ValueError, KeyError, TypeError) as error:
                        issue(task_id, error, latest_id)
            for folder in (store.root / "attempts").glob("*"):
                budget()
                if not folder.is_dir():
                    continue
                if not (folder / "receipt.json").exists():
                    counts["unsealed_attempt_directories"] += 1
                    continue
                if folder.name in attempted and folder.name not in receipts:
                    continue
                try:
                    receipt = load(folder.name)
                    if folder.name not in referenced:
                        scope = receipt["attempt"]["scope"]
                        latest_number = latest_numbers.get((scope["task_id"], scope["start"]), 0)
                        category = (superseded_states if receipt["attempt"]["attempt_number"]
                                    < latest_number else orphan_states)
                        category[receipt["quality"]["state"]] += 1
                except (OSError, ValueError, KeyError, TypeError) as error:
                    issue(None, error, folder.name)
        except AuditLimit:
            limited = True
        state = ("INVALID_EVIDENCE" if counts["integrity_errors"] else "PARTIAL_AUDIT_LIMIT"
                 if limited else "VERIFIED_WITH_PENDING_RECOVERY" if counts["active_claims"]
                 or counts["cursor_repairs_needed"] or counts["unsealed_attempt_directories"]
                 or orphan_states else "VERIFIED")
        return signed({
            **AUTHORITY, "schema_version": "qtrade.delisted-history-audit.v1", "state": state,
            "audited_at": now.isoformat(), "code_revision": revision, "source_kind": SOURCE_KIND,
            "implementation_sha256": {name: sha256((Path(__file__).parent / name).read_bytes())
                                      .hexdigest() for name in (
                                          "audit_delisted_history.py", "delisted_history_queue.py",
                                          "history_backfill_queue.py", "history_backfill_store.py",
                                      )},
            "source_bundle_sha256": queue.bundle_identity, "plan_sha256": queue.identity,
            "scope_version": queue.scope_version, "planned_scopes_metadata_only": len(queue.tasks),
            "verified_accepted_rows": rows, "verified_observed_sessions": len(sessions),
            "verified_instrument_count": len(instruments),
            "checkpoint_counts": dict(counts), "latest_window_states": dict(states),
            "unreferenced_receipt_states": dict(orphan_states), "limit_reached": limited,
            "superseded_archived_receipt_states": dict(superseded_states),
            "receipt_checks_attempted": len(attempted), "verified_receipt_attempts": len(receipts),
            "verified_query_attempts": sum(r["stage_counts"]["requested"]
                                           for r in receipts.values()),
            "downloaded_rows_including_reobservations": sum(r["downloaded_rows"]
                                                          for r in receipts.values()),
            "accepted_receipts_sha256": digest(hashes), "checkpoints_sha256": digest(checkpoints),
            "verified_receipts_sha256": digest({k: v["evidence_sha256"]
                                               for k, v in receipts.items()}),
            "coverage_basis": "RAW_AND_PARQUET_REPLAY_OF_CHECKPOINT_ACCEPTED_RECEIPTS_ONLY",
            "coverage_is_lower_bound_if_audit_incomplete": True, "price_basis": PRICE_BASIS,
            "current_universe_coverage_delta": 0, "published_qlib_versions": 0,
            "historical_pit_certified": False, "complete_historical_pit_symbols": 0,
            "historical_available_at": None, "independent_source_reconciled": False,
            "ten_year_history_certified": False, "errors": errors, "gap_examples": gaps,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        })


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--maximum-receipts", type=int, default=10000)
    parser.add_argument("--maximum-details", type=int, default=50)
    parser.add_argument("--lease-dir", type=Path, default=Path("/root/qtrade-local"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = audit_delisted(args.root, args.bundle, revision=args.source_revision,
                                now=datetime.now(UTC), seconds=args.seconds,
                                maximum_receipts=args.maximum_receipts,
                                maximum_details=args.maximum_details, lease_dir=args.lease_dir)
    except BlockingIOError:
        print("DELISTED_AUDIT_YIELDED_TO_SINGLE_HISTORY_WRITER")
        raise SystemExit(2) from None
    if args.output:
        write_json_immutable(args.output, result)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["state"] == "VERIFIED" else 2)


if __name__ == "__main__":
    main()
