from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from qtrade.domain import Side


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

    def snapshot(self) -> tuple[tuple[str, AcquisitionLot], ...]:
        return tuple(
            (symbol, lot)
            for symbol in sorted(self._lots)
            for lot in self._lots[symbol]
        )

    def restore(self, symbol: str, lot: AcquisitionLot) -> None:
        if not symbol or lot.quantity <= 0 or lot.sellable_on < lot.acquired_on:
            raise ValueError("invalid acquisition lot")
        self._lots.setdefault(symbol, []).append(lot)

    def project_fill(
        self,
        symbol: str,
        side: Side,
        quantity: int,
        trading_day: date,
        sellable_on: date | None,
    ) -> tuple[tuple[str, AcquisitionLot], ...]:
        projected = SellabilityLedger()
        for existing_symbol, lot in self.snapshot():
            projected.restore(existing_symbol, lot)
        if side is Side.BUY:
            if sellable_on is None:
                raise ValueError("buy fill requires a sellability date")
            projected.acquire(symbol, quantity, trading_day, sellable_on)
        else:
            projected.dispose(symbol, quantity, trading_day)
        return projected.snapshot()

    def replace(self, lots: tuple[tuple[str, AcquisitionLot], ...]) -> None:
        replacement = SellabilityLedger()
        for symbol, lot in lots:
            replacement.restore(symbol, lot)
        self._lots = replacement._lots
