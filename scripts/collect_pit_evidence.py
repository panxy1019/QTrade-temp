"""Version vendor master/status backfills; never invent historical availability."""

from __future__ import annotations

import argparse
import json
import os
import socket
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.workflow import digest
from scripts.atomic_json import write_json_atomic
from scripts.vendor_session import baostock_session

FIELDS = "date,code,open,high,low,close,preclose,volume,amount,tradestatus,isST"


def rows(result: Any) -> list[dict[str, str]]:
    if result.error_code != "0":
        raise ValueError("VENDOR_QUERY_FAILED")
    fields = result.fields
    values = []
    while result.next():
        row = result.get_row_data()
        if len(row) != len(fields):
            raise ValueError("VENDOR_ROW_FIELDS_MISMATCH")
        values.append(dict(zip(fields, row, strict=True)))
    if result.error_code != "0":
        raise ValueError("VENDOR_ITERATION_FAILED")
    return values


def collect(base: Path, maximum: int) -> dict[str, Any]:
    import baostock as bs
    root = base / "pit-repair"
    root.mkdir(exist_ok=True)
    dataset = json.loads((base / "qlib-data-20261023/dataset.json").read_text())
    stocks = dataset["stocks"][:maximum]
    failures = []
    socket.setdefaulttimeout(20)
    observed = datetime.now(UTC).isoformat()
    with baostock_session():
        if bs.login().error_code != "0":
            raise ValueError("VENDOR_LOGIN_FAILED")
        try:
            master = rows(bs.query_stock_basic())
            payload = {"source": "BaoStock query_stock_basic", "observed_at": observed,
                       "historical_available_at": None, "rows": master}
            master_hash = digest(payload)
            write_json_atomic(root / "master" / (master_hash + ".json"), payload)
            for stock in stocks:
                symbol = stock["symbol"]
                code = ("sh." if symbol.startswith("6") else "sz.") + symbol
                checkpoint = root / "status" / (symbol + ".json")
                if checkpoint.exists():
                    continue
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
                    failures.append({"symbol": symbol, "code": type(error).__name__})
        finally:
            bs.logout()
    files = sorted((root / "status").glob("*.json"))
    records = [json.loads(p.read_text()) for p in files]
    coverage = signed({
        **AUTHORITY, "schema_version": "qtrade.pit-repair.v1", "observed_at": observed,
        "state": "SOURCE_BACKFILL_REQUIRES_RECONCILIATION",
        "master_rows": len(master), "master_sha256": master_hash,
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
