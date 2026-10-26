"""Content admission for old expansion checkpoints; original bytes remain unchanged."""

from __future__ import annotations

import json
import re
from collections import Counter
from datetime import UTC, date, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from qtrade.data.models import Bar, DataManifest, QualityStatus
from qtrade.data.quality import BarQualityGate
from qtrade.data.storage import ParquetBarStore
from qtrade.market_data.akshare import SHANGHAI
from qtrade.market_data.sina_minute import SINA_FIVE_MINUTE_SESSION_TIMES
from qtrade.monitoring.data_freshness import AUTHORITY, signed

RULE_VERSION = "qtrade.legacy-minute-admission.v1"


def validate_minute_window(
    bars: list[Bar], *, symbol: str, requested_end: date,
    requested_start: date = date(2023, 10, 24), minimum_sessions: int = 650,
) -> dict[str, Any]:
    """The legacy three-year contract, not a universal listing/eligibility rule."""
    if re.fullmatch(r"[036]\d{5}", symbol) is None or minimum_sessions < 1:
        raise ValueError("MINUTE_ADMISSION_INVALID_REQUEST")
    issues = {q.code for q in BarQualityGate().validate(bars, expected_symbol=symbol)}
    counts = Counter(b.trading_day for b in bars)
    exchange = "SSE" if symbol.startswith("6") else "SZSE"
    if not bars:
        issues.add("EMPTY_DATA")
    if any(b.exchange != exchange for b in bars):
        issues.add("EXCHANGE_MISMATCH")
    if len(counts) < minimum_sessions:
        issues.add("INSUFFICIENT_SESSIONS")
    if counts and (min(counts) < requested_start or max(counts) > requested_end):
        issues.add("OUTSIDE_REQUESTED_INTERVAL")
    if not counts or max(counts) != requested_end:
        issues.add("STALE_END")
    # Preserve the existing eight-day allowance; missing days never certify suspension.
    if not counts or (min(counts) - requested_start).days > 8:
        issues.add("LATE_START")
    partial = sum(n != 48 for n in counts.values())
    if partial:
        issues.add("INCOMPLETE_SESSION")
    if any(b.trading_day.weekday() > 4 for b in bars):
        issues.add("WEEKEND_SESSION")
    if any(
        b.timestamp.tzinfo is None
        or b.timestamp.astimezone(SHANGHAI).time() not in SINA_FIVE_MINUTE_SESSION_TIMES
        for b in bars
    ):
        issues.add("INVALID_SESSION_GRID")
    return {
        "rule_version": RULE_VERSION, "symbol": symbol,
        "requested_start": str(requested_start), "requested_end": str(requested_end),
        "minimum_sessions": minimum_sessions, "bar_count": len(bars),
        "session_count": len(counts), "partial_sessions": partial,
        "observed_start": str(min(counts)) if counts else None,
        "observed_end": str(max(counts)) if counts else None,
        "state": "CONTENT_COVERAGE_VERIFIED" if not issues else "QUARANTINED",
        "issues": sorted(issues),
        "historical_calendar_certified": False,
        "amount_verified": False, "adjustment_pit_certified": False,
        "missing_sessions_policy": "No synthetic fill; missing reasons remain unknown",
    }


def audit_minute_checkpoint(
    root: Path, checkpoint: Path, *, symbol: str, requested_end: date,
) -> dict[str, Any]:
    """Read all rows, not caller-supplied counts; never modify a legacy checkpoint."""
    raw = checkpoint.read_bytes()
    value = json.loads(raw)
    manifest = value["manifest"]
    base = {
        **AUTHORITY, "schema_version": "qtrade.minute-admission.v1",
        "checkpoint_sha256": sha256(raw).hexdigest(),
        "data_version": manifest["data_version"], "data_sha256": manifest["checksum"],
    }
    bars = ParquetBarStore(root / "bars").read(DataManifest(
        manifest["data_version"], "Legacy BaoStock qfq history; availability uncertified",
        "qtrade.bar.v1", datetime.now(UTC), manifest["checksum"], QualityStatus.PASSED,
    ))
    result = validate_minute_window(bars, symbol=symbol, requested_end=requested_end)
    if value.get("symbol") != symbol:
        result["issues"] = sorted({*result["issues"], "CHECKPOINT_SYMBOL_MISMATCH"})
        result["state"] = "QUARANTINED"
    target = (root / "bars" / manifest["data_version"] / "bars.parquet")
    return signed({**base, **result, "bytes": target.stat().st_size})


def admitted_checkpoint(root: Path, checkpoint: Path, *, symbol: str, end: date) -> dict[str, Any]:
    receipt = audit_minute_checkpoint(root, checkpoint, symbol=symbol, requested_end=end)
    if receipt["state"] != "CONTENT_COVERAGE_VERIFIED":
        raise ValueError("MINUTE_CHECKPOINT_ADMISSION_FAILED:" + ",".join(receipt["issues"]))
    return {
        "symbol": symbol,
        **{key: receipt[key] for key in (
            "bar_count", "session_count", "observed_start", "observed_end",
            "missing_sessions_policy",
        )},
        "manifest": {"data_version": receipt["data_version"],
                     "checksum": receipt["data_sha256"], "bytes": receipt["bytes"]},
        "admission": receipt,
    }
