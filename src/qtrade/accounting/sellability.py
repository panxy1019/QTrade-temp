from __future__ import annotations

from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True, slots=True)
class AcquisitionLot:
    quantity: int
    acquired_on: date
    sellable_on: date


class SellabilityLedger:
    """Tracks acquisition lots separately from aggregate position quantity."""

    def __init__(self) -> None:
        self._lots: dict[str, list[AcquisitionLot]] = {}

    def acquire(self, symbol: str, quantity: int, trading_day: date, sellable_on: date) -> None:
        if quantity <= 0 or sellable_on < trading_day:
            raise ValueError("invalid acquisition lot")
        self._lots.setdefault(symbol, []).append(AcquisitionLot(quantity, trading_day, sellable_on))

    def sellable_quantity(self, symbol: str, trading_day: date) -> int:
        return sum(
            lot.quantity for lot in self._lots.get(symbol, []) if lot.sellable_on <= trading_day
        )

    def dispose(self, symbol: str, quantity: int, trading_day: date) -> None:
        if quantity <= 0 or quantity > self.sellable_quantity(symbol, trading_day):
            raise ValueError("insufficient sellable quantity")
        remaining = quantity
        updated: list[AcquisitionLot] = []
        for lot in self._lots.get(symbol, []):
            if remaining and lot.sellable_on <= trading_day:
                consumed = min(remaining, lot.quantity)
                remaining -= consumed
                if consumed < lot.quantity:
                    updated.append(
                        AcquisitionLot(lot.quantity - consumed, lot.acquired_on, lot.sellable_on)
                    )
            else:
                updated.append(lot)
        self._lots[symbol] = updated
