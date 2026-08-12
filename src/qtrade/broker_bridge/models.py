from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum
from typing import Protocol


class BrokerConnectionState(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    AUTHENTICATED = "AUTHENTICATED"
    RECOVERING = "RECOVERING"
    READ_ONLY_READY = "READ_ONLY_READY"
    DEGRADED = "DEGRADED"


@dataclass(frozen=True, slots=True)
class BrokerCapabilities:
    protocol_version: str
    broker: str
    gateway_version: str
    sdk_version: str
    query_accounts: bool
    query_positions: bool
    query_orders: bool
    query_trades: bool
    market_data: bool = False
    external_order_transmission: bool = False
    can_cancel: bool = False

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.protocol_version,
                self.broker,
                self.gateway_version,
                self.sdk_version,
            )
        ):
            raise ValueError("broker capability identity is required")
        if self.external_order_transmission or self.can_cancel:
            raise ValueError("read-only broker bridge cannot transmit or cancel orders")


@dataclass(frozen=True, slots=True)
class BrokerStatus:
    broker: str
    state: BrokerConnectionState
    connection_epoch: int
    last_heartbeat_at: datetime | None
    last_query_at: datetime | None
    detail: str = ""

    def __post_init__(self) -> None:
        if not self.broker or self.connection_epoch < 0:
            raise ValueError("broker status identity is invalid")
        if any(
            value is not None and value.tzinfo is None
            for value in (self.last_heartbeat_at, self.last_query_at)
        ):
            raise ValueError("broker status timestamps must be timezone-aware")


@dataclass(frozen=True, slots=True)
class BrokerAccount:
    account_id: str
    currency: str
    total_equity: Decimal
    cash_available: Decimal
    cash_frozen: Decimal
    as_of: datetime


@dataclass(frozen=True, slots=True)
class BrokerPosition:
    account_id: str
    instrument: str
    total: int
    available: int
    frozen: int
    cost_price: Decimal | None
    market_value: Decimal | None
    as_of: datetime


@dataclass(frozen=True, slots=True)
class BrokerOrder:
    account_id: str
    native_order_id: str
    client_order_ref: str | None
    exchange_order_id: str | None
    instrument: str
    side: str
    price: Decimal
    quantity: int
    filled_quantity: int
    status: str
    updated_at: datetime


@dataclass(frozen=True, slots=True)
class BrokerTrade:
    account_id: str
    native_trade_id: str
    native_order_id: str
    instrument: str
    side: str
    price: Decimal
    quantity: int
    total_fee: Decimal | None
    occurred_at: datetime


class ReadOnlyBrokerBridge(Protocol):
    @property
    def capabilities(self) -> BrokerCapabilities: ...

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def heartbeat(self) -> BrokerStatus: ...

    def query_accounts(self) -> tuple[BrokerAccount, ...]: ...

    def query_positions(self) -> tuple[BrokerPosition, ...]: ...

    def query_orders(self) -> tuple[BrokerOrder, ...]: ...

    def query_trades(self) -> tuple[BrokerTrade, ...]: ...
