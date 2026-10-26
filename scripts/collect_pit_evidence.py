"""Version vendor master/status backfills; never invent historical availability."""

from __future__ import annotations

import argparse
import base64
import hashlib
import json
import os
import re
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.pit_quality import status_quality
from qtrade.research.workflow import digest
from scripts.atomic_json import write_json_atomic, write_json_immutable
from scripts.pit_collection_queue import (
    begin_attempt,
    end_attempt,
    queue_progress,
    read_attempts,
    save_attempts,
    select_batch,
)
from scripts.vendor_session import baostock_session

FIELDS = "date,code,open,high,low,close,preclose,volume,amount,tradestatus,isST"


class VendorQueryError(ValueError):
    def __init__(self, code: Any) -> None:
        self.vendor_code = str(code) if re.fullmatch(r"[0-9]{1,12}", str(code)) else "UNKNOWN"
        super().__init__("VENDOR_QUERY_FAILED")


def rows(result: Any) -> list[dict[str, str]]:
    if result.error_code != "0":
        raise VendorQueryError(result.error_code)
    fields = result.fields
    if not isinstance(fields, list) or not fields or len(set(fields)) != len(fields):
        raise ValueError("VENDOR_ROW_FIELDS_INVALID")
    values = []
    while result.next():
        row = result.get_row_data()
        if len(row) != len(fields):
            raise ValueError("VENDOR_ROW_FIELDS_MISMATCH")
        values.append(dict(zip(fields, row, strict=True)))
    if result.error_code != "0":
        raise VendorQueryError(result.error_code)
    return values


def verified_master(root: Path) -> tuple[dict[str, Any], str] | None:
    pointer = root / "coverage.json"
    if not pointer.exists():
        return None
    value = json.loads(pointer.read_text())
    if signed({k: v for k, v in value.items() if k != "evidence_sha256"}) != value:
        raise ValueError("CACHED_COVERAGE_HASH_MISMATCH")
    identity = value.get("master_sha256", "")
    if not isinstance(identity, str) or re.fullmatch(r"[a-f0-9]{64}", identity) is None:
        raise ValueError("CACHED_MASTER_IDENTITY_INVALID")
    payload = json.loads((root / "master" / (identity + ".json")).read_text())
    if digest(payload) != identity or payload.get("source") != "BaoStock query_stock_basic":
        raise ValueError("CACHED_MASTER_HASH_MISMATCH")
    observed = datetime.fromisoformat(payload["observed_at"])
    if observed.tzinfo is None or observed > datetime.now(UTC):
        raise ValueError("CACHED_MASTER_CLOCK_INVALID")
    if not isinstance(payload.get("rows"), list) or not payload["rows"]:
        raise ValueError("CACHED_MASTER_ROWS_INVALID")
    if payload.get("historical_available_at") is not None:
        raise ValueError("CACHED_MASTER_MUST_NOT_INVENT_HISTORICAL_AVAILABILITY")
    return payload, identity


def record_attempt(base: Path, maximum: int, state: str, **details: Any) -> None:
    write_json_atomic(base / "pit-repair/attempt.json", signed({
        **AUTHORITY, "schema_version": "qtrade.pit-source-attempt.v1",
        "updated_at": datetime.now(UTC).isoformat(), "requested_maximum": maximum,
        "state": state, "historical_pit_certified": False, **details,
    }))


def verified_status(path: Path, *, end: str | None = None) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_text())
    if not isinstance(value, dict) or signed({
        k: v for k, v in value.items() if k != "evidence_sha256"
    }) != value or (
        value.get("symbol") != path.stem
    ):
        raise ValueError("CACHED_STATUS_HASH_OR_IDENTITY_MISMATCH")
    if value.get("certified_at_decision") is not False or value.get(
        "historical_available_at"
    ) is not None:
        raise ValueError("BACKFILL_MUST_NOT_INVENT_HISTORICAL_AVAILABILITY")
    status_quality(value, path.stem, datetime.now(UTC), start="2016-10-24", end=end)
    return value


