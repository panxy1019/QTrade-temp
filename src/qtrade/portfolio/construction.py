from __future__ import annotations

from dataclasses import dataclass
from decimal import ROUND_DOWN, Decimal


@dataclass(frozen=True, slots=True)
class PortfolioConstraints:
    top_k: int
    max_weight: Decimal
    cash_reserve_fraction: Decimal

    def __post_init__(self) -> None:
        if self.top_k <= 0:
            raise ValueError("top_k must be positive")
        if not Decimal("0") < self.max_weight <= Decimal("1"):
            raise ValueError("max_weight must be in (0, 1]")
        if not Decimal("0") <= self.cash_reserve_fraction < Decimal("1"):
            raise ValueError("cash reserve must be in [0, 1)")


@dataclass(frozen=True, slots=True)
class TargetPosition:
    symbol: str
    quantity: int
    target_weight: Decimal


def construct_targets(
    signals: dict[str, Decimal],
    prices: dict[str, Decimal],
    lot_sizes: dict[str, int],
    equity: Decimal,
    constraints: PortfolioConstraints,
) -> tuple[TargetPosition, ...]:
    if equity <= 0:
        raise ValueError("equity must be positive")
    eligible = [(symbol, score) for symbol, score in signals.items() if score > 0]
    ranked = sorted(eligible, key=lambda item: (-item[1], item[0]))[: constraints.top_k]
    if not ranked:
        return ()
    raw_weight = (Decimal("1") - constraints.cash_reserve_fraction) / len(ranked)
    weight = min(raw_weight, constraints.max_weight)
    targets: list[TargetPosition] = []
    for symbol, _ in ranked:
        price = prices.get(symbol)
        lot = lot_sizes.get(symbol)
        if price is None or price <= 0 or lot is None or lot <= 0:
            continue
        lots = (equity * weight / (price * lot)).to_integral_value(rounding=ROUND_DOWN)
        targets.append(TargetPosition(symbol, int(lots) * lot, weight))
    return tuple(targets)
