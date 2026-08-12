from __future__ import annotations

from collections import deque
from decimal import Decimal

from qtrade.data import Bar


class BuyAndHoldStrategy:
    def __init__(self, quantity: int) -> None:
        self._quantity = quantity

    def on_bar(self, bar: Bar, current_quantity: int) -> int:
        return self._quantity


class MovingAverageTrendStrategy:
    def __init__(self, window: int, quantity: int) -> None:
        if window <= 0 or quantity <= 0:
            raise ValueError("window and quantity must be positive")
        self._prices: deque[Decimal] = deque(maxlen=window)
        self._window = window
        self._quantity = quantity

    def on_bar(self, bar: Bar, current_quantity: int) -> int:
        self._prices.append(bar.close)
        if len(self._prices) < self._window:
            return current_quantity
        average = sum(self._prices, start=Decimal("0")) / self._window
        return self._quantity if bar.close >= average else 0
