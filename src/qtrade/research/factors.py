from __future__ import annotations

from dataclasses import dataclass
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


def skipped_momentum(closes: list[Decimal], lookback: int, *, skip: int = 0) -> Decimal:
    if lookback <= 0 or skip < 0:
        raise ValueError("lookback must be positive and skip nonnegative")
    _validate_series(closes, lookback + skip + 1)
    start = closes[-lookback - skip - 1]
    end = closes[-skip - 1]
    if start <= 0 or end <= 0:
        raise ValueError("prices must be positive")
    return end / start - Decimal("1")


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


@dataclass(frozen=True, slots=True)
class PriceFactorSpec:
    name: str
    lookback: int
    skip: int = 0

    def __post_init__(self) -> None:
        if self.name not in {"momentum", "reversal", "low_volatility"}:
            raise ValueError("unsupported price factor")
        if self.lookback <= 0 or self.skip < 0:
            raise ValueError("factor lookback must be positive and skip nonnegative")
        if self.name != "momentum" and self.skip:
            raise ValueError("only momentum supports a skip window")
        if self.name == "low_volatility" and self.lookback < 2:
            raise ValueError("low-volatility lookback must be at least two")

    @property
    def factor_id(self) -> str:
        suffix = f"-skip-{self.skip}" if self.skip else ""
        return f"{self.name}-{self.lookback}{suffix}"

    @property
    def required_closes(self) -> int:
        return self.lookback + self.skip + 1

    def value(self, closes: tuple[Decimal, ...]) -> Decimal:
        values = list(closes)
        if self.name == "momentum":
            return skipped_momentum(values, self.lookback, skip=self.skip)
        if self.name == "reversal":
            return reversal(values, self.lookback)
        return -realized_volatility(values, self.lookback)
