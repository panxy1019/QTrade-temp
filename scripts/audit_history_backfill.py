"""Offline receipt replay and bounded coverage summary; never downloads or repairs state."""

from __future__ import annotations

import argparse
import json
import re
import time
from collections import Counter
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from qtrade.market_data.backfill_plan import build_backfill_plan, month_window
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts.atomic_json import write_json_immutable
from scripts.history_backfill_queue import read_signed
from scripts.history_backfill_store import PRICE_BASIS, HistoryStore
from scripts.run_history_backfill import worker_lease
from scripts.unified_store import canonical

YIELDED = {"YIELDED_TO_SHARED_VENDOR_SESSION", "YIELDED_TO_CURRENT_COLLECTION"}


class AuditLimit(Exception):
    pass


def audit_history(
    root: Path, *, revision: str, now: datetime, seconds: float = 60,
    maximum_receipts: int = 10000, maximum_details: int = 50,
    lease_dir: Path = Path("/root/qtrade-local"),
) -> dict[str, Any]:
    if (now.tzinfo is None or re.fullmatch(r"[a-f0-9]{40}", revision) is None
            or not 1 <= seconds <= 1800 or not 1 <= maximum_receipts <= 100000
            or not 0 <= maximum_details <= 1000):
        raise ValueError("HISTORY_AUDIT_BUDGET_OR_IDENTITY_INVALID")
    started = time.monotonic()
    with worker_lease(lease_dir):
        plan, master = read_signed(root / "plan.json"), read_signed(root / "master.json")
        expected = build_backfill_plan(
            master, requested_end=date.fromisoformat(plan["requested_end"]),
            observed_at=datetime.fromisoformat(plan["observed_at"]),
            code_revision=plan["code_revision"], years=plan["years"],
        )
        if plan != expected:
            raise ValueError("HISTORY_AUDIT_FROZEN_INPUT_INVALID")
        definitions = {t["task_id"]: t for t in plan["tasks"]}
        securities = {s["instrument_id"]: s for s in master["securities"]}
        windows: dict[str, str] = {}
        before = None
        while (window := month_window(date.fromisoformat(plan["requested_start"]),
                                      date.fromisoformat(plan["requested_end"]),
                                      before=before)) is not None:
            windows[str(window[0])] = str(window[1])
            before = window[0]
        store = HistoryStore(root / "lake")
        receipts: dict[str, dict[str, Any]] = {}
        checked_attempts: set[str] = set()
        referenced: set[str] = set()
        latest_numbers: dict[tuple[str, str], int] = {}
        checkpoint_hashes: dict[str, str] = {}
        accepted_hashes: dict[str, str] = {}
        errors: list[dict[str, Any]] = []
        gaps: list[dict[str, Any]] = []
        pending: list[str] = []
        latest_states: Counter[str] = Counter()
        uncommitted_states: Counter[str] = Counter()
        superseded_states: Counter[str] = Counter()
        frequencies: dict[str, dict[str, Any]] = {
            f: {"verified_accepted_rows": 0, "verified_observed_sessions": 0,
                "qualified_instruments": set(), "qualified_start": None, "qualified_end": None}
            for f in ("day", "5min")}
        counts = Counter({k: 0 for k in (
            "persisted_scope_checkpoints", "verified_latest_windows", "verified_accepted_windows",
            "unsealed_attempt_directories", "active_claims", "cursor_repairs_needed",
            "cursor_exhausted_scopes", "integrity_errors")})
        limited = False

        def budget() -> None:
            if time.monotonic() - started >= seconds:
                raise AuditLimit

        def issue(task_id: str | None, code: str, attempt_id: str | None = None) -> None:
            if len(errors) < maximum_details:
                errors.append({"task_id": task_id, "attempt_id": attempt_id, "error_code": code})
            counts["integrity_errors"] += 1

        def load(attempt_id: str) -> dict[str, Any]:
            budget()
            if attempt_id in receipts:
                return receipts[attempt_id]
            if attempt_id in checked_attempts:
                raise ValueError("HISTORY_AUDIT_RECEIPT_PREVIOUSLY_REJECTED")
            if len(checked_attempts) >= maximum_receipts:
                raise AuditLimit
            checked_attempts.add(attempt_id)
            value = store.load(attempt_id)
            scope = value["attempt"]["scope"]
            definition = definitions[scope["task_id"]]
            security = securities[definition["instrument_id"]]
            binding = {"plan_sha256": plan["evidence_sha256"],
                       "master_sha256": plan["master_sha256"],
                       "universe_version": plan["universe_version"],
                       "instrument_id": definition["instrument_id"], "symbol": security["symbol"],
                       "security_type": security["security_type"],
                       "frequency": definition["frequency"],
                       "original_candidate_source": definition["candidate_source"],
                       "execution_source": "BAOSTOCK_RAW_ADJUSTFLAG_3"}
            if (any(scope.get(k) != v for k, v in binding.items())
                    or windows.get(scope["start"]) != scope["end"]
                    or re.fullmatch(r"[a-f0-9]{40}", scope["code_revision"]) is None
                    or value["price_basis"] != PRICE_BASIS):
                raise ValueError("HISTORY_AUDIT_RECEIPT_SCOPE_INVALID")
            receipts[attempt_id] = value
            return value

        try:
            for task_id, definition in definitions.items():
                budget()
                path = root / "tasks" / (task_id + ".json")
                if not path.exists():
                    continue  # Planned scope is never coverage.
                try:
                    task = read_signed(path)
                    if task["task_id"] != task_id or task["plan_sha256"] != plan["evidence_sha256"]:
                        raise ValueError("HISTORY_AUDIT_CHECKPOINT_SCOPE_INVALID")
                    counts["persisted_scope_checkpoints"] += 1
                    checkpoint_hashes[task_id] = task["evidence_sha256"]
                    cursor = task["before"]
                    if cursor is not None:
                        if cursor not in windows:
                            raise ValueError("HISTORY_AUDIT_CURSOR_INVALID")
                        for start in windows:
                            if start < cursor:
                                continue
                            if (start not in task["windows"]
                                    or task["windows"][start]["state"] in YIELDED):
                                issue(task_id, "HISTORY_AUDIT_CURSOR_SKIPS_UNEXAMINED_WINDOW")
                    frontier = month_window(
                        date.fromisoformat(definition["requested_start"]),
                        date.fromisoformat(definition["requested_end"]),
                        before=date.fromisoformat(cursor) if cursor else None,
                    )
                    needs_repair = task["new_windows_exhausted"] != (frontier is None)
                    if frontier is None:
                        counts["cursor_exhausted_scopes"] += 1
                    else:
                        prior = task["windows"].get(str(frontier[0]))
                        if prior and prior["state"] not in YIELDED:
                            needs_repair = True
                    counts["cursor_repairs_needed"] += int(needs_repair)
                    if task["active"] is not None:
                        counts["active_claims"] += 1
                        pending.append(task["active"]["attempt_id"])
                    for start, saved in task["windows"].items():
                        budget()
                        latest_id = saved["latest_attempt_id"]
                        referenced.add(latest_id)
                        if saved["accepted_attempt_id"] is not None:
                            referenced.add(saved["accepted_attempt_id"])
                        try:
                            value = load(latest_id)
                            scope = value["attempt"]["scope"]
                            if (scope["task_id"] != task_id or scope["start"] != start
                                    or windows.get(start) != saved["end"]
                                    or saved["start"] != start
                                    or saved["receipt_sha256"] != value["evidence_sha256"]
                                    or saved["state"] != value["quality"]["state"]):
                                raise ValueError("HISTORY_AUDIT_WINDOW_RECEIPT_INVALID")
                            latest_numbers[(task_id, start)] = value["attempt"]["attempt_number"]
                            head = load(saved["accepted_attempt_id"]) if saved[
                                "accepted_attempt_id"] else None
                            quality = head["quality"] if head else None
                            expected_rows = quality["qualified_rows"] if quality else 0
                            if (head is not None and (
                                    head["attempt"]["scope"]["task_id"] != task_id
                                    or head["attempt"]["scope"]["start"] != start
                                    or expected_rows <= 0)
                                    or saved["qualified_rows"] != expected_rows
                                    or saved["accepted_quality"] != quality):
                                raise ValueError("HISTORY_AUDIT_CHECKPOINT_COVERAGE_MISMATCH")
                            counts["verified_latest_windows"] += 1
                            latest_states[value["quality"]["state"]] += 1
                            if len(gaps) < maximum_details and (head is None or saved[
                                    "next_attempt_at"] or saved["source_conflict"]):
                                gaps.append({"instrument_id": definition["instrument_id"],
                                             "frequency": definition["frequency"], "start": start,
                                             "end": saved["end"], "state": saved["state"],
                                             "verified_accepted_rows": expected_rows,
                                             "next_attempt_at": saved["next_attempt_at"],
                                             "source_conflict": saved["source_conflict"]})
                            if head is not None:
                                accepted_hashes[task_id + "." + start] = head["evidence_sha256"]
                                counts["verified_accepted_windows"] += 1
                                result = frequencies[definition["frequency"]]
                                result["verified_accepted_rows"] += expected_rows
                                days = quality["qualified_observed_sessions"]
                                result["verified_observed_sessions"] += len(days)
                                result["qualified_instruments"].add(definition["instrument_id"])
                                result["qualified_start"] = min(days + ([result["qualified_start"]]
                                    if result["qualified_start"] else []))
                                result["qualified_end"] = max(days + ([result["qualified_end"]]
                                    if result["qualified_end"] else []))
                        except (OSError, ValueError, KeyError, TypeError) as error:
                            code = str(error)
                            issue(task_id, code if re.fullmatch(r"[A-Z_]{1,100}", code)
                                  else type(error).__name__, latest_id)
                except (OSError, ValueError, KeyError, TypeError) as error:
                    code = str(error)
                    issue(task_id, code if re.fullmatch(r"[A-Z_]{1,100}", code)
                          else type(error).__name__)
            folders = store.root / "attempts"
            if folders.exists():
                for folder in folders.iterdir():
                    budget()
                    if not folder.is_dir():
                        continue
                    if not (folder / "receipt.json").exists():
                        counts["unsealed_attempt_directories"] += 1
                        continue
                    if folder.name in checked_attempts and folder.name not in receipts:
                        continue
                    try:
                        value = load(folder.name)
                        if folder.name not in referenced:
                            scope = value["attempt"]["scope"]
                            number = latest_numbers.get((scope["task_id"], scope["start"]), 0)
                            target = (superseded_states if value["attempt"]["attempt_number"]
                                      < number
                                      else uncommitted_states)
                            target[value["quality"]["state"]] += 1
                    except (OSError, ValueError, KeyError, TypeError) as error:
                        code = str(error)
                        issue(None, code if re.fullmatch(r"[A-Z_]{1,100}", code)
                              else type(error).__name__, folder.name)
        except AuditLimit:
            limited = True
        for result in frequencies.values():
            result["qualified_instrument_count"] = len(result.pop("qualified_instruments"))
        pending_sealed = sum(attempt_id in receipts for attempt_id in pending)
        state = ("INVALID_EVIDENCE" if counts["integrity_errors"] else "PARTIAL_AUDIT_LIMIT"
                 if limited else "VERIFIED_WITH_PENDING_RECOVERY"
                 if pending or counts["cursor_repairs_needed"] or uncommitted_states
                 or counts["unsealed_attempt_directories"] else "VERIFIED")
        implementation = {name: sha256((Path(__file__).parent / name).read_bytes()).hexdigest()
                          for name in ("audit_history_backfill.py", "history_backfill_queue.py",
                                       "history_backfill_store.py")}
        return signed({
            **AUTHORITY, "schema_version": "qtrade.history-receipt-audit.v1", "state": state,
            "observed_at": now.isoformat(), "code_revision": revision,
            "implementation_sha256": implementation, "plan_sha256": plan["evidence_sha256"],
            "master_sha256": plan["master_sha256"], "universe_version": plan["universe_version"],
            "planned_scope_count_metadata_only": len(definitions),
            "planned_windows_per_scope_metadata_only": len(windows),
            "frequency_coverage": frequencies, "checkpoint_counts": dict(counts),
            "latest_window_states": dict(latest_states),
            "receipt_checks_attempted": len(checked_attempts),
            "verified_receipt_attempts": len(receipts),
            "producer_code_revisions": dict(Counter(r["attempt"]["scope"]["code_revision"]
                                                     for r in receipts.values())),
            "verified_query_attempts": sum(r["stage_counts"]["requested"]
                                           for r in receipts.values()),
            "downloaded_rows_including_reobservations": sum(r["downloaded_rows"]
                                                          for r in receipts.values()),
            "unreferenced_sealed_receipt_states": dict(uncommitted_states),
            "superseded_archived_receipt_states": dict(superseded_states),
            "pending_sealed_claims": pending_sealed, "limit_reached": limited,
            "verified_attempt_ids_sha256": sha256(canonical(sorted(receipts))).hexdigest(),
            "verified_receipts_sha256": sha256(canonical({
                k: v["evidence_sha256"] for k, v in receipts.items()})).hexdigest(),
            "checkpoint_inputs_sha256": sha256(canonical(checkpoint_hashes)).hexdigest(),
            "accepted_receipts_sha256": sha256(canonical(accepted_hashes)).hexdigest(),
            "gap_examples": gaps, "error_examples": errors,
            "coverage_basis": "RAW_AND_PARQUET_REPLAY_OF_CHECKPOINT_ACCEPTED_RECEIPTS_ONLY",
            "price_basis": PRICE_BASIS,
            "coverage_is_lower_bound_if_audit_incomplete": True,
            "ten_year_history_certified": False, "historical_pit_certified": False,
            "historical_available_at": None, "independent_source_reconciled": False,
            "elapsed_seconds": round(time.monotonic() - started, 3),
        })


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--seconds", type=float, default=60)
    parser.add_argument("--maximum-receipts", type=int, default=10000)
    parser.add_argument("--maximum-details", type=int, default=50)
    parser.add_argument("--lease-dir", type=Path, default=Path("/root/qtrade-local"))
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    try:
        result = audit_history(
            args.root, revision=args.source_revision, now=datetime.now(UTC), seconds=args.seconds,
            maximum_receipts=args.maximum_receipts, maximum_details=args.maximum_details,
            lease_dir=args.lease_dir,
        )
    except BlockingIOError:
        print("HISTORY_AUDIT_YIELDED_TO_ACTIVE_WRITER")
        raise SystemExit(2) from None
    if args.output:
        write_json_immutable(args.output, result)
    print(json.dumps(result, ensure_ascii=False))
    raise SystemExit(0 if result["state"] == "VERIFIED" else 2)


if __name__ == "__main__":
    main()
