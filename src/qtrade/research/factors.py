from __future__ import annotations

from decimal import Decimal
from statistics import pstdev


def _validate_series(values: list[Decimal], minimum: int) -> None:
    if len(values) < minimum or any(not value.is_finite() for value in values):
        raise ValueError(f"factor series requires at least {minimum} finite values")


def momentum(closes: list[Decimal], lookback: int) -> Decimal:
    if lookback <= 0:
        raise ValueError("lookback must be positive")
    _validate_series(closes, lookback + 1)
    base = closes[-lookback - 1]
    if base <= 0 or closes[-1] <= 0:
        raise ValueError("prices must be positive")
    return closes[-1] / base - Decimal("1")


def reversal(closes: list[Decimal], lookback: int) -> Decimal:
    return -momentum(closes, lookback)


def realized_volatility(closes: list[Decimal], lookback: int) -> Decimal:
    if lookback < 2:
        raise ValueError("volatility lookback must be at least two")
    _validate_series(closes, lookback + 1)
    selected = closes[-lookback - 1 :]
    if any(value <= 0 for value in selected):
        raise ValueError("prices must be positive")
    returns = [
        float(selected[index] / selected[index - 1] - Decimal("1"))
        for index in range(1, len(selected))
    ]
    return Decimal(str(pstdev(returns)))


def liquidity_turnover(
    closes: list[Decimal], volumes: list[Decimal], *, free_float_units: Decimal
) -> Decimal:
    if len(closes) != len(volumes) or not closes:
        raise ValueError("price and volume series must align and be nonempty")
    if free_float_units <= 0 or not free_float_units.is_finite():
        raise ValueError("free_float_units must be finite and positive")
    _validate_series(closes + volumes, 2)
    if any(price <= 0 for price in closes) or any(volume < 0 for volume in volumes):
        raise ValueError("prices must be positive and volume nonnegative")
    return sum(volumes, start=Decimal("0")) / len(volumes) / free_float_units
