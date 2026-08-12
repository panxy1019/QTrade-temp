from datetime import UTC, datetime, timedelta
from decimal import Decimal
from threading import Barrier, Thread
from time import sleep

import pytest

from qtrade.market_data import (
    AkshareMarketDataProvider,
    AksharePollingPolicy,
    ProviderConnectionStatus,
)


class FakeAkshareClient:
    version = "1.18.84"

    def __init__(self, rows=None) -> None:
        self.rows = list(rows or [])
        self.calls = 0
        self.fail = False

    def etf_spot_rows(self):
        self.calls += 1
        if self.fail:
            raise TimeoutError("upstream timeout")
        return list(self.rows)

    def etf_daily_rows(self, symbol, start_date, end_date):
        return [
            {
                "日期": "2026-08-11",
                "开盘": 4.0,
                "最高": 4.2,
                "最低": 3.9,
                "收盘": 4.1,
                "成交量": 1000,
            }
        ]

    def etf_minute_rows(self, symbol, start_at, end_at, period):
        return [
            {
                "时间": "2026-08-12 09:31:00",
                "开盘": 4.1,
                "最高": 4.2,
                "最低": 4.0,
                "收盘": 4.15,
                "成交量": 100,
            }
        ]


class BlockingAkshareClient(FakeAkshareClient):
    def etf_spot_rows(self):
        self.calls += 1
        if self.calls > 1:
            sleep(0.2)
        return list(self.rows)

def spot_row(
    symbol="510300", name="沪深300ETF", amount=Decimal("100000000")
):
    return {
        "代码": symbol,
        "名称": name,
        "最新价": Decimal("4.1"),
        "成交量": Decimal("10000"),
        "成交额": amount,
        "买一": Decimal("4.099"),
        "卖一": Decimal("4.101"),
        "更新时间": datetime(2026, 8, 12, 9, 31),
    }


def provider(client, *, now=None, monotonic=None, policy=None):
    return AkshareMarketDataProvider(
        client,
        policy=policy,
        noncommercial_research_acknowledged=True,
        clock=lambda: now or datetime(2026, 8, 12, 1, 31, 1, tzinfo=UTC),
        monotonic=monotonic or (lambda: 100.0),
        sleep=lambda _: None,
        session_nonce_factory=lambda: "session-1",
    )


def test_requires_explicit_noncommercial_research_acknowledgement():
    with pytest.raises(ValueError, match="noncommercial"):
        AkshareMarketDataProvider(FakeAkshareClient([spot_row()]))


def test_connect_probes_upstream_and_emits_deduped_polling_observation():
    client = FakeAkshareClient([spot_row()])
    value = provider(client)
    value.connect()
    assert value.capabilities.snapshot_polling
    assert not value.capabilities.ticks
    assert not value.capabilities.production_grade
    assert not value.capabilities.external_order_transmission
    value.subscribe(("510300",))

    observed = []
    value.on_tick(observed.append)
    first = value.poll_once()
    second = value.poll_once()
    assert len(first) == 1 and second == ()
    assert first[0].provider == "AKShare"
    assert first[0].session_nonce == "session-1"
    assert first[0].bid_price == Decimal("4.099")
    assert observed == list(first)
    assert client.calls == 1


def test_connect_rejects_nonempty_but_invalid_probe_rows():
    value = provider(FakeAkshareClient([{}]))
    with pytest.raises(ValueError, match="no eligible fresh canonical row"):
        value.connect()
    assert value.get_status().connection is ProviderConnectionStatus.DISCONNECTED


def test_reconnect_resets_each_instrument_source_sequence():
    client = FakeAkshareClient([spot_row()])
    nonces = iter(("session-1", "session-2"))
    value = AkshareMarketDataProvider(
        client,
        noncommercial_research_acknowledged=True,
        clock=lambda: datetime(2026, 8, 12, 1, 31, 1, tzinfo=UTC),
        monotonic=lambda: 100.0,
        sleep=lambda _: None,
        session_nonce_factory=lambda: next(nonces),
    )
    value.connect()
    value.subscribe(("510300",))
    first = value.poll_once()[0]
    value.disconnect()
    value.connect()
    value.subscribe(("510300",))
    second = value.poll_once()[0]
    assert (first.session_nonce, first.sequence) == ("session-1", 0)
    assert (second.session_nonce, second.sequence) == ("session-2", 0)
    assert first.ingest_ordinal is None and second.ingest_ordinal is None


