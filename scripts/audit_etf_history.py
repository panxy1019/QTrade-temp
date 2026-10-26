"""Read-only ETF capture coverage/overlap audit; no strategy performance or fills.

Example: python -m scripts.audit_etf_history --archive ARCHIVE --calendar CALENDAR
         --revision COMMIT [--legacy-manifest MANIFEST --legacy-bars PARQUET]
The signed JSON report goes to stdout. Source files are never written.
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path
from typing import Any

import pyarrow.parquet as pq  # type: ignore[import-untyped]

from qtrade.market_data.akshare import SHANGHAI
from qtrade.market_data.sina_incremental import PRICE_BASIS, completed_klines
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.data_versions import file_hash
from qtrade.research.etf_trend import LIMITATIONS, verify_etf_archive
from qtrade.research.qlib_publication import verify_signed
from scripts.unified_store import UnifiedBatchStore

FIELDS = ("open", "high", "low", "close", "volume")
MISSING_INPUTS = [
    "HISTORICAL_PIT_MASTER_MEMBERSHIP_NOT_REPLAYED",
    "LONGER_COMMON_HISTORY_AND_AMOUNT_COVERAGE_NOT_CERTIFIED",
    "LEGACY_NEW_SOURCE_EQUIVALENCE_NOT_CERTIFIED",
    "DISTRIBUTIONS_SPLITS_AND_ADJUSTMENT_BASIS_NOT_RECONCILED",
    "ETF_LOT_FEE_SETTLEMENT_LIMIT_SUSPENSION_AND_PREVIOUS_VOLUME_RULES_NOT_ADMITTED",
    "ACTUAL_NEXT_SESSION_FORWARD_ACCEPTANCE_REQUIRED",
]


def _days(calendar: Calendar, start: date, end: date) -> list[str]:
    if (not calendar.start <= start <= end <= calendar.end
            or (end-start).days > 3660):
        raise ValueError("ETF_HISTORY_CALENDAR_RANGE_INVALID")
    return [str(start+timedelta(days=index)) for index in range((end-start).days+1)
            if calendar.is_open(start+timedelta(days=index))]


def _code(error: ValueError) -> str:
    value = str(error)
    return value if re.fullmatch(r"[A-Z][A-Z0-9_:,]{0,160}", value) else "INVALID_SOURCE_ROW"


def _capture(capture: dict[str, Any], calendar: Calendar, as_of: date) -> dict[str, Any]:
    symbol = capture["request"]["symbol"][2:]
    received = datetime.fromisoformat(capture["received_at"])
    base = {"symbol": symbol, "request": capture["request"],
            "received_at": capture["received_at"], "body_sha256": capture["body_sha256"]}
    try:
        payload = json.loads(capture["body"])
        if not isinstance(payload, list) or not 1 <= len(payload) <= 1970:
            raise ValueError("INVALID_BOUNDED_KLINE_RESPONSE")
    except (ValueError, UnicodeError) as error:
        return {**base, "raw_row_count": None, "dates": [], "duplicate_dates": [],
                "row_issues": [{"index": None, "reason": "INVALID_BOUNDED_KLINE_RESPONSE"}],
                "rejected_days": [], "normalized": [], "source_error": type(error).__name__}
    dates, issues = [], []
    for index, row in enumerate(payload):
        try:
            if not isinstance(row, dict) or not isinstance(row.get("day"), str):
                raise ValueError("INVALID_KLINE_ROW")
            day = date.fromisoformat(row["day"][:10])
            dates.append(str(day))
            if str(day) != row["day"]:
                issues.append({"index": index, "reason": "INVALID_DAILY_SOURCE_DATE"})
        except (ValueError, TypeError):
            issues.append({"index": index, "reason": "INVALID_DAILY_SOURCE_DATE"})
    counts = Counter(dates)
    normalized, rejected = [], []
    response_ok = (capture["status_code"] == 200 and capture["transport_error"] is None
                   and capture["body_complete"] is True)
    cutoff = calendar.expected(received, "daily").date()
    for value in sorted(counts):
        day = date.fromisoformat(value)
        if day < calendar.start or day > calendar.end:
            reason = "CALENDAR_OUTSIDE_VERIFIED_HORIZON"
        elif day > as_of:
            reason = "AFTER_ARCHIVE_AS_OF"
        elif not calendar.is_open(day):
            reason = "NOT_EXCHANGE_SESSION"
        elif day > cutoff:
            reason = "REQUESTED_DAY_NOT_COMPLETE"
        elif not response_ok:
            reason = "ETF_SOURCE_RESPONSE_INCOMPLETE"
        else:
            try:
                parsed, = completed_klines(capture["body"], symbol=symbol, security_type="ETF",
                    frequency="day", requested_day=day, observed_at=received, calendar=calendar)
                normalized.append({"date": value,
                    **{key: str(getattr(parsed.bar, key)) for key in FIELDS},
                    "amount": str(parsed.amount) if parsed.amount is not None else None,
                    "vwap": str(parsed.vwap) if parsed.vwap is not None else None})
                continue
            except ValueError as error:
                reason = _code(error)
        rejected.append({"date": value, "reason": reason})
    return {**base, "raw_row_count": len(payload), "dates": sorted(counts),
            "duplicate_dates": [{"date": day, "count": count}
                                for day, count in sorted(counts.items()) if count > 1],
            "row_issues": issues, "rejected_days": rejected, "normalized": normalized,
            "source_error": None}


def _runs(common: list[str], expected: list[str]) -> list[list[str]]:
    selected = set(common)
    result: list[list[str]] = []
    current: list[str] = []
    for day in expected:
        if day in selected:
            current.append(day)
        elif current:
            result.append(current)
            current = []
    if current:
        result.append(current)
    return result


def _legacy(manifest_path: Path, bars_path: Path, reports: list[dict[str, Any]],
            calendar: Calendar) -> dict[str, Any]:
    if (manifest_path.is_symlink() or bars_path.is_symlink()
            or manifest_path.stat().st_size > 300_000
            or not 0 < bars_path.stat().st_size <= 256_000_000):
        raise ValueError("ETF_HISTORY_LEGACY_INPUT_BOUND_INVALID")
    value = json.loads(manifest_path.read_bytes())
    verify_signed(value)
    manifest = value["manifest"]
    if (manifest["schema_version"] != "qtrade.bar.v1"
            or manifest["quality_status"] != "PASSED"
            or file_hash(bars_path) != manifest["checksum"]):
        raise ValueError("ETF_HISTORY_LEGACY_MANIFEST_OR_HASH_INVALID")
    metadata = pq.ParquetFile(bars_path)
    fields = {"symbol", "exchange", "timestamp", "trading_day", *FIELDS, "data_version"}
    if set(metadata.schema_arrow.names) != fields or not 1 <= metadata.metadata.num_rows <= 500_000:
        raise ValueError("ETF_HISTORY_LEGACY_SCHEMA_OR_ROWS_INVALID")
    rows = pq.read_table(bars_path).to_pylist()
    indexed: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for row in rows:
        if row["data_version"] != manifest["data_version"]:
            raise ValueError("ETF_HISTORY_LEGACY_DATA_VERSION_MISMATCH")
        symbol, day = row["symbol"], str(row["trading_day"])
        if (not isinstance(symbol, str) or re.fullmatch(r"[15]\d{5}", symbol) is None
                or str(date.fromisoformat(day)) != day):
            raise ValueError("ETF_HISTORY_LEGACY_IDENTITY_INVALID")
        indexed.setdefault((symbol, day), []).append(row)
    equal = mismatched = invalid = 0
    details: list[dict[str, Any]] = []
    overlapping: set[str] = set()
    for report in reports:
        symbol = report["symbol"]
        for current in report["normalized"]:
            key = (symbol, current["date"])
            matches = indexed.get(key, [])
            if not matches:
                continue
            overlapping.add(current["date"])
            reason = None
            if len(matches) != 1:
                reason = "DUPLICATE_LEGACY_SYMBOL_DAY"
            else:
                old = matches[0]
                try:
                    stamp = datetime.fromisoformat(old["timestamp"])
                    pair_day = date.fromisoformat(current["date"])
                    if (old["exchange"] != ("SSE" if symbol.startswith("5") else "SZSE")
                            or stamp.tzinfo is None or stamp.astimezone(SHANGHAI).date() != pair_day
                            or stamp.astimezone(SHANGHAI).strftime("%H:%M:%S") != "15:00:00"):
                        raise ValueError("LEGACY_EXCHANGE_OR_TIMESTAMP_INVALID")
                    raw = json.dumps([{"day": str(pair_day),
                                       **{k: old[k] for k in FIELDS}}]).encode()
                    parsed, = completed_klines(raw, symbol=symbol, security_type="ETF",
                        frequency="day", requested_day=pair_day,
                        observed_at=datetime.fromisoformat(report["received_at"]),
                        calendar=calendar)
                    differences = [key for key in FIELDS
                                   if getattr(parsed.bar, key) != Decimal(current[key])]
                except (ValueError, TypeError) as error:
                    reason = _code(error) if isinstance(error, ValueError) else "INVALID_SOURCE_ROW"
            if reason is not None:
                invalid += 1
                detail = {"symbol": symbol, "date": current["date"], "reason": reason}
            elif differences:
                mismatched += 1
                detail = {"symbol": symbol, "date": current["date"],
                          "reason": "OHLCV_MISMATCH", "fields": differences,
                          "legacy": {key: str(old[key]) for key in FIELDS},
                          "captured": {key: current[key] for key in FIELDS},
                          "close_ratio_legacy_to_capture": str(
                              Decimal(str(old["close"]))/Decimal(current["close"]))}
            else:
                equal += 1
                continue
            if len(details) < 50:
                details.append(detail)
    pairs = equal+mismatched+invalid
    return {"manifest_sha256": file_hash(manifest_path),
            "manifest_evidence_sha256": value["evidence_sha256"],
            "bars_sha256": manifest["checksum"], "data_version": manifest["data_version"],
            "declared_source": manifest["source"], "rows": len(rows),
            "symbols": len({key[0] for key in indexed}),
            "coverage_start": min(key[1] for key in indexed),
            "coverage_end": max(key[1] for key in indexed),
            "overlap_dates": sorted(overlapping), "overlap_pairs": pairs,
            "equal_ohlcv_pairs": equal, "mismatched_pairs": mismatched,
            "invalid_pairs": invalid, "details": details,
            "details_truncated": mismatched+invalid > len(details),
            "state": ("NO_OVERLAP" if not pairs else "OVERLAP_DIFFERENCES" if mismatched+invalid
                      else "EXACT_OHLCV_OVERLAP_ONLY"),
            "source_equivalence_certified": False, "adjustment_certified": False,
            "combined_history_admitted": False}


def audit_etf_history(archive: Path, calendar: Calendar, revision: str, *,
                      legacy_manifest: Path | None = None,
                      legacy_bars: Path | None = None) -> dict[str, Any]:
    """Replay immutable source bytes; normalize fixed-cohort coverage, never performance."""
    if re.fullmatch(r"[a-f0-9]{40}", revision) is None:
        raise ValueError("ETF_HISTORY_IMPLEMENTATION_REVISION_REQUIRED")
    if (legacy_manifest is None) != (legacy_bars is None):
        raise ValueError("ETF_HISTORY_LEGACY_INPUT_PAIR_REQUIRED")
    diagnostic = verify_etf_archive(archive, calendar)
    receipt_path = archive/"receipt.json"
    receipt = json.loads(receipt_path.read_bytes())
    captures = UnifiedBatchStore(archive/"capture-store").read_captures(receipt["raw"])
    cohort = sorted(set(diagnostic["close_histories"]) |
                    {row["symbol"] for row in diagnostic["excluded"]})
    if sorted(row["request"]["symbol"][2:] for row in captures) != cohort:
        raise ValueError("ETF_HISTORY_DIAGNOSTIC_COHORT_MISMATCH")
    as_of = date.fromisoformat(diagnostic["as_of"])
    reports = sorted((_capture(row, calendar, as_of) for row in captures),
                     key=lambda r: r["symbol"])
    candidate_dates = [date.fromisoformat(day) for row in reports for day in row["dates"]]
    start = max(calendar.start, min(candidate_dates, default=as_of))
    start = min(start, as_of)
    expected = _days(calendar, start, as_of)
    common = set(expected)
    for report in reports:
        available = {row["date"] for row in report["normalized"]}
        report["missing_sessions"] = sorted(set(expected)-available)
        report["amount_present_sessions"] = sum(row["amount"] is not None
                                                 for row in report["normalized"])
        common &= available
    runs = _runs(sorted(common), expected)
    grid = runs[-1] if runs and runs[-1][-1] == str(as_of) else []
    missing = list(MISSING_INPUTS)
    if any(row["reason"] == "CALENDAR_OUTSIDE_VERIFIED_HORIZON"
           for report in reports for row in report["rejected_days"]):
        missing.append("CALENDAR_EXTENSION_REQUIRES_INDEPENDENT_EVIDENCE")
    if len(grid) < 22:
        missing.append("INSUFFICIENT_COMMON_CONTIGUOUS_HISTORY_FOR_MATURED_PRICE_LABEL")
    overlap = (_legacy(legacy_manifest, legacy_bars, reports, calendar)
               if legacy_manifest is not None and legacy_bars is not None else None)
    return signed({**AUTHORITY, "schema_version": "qtrade.etf-history-audit.v1",
        "stage": "SOURCE_COVERAGE_AUDIT_ONLY", "security_type": "ETF",
        "strategy_backtest_ready": False, "execution_admitted": False,
        "price_basis": PRICE_BASIS, "return_adjustment_certified": False,
        "master_membership_independently_replayed": False,
        "bindings": {"diagnostic_id": archive.name,
            "diagnostic_sha256": diagnostic["evidence_sha256"],
            "receipt_sha256": file_hash(receipt_path), "raw": receipt["raw"],
            "master_sha256": diagnostic["bindings"]["master_sha256"],
            "calendar_sha256": signed(calendar.document)["evidence_sha256"],
            "source_revision": diagnostic["bindings"]["code_revision"],
            "implementation_revision": revision, "as_of": str(as_of)},
        "cohort_basis": "DIAGNOSTIC_DECLARED_COHORT_MASTER_HASH_BOUND",
        "cohort": cohort, "excluded_from_latest_diagnostic": diagnostic["excluded"],
        "calendar": {"valid_from": str(calendar.start), "valid_through": str(calendar.end)},
        "expected_sessions": expected, "captures": reports,
        "common_sessions": sorted(common), "common_contiguous_runs": runs,
        "comparison_grid": grid, "grid_state": "COMMON_CONTIGUOUS_GRID_AVAILABLE" if len(grid) >= 22
            else "INSUFFICIENT_COMMON_CONTIGUOUS_HISTORY",
        "label_capacity_upper_bounds": {
            "lookback_sessions": 20, "native_close_t_to_t_plus_1": max(0, len(grid)-21),
            "after_next_open_close_t_plus_1_to_t_plus_2": max(0, len(grid)-22),
            "observed_labels_evaluated": 0, "execution_return_labels": None},
        "legacy_overlap": overlap, "missing_inputs": missing,
        "orders": [], "fills": [], "holdings": [], "net_returns": None,
        "performance_metrics": None, "forward_return": None,
        "limitations": LIMITATIONS+["COVERAGE_AND_OVERLAP_AUDIT_NOT_STRATEGY_BACKTEST",
            "CLOSE_T_TO_NEXT_CLOSE_INCLUDES_MOVE_BEFORE_NEXT_OPEN_EXECUTION",
            "NO_SOURCE_EQUIVALENCE_OR_CORPORATE_ACTION_CERTIFICATION_FROM_OVERLAP"]})


def verify_etf_history_audit(path: Path, archive: Path, calendar: Calendar, *,
                             legacy_manifest: Path | None = None,
                             legacy_bars: Path | None = None) -> dict[str, Any]:
    """Read-only full raw replay; signed output alone is not source admission."""
    if path.is_symlink() or not 0 < path.stat().st_size <= 32_000_000:
        raise ValueError("ETF_HISTORY_AUDIT_BOUND_INVALID")
    value = json.loads(path.read_bytes())
    verify_signed(value)
    expected = audit_etf_history(archive, calendar, value["bindings"]["implementation_revision"],
                                legacy_manifest=legacy_manifest, legacy_bars=legacy_bars)
    if json.dumps(value, sort_keys=True, allow_nan=False) != json.dumps(
            expected, sort_keys=True, allow_nan=False):
        raise ValueError("ETF_HISTORY_AUDIT_RAW_REPLAY_MISMATCH")
    return expected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--archive", required=True, type=Path)
    parser.add_argument("--calendar", required=True, type=Path)
    parser.add_argument("--revision", required=True)
    parser.add_argument("--legacy-manifest", type=Path)
    parser.add_argument("--legacy-bars", type=Path)
    args = parser.parse_args(argv)
    try:
        result = audit_etf_history(args.archive, Calendar(args.calendar), args.revision,
                                  legacy_manifest=args.legacy_manifest,
                                  legacy_bars=args.legacy_bars)
    except (OSError, ValueError, KeyError, TypeError) as error:
        reason = _code(error) if isinstance(error, ValueError) else type(error).__name__
        parser.exit(2, f"ETF_HISTORY_AUDIT_FAILED:{reason}\n")
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
