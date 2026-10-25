"""Exchange-calendar-aware research freshness; no trading authority."""

from __future__ import annotations

import json
from datetime import date, datetime, timedelta
from datetime import time as wall_time
from hashlib import sha256
from pathlib import Path
from typing import Any

from qtrade.market_data.akshare import SHANGHAI

AUTHORITY = {
    "classification": "PUBLIC_SOURCE_RESEARCH_OBSERVATION_ONLY",
    "point_in_time": False,
    "realtime_readiness": False,
    "real_data_paper_readiness": False,
    "external_order_transmission": False,
    "live_trading_enabled": False,
    "promotion": "NOT_ELIGIBLE",
}


def signed(value: dict[str, Any]) -> dict[str, Any]:
    payload = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return {**value, "evidence_sha256": sha256(payload.encode()).hexdigest()}


class Calendar:
    def __init__(self, source: Path | dict[str, Any]):
        self.document = (
            json.loads(source.read_text(encoding="utf-8")) if isinstance(source, Path) else source
        )
        self.start = date.fromisoformat(self.document["valid_from"])
        self.end = date.fromisoformat(self.document["valid_through"])
        self.closed = {date.fromisoformat(day) for day in self.document["weekday_closures"]}

    def is_open(self, day: date) -> bool:
        if not self.start <= day <= self.end:
            raise ValueError("CALENDAR_OUTSIDE_VERIFIED_HORIZON")
        return day.weekday() < 5 and day not in self.closed

    def previous(self, day: date) -> date:
        while not self.is_open(day):
            day -= timedelta(days=1)
        return day

    def expected(self, now: datetime, kind: str) -> datetime:
        if now.tzinfo is None:
            raise ValueError("timezone required")
        local = now.astimezone(SHANGHAI)
        self.is_open(local.date())  # expiry never becomes a green weekend
        if kind == "daily":
            day = (
                local.date()
                if local.time() >= wall_time(16, 30)
                else local.date() - timedelta(days=1)
            )
            return datetime.combine(self.previous(day), wall_time(15), SHANGHAI)
        cutoff = local - timedelta(seconds=60)
        day = cutoff.date()
        if not self.is_open(day) or cutoff.time() < wall_time(9, 35):
            return datetime.combine(self.previous(day - timedelta(days=1)), wall_time(15), SHANGHAI)
        if cutoff.time() < wall_time(13, 5):
            minute = min(cutoff.hour * 60 + cutoff.minute, 11 * 60 + 30)
        else:
            minute = min(cutoff.hour * 60 + cutoff.minute, 15 * 60)
        minute -= minute % 5
        return datetime.combine(day, wall_time(minute // 60, minute % 60), SHANGHAI)


def freshness(
    report: dict[str, Any] | None, expected: datetime, now: datetime, kind: str
) -> dict[str, Any]:
    result: dict[str, Any] = {"expected_through": expected.isoformat(), "status": "MISSING"}
    if report is None:
        return result
    completed = datetime.fromisoformat(report["completed_at"])
    if completed.tzinfo is None or completed > now + timedelta(seconds=60):
        raise ValueError("FUTURE_OR_NAIVE_COLLECTION_CLOCK")
    latest = {
        symbol: datetime.fromisoformat(value)
        for symbol, value in report["latest_by_symbol"].items()
    }
    if not latest or any(value.tzinfo is None or value > now for value in latest.values()):
        raise ValueError("FUTURE_OR_NAIVE_MARKET_CLOCK")
    tolerance = timedelta(0) if kind == "daily" else timedelta(minutes=20)
    stale = sorted(symbol for symbol, value in latest.items() if expected - value > tolerance)
    complete = len(latest) == report["required_symbol_count"]
    result.update(
        {
            "status": "FRESH" if complete and not stale else "STALE_OR_INCOMPLETE",
            "latest_minimum": min(latest.values()).isoformat(),
            "latest_maximum": max(latest.values()).isoformat(),
            "observed_symbols": len(latest),
            "required_symbols": report["required_symbol_count"],
            "stale_symbols": stale,
            "collected_at": completed.isoformat(),
            "data_version": report["manifest"]["data_version"],
            "checksum": report["manifest"]["checksum"],
            "maximum_tolerated_lag_seconds": tolerance.total_seconds(),
        }
    )
    return result
