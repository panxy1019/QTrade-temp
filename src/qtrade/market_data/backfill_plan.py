"""Immutable historical work scope; source claims never grant coverage admission."""

from __future__ import annotations

import calendar
import re
from collections import Counter
from datetime import date, datetime, timedelta
from typing import Any

from qtrade.instruments.observed import SCHEMA, instrument_id
from qtrade.monitoring.data_freshness import AUTHORITY, signed

PLAN_SCHEMA = "qtrade.unified-backfill-plan.v1"


def month_window(start: date, end: date, *, before: date | None = None) -> tuple[date, date] | None:
    """Newest remaining calendar month, inclusive; no fabricated trading calendar."""
    if start > end:
        raise ValueError("BACKFILL_RANGE_REVERSED")
    right = min(end, before - timedelta(days=1)) if before else end
    if right < start:
        return None
    return max(start, right.replace(day=1)), right


def _years_before(end: date, years: int) -> date:
    year = end.year - years
    return end.replace(year=year, day=min(end.day, calendar.monthrange(year, end.month)[1]))


def build_backfill_plan(
    master: dict[str, Any], *, requested_end: date, observed_at: datetime,
    code_revision: str, years: int = 10,
) -> dict[str, Any]:
    if (observed_at.tzinfo is None or requested_end >= observed_at.date()
            or not 1 <= years <= 10 or re.fullmatch(r"[a-f0-9]{40}", code_revision) is None):
        raise ValueError("BACKFILL_REQUEST_CLOCK_OR_BOUND_INVALID")
    if (signed({k: v for k, v in master.items() if k != "evidence_sha256"}) != master
            or any(master.get(k) != v for k, v in AUTHORITY.items())
            or master.get("schema_version") != SCHEMA
            or re.fullmatch(r"[a-f0-9]{64}", master["universe_version"]) is None):
        raise ValueError("BACKFILL_MASTER_IDENTITY_INVALID")
    if datetime.fromisoformat(master["observed_at"]) > observed_at:
        raise ValueError("BACKFILL_MASTER_FROM_FUTURE")
    targets = [r for r in master["securities"] if r["in_target"]]
    if (not 1 <= len(targets) <= 6000 or len(targets) != master["target_actual"]
            or len({r["instrument_id"] for r in targets}) != len(targets)):
        raise ValueError("BACKFILL_TARGET_COUNTS_INVALID")
    start = _years_before(requested_end, years)
    tasks = []
    for row in sorted(targets, key=lambda r: r["instrument_id"]):
        if (row["instrument_id"] != instrument_id(row["symbol"], row["security_type"])
                or row["status"] != "ACTIVE_OBSERVED" or row["issues"]
                or row["available_at"] is not None):
            raise ValueError("BACKFILL_UNSAFE_OBSERVED_IDENTITY")
        for frequency in ("day", "5min"):
            old = row["frequencies"][frequency]
            legacy = {key: old.get(key) for key in (
                "state", "observed_start", "observed_end", "bar_count", "issues", "sources",
            )}
            # A price-only legacy file and a qfq minute file cannot satisfy the new
            # raw OHLCV+amount contract, even when their checksums are correct.
            source = "SINA_HISTORY_RAW_WITH_TURNOVER" if frequency == "day" else (
                "BAOSTOCK_RAW_5MIN_ADJUSTFLAG_3")
            scope = {
                "universe_version": master["universe_version"],
                "instrument_id": row["instrument_id"], "frequency": frequency,
                "requested_start": str(start), "requested_end": str(requested_end),
                "candidate_source": source,
            }
            tasks.append({
                **scope, "task_id": signed(scope)["evidence_sha256"],
                "state": "WAITING_FOR_SOURCE_PROBE", "priority": "HISTORICAL_LOW",
                "legacy_observation": legacy,
                "vendor_listing_date_claim": row["listing_date"],
                "listing_claim_used_as_authoritative_boundary": False,
                "historical_available_at": None, "admitted_bar_count": 0,
                "next_probe_window": [str(v) for v in month_window(start, requested_end) or ()],
                "oldest_probe_window": [str(start), str(min(
                    requested_end,
                    start.replace(day=calendar.monthrange(start.year, start.month)[1]),
                ))],
                "unavailable_history_reason": "SOURCE_RANGE_AND_PRICE_BASIS_NOT_YET_VERIFIED",
            })
    task_identity = signed({"tasks": tasks})["evidence_sha256"]
    return signed({
        **AUTHORITY, "schema_version": PLAN_SCHEMA, "observed_at": observed_at.isoformat(),
        "master_sha256": master["evidence_sha256"],
        "universe_version": master["universe_version"], "code_revision": code_revision,
        "requested_start": str(start), "requested_end": str(requested_end), "years": years,
        "task_identity": task_identity, "target_securities": len(targets),
        "frequency_counts": dict(Counter(t["frequency"] for t in tasks)),
        "state_counts": dict(Counter(t["state"] for t in tasks)),
        "admitted_bar_count": 0, "source_probe_complete": False,
        "execution_policy": {
            "enabled": False, "maximum_calendar_days_per_query": 31,
            "maximum_concurrent_baostock_sessions": 1,
            "session_lease": "/root/qtrade-local/baostock-session.lock",
            "disk_reserve_bytes": 100 * 1024**3,
            "yield_to": ["CURRENT_INCREMENTAL", "DAILY_PREDICTION", "PIT_REPAIR"],
            "raw_capture_before_parse": True, "reuse_requires_content_revalidation": True,
            "retry_requires_persisted_attempt_and_backoff": True,
        },
        "limitations": [
            "A work plan is not a download, admitted coverage or a Qlib version",
            "Current membership retains survivorship and liquidity selection bias",
            "Legacy price bases remain independent; no automatic stitching",
            "Unverified historical calendars, state and corporate actions remain unknown",
            "A missing month cannot establish suspension, pre-listing or unavailable source range",
        ], "tasks": tasks,
    })
