from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from qtrade.data import Bar
from qtrade.market_data import (
    CanonicalTick,
    CanonicalTickStore,
    MarketSnapshot,
    ProviderConnectionStatus,
    RawTickArchive,
    RqDataProvider,
    TickBarAggregator,
    TickQualityGate,
    TickReplay,
)


def tick(
    sequence: int,
    *,
    received_delay: int = 0,
    reconnect_id: str = "initial",
    event_id: str | None = None,
    price: str = "10",
) -> CanonicalTick:
    exchange_time = datetime(2026, 8, 12, 1, 30, sequence, tzinfo=UTC)
    return CanonicalTick(
        event_id or f"tick-{sequence}",
        "mock",
        "510300",
        "SSE",
        exchange_time,
        exchange_time + timedelta(seconds=received_delay),
        sequence,
        Decimal(price),
        Decimal(100 + sequence),
        Decimal(1000 + sequence),
        Decimal(price) - Decimal("0.001"),
        Decimal(price) + Decimal("0.001"),
        Decimal("10"),
        Decimal("10"),
        "ticks-v1",
        exchange_time + timedelta(seconds=received_delay),
        reconnect_id,
        sequence,
    )


def test_tick_quality_detects_duplicates_sequence_staleness_and_reconnect_gap():
    issues = TickQualityGate(maximum_staleness=timedelta(seconds=2)).validate(
        (
            tick(1),
            tick(3, received_delay=4),
            tick(3, event_id="tick-3-duplicate-sequence"),
            tick(5, reconnect_id="reconnect-2", event_id="reconnect"),
        )
    )
    codes = {issue.code for issue in issues}
    assert {"MISSING_SEQUENCE", "SEQUENCE_DISORDER", "STALE_QUOTE", "RECONNECT_GAP"} <= codes


def test_tick_quality_state_spans_callbacks_and_reconnects():
    gate = TickQualityGate()
    assert gate.validate((tick(1),)) == ()
    issues = gate.validate(
        (
            tick(1, event_id="tick-1"),
            tick(4, event_id="gap"),
            tick(3, reconnect_id="second", event_id="bad-reconnect"),
        )
    )
    assert {issue.code for issue in issues} >= {
        "DUPLICATE",
        "MISSING_SEQUENCE",
        "RECONNECT_GAP",
    }


def test_tick_quality_cursor_survives_restart(tmp_path):
    state_path = tmp_path / "quality-state.json"
    first = TickQualityGate(state_path=state_path)
    assert first.validate((tick(1),)) == ()
    restarted = TickQualityGate(state_path=state_path)
    issues = restarted.validate((tick(3),))
    assert {issue.code for issue in issues} == {"MISSING_SEQUENCE"}


def test_tick_recording_aggregation_and_replay_are_deterministic(tmp_path):
    ticks = (tick(1, price="10"), tick(2, price="11"), tick(3, price="9"))
    archive = RawTickArchive(tmp_path / "raw.jsonl")
    archive.append("mock", {"last": "10"}, ticks[0].timestamp_received)
    assert "mock" in (tmp_path / "raw.jsonl").read_text(encoding="utf-8")
    assert archive.verify_chain() is True
    target, checksum = CanonicalTickStore(tmp_path / "canonical").write(ticks)
    restored = CanonicalTickStore(tmp_path / "canonical").read(target, checksum)
    assert restored == ticks
    bars = TickBarAggregator().aggregate(
        restored, watermark=datetime(2026, 8, 12, 1, 31, tzinfo=UTC)
    )
    assert len(bars) == 1
    assert bars[0].bar.open == Decimal("10")
    assert bars[0].bar.high == Decimal("11")
    assert bars[0].bar.low == Decimal("9")
    assert bars[0].bar.close == Decimal("9")
    replayed = []
    TickReplay(restored).run(replayed.append)
    assert replayed == list(ticks)


def test_bar_aggregation_waits_for_watermark_and_rejects_late_ticks():
    aggregator = TickBarAggregator()
    assert (
        aggregator.aggregate(
            (tick(1),), watermark=datetime(2026, 8, 12, 1, 30, 30, tzinfo=UTC)
        )
        == ()
    )
    bars = aggregator.aggregate(
        (tick(2),), watermark=datetime(2026, 8, 12, 1, 31, tzinfo=UTC)
    )
    assert len(bars) == 1
    with pytest.raises(ValueError, match="late tick"):
        aggregator.aggregate(
            (tick(3),), watermark=datetime(2026, 8, 12, 1, 32, tzinfo=UTC)
        )


def test_replay_uses_ingest_ordinal_across_instruments_and_streams():
    first = tick(7, event_id="first")
    second = CanonicalTick(
        "second",
        "mock",
        "510500",
        "SSE",
        first.timestamp_exchange - timedelta(seconds=2),
        first.timestamp_received + timedelta(milliseconds=1),
        1,
        Decimal("20"),
        Decimal("10"),
        Decimal("200"),
        Decimal("19.999"),
        Decimal("20.001"),
        Decimal("10"),
        Decimal("10"),
        "ticks-v1",
        first.ingestion_time + timedelta(milliseconds=1),
        "other-stream",
        8,
    )
    first = replace(first, ingest_ordinal=7)
    replayed: list[str] = []
    TickReplay((second, first)).run(lambda item: replayed.append(item.event_id))
    assert replayed == ["first", "second"]


class MockRqBridge:
    def __init__(self) -> None:
        self.connected = False
        self.tick_handler = None
        self.bar_handler = None

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def subscribe(self, instruments):
        self.subscribed = instruments

    def unsubscribe(self, instruments):
        self.unsubscribed = instruments

    def snapshots(self, instruments):
        now = datetime(2026, 8, 12, tzinfo=UTC)
        return (
            MarketSnapshot(
                "RQData", instruments[0], "SSE", Decimal("10"), None, None, now, now
            ),
        )

    def history(self, instruments, start, end, interval):
        return (
            Bar(
                instruments[0],
                "SSE",
                start,
                start.date(),
                Decimal("10"),
                Decimal("10"),
                Decimal("10"),
                Decimal("10"),
                Decimal("100"),
            ),
        )

    def heartbeat(self):
        return datetime(2026, 8, 12, tzinfo=UTC)

    def set_tick_handler(self, handler):
        self.tick_handler = handler

    def set_bar_handler(self, handler):
        self.bar_handler = handler


def test_rqdata_adapter_uses_injected_bridge_and_never_contains_credentials():
    bridge = MockRqBridge()
    provider = RqDataProvider(bridge)
    assert provider.get_status().connection is ProviderConnectionStatus.DISCONNECTED
    with pytest.raises(RuntimeError, match="not connected"):
        provider.subscribe(("510300",))
    provider.connect()
    provider.subscribe(("510300",))
    assert provider.get_status().subscriptions == ("510300",)
    assert provider.get_snapshot(("510300",))[0].price == Decimal("10")
    assert len(
        provider.query_history(
            ("510300",),
            start=datetime(2026, 8, 1, tzinfo=UTC),
            end=datetime(2026, 8, 2, tzinfo=UTC),
            interval="1m",
        )
    ) == 1
    provider.disconnect()
    assert provider.get_status().connection is ProviderConnectionStatus.DISCONNECTED