def status_inventory(
    root: Path, *, end: str, expected: dict[str, str] | None = None,
) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    """Keep invalid source bytes immutable and isolate one symbol from the good queue."""
    accepted, quarantined = {}, {}
    for path in sorted((root / "status").glob("*.json")):
        if re.fullmatch(r"[036][0-9]{5}", path.stem) is None:
            raise ValueError("STATUS_CHECKPOINT_PATH_INVALID")
        try:
            record = verified_status(path, end=end)
            if expected is not None and path.name in expected and digest(record) != expected[
                path.name
            ]:
                raise ValueError("CACHED_STATUS_ACCEPTED_IDENTITY_MISMATCH")
            accepted[path.stem] = record
        except (ValueError, KeyError, TypeError) as error:
            raw = path.read_bytes()
            identity = hashlib.sha256(raw).hexdigest()
            archive = root / "quarantine" / path.stem / (identity + ".json")
            receipt = signed({
                **AUTHORITY, "schema_version": "qtrade.pit-status-quarantine.v1",
                "symbol": path.stem, "raw_bytes_sha256": identity,
                "raw_bytes_base64": base64.b64encode(raw).decode("ascii"),
                "historical_pit_certified": False,
                "reason": "STATUS_CHECKPOINT_CONTENT_INVALID",
                "error_type": type(error).__name__,
                "error_code": str(error) if re.fullmatch(r"[A-Z_]{1,80}", str(error)) else None,
            })
            if archive.exists():
                if json.loads(archive.read_bytes()) != receipt:
                    raise ValueError("STATUS_QUARANTINE_IDENTITY_MISMATCH") from None
            else:
                write_json_immutable(archive, receipt)
            quarantined[path.stem] = {"symbol": path.stem, "raw_bytes_sha256": identity,
                                      "quarantine_receipt_sha256": receipt["evidence_sha256"]}
    return accepted, quarantined


def check_global_backoff(base: Path, now: datetime) -> None:
    marker = base / "pit-repair/next-pass.json"
    if marker.exists():
        value = json.loads(marker.read_bytes())
        if value.get("next_attempt_at"):
            deadline = datetime.fromisoformat(value["next_attempt_at"])
            if deadline.tzinfo is None:
                raise ValueError("SOURCE_GLOBAL_RETRY_CLOCK_INVALID")
            if deadline > now:
                raise ValueError("SOURCE_GLOBAL_BACKOFF_NOT_DUE")


def collect(base: Path, maximum: int, *, batch_size: int | None = None) -> dict[str, Any]:
    check_global_backoff(base, datetime.now(UTC))
    record_attempt(base, maximum, "RUNNING", stage="WAITING_VENDOR_SESSION")
    try:
        value = collect_pass(base, maximum, batch_size=batch_size)
    except Exception as error:
        record_attempt(base, maximum, "FAILED", error_type=type(error).__name__,
                       vendor_code=getattr(error, "vendor_code", None),
                       error_code=str(error) if re.fullmatch(r"[A-Z_]{1,80}", str(error)) else None)
        raise
    progress = value.get("collection_progress")
    partial = bool(value["failures"]) or (progress is not None and bool(
        progress["unresolved_symbols"]
    ))
    record_attempt(base, maximum, "PARTIAL" if partial else "COMPLETE",
                   status_symbols=value["status_symbols"], status_rows=value["status_rows"],
                   failures=value["failures"], coverage_sha256=value["evidence_sha256"],
                   master_fetch_outcome=value["master_fetch_outcome"],
                   collection_progress=progress)
    return value


