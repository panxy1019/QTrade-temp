from __future__ import annotations

from datetime import UTC, datetime
from decimal import Decimal

import pytest

from qtrade.data import Bar
from qtrade.market_data import (
    CanonicalTick,
    NativeRqDataBridgeClient,
    ProviderConnectionStatus,
    RqDataProvider,
)


class FakeFrame:
    def __init__(self, rows: list[dict[str, object]]) -> None:
        self.rows = rows

    def reset_index(self) -> FakeFrame:
        return self

    def to_dict(self, *, orient: str) -> list[dict[str, object]]:
        assert orient == "records"
        return self.rows


class FakeLiveClient:
    def __init__(self) -> None:
        self.subscriptions: list[list[str]] = []
        self.unsubscriptions: list[list[str]] = []
        self.handler = None
        self.closed = False

    def subscribe(self, channels):
        self.subscriptions.append(list(channels))

    def unsubscribe(self, channels):
        self.unsubscriptions.append(list(channels))

    def listen(self, handler=None):
        self.handler = handler
        return FakeListener(self)

    def close(self):
        self.closed = True

    def emit(self, row: dict[str, object]) -> None:
        assert self.handler is not None
        self.handler(row)


class FakeListener:
    def __init__(self, client: FakeLiveClient) -> None:
        self.client = client

    def is_alive(self) -> bool:
        return not self.client.closed


class FakeRqDataSdk:
    __version__ = "3.6.3-test"

    def __init__(self, *, fail_authorization: bool = False) -> None:
        self.live = FakeLiveClient()
        self.fail_authorization = fail_authorization
        self.init_calls: list[tuple[str, str]] = []
        self.reset_calls = 0
        self.price_calls: list[dict[str, object]] = []
        self.LiveMarketDataClient = self._live_client

    def _live_client(self):
        if self.fail_authorization:
            raise PermissionError("vendor rejected credential secret-token")
        return self.live

    def init(self, username: str, password: str):
        self.init_calls.append((username, password))

    def reset(self, flush_cache: bool = False):
        del flush_cache
        self.reset_calls += 1

    def current_snapshot(self, order_book_ids, market="cn"):
        assert market == "cn"
        return [
            {
                "order_book_id": value,
                "datetime": 20260825100000000,
                "last": 4 if value.startswith("510300") else 10,
                "bids": [3.99 if value.startswith("510300") else 9.99],
                "asks": [4.01 if value.startswith("510300") else 10.01],
            }
            for value in order_book_ids
        ]

    def get_price(
        self,
        order_book_ids,
        start_date,
        end_date,
        frequency,
        fields,
        adjust_type,
        expect_df,
        market,
    ):
        self.price_calls.append(
            {
                "order_book_ids": order_book_ids,
                "start_date": start_date,
                "end_date": end_date,
                "frequency": frequency,
                "fields": fields,
                "adjust_type": adjust_type,
                "expect_df": expect_df,
                "market": market,
            }
        )
        return FakeFrame(
            [
                {
                    "datetime": datetime(2026, 8, 25, 9, 31),
                    "open": 10,
                    "high": 10.2,
                    "low": 9.9,
                    "close": 10.1,
                    "volume": 1000,
                }
            ]
        )


def bridge(sdk: FakeRqDataSdk) -> NativeRqDataBridgeClient:
    return NativeRqDataBridgeClient(
        environment={
            "QTRADE_RQDATA_USERNAME": "licensed-user",
            "QTRADE_RQDATA_TOKEN": "secret-token",
        },
        clock=lambda: datetime(2026, 8, 25, 2, 0, tzinfo=UTC),
        session_nonce_factory=lambda: "rq-session",
        sdk_loader=lambda: sdk,
    )


def test_rqdata_bridge_missing_configuration_fails_before_sdk_import():
    loaded = False

    def load():
        nonlocal loaded
        loaded = True
        return FakeRqDataSdk()

    client = NativeRqDataBridgeClient(environment={}, sdk_loader=load)
    with pytest.raises(RuntimeError, match="QTRADE_RQDATA_USERNAME"):
        client.connect()
    assert loaded is False


def test_rqdata_bridge_authorization_failure_is_sanitized_and_reset():
    sdk = FakeRqDataSdk(fail_authorization=True)
    client = bridge(sdk)
    with pytest.raises(RuntimeError) as raised:
        client.connect()
    assert "secret-token" not in str(raised.value)
    assert "realtime entitlement" in str(raised.value)
    assert sdk.reset_calls == 1


def test_rqdata_bridge_connect_handshake_snapshot_heartbeat_and_disconnect():
    sdk = FakeRqDataSdk()
    client = bridge(sdk)
    client.connect()
    capabilities = client.handshake()
    assert capabilities.provider == "RQData"
    assert capabilities.sdk_version == "3.6.3-test"
    assert capabilities.session_nonce == "rq-session"
    assert capabilities.ticks and capabilities.minute_bars
    assert capabilities.external_order_transmission is False
    assert capabilities.account_access is False
    assert capabilities.production_grade is False

    snapshots = tuple(client.snapshots(("600000", "000001", "510300")))
    assert [(value.instrument, value.exchange) for value in snapshots] == [
        ("600000", "SSE"),
        ("000001", "SZSE"),
        ("510300", "SSE"),
    ]
    assert snapshots[0].price == Decimal("10")
    assert client.heartbeat() == datetime(2026, 8, 25, 2, 0, tzinfo=UTC)
    client.disconnect()
    assert sdk.live.closed is True
    assert sdk.reset_calls == 1


