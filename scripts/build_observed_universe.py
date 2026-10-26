"""Build a versioned observed master from preserved files, without vendor requests."""

from __future__ import annotations

import argparse
import json
from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from qtrade.data import DataManifest, ParquetBarStore, QualityStatus
from qtrade.instruments.observed import build_observed_master
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts.atomic_json import write_json_atomic, write_json_immutable
from scripts.run_data_observer import read_report

REPO = Path(__file__).resolve().parents[1]


def checked_json(path: Path) -> dict[str, Any]:
    value: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    if signed({k: v for k, v in value.items() if k != "evidence_sha256"}) != value:
        raise ValueError("OBSERVATION_SOURCE_CONTENT_HASH_MISMATCH")
    return value


def build(
    base: Path, *, vendor_master: Path, candidate_file: Path, target_count: int,
    observed_at: datetime, verify_daily: bool = True, legacy_universe: Path | None = None,
) -> dict[str, Any]:
    groups = []
    captured: dict[Path, bytes] = {}

    def capture(path: Path) -> Any:
        captured[path] = path.read_bytes()
        return json.loads(captured[path])

    def group(path: Path, source_id: str, role: str, rows: list[dict[str, Any]],
              known_at: str | None = None) -> None:
        groups.append({"source_id": source_id, "role": role, "rows": rows,
                       "sha256": sha256(captured[path]).hexdigest(), "known_at": known_at})

    etf_file = REPO / "configs/akshare_etf_universe_v1.json"
    etfs = capture(etf_file)
    group(etf_file, "legacy_etf30", "legacy",
          [{"symbol": s, "security_type": "ETF"} for s in etfs["symbols"]], etfs["known_at"])
    minute_file = REPO / "deployment/evidence/long-five-minute-market-data/manifest.json"
    minute_manifest = capture(minute_file)
    group(minute_file, "legacy_stock75", "legacy", [
        {"symbol": s, "security_type": "STOCK"} for s in minute_manifest["eligible_symbols"]
    ])
    daily_path = legacy_universe or base / "expansion/universe.json"
    daily = capture(daily_path)
    if signed({k: v for k, v in daily.items() if k != "evidence_sha256"}) != daily:
        raise ValueError("DAILY_UNIVERSE_HASH_MISMATCH")
    group(daily_path, "legacy_daily1000", "legacy", [
        {"symbol": s["symbol"], "name": s.get("name", ""), "security_type": "STOCK"}
        for s in daily["stocks"]
    ], daily.get("known_at"))
    candidate_doc = capture(candidate_file)
    group(candidate_file, "frozen_liquidity_candidates", "candidate", [
        {**row, "security_type": "STOCK"} for row in candidate_doc["rows"]
    ])
    audit_path = base / "unified-data/minute-admission/latest.json"
    minute_audit = checked_json(audit_path) if audit_path.exists() else None
    if minute_audit is not None:
        captured[audit_path] = audit_path.read_bytes()
        if json.loads(captured[audit_path]) != minute_audit:
            raise ValueError("MINUTE_AUDIT_CHANGED_DURING_CAPTURE")
        group(audit_path, "legacy_minute_checkpoints", "legacy", [
            {"symbol": s, "security_type": "STOCK"}
            for s in sorted({r["symbol"] for r in minute_audit["receipts"]})
        ])
    vendor = capture(vendor_master)
    master = build_observed_master(
        vendor_rows=vendor["rows"] if isinstance(vendor, dict) else vendor,
        vendor_sha256=sha256(captured[vendor_master]).hexdigest(), source_groups=groups,
        observed_at=observed_at, target_count=target_count,
    )
    coverage: dict[str, dict[str, dict[str, Any]]] = {}
    for stock in daily["stocks"]:
        symbol = stock["symbol"]
        state, issues = "DOWNLOADED_UNVERIFIED", []
        if verify_daily:
            try:
                m = stock["manifest"]
                bars = ParquetBarStore(base / "expansion/bars").read(DataManifest(
                    m["data_version"], "Legacy Sina raw daily", "qtrade.bar.v1",
                    observed_at, m["checksum"], QualityStatus.PASSED,
                ))
                if (any(b.symbol != symbol or b.exchange != (
                        "SSE" if symbol.startswith("6") else "SZSE") for b in bars)
                        or len(bars) != stock["bar_count"]
                        or str(bars[0].trading_day) != stock["observed_start"]
                        or str(bars[-1].trading_day) != stock["observed_end"]):
                    raise ValueError("DAILY_COVERAGE_METADATA_MISMATCH")
                state = "CONTENT_COVERAGE_VERIFIED"
            except Exception:
                state, issues = "QUARANTINED", ["DAILY_INTEGRITY_OR_CONTENT_FAILED"]
        coverage[symbol] = {"day": {
            "state": state, "issues": issues, "observed_start": stock["observed_start"],
            "observed_end": stock["observed_end"], "bar_count": stock["bar_count"],
            "data_version": stock["manifest"]["data_version"],
            "data_sha256": stock["manifest"]["checksum"],
            "amount_verified": False, "historical_pit_certified": False,
        }}
    if minute_audit is not None:
        for receipt in minute_audit["receipts"]:
            coverage.setdefault(receipt["symbol"], {})["5min"] = {
                key: receipt.get(key) for key in (
                    "state", "issues", "observed_start", "observed_end", "bar_count",
                    "data_version", "data_sha256", "evidence_sha256", "requested_end",
                )
            }
    # Current small streams remain distinct observations, never long-history proof.
    observer = json.loads((base / "runtime/collector-state.json").read_text())
    recent = {}
    for kind in ("daily", "minute"):
        report = read_report(Path(observer[kind]["report_path"]))
        recent[kind] = {key: report[key] for key in (
            "completed_at", "required_symbol_count", "bar_count", "latest_by_symbol",
        )}
    for row in master["securities"]:
        row["frequencies"] = {}
        for freq in ("day", "5min"):
            row["frequencies"][freq] = {
                "planned": row["in_target"],
                **coverage.get(row["symbol"], {}).get(freq, {
                    "state": "PLANNED" if row["in_target"] else "NOT_PLANNED",
                    "issues": [], "observed_start": None, "observed_end": None,
                    "bar_count": 0,
                }),
            }
    frequency_counts = {freq: dict(Counter(
        r["frequencies"][freq]["state"] for r in master["securities"] if r["in_target"]
    )) for freq in ("day", "5min")}
    master = signed({**{k: v for k, v in master.items() if k != "evidence_sha256"},
                     "frequency_counts": frequency_counts, "recent_streams": recent,
                     "vendor_master_observed_at": vendor.get("observed_at")
                     if isinstance(vendor, dict) else None})
    destination = base / "unified-data/catalog"
    version_file = destination / "versions" / (master["evidence_sha256"] + ".json")
    if not version_file.exists():
        write_json_immutable(version_file, master)
    elif json.loads(version_file.read_text()) != master:
        raise ValueError("OBSERVED_MASTER_VERSION_CONFLICT")
    write_json_atomic(destination / "latest.json", master)
    public = signed({**AUTHORITY, "schema_version": "qtrade.unified-catalog.v1",
                     "built_at": observed_at.isoformat(), "master": master})
    write_json_atomic(base / "unified-data/public/unified.json", public)
    return public


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--vendor-master", type=Path, required=True)
    parser.add_argument("--candidates", type=Path, required=True)
    parser.add_argument("--target", type=int, default=1500)
    parser.add_argument("--legacy-universe", type=Path)
    args = parser.parse_args()
    result = build(args.base, vendor_master=args.vendor_master, candidate_file=args.candidates,
                   target_count=args.target, observed_at=datetime.now(UTC),
                   legacy_universe=args.legacy_universe)
    print(json.dumps({k: result["master"][k] for k in (
        "master_count", "target_actual", "legacy_union_count", "top_up_count", "frequency_counts",
    )}))


if __name__ == "__main__":
    main()
