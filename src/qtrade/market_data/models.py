from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from enum import StrEnum


class ProviderConnectionStatus(StrEnum):
    DISCONNECTED = "DISCONNECTED"
    CONNECTING = "CONNECTING"
    CONNECTED = "CONNECTED"
    DEGRADED = "DEGRADED"


@dataclass(frozen=True, slots=True)
class ProviderCapabilities:
    provider: str
    protocol_version: str
    sdk_version: str
    schema_version: str
    session_nonce: str
    historical: bool
    realtime: bool
    ticks: bool
    minute_bars: bool
    etf: bool
    stocks: bool
    indices: bool
    corporate_actions: bool
    external_order_transmission: bool = False
    account_access: bool = False

    def __post_init__(self) -> None:
        if not all(
            value.strip()
            for value in (
                self.provider,
                self.protocol_version,
                self.sdk_version,
                self.schema_version,
                self.session_nonce,
            )
        ):
            raise ValueError("provider capability identity is required")
        if self.external_order_transmission or self.account_access:
            raise ValueError("market data provider must be data-only")


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    provider: str
    connection: ProviderConnectionStatus
    subscriptions: tuple[str, ...]
    last_event_at: datetime | None
    last_heartbeat_at: datetime | None
    detail: str = ""


@dataclass(frozen=True, slots=True)
class MarketSnapshot:
    provider: str
    instrument: str
    exchange: str
    price: Decimal
    bid_price: Decimal | None
    ask_price: Decimal | None
    timestamp_exchange: datetime
    timestamp_received: datetime


@dataclass(frozen=True, slots=True)
class CanonicalTick:
    event_id: str
    provider: str
    instrument: str
    exchange: str
    timestamp_exchange: datetime
    timestamp_received: datetime
    sequence: int
    price: Decimal
    volume: Decimal
    turnover: Decimal
    bid_price: Decimal | None
    ask_price: Decimal | None
    bid_volume: Decimal | None
    ask_volume: Decimal | None
    data_version: str
    ingestion_time: datetime
    reconnect_id: str = "initial"
    ingest_ordinal: int | None = None
    session_nonce: str = "unattested"

    def __post_init__(self) -> None:
        required = (
            self.event_id,
            self.provider,
            self.instrument,
            self.exchange,
            self.data_version,
            self.reconnect_id,
            self.session_nonce,
        )
        if not all(value.strip() for value in required):
            raise ValueError("canonical tick identity fields are required")
        times = (
            self.timestamp_exchange,
            self.timestamp_received,
            self.ingestion_time,
        )
        if any(value.tzinfo is None for value in times):
            raise ValueError("canonical tick timestamps must be timezone-aware")
        if self.sequence < 0:
            raise ValueError("canonical tick sequence cannot be negative")
        if self.ingest_ordinal is not None and self.ingest_ordinal < 0:
            raise ValueError("canonical tick ingest ordinal cannot be negative")
        numeric = (self.price, self.volume, self.turnover)
        optional = (
            self.bid_price,
            self.ask_price,
            self.bid_volume,
            self.ask_volume,
        )
        if any(not value.is_finite() for value in numeric):
            raise ValueError("canonical tick values must be finite")
        if any(value is not None and not value.is_finite() for value in optional):
            raise ValueError("canonical tick L1 values must be finite")
        if self.price <= 0 or self.volume < 0 or self.turnover < 0:
            raise ValueError("canonical tick price/volume/turnover are invalid")
        if any(
            value is not None and value <= 0
            for value in (self.bid_price, self.ask_price)
        ):
            raise ValueError("canonical tick bid/ask price must be positive")
        if any(
            value is not None and value < 0
            for value in (self.bid_volume, self.ask_volume)
        ):
            raise ValueError("canonical tick bid/ask volume cannot be negative")
        if (
            self.bid_price is not None
            and self.ask_price is not None
            and self.bid_price > self.ask_price
        ):
            raise ValueError("canonical tick bid cannot exceed ask")


@dataclass(frozen=True, slots=True)
class TickQualityIssue:
    code: str
    message: str
    event_id: str | None = None
