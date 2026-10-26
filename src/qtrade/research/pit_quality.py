"""Content checks for public backfills; validation never supplies historical knowledge."""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation
from typing import Any

PRICE_FIELDS = ("open", "high", "low", "close", "preclose", "volume", "amount")
SHANGHAI = timezone(timedelta(hours=8))


def status_quality(
    record: dict[str, Any], symbol: str, now: datetime, *,
    start: str | None = None, end: str | None = None,
) -> dict[str, Any]:
    """Validate identity, clocks, ordered dates and supplied numbers without filling blanks.

    Missing flags/numbers are counted as unknown. An absent session is not evidence
    of suspension. This deliberately does not claim calendar or listing coverage.
    """
    if now.tzinfo is None or re.fullmatch(r"[036][0-9]{5}", symbol) is None:
        raise ValueError("SOURCE_STATUS_REQUEST_INVALID")
    if record.get("symbol") != symbol or record.get("source") != (
        "BaoStock raw daily status backfill"
    ):
        raise ValueError("SOURCE_STATUS_IDENTITY_INVALID")
    if record.get("historical_available_at") is not None or record.get(
        "certified_at_decision"
    ) is not False:
        raise ValueError("BACKFILL_MUST_NOT_INVENT_HISTORICAL_AVAILABILITY")
    try:
        observed = datetime.fromisoformat(record["observed_at"])
    except (KeyError, TypeError, ValueError):
        raise ValueError("SOURCE_STATUS_CLOCK_INVALID") from None
    if observed.tzinfo is None or observed > now:
        raise ValueError("SOURCE_STATUS_CLOCK_INVALID")
    rows = record.get("rows")
    if not isinstance(rows, list) or not rows:
        raise ValueError("SOURCE_STATUS_ROWS_INVALID")
    counts = {key: 0 for key in (
        "unknown_trade_state_rows", "unknown_st_state_rows", "missing_price_rows",
        "source_suspended_rows", "source_st_rows",
    )}
    previous = ""
    code = ("sh." if symbol.startswith("6") else "sz.") + symbol
    last_possible = observed.astimezone(SHANGHAI).date().isoformat()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("SOURCE_STATUS_ROW_INVALID")
        day = row.get("date")
        if not isinstance(day, str):
            raise ValueError("SOURCE_STATUS_ROW_DATE_INVALID")
        try:
            canonical = date.fromisoformat(day).isoformat()
        except (TypeError, ValueError):
            raise ValueError("SOURCE_STATUS_ROW_DATE_INVALID") from None
        if canonical != day or day <= previous or day > last_possible or (
            start is not None and day < start
        ) or (end is not None and day > end) or row.get("code") != code:
            raise ValueError("SOURCE_STATUS_ROW_IDENTITY_OR_RANGE_INVALID")
        previous = day
        for field, counter in (("tradestatus", "unknown_trade_state_rows"),
                               ("isST", "unknown_st_state_rows")):
            value = row.get(field)
            if value not in ("0", "1", "", None):
                raise ValueError("SOURCE_STATUS_ROW_STATE_INVALID")
            counts[counter] += value in ("", None)
        counts["source_suspended_rows"] += row.get("tradestatus") == "0"
        counts["source_st_rows"] += row.get("isST") == "1"
        numbers = {}
        for field in PRICE_FIELDS:
            value = row.get(field)
            if value in (None, ""):
                continue
            try:
                parsed = Decimal(str(value))
            except InvalidOperation:
                raise ValueError("SOURCE_STATUS_NUMBER_INVALID") from None
            if not parsed.is_finite() or parsed < 0:
                raise ValueError("SOURCE_STATUS_NUMBER_INVALID")
            numbers[field] = parsed
        counts["missing_price_rows"] += len(numbers) != len(PRICE_FIELDS)
        if all(key in numbers for key in ("open", "high", "low", "close")) and not (
            numbers["low"] <= min(numbers["open"], numbers["close"])
            <= max(numbers["open"], numbers["close"]) <= numbers["high"]
        ):
            raise ValueError("SOURCE_STATUS_OHLC_RELATION_INVALID")
        if row.get("tradestatus") == "1" and any(
            numbers.get(key) == 0 for key in ("open", "high", "low", "close")
        ):
            raise ValueError("SOURCE_STATUS_TRADING_PRICE_INVALID")
    return {
        "schema_version": "qtrade.pit-status-quality.v1", "rows": len(rows),
        "first_date": rows[0]["date"], "last_date": rows[-1]["date"], **counts,
        "historical_available_at": None, "historical_pit_certified": False,
        "calendar_coverage_certified": False, "exact_price_limits_certified": False,
    }
