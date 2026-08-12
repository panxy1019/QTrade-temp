from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from decimal import Decimal

from qtrade.data.models import Bar, PriceAdjustment


@dataclass(frozen=True, slots=True)
class AdjustedBarSet:
    bars: tuple[Bar, ...]
    adjustment_ids: tuple[str, ...]


def apply_backward_adjustments(
    bars: list[Bar],
    adjustments: list[PriceAdjustment],
    *,
    known_at: datetime,
) -> AdjustedBarSet:
    """Apply sourced normalized factors to pre-effective-date history.

    Q deliberately does not derive exchange/corporate-action factors here; a
    licensed upstream adapter must provide the factor and authoritative source.
    """
    if known_at.tzinfo is None:
        raise ValueError("known_at must be timezone-aware")
    if not bars:
        raise ValueError("bars cannot be empty")
    if any(bar.timestamp.tzinfo is None for bar in bars):
        raise ValueError("bar timestamps must be timezone-aware")
    if any(bars[index].timestamp <= bars[index - 1].timestamp for index in range(1, len(bars))):
        raise ValueError("bars must be unique and strictly chronological")
    symbols = {bar.symbol for bar in bars}
    exchanges = {bar.exchange for bar in bars}
    if len(symbols) != 1 or len(exchanges) != 1:
        raise ValueError("adjustment input must contain exactly one instrument")
    symbol = next(iter(symbols))
    applicable = sorted(
        (
            item
            for item in adjustments
            if item.symbol == symbol
            and item.known_at <= known_at
            and item.effective_date <= bars[-1].trading_day
        ),
        key=lambda item: (item.effective_date, item.adjustment_id),
    )
    ids = [item.adjustment_id for item in applicable]
    if len(ids) != len(set(ids)):
        raise ValueError("adjustment IDs must be unique")
    adjusted: list[Bar] = []
    for bar in bars:
        factor = Decimal("1")
        for item in applicable:
            if bar.trading_day < item.effective_date:
                factor *= item.price_factor
        adjusted.append(
            replace(
                bar,
                open=bar.open * factor,
                high=bar.high * factor,
                low=bar.low * factor,
                close=bar.close * factor,
                volume=bar.volume / factor,
            )
        )
    return AdjustedBarSet(tuple(adjusted), tuple(ids))