def collect_pass(
    base: Path, maximum: int, *, batch_size: int | None = None,
) -> dict[str, Any]:
    import baostock as bs
    root = base / "pit-repair"
    root.mkdir(exist_ok=True)
    dataset = json.loads((base / "qlib-data-20261023/dataset.json").read_text())
    scope = [s["symbol"] for s in dataset["stocks"][:maximum]]
    entries: dict[str, dict[str, Any]] = {}
    selected = scope
    pointer = root / "coverage.json"
    previous = json.loads(pointer.read_bytes()) if pointer.exists() else None
    if previous is not None and signed({
        k: v for k, v in previous.items() if k != "evidence_sha256"
    }) != previous:
        raise ValueError("CACHED_COVERAGE_HASH_MISMATCH")
    expected = previous.get("source_checksums", {}) if previous is not None else {}
    if not isinstance(expected, dict):
        raise ValueError("CACHED_STATUS_CHECKSUMS_INVALID")
    accepted, quarantined = status_inventory(root, end=dataset["end"], expected=expected)
    if (set(accepted) | set(quarantined)) - {s["symbol"] for s in dataset["stocks"]}:
        raise ValueError("STATUS_CHECKPOINT_OUTSIDE_DATASET")
    if batch_size is not None:
        entries = read_attempts(root, dataset, datetime.now(UTC))
        for symbol in quarantined:
            if symbol not in entries or entries[symbol]["state"] == "SUCCEEDED":
                begin_attempt(entries, symbol, datetime.now(UTC))
                end_attempt(entries, symbol, ValueError("STATUS_CHECKPOINT_CONTENT_INVALID"))
        save_attempts(root, dataset, entries)
        selected = select_batch(scope, set(accepted), entries, datetime.now(UTC), batch_size)
    failures = []
    socket.setdefaulttimeout(20)
    observed = datetime.now(UTC).isoformat()
    cached = verified_master(root)
    master_error = None
    master_outcome = "FETCHED"
    with baostock_session():
        if bs.login().error_code != "0":
            raise ValueError("VENDOR_LOGIN_FAILED")
        try:
            record_attempt(base, maximum, "RUNNING", stage="MASTER")
            age = ((datetime.now(UTC) - datetime.fromisoformat(cached[0]["observed_at"]))
                   .total_seconds() if cached else float("inf"))
            if cached and age < 86400:
                payload, master_hash = cached
                master_outcome = "REUSED_VERIFIED_SNAPSHOT"
            else:
                try:
                    master = rows(bs.query_stock_basic())
                    if not master:
                        raise ValueError("EMPTY_MASTER_RESPONSE")
                    payload = {"source": "BaoStock query_stock_basic",
                               "observed_at": datetime.now(UTC).isoformat(),
                               "historical_available_at": None, "rows": master}
                    master_hash = digest(payload)
                    write_json_atomic(root / "master" / (master_hash + ".json"), payload)
                except Exception as error:
                    if cached is None:
                        raise
                    payload, master_hash = cached
                    master_outcome = "REFRESH_FAILED_USING_VERIFIED_CACHE"
                    master_error = {"type": type(error).__name__,
                                    "vendor_code": getattr(error, "vendor_code", None)}
                    # A failed request can poison BaoStock's process-global socket.
                    bs.logout()
                    if bs.login().error_code != "0":
                        raise ValueError("VENDOR_RELOGIN_FAILED") from None
            master = payload["rows"]
            for symbol in selected:
                code = ("sh." if symbol.startswith("6") else "sz.") + symbol
                checkpoint = root / "status" / (symbol + ".json")
                if symbol in accepted:
                    continue
                record_attempt(base, maximum, "RUNNING", stage="STATUS", symbol=symbol,
                               master_fetch_outcome=master_outcome)
                if batch_size is not None:
                    begin_attempt(entries, symbol, datetime.now(UTC))
                    save_attempts(root, dataset, entries)
                try:
                    values = rows(bs.query_history_k_data_plus(
                        code, FIELDS, start_date="2016-10-24", end_date=dataset["end"],
                        frequency="d", adjustflag="3",
                    ))
                    record = signed({
                        "source": "BaoStock raw daily status backfill", "symbol": symbol,
                        "observed_at": datetime.now(UTC).isoformat(),
                        "historical_available_at": None,
                        "certified_at_decision": False, "rows": values,
                    })
                    try:
                        status_quality(record, symbol, datetime.now(UTC),
                                       start="2016-10-24", end=dataset["end"])
                    except ValueError:
                        rejected = root / "rejected-responses" / symbol / (digest(record) + ".json")
                        if not rejected.exists():
                            write_json_immutable(rejected, record)
                        raise
                    write_json_atomic(checkpoint, record)
                    if batch_size is not None:
                        end_attempt(entries, symbol, None)
                        save_attempts(root, dataset, entries)
                except Exception as error:
                    if batch_size is not None:
                        end_attempt(entries, symbol, error)
                        save_attempts(root, dataset, entries)
                    failures.append({"symbol": symbol, "code": type(error).__name__,
                                     "vendor_code": getattr(error, "vendor_code", None)})
                    bs.logout()
                    if bs.login().error_code != "0":
                        raise ValueError("VENDOR_RELOGIN_FAILED") from None
        finally:
            bs.logout()
    accepted, quarantined = status_inventory(root, end=dataset["end"])
    records = list(accepted.values())
    progress = queue_progress(scope, set(accepted), entries, datetime.now(UTC)) if (
        batch_size is not None
    ) else None
    if batch_size is not None:
        completed = set(accepted)
        # Preserve outstanding failures even when this batch only collected new symbols.
        failures = [{"symbol": s, "code": e.get("error_type", "CollectorInterrupted"),
                     "vendor_code": e.get("vendor_code"), "attempts": e["attempts"],
                     "next_attempt_at": e["next_attempt_at"]}
                    for s, e in sorted(entries.items()) if s in scope and s not in completed]
    coverage = signed({
        **AUTHORITY, "schema_version": "qtrade.pit-repair.v1",
        "observed_at": datetime.now(UTC).isoformat(), "pass_started_at": observed,
        "state": "SOURCE_BACKFILL_REQUIRES_RECONCILIATION",
        "master_rows": len(master), "master_sha256": master_hash,
        "master_observed_at": payload["observed_at"],
        "master_fetch_outcome": master_outcome, "master_refresh_error": master_error,
        "master_inactive_rows": sum(r.get("status") == "0" for r in master),
        "master_is_historical_asof_snapshot": False,
        "status_symbols": len(accepted), "input_symbols": dataset["accepted_symbols"],
        "status_rows": sum(len(r["rows"]) for r in records),
        "source_checksums": {s + ".json": digest(r) for s, r in accepted.items()},
        "quality_policy": "CONTENT_VALIDATED_UNKNOWNS_RETAINED_V1",
        "quarantined_checkpoints": list(quarantined.values()),
        "historical_pit_certified": False, "historical_available_at_known": False,
        "execution_enforcement": "NOT_USED_DAILY_PROXY_REMAINS",
        "failures": failures,
        "collection_progress": progress,
        "missing": ["INDEPENDENT_MASTER_AND_DELISTED_PRICE_RECONCILIATION",
                    "HISTORICAL_INFORMATION_AVAILABILITY", "OFFICIAL_RAW_PRICE_LIMITS",
                    "AUCTION_REPLAY_AND_CORPORATE_ACTION_LEDGER"],
    })
    version_path = root / "coverage-versions" / (coverage["evidence_sha256"] + ".json")
    write_json_atomic(version_path, coverage)
    write_json_atomic(root / "coverage.json", coverage)
    return coverage


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--maximum", type=int, default=20)
    parser.add_argument("--batch-size", type=int, default=None,
                        help="new symbols per pass, plus at most five due retries")
    args = parser.parse_args()
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise RuntimeError("live trading must remain disabled")
    if not 1 <= args.maximum <= 1000:
        raise ValueError("BOUNDED_SOURCE_PASS_REQUIRED")
    value = collect(args.base, args.maximum, batch_size=args.batch_size)
    print(json.dumps({k: value[k] for k in (
        "state", "master_rows", "status_symbols", "status_rows"
    )}))


if __name__ == "__main__":
    main()
