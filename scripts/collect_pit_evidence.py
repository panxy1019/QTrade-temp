"""Version vendor master/status backfills; never invent historical availability."""

from __future__ import annotations

import argparse
import json
import os
import re
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.workflow import digest
from scripts.atomic_json import write_json_atomic
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
    return payload, identity


def record_attempt(base: Path, maximum: int, state: str, **details: Any) -> None:
    write_json_atomic(base / "pit-repair/attempt.json", signed({
        **AUTHORITY, "schema_version": "qtrade.pit-source-attempt.v1",
        "updated_at": datetime.now(UTC).isoformat(), "requested_maximum": maximum,
        "state": state, "historical_pit_certified": False, **details,
    }))


def verified_status(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_text())
    if signed({k: v for k, v in value.items() if k != "evidence_sha256"}) != value or (
        value.get("symbol") != path.stem
    ):
        raise ValueError("CACHED_STATUS_HASH_OR_IDENTITY_MISMATCH")
    if value.get("certified_at_decision") is not False or value.get(
        "historical_available_at"
    ) is not None:
        raise ValueError("BACKFILL_MUST_NOT_INVENT_HISTORICAL_AVAILABILITY")
    return value


def collect(base: Path, maximum: int) -> dict[str, Any]:
    record_attempt(base, maximum, "RUNNING", stage="WAITING_VENDOR_SESSION")
    try:
        value = collect_pass(base, maximum)
    except Exception as error:
        record_attempt(base, maximum, "FAILED", error_type=type(error).__name__,
                       vendor_code=getattr(error, "vendor_code", None),
                       error_code=str(error) if re.fullmatch(r"[A-Z_]{1,80}", str(error)) else None)
        raise
    record_attempt(base, maximum, "PARTIAL" if value["failures"] else "COMPLETE",
                   status_symbols=value["status_symbols"], status_rows=value["status_rows"],
                   failures=value["failures"], coverage_sha256=value["evidence_sha256"],
                   master_fetch_outcome=value["master_fetch_outcome"])
    return value


def collect_pass(base: Path, maximum: int) -> dict[str, Any]:
    import baostock as bs
    root = base / "pit-repair"
    root.mkdir(exist_ok=True)
    dataset = json.loads((base / "qlib-data-20261023/dataset.json").read_text())
    stocks = dataset["stocks"][:maximum]
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
                    payload = {"source": "BaoStock query_stock_basic", "observed_at": observed,
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
            for stock in stocks:
                symbol = stock["symbol"]
                code = ("sh." if symbol.startswith("6") else "sz.") + symbol
                checkpoint = root / "status" / (symbol + ".json")
                if checkpoint.exists():
                    verified_status(checkpoint)
                    continue
                record_attempt(base, maximum, "RUNNING", stage="STATUS", symbol=symbol,
                               master_fetch_outcome=master_outcome)
                try:
                    values = rows(bs.query_history_k_data_plus(
                        code, FIELDS, start_date="2016-10-24", end_date=dataset["end"],
                        frequency="d", adjustflag="3",
                    ))
                    if not values or any(r["code"] != code for r in values):
                        raise ValueError("STATUS_IDENTITY_OR_COVERAGE_MISSING")
                    write_json_atomic(checkpoint, signed({
                        "source": "BaoStock raw daily status backfill", "symbol": symbol,
                        "observed_at": datetime.now(UTC).isoformat(),
                        "historical_available_at": None,
                        "certified_at_decision": False, "rows": values,
                    }))
                except Exception as error:
                    failures.append({"symbol": symbol, "code": type(error).__name__,
                                     "vendor_code": getattr(error, "vendor_code", None)})
                    bs.logout()
                    if bs.login().error_code != "0":
                        raise ValueError("VENDOR_RELOGIN_FAILED") from None
        finally:
            bs.logout()
    files = sorted((root / "status").glob("*.json"))
    records = [verified_status(p) for p in files]
    coverage = signed({
        **AUTHORITY, "schema_version": "qtrade.pit-repair.v1", "observed_at": observed,
        "state": "SOURCE_BACKFILL_REQUIRES_RECONCILIATION",
        "master_rows": len(master), "master_sha256": master_hash,
        "master_observed_at": payload["observed_at"],
        "master_fetch_outcome": master_outcome, "master_refresh_error": master_error,
        "master_inactive_rows": sum(r.get("status") == "0" for r in master),
        "master_is_historical_asof_snapshot": False,
        "status_symbols": len(files), "input_symbols": dataset["accepted_symbols"],
        "status_rows": sum(len(r["rows"]) for r in records),
        "source_checksums": {p.name: digest(r) for p, r in zip(files, records, strict=True)},
        "historical_pit_certified": False, "historical_available_at_known": False,
        "execution_enforcement": "NOT_USED_DAILY_PROXY_REMAINS",
        "failures": failures,
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
    args = parser.parse_args()
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise RuntimeError("live trading must remain disabled")
    if not 1 <= args.maximum <= 1000:
        raise ValueError("BOUNDED_SOURCE_PASS_REQUIRED")
    value = collect(args.base, args.maximum)
    print(json.dumps({k: value[k] for k in (
        "state", "master_rows", "status_symbols", "status_rows"
    )}))


if __name__ == "__main__":
    main()
