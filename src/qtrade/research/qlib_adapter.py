"""Auditable daily Qlib price/volume conversion, independent of Qlib installation."""

from __future__ import annotations

from bisect import bisect_right
from decimal import Decimal
from typing import Any


def positive(value: Any) -> Decimal:
    number = Decimal(str(value))
    if not number.is_finite() or number <= 0:
        raise ValueError("NON_POSITIVE_OR_NON_FINITE_SOURCE_FIELD")
    return number


def normalize_daily(
    rows: list[dict[str, Any]], factors: list[dict[str, Any]], *, symbol: str
) -> list[dict[str, Any]]:
    """Use effective-date HFQ multipliers, never bfill a future action or a bar.

    Price * inverse-volume preserves cash turnover. Qlib's $factor converts
    normalized quantities/prices back to raw shares/CNY. Unknown historical ST
    status uses conservative 4.5% open-gap blocks; STAR buys are excluded because
    this baseline uses a 100-share lot, rather than pretending to model its lot.
    """
    actions = sorted((str(r["d"])[:10], positive(r["f"])) for r in factors)
    if not actions or len({r[0] for r in actions}) != len(actions):
        raise ValueError("MISSING_OR_DUPLICATE_ADJUSTMENT_FACTORS")
    days = [r[0] for r in actions]
    result: list[dict[str, Any]] = []
    scale: Decimal | None = None
    previous: Decimal | None = None
    for row in rows:
        day = str(row["date"])[:10]
        if result and day <= result[-1]["date"]:
            raise ValueError("DUPLICATE_OR_UNSORTED_SOURCE_DAYS")
        volume = Decimal(str(row["volume"]))
        if not volume.is_finite() or volume < 0:
            raise ValueError("INVALID_VOLUME")
        if volume == 0:
            continue  # A non-traded day remains absent, not a filled zero-return bar.
        prices = {k: positive(row[k]) for k in ("open", "high", "low", "close")}
        amount = positive(row["amount"])
        vwap = amount / volume
        if (
            prices["low"] > min(prices["open"], prices["close"])
            or prices["high"] < max(prices["open"], prices["close"])
            or not prices["low"] - Decimal("0.02") <= vwap <= prices["high"] + Decimal("0.02")
        ):
            raise ValueError("OHLC_OR_VWAP_UNIT_MISMATCH")
        index = bisect_right(days, day) - 1
        if index < 0:
            raise ValueError("NO_FACTOR_KNOWN_ON_BAR_DATE")
        multiplier = actions[index][1]
        if scale is None:
            scale = prices["close"] * multiplier
        factor = multiplier / scale
        adjusted = {key: float(value * factor) for key, value in prices.items()}
        gap = prices["open"] * factor / previous - 1 if previous else Decimal(0)
        result.append(
            {
                "date": day,
                "symbol": symbol,
                **adjusted,
                "volume": float(volume / factor),
                "vwap": float(vwap * factor),
                "factor": float(factor),
                "amount": float(amount),
                "raw_open": float(prices["open"]),
                "raw_close": float(prices["close"]),
                "raw_volume": float(volume),
                "raw_vwap": float(vwap),
                "hfq_multiplier": float(multiplier),
                "buy_block": float(
                    previous is None or gap >= Decimal("0.045") or symbol.startswith("688")
                ),
                "sell_block": float(previous is None or gap <= Decimal("-0.045")),
            }
        )
        previous = prices["close"] * factor
    if not result:
        raise ValueError("EMPTY_NORMALIZED_STOCK")
    return result


def verify_reused_bars(source: list[dict[str, Any]], existing: list[dict[str, Any]]) -> int:
    """Bind recovered amount/factors to stored OHLCV; reject unexplained changes."""
    lookup = {str(row["date"])[:10]: row for row in source}
    for row in existing:
        observed = lookup.get(str(row["trading_day"])[:10])
        if observed is None:
            raise ValueError("EXISTING_BAR_ABSENT_FROM_RAW_SOURCE")
        for key in ("open", "high", "low", "close"):
            if abs(Decimal(str(row[key])) - Decimal(str(observed[key]))) > Decimal("0.011"):
                raise ValueError("REUSED_OHLC_CHANGED")
        if Decimal(str(row["volume"])) != Decimal(str(observed["volume"])):
            raise ValueError("REUSED_VOLUME_CHANGED")
    return len(existing)


def causal_capacity(rows: list[dict[str, Any]], calendar: list[str]) -> dict[str, float]:
    """One percent of previous *exchange session* raw volume in today's units.

    A first quote or a resumed quote without previous-session volume gets zero
    capacity. This deliberately does not fill volume across a suspension.
    """
    previous_day = dict(zip(calendar[1:], calendar[:-1], strict=True))
    lookup = {row["date"]: row for row in rows}
    result = {}
    for row in rows:
        previous = lookup.get(previous_day.get(row["date"]))
        result[row["date"]] = (
            float(positive(previous["raw_volume"]) * Decimal("0.01") / positive(row["factor"]))
            if previous
            else 0.0
        )
    return result