def test_rqdata_bridge_normalizes_live_ticks_and_minute_bars():
    sdk = FakeRqDataSdk()
    client = bridge(sdk)
    ticks: list[CanonicalTick] = []
    bars: list[Bar] = []
    client.set_tick_handler(ticks.append)
    client.set_bar_handler(bars.append)
    client.connect()
    client.subscribe(("600000", "000001"))
    assert sdk.live.subscriptions == [
        [
            "tick_600000.XSHG",
            "bar_600000.XSHG",
            "tick_000001.XSHE",
            "bar_000001.XSHE",
        ]
    ]

    sdk.live.emit(
        {
            "action": "feed",
            "channel": "tick_600000.XSHG",
            "order_book_id": "600000.XSHG",
            "datetime": 20260825100102003,
            "last": 10,
            "volume": 1200,
            "total_turnover": 12000,
            "bid": [9.99],
            "ask": [10.01],
            "bid_vol": [100],
            "ask_vol": [200],
        }
    )
    sdk.live.emit(
        {
            "action": "feed",
            "channel": "tick_600000.XSHG",
            "order_book_id": "600000.XSHG",
            "datetime": 20260825100103000,
            "last": 10.01,
            "volume": 1300,
            "total_turnover": 13001,
            "bid": [10],
            "ask": [10.01],
            "bid_vol": [100],
            "ask_vol": [200],
        }
    )
    sdk.live.emit(
        {
            "action": "feed",
            "channel": "tick_000001.XSHE",
            "order_book_id": "000001.XSHE",
            "datetime": 20260825100104000,
            "last": 12,
            "volume": 500,
            "total_turnover": 6000,
            "bid": [11.99],
            "ask": [12.01],
            "bid_vol": [100],
            "ask_vol": [200],
        }
    )
    sdk.live.emit(
        {
            "action": "feed",
            "channel": "bar_000001.XSHE",
            "order_book_id": "000001.XSHE",
            "datetime": 20260825100200,
            "trading_date": 20260825,
            "open": 12,
            "high": 12.2,
            "low": 11.9,
            "close": 12.1,
            "volume": 5000,
        }
    )
    assert [value.sequence for value in ticks] == [0, 1, 0]
    assert [value.ingest_ordinal for value in ticks] == [0, 1, 2]
    assert ticks[0].instrument == "600000"
    assert ticks[0].exchange == "SSE"
    assert ticks[0].timestamp_exchange.microsecond == 3000
    assert ticks[0].data_version == "rqdata-live-3.6.3-test-v1"
    assert ticks[0].session_nonce == "rq-session"
    assert bars[0].symbol == "000001"
    assert bars[0].exchange == "SZSE"
    assert bars[0].trading_day.isoformat() == "2026-08-25"

    client.unsubscribe(("600000",))
    assert sdk.live.unsubscriptions == [
        ["tick_600000.XSHG", "bar_600000.XSHG"]
    ]


def test_native_bridge_runs_through_verified_rqdata_provider_callback():
    sdk = FakeRqDataSdk()
    provider = RqDataProvider(bridge(sdk))
    observed: list[CanonicalTick] = []
    provider.on_tick(observed.append)
    provider.connect()
    provider.subscribe(("510300",))
    sdk.live.emit(
        {
            "action": "feed",
            "channel": "tick_510300.XSHG",
            "order_book_id": "510300.XSHG",
            "datetime": 20260825100102003,
            "last": 4,
            "volume": 1200,
            "total_turnover": 4800,
            "bid": [3.99],
            "ask": [4.01],
            "bid_vol": [100],
            "ask_vol": [200],
        }
    )
    assert len(observed) == 1
    assert observed[0].provider == "RQData"
    assert observed[0].session_nonce == provider.capabilities.session_nonce
    assert provider.get_status().connection is ProviderConnectionStatus.CONNECTED
    assert provider.get_status().last_event_at == observed[0].timestamp_received


def test_rqdata_bridge_history_is_raw_and_half_open():
    sdk = FakeRqDataSdk()
    client = bridge(sdk)
    client.connect()
    start = datetime(2026, 8, 25, 1, 0, tzinfo=UTC)
    end = datetime(2026, 8, 25, 2, 0, tzinfo=UTC)
    values = tuple(client.history(("000300.XSHG",), start, end, "1m"))
    assert len(values) == 1
    assert values[0].symbol == "000300.XSHG"
    assert values[0].exchange == "SSE"
    assert values[0].timestamp.tzinfo is not None
    assert sdk.price_calls[0]["adjust_type"] == "none"
    assert sdk.price_calls[0]["frequency"] == "1m"


def test_rqdata_bridge_fails_closed_after_invalid_live_payload():
    sdk = FakeRqDataSdk()
    client = bridge(sdk)
    client.connect()
    client.subscribe(("600000",))
    sdk.live.emit(
        {
            "action": "feed",
            "channel": "tick_600000.XSHG",
            "order_book_id": "600000.XSHG",
            "datetime": 20260825100102003,
            "last": 10,
            "volume": 1200,
            "total_turnover": 12000,
            "bid": [10.02],
            "ask": [10.01],
            "bid_vol": [100],
            "ask_vol": [200],
        }
    )
    assert sdk.live.closed is True
    with pytest.raises(RuntimeError, match="failed closed"):
        client.heartbeat()


def test_rqdata_bridge_heartbeat_rejects_a_stopped_listener():
    sdk = FakeRqDataSdk()
    client = bridge(sdk)
    client.connect()
    client.subscribe(("600000",))
    sdk.live.closed = True
    with pytest.raises(RuntimeError, match="listener stopped"):
        client.heartbeat()


def test_rqdata_bridge_rejects_ambiguous_duplicate_aliases():
    sdk = FakeRqDataSdk()
    client = bridge(sdk)
    client.connect()
    with pytest.raises(ValueError, match="duplicate aliases"):
        client.subscribe(("600000", "600000.XSHG"))