def test_concurrent_cache_misses_are_single_flight():
    client = FakeAkshareClient([spot_row()])
    monotonic_value = [100.0]
    value = provider(client, monotonic=lambda: monotonic_value[0])
    value.connect()
    monotonic_value[0] = 200.0
    start = Barrier(3)

    def request():
        start.wait()
        value.get_snapshot(("510300",))

    threads = (Thread(target=request), Thread(target=request))
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join()
    assert client.calls == 2


def test_raw_etf_daily_and_minute_history_are_canonicalized():
    client = FakeAkshareClient([spot_row()])
    value = provider(client)
    value.connect()
    start = datetime(2026, 8, 11, tzinfo=UTC)
    end = datetime(2026, 8, 13, tzinfo=UTC)
    daily = value.query_history(("510300",), start=start, end=end, interval="1d")
    minute = value.query_history(("510300",), start=start, end=end, interval="1m")
    assert daily[0].exchange == "SSE" and daily[0].close == Decimal("4.1")
    assert minute[0].timestamp.tzinfo is not None
    assert minute[0].close == Decimal("4.15")


def test_liquidity_universe_is_bounded_and_excludes_non_domestic_exposures():
    rows = [
        spot_row(f"51{index:04d}", f"宽基ETF{index}", Decimal(100 - index))
        for index in range(25)
    ]
    rows.append(spot_row("513500", "标普ETF", Decimal("999999")))
    value = provider(FakeAkshareClient(rows))
    value.connect()
    universe = value.select_liquid_etf_universe(size=20)
    assert len(universe) == 20
    assert "513500" not in universe


def test_retries_then_opens_circuit_and_marks_health_degraded():
    client = FakeAkshareClient([spot_row()])
    monotonic_value = [100.0]
    value = provider(
        client,
        monotonic=lambda: monotonic_value[0],
        policy=AksharePollingPolicy(
            minimum_request_interval=timedelta(milliseconds=1),
            snapshot_poll_interval=timedelta(seconds=1),
            request_timeout=timedelta(seconds=1),
            maximum_attempts=2,
            initial_backoff=timedelta(milliseconds=1),
            maximum_quote_age=timedelta(minutes=2),
            circuit_failure_threshold=1,
            circuit_cooldown=timedelta(minutes=1),
        ),
    )
    value.connect()
    value.subscribe(("510300",))
    client.fail = True
    monotonic_value[0] = 102.0
    with pytest.raises(RuntimeError, match="upstream request failed"):
        value.poll_once()
    assert value.get_status().connection is ProviderConnectionStatus.DEGRADED
    with pytest.raises(RuntimeError, match="not connected and healthy"):
        value.poll_once()


def test_stale_snapshot_is_rejected():
    stale = spot_row()
    stale["更新时间"] = datetime(2026, 8, 12, 9, 0)
    value = provider(FakeAkshareClient([stale]))
    with pytest.raises(ValueError, match="no eligible fresh canonical row"):
        value.connect()


def test_hard_timeout_degrades_without_starting_fake_retry():
    client = BlockingAkshareClient([spot_row()])
    monotonic_value = [100.0]
    value = provider(
        client,
        monotonic=lambda: monotonic_value[0],
        policy=AksharePollingPolicy(
            minimum_request_interval=timedelta(milliseconds=1),
            snapshot_poll_interval=timedelta(seconds=1),
            request_timeout=timedelta(milliseconds=20),
            maximum_attempts=3,
            initial_backoff=timedelta(milliseconds=1),
            maximum_quote_age=timedelta(minutes=2),
            circuit_failure_threshold=1,
            circuit_cooldown=timedelta(minutes=1),
        ),
    )
    value.connect()
    value.subscribe(("510300",))
    monotonic_value[0] = 102.0
    with pytest.raises(RuntimeError, match="upstream request failed"):
        value.poll_once()
    assert client.calls == 2
    assert value.get_status().connection is ProviderConnectionStatus.DEGRADED
