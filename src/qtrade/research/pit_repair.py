"""Causal admission within an explicitly biased sample and historical-state checks."""

from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal
from statistics import median
from typing import Any


def causal_intervals(
    stocks: dict[str, list[dict[str, Any]]], calendar: list[str],
    *, minimum_history: int = 120, liquidity_window: int = 20, top_n: int = 1000,
) -> tuple[dict[str, list[tuple[str, str]]], dict[str, Any]]:
    """Only completed signal-day history; never filter by a later date's liquidity."""
    if minimum_history < liquidity_window or liquidity_window < 2 or top_n < 1:
        raise ValueError("INVALID_CAUSAL_UNIVERSE_PARAMETERS")
    lookup = {
        symbol: {row["date"]: row for row in rows}
        for symbol, rows in stocks.items() if not symbol.startswith("688")
    }
    histories: dict[str, list[float]] = {symbol: [] for symbol in lookup}
    intervals: dict[str, list[tuple[str, str]]] = {symbol: [] for symbol in lookup}
    previous: set[str] = set()
    starts: dict[str, str] = {}
    counts = []
    prior_day: str | None = None
    for day in calendar:
        candidates = []
        for symbol, rows in lookup.items():
            row = rows.get(day)
            if row is None:
                continue
            amount = float(row["amount"])
            if amount <= 0:
                continue
            histories[symbol].append(amount)
            history = histories[symbol]
            if len(history) >= minimum_history:
                candidates.append((median(history[-liquidity_window:]), symbol))
        current = {
            symbol for _, symbol in sorted(candidates, key=lambda r: (-r[0], r[1]))[:top_n]
        }
        for symbol in previous - current:
            if prior_day is None:
                raise ValueError("UNIVERSE_BOUNDARY_UNAVAILABLE")
            intervals[symbol].append((starts.pop(symbol), prior_day))
        for symbol in current - previous:
            starts[symbol] = day
        counts.append(len(current))
        previous, prior_day = current, day
    for symbol, start in starts.items():
        intervals[symbol].append((start, calendar[-1]))
    return {s: windows for s, windows in intervals.items() if windows}, {
        "status": "CAUSAL_ADMISSION_WITHIN_BIASED_SAMPLE",
        "historical_pit_certified": False,
        "minimum_observed_sessions": minimum_history,
        "liquidity_window": liquidity_window, "top_n": top_n,
        "minimum_active": min(counts) if counts else 0,
        "maximum_active": max(counts) if counts else 0,
        "source_sample_bias_removed": False,
        "missing": ["DELISTED_FULL_HISTORY", "AUTHORITATIVE_HISTORICAL_MASTER"],
    }


def known_state(
    evidence: dict[str, Any], *, symbol: str, day: str, decision_at: datetime
) -> dict[str, Any]:
    if decision_at.tzinfo is None:
        raise ValueError("TIMEZONE_REQUIRED")
    if evidence.get("symbol") != symbol or evidence.get("date") != day:
        raise ValueError("STATE_IDENTITY_MISMATCH")
    available = datetime.fromisoformat(evidence["available_at"])
    observed = datetime.fromisoformat(evidence["observed_at"])
    if available.tzinfo is None or observed.tzinfo is None or available > decision_at:
        raise ValueError("STATE_NOT_KNOWN_AT_DECISION")
    if not evidence.get("source") or not evidence.get("source_sha256"):
        raise ValueError("STATE_SOURCE_REQUIRED")
    date.fromisoformat(day)
    return evidence


def historical_blocks(
    *, raw_open: Decimal, state: dict[str, Any], side: str
) -> tuple[bool, str]:
    """Requires independently supplied raw limit prices; unknown is never certified."""
    if side not in {"BUY", "SELL"} or raw_open <= 0:
        raise ValueError("INVALID_TRADE_CHECK")
    if state.get("tradestatus") == "0":
        return True, "SOURCE_REPORTED_SUSPENDED"
    if state.get("tradestatus") != "1":
        return True, "UNKNOWN_TRADE_STATE"
    field = "limit_up" if side == "BUY" else "limit_down"
    if state.get(field) is None:
        return True, "EXACT_LIMIT_PRICE_UNAVAILABLE"
    limit = Decimal(str(state[field]))
    if not limit.is_finite() or limit <= 0:
        raise ValueError("INVALID_RAW_LIMIT_PRICE")
    blocked = raw_open >= limit if side == "BUY" else raw_open <= limit
    return blocked, "AT_DIRECTIONAL_LIMIT" if blocked else "WITHIN_VERIFIED_LIMIT"
