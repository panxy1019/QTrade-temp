"""Dependency-free domain models.

Exchange-specific rules are data on :class:`Instrument`; they are deliberately
not hard-coded here because rules must be versioned and sourced by date.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, date, datetime
from decimal import Decimal
from enum import StrEnum


def utc_now() -> datetime:
    return datetime.now(UTC)


class RuntimeMode(StrEnum):
    BACKTEST = "BACKTEST"
    PAPER = "PAPER"
    SHADOW = "SHADOW"


class Side(StrEnum):
    BUY = "BUY"
    SELL = "SELL"


class OrderStatus(StrEnum):
    CREATED = "CREATED"
    RISK_PENDING = "RISK_PENDING"
    RISK_REJECTED = "RISK_REJECTED"
    RISK_PASSED = "RISK_PASSED"
    SUBMITTED = "SUBMITTED"
    SUBMISSION_UNKNOWN = "SUBMISSION_UNKNOWN"
    ACCEPTED = "ACCEPTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, slots=True)
class Instrument:
    symbol: str
    exchange: str
    security_type: str
    lot_size: int
    price_tick: Decimal
    tradable: bool = True

    def __post_init__(self) -> None:
        if not self.symbol or not self.exchange:
            raise ValueError("instrument symbol and exchange are required")
        if self.lot_size <= 0 or self.price_tick <= 0:
            raise ValueError("lot_size and price_tick must be positive")


@dataclass(frozen=True, slots=True)
class OrderRequest:
    idempotency_key: str
    strategy_id: str
    portfolio_id: str
    symbol: str
    side: Side
    price: Decimal
    quantity: int
    created_at: datetime = field(default_factory=utc_now)
    trading_day: date | None = None
    sellable_on: date | None = None

    def __post_init__(self) -> None:
        if not self.idempotency_key:
            raise ValueError("idempotency_key is required")
        if not self.strategy_id or not self.portfolio_id or not self.symbol:
            raise ValueError("strategy_id, portfolio_id, and symbol are required")
        if self.created_at.tzinfo is None:
            raise ValueError("created_at must be timezone-aware")
        if self.sellable_on is not None and self.trading_day is None:
            raise ValueError("sellable_on requires trading_day")
        if self.side is Side.BUY and self.trading_day is not None and self.sellable_on is None:
            raise ValueError("dated buy orders require sellable_on")
        if self.side is Side.SELL and self.sellable_on is not None:
            raise ValueError("sell orders cannot define sellable_on")
        if (
            self.trading_day is not None
            and self.sellable_on is not None
            and self.sellable_on < self.trading_day
        ):
            raise ValueError("sellable_on cannot precede trading_day")


@dataclass(frozen=True, slots=True)
class CancelCommand:
    internal_order_id: str
    idempotency_key: str

    def __post_init__(self) -> None:
        if not self.internal_order_id or not self.idempotency_key:
            raise ValueError("internal_order_id and idempotency_key are required")


@dataclass(slots=True)
class Order:
    internal_order_id: str
    request: OrderRequest
    status: OrderStatus = OrderStatus.CREATED
    broker_order_id: str | None = None
    filled_quantity: int = 0
    rejection_reason: str | None = None
    created_at: datetime = field(default_factory=utc_now)
    updated_at: datetime = field(default_factory=utc_now)

    @property
    def remaining_quantity(self) -> int:
        return self.request.quantity - self.filled_quantity


@dataclass(frozen=True, slots=True)
class Trade:
    trade_id: str
    internal_order_id: str
    symbol: str
    side: Side
    price: Decimal
    quantity: int
    fee: Decimal
    timestamp: datetime = field(default_factory=utc_now)


@dataclass(slots=True)
class Position:
    symbol: str
    quantity: int = 0
    average_cost: Decimal = Decimal("0")
    realized_pnl: Decimal = Decimal("0")


@dataclass(slots=True)
class AccountSnapshot:
    cash: Decimal
    positions: dict[str, Position] = field(default_factory=dict)

    def position_quantity(self, symbol: str) -> int:
        position = self.positions.get(symbol)
        return 0 if position is None else position.quantity

    def gross_market_value(self, prices: dict[str, Decimal]) -> Decimal:
        values = (
            Decimal(position.quantity) * prices.get(symbol, position.average_cost)
            for symbol, position in self.positions.items()
        )
        return sum(values, start=Decimal("0"))
