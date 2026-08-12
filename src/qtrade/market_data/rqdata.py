from __future__ import annotations

from contextlib import suppress
from datetime import datetime

from qtrade.data import Bar
from qtrade.market_data.models import (
    CanonicalTick,
    MarketSnapshot,
    ProviderCapabilities,
    ProviderConnectionStatus,
    ProviderStatus,
)
from qtrade.market_data.provider import BarHandler, ProviderBridgeClient, TickHandler


class RqDataProvider:
    """RQData adapter over an injected licensed/native bridge client.

    This package contains no credential defaults and does not claim connection
    validation until a legally authorized client is supplied.
    """

    def __init__(
        self,
        client: ProviderBridgeClient,
        *,
        required_protocol: str = "1",
        required_schema: str = "canonical-tick-v1",
    ) -> None:
        self._client = client
        self._capabilities: ProviderCapabilities | None = None
        self._required_protocol = required_protocol
        self._required_schema = required_schema
        self._connection = ProviderConnectionStatus.DISCONNECTED
        self._subscriptions: set[str] = set()
        self._last_event_at: datetime | None = None
        self._last_heartbeat_at: datetime | None = None

    @property
    def capabilities(self) -> ProviderCapabilities:
        if self._capabilities is None:
            raise RuntimeError("RQData bridge capability handshake is not verified")
        return self._capabilities

    def connect(self) -> None:
        if self._connection is not ProviderConnectionStatus.DISCONNECTED:
            return
        self._connection = ProviderConnectionStatus.CONNECTING
        try:
            self._client.connect()
            capabilities = self._client.handshake()
            self._verify_capabilities(capabilities)
        except Exception:
            self._capabilities = None
            self._connection = ProviderConnectionStatus.DISCONNECTED
            with suppress(Exception):
                self._client.disconnect()
            raise
        self._capabilities = capabilities
        self._connection = ProviderConnectionStatus.CONNECTED

    def disconnect(self) -> None:
        self._client.disconnect()
        self._capabilities = None
        self._connection = ProviderConnectionStatus.DISCONNECTED
        self._subscriptions.clear()

    def subscribe(self, instruments: tuple[str, ...]) -> None:
        self._require_connected()
        if not self.capabilities.realtime or not self.capabilities.ticks:
            raise RuntimeError("RQData bridge is not entitled for realtime ticks")
        if not instruments or any(not item for item in instruments):
            raise ValueError("provider subscription instruments are required")
        self._client.subscribe(instruments)
        self._subscriptions.update(instruments)

    def unsubscribe(self, instruments: tuple[str, ...]) -> None:
        self._require_connected()
        self._client.unsubscribe(instruments)
        self._subscriptions.difference_update(instruments)

    def get_snapshot(self, instruments: tuple[str, ...]) -> tuple[MarketSnapshot, ...]:
        self._require_connected()
        return tuple(self._client.snapshots(instruments))

    def get_status(self) -> ProviderStatus:
        return ProviderStatus(
            "RQData",
            self._connection,
            tuple(sorted(self._subscriptions)),
            self._last_event_at,
            self._last_heartbeat_at,
            (
                "authorized bridge required"
                if self._connection is ProviderConnectionStatus.DISCONNECTED
                else ""
            ),
        )

    def heartbeat(self) -> datetime:
        self._require_connected()
        value = self._client.heartbeat()
        if value.tzinfo is None:
            raise ValueError("provider heartbeat must be timezone-aware")
        self._last_heartbeat_at = value
        return value

    def on_tick(self, handler: TickHandler) -> None:
        def observed(tick: CanonicalTick) -> None:
            self._last_event_at = tick.timestamp_received
            handler(tick)

        self._client.set_tick_handler(observed)

    def on_bar(self, handler: BarHandler) -> None:
        self._client.set_bar_handler(handler)

    def query_history(
        self,
        instruments: tuple[str, ...],
        *,
        start: datetime,
        end: datetime,
        interval: str,
    ) -> tuple[Bar, ...]:
        if start.tzinfo is None or end.tzinfo is None or start >= end or not interval:
            raise ValueError("provider history query is invalid")
        if not self.capabilities.historical:
            raise RuntimeError("RQData bridge is not entitled for history")
        self._require_connected()
        return tuple(self._client.history(instruments, start, end, interval))

    def _require_connected(self) -> None:
        if self._connection is not ProviderConnectionStatus.CONNECTED:
            raise RuntimeError("RQData provider is not connected")

    def _verify_capabilities(self, value: ProviderCapabilities) -> None:
        if value.provider != "RQData":
            raise ValueError("RQData bridge capability identity mismatch")
        if value.protocol_version != self._required_protocol:
            raise ValueError("RQData bridge protocol mismatch")
        if value.schema_version != self._required_schema:
            raise ValueError("RQData bridge schema mismatch")
        if value.external_order_transmission or value.account_access:
            raise ValueError("RQData bridge must be data-only")
