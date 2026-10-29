"""Bounded status backfill selection; failed symbols cannot starve unattempted ones."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.workflow import digest
from scripts.atomic_json import write_json_atomic


def read_attempts(
    root: Path, dataset: dict[str, Any], now: datetime,
) -> dict[str, dict[str, Any]]:
    path = root / "symbol-attempts.json"
    if not path.exists():
        return {}
    value = json.loads(path.read_text())
    if signed({k: v for k, v in value.items() if k != "evidence_sha256"}) != value or (
        value.get("schema_version") != "qtrade.pit-symbol-attempts.v1"
        or value.get("dataset_identity") != digest(dataset)
        or any(value.get(k) != v for k, v in AUTHORITY.items())
    ):
        raise ValueError("SYMBOL_ATTEMPT_LEDGER_IDENTITY_INVALID")
    allowed = {s["symbol"] for s in dataset["stocks"]}
    entries = value.get("entries")
    if not isinstance(entries, dict) or set(entries) - allowed:
        raise ValueError("SYMBOL_ATTEMPT_UNIVERSE_INVALID")
    for entry in entries.values():
        at = datetime.fromisoformat(entry["attempted_at"])
        retry = datetime.fromisoformat(entry["next_attempt_at"])
        if at.tzinfo is None or retry.tzinfo is None or at > now or (
            not 1800 <= (retry - at).total_seconds() <= 21600
        ) or (
            type(entry["attempts"]) is not int or not 1 <= entry["attempts"] <= 1_000_000
            or entry["state"] not in {"RUNNING", "FAILED", "SUCCEEDED"}
        ):
            raise ValueError("SYMBOL_ATTEMPT_STATE_OR_CLOCK_INVALID")
        error_type, vendor_code = entry.get("error_type"), entry.get("vendor_code")
        if (error_type is not None and (not isinstance(error_type, str) or re.fullmatch(
            r"[A-Za-z][A-Za-z0-9_]{0,79}", error_type
        ) is None)) or (vendor_code is not None and (
            not isinstance(vendor_code, str) or re.fullmatch(r"[0-9]{1,12}", vendor_code) is None
        )):
            raise ValueError("SYMBOL_ATTEMPT_ERROR_CODE_INVALID")
    return entries


def save_attempts(root: Path, dataset: dict[str, Any], entries: dict[str, Any]) -> None:
    write_json_atomic(root / "symbol-attempts.json", signed({
        **AUTHORITY, "schema_version": "qtrade.pit-symbol-attempts.v1",
        "dataset_identity": digest(dataset), "entries": entries,
    }))


def select_batch(
    symbols: list[str], completed: set[str], entries: dict[str, dict[str, Any]],
    now: datetime, batch_size: int,
) -> list[str]:
    if type(batch_size) is not int or not 1 <= batch_size <= 50 or (
        len(set(symbols)) != len(symbols)
        or any(re.fullmatch(r"[0-9]{6}", s) is None for s in symbols)
        or now.tzinfo is None
    ):
        raise ValueError("INVALID_BOUNDED_SOURCE_BATCH")
    missing = [s for s in symbols if s not in completed]
    if any(entries.get(s, {}).get("state") == "SUCCEEDED" for s in missing):
        raise ValueError("SUCCESSFUL_STATUS_CHECKPOINT_MISSING")
    unseen = [s for s in missing if s not in entries]
    due = sorted(
        (s for s in missing if s in entries
         and datetime.fromisoformat(entries[s]["next_attempt_at"]) <= now),
        key=lambda s: (entries[s]["next_attempt_at"], s),
    )
    # Separate budgets guarantee progress for both queues, even with persistent failures.
    return unseen[:batch_size] + due[:5]


def begin_attempt(entries: dict[str, dict[str, Any]], symbol: str, now: datetime) -> None:
    count = entries.get(symbol, {}).get("attempts", 0) + 1
    delay = min(21600, 1800 * 2 ** min(4, count - 1))
    entries[symbol] = {
        "state": "RUNNING", "attempts": count, "attempted_at": now.isoformat(),
        "next_attempt_at": (now + timedelta(seconds=delay)).isoformat(),
    }


def end_attempt(
    entries: dict[str, dict[str, Any]], symbol: str, error: Exception | None,
) -> None:
    entry = entries[symbol]
    entry["state"] = "FAILED" if error else "SUCCEEDED"
    if error:
        entry["error_type"] = type(error).__name__
        code = getattr(error, "vendor_code", None)
        entry["vendor_code"] = code if isinstance(code, str) and re.fullmatch(
            r"[0-9]{1,12}", code
        ) else None


def queue_progress(
    symbols: list[str], completed: set[str], entries: dict[str, dict[str, Any]], now: datetime,
) -> dict[str, Any]:
    missing = [s for s in symbols if s not in completed]
    retries = [datetime.fromisoformat(entries[s]["next_attempt_at"])
               for s in missing if s in entries]
    return {
        "mode": "BOUNDED_UNIVERSE_QUEUE_V2", "scope_symbols": len(symbols),
        "completed_symbols": len(symbols) - len(missing), "unresolved_symbols": len(missing),
        "unattempted_symbols": sum(s not in entries for s in missing),
        "due_retry_symbols": sum(at <= now for at in retries),
        "waiting_retry_symbols": sum(at > now for at in retries),
        "next_retry_at": min(retries).isoformat() if retries else None,
        "historical_pit_certified": False,
    }
