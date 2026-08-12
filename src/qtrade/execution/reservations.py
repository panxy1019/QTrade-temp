from __future__ import annotations

from dataclasses import dataclass
from decimal import Decimal

from qtrade.domain import OrderRequest, Side


@dataclass(frozen=True, slots=True)
class Reservation:
    order_id: str
    symbol: str
    side: Side
    remaining_quantity: int
    remaining_cash: Decimal


class ReservationBook:
    """In-process reservation reference; durable atomic storage is planned for M5."""

    def __init__(self) -> None:
        self._reservations: dict[str, Reservation] = {}

    @property
    def reserved_cash(self) -> Decimal:
        return sum(
            (item.remaining_cash for item in self._reservations.values()),
            start=Decimal("0"),
        )

    def reserved_sell_quantity(self, symbol: str) -> int:
        return sum(
            item.remaining_quantity
            for item in self._reservations.values()
            if item.side is Side.SELL and item.symbol == symbol
        )

    def reserved_buy_quantity(self, symbol: str) -> int:
        return sum(
            item.remaining_quantity
            for item in self._reservations.values()
            if item.side is Side.BUY and item.symbol == symbol
        )

    @property
    def reserved_buy_notional(self) -> Decimal:
        return sum(
            (item.remaining_cash for item in self._reservations.values() if item.side is Side.BUY),
            start=Decimal("0"),
        )

    def reserve(self, order_id: str, request: OrderRequest, required_cash: Decimal) -> None:
        if order_id in self._reservations:
            raise ValueError("order already has a reservation")
        self._reservations[order_id] = Reservation(
            order_id=order_id,
            symbol=request.symbol,
            side=request.side,
            remaining_quantity=request.quantity,
            remaining_cash=required_cash if request.side is Side.BUY else Decimal("0"),
        )

    def consume(self, order_id: str, quantity: int) -> None:
        reservation = self._reservations[order_id]
        replacement = self._consume_one(reservation, quantity)
        if replacement is None:
            del self._reservations[order_id]
        else:
            self._reservations[order_id] = replacement

    def project_consume(self, order_id: str, quantity: int) -> tuple[Reservation, ...]:
        """Validate consumption and return a detached post-fill snapshot."""
        reservation = self._reservations[order_id]
        replacement = self._consume_one(reservation, quantity)
        return tuple(
            replacement if item.order_id == order_id and replacement is not None else item
            for item in self._reservations.values()
            if item.order_id != order_id or replacement is not None
        )

    @staticmethod
    def _consume_one(reservation: Reservation, quantity: int) -> Reservation | None:
        if quantity <= 0 or quantity > reservation.remaining_quantity:
            raise ValueError("invalid reservation consumption")
        fraction = Decimal(quantity) / reservation.remaining_quantity
        remaining_cash = reservation.remaining_cash * (Decimal("1") - fraction)
        remaining_quantity = reservation.remaining_quantity - quantity
        if remaining_quantity == 0:
            return None
        return Reservation(
            order_id=reservation.order_id,
            symbol=reservation.symbol,
            side=reservation.side,
            remaining_quantity=remaining_quantity,
            remaining_cash=remaining_cash,
        )

    def release(self, order_id: str) -> None:
        self._reservations.pop(order_id, None)

    def snapshot(self) -> tuple[Reservation, ...]:
        return tuple(self._reservations.values())

    def restore(self, reservation: Reservation) -> None:
        if reservation.order_id in self._reservations:
            raise ValueError("reservation already exists")
        self._reservations[reservation.order_id] = reservation

    def replace(self, reservations: tuple[Reservation, ...]) -> None:
        """Install an already validated/durable reservation snapshot."""
        replacement = {item.order_id: item for item in reservations}
        if len(replacement) != len(reservations):
            raise ValueError("reservation snapshot contains duplicate order IDs")
        self._reservations = replacement
