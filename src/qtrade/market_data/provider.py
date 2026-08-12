from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import datetime
from typing import Protocol

from qtrade.data import Bar
from qtrade.market_data.models import (
    CanonicalTick,
    MarketSnapshot,
    ProviderCapabilities,
    ProviderStatus,
)

TickHandler = Callable[[CanonicalTick], None]
BarHandler = Callable[[Bar], None]


class HistoricalDataProvider(Protocol):
    @property
    def capabilities(self) -> ProviderCapabilities: ...

    def query_history(
        self,
        instruments: tuple[str, ...],
        *,
        start: datetime,
        end: datetime,
        interval: str,
    ) -> tuple[Bar, ...]: ...


class RealtimeMarketDataProvider(Protocol):
    @property
    def capabilities(self) -> ProviderCapabilities: ...

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def subscribe(self, instruments: tuple[str, ...]) -> None: ...

    def unsubscribe(self, instruments: tuple[str, ...]) -> None: ...

    def get_snapshot(self, instruments: tuple[str, ...]) -> tuple[MarketSnapshot, ...]: ...

    def get_status(self) -> ProviderStatus: ...

    def heartbeat(self) -> datetime: ...

    def on_tick(self, handler: TickHandler) -> None: ...

    def on_bar(self, handler: BarHandler) -> None: ...


class MarketDataProvider(HistoricalDataProvider, RealtimeMarketDataProvider, Protocol):
    pass


class ProviderBridgeClient(Protocol):
    """Vendor/native-process seam. Vendor SDK imports stay outside QTrade core."""

    def connect(self) -> None: ...

    def disconnect(self) -> None: ...

    def subscribe(self, instruments: tuple[str, ...]) -> None: ...

    def unsubscribe(self, instruments: tuple[str, ...]) -> None: ...

    def snapshots(self, instruments: tuple[str, ...]) -> Iterable[MarketSnapshot]: ...

    def history(
        self,
        instruments: tuple[str, ...],
        start: datetime,
        end: datetime,
        interval: str,
    ) -> Iterable[Bar]: ...

    def heartbeat(self) -> datetime: ...

    def set_tick_handler(self, handler: TickHandler) -> None: ...

    def set_bar_handler(self, handler: BarHandler) -> None: ...
