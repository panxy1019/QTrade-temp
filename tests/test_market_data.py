import json
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from decimal import Decimal

import pytest

from qtrade.data import Bar
from qtrade.market_data import (
    CanonicalTick,
    CanonicalTickStore,
    MarketSnapshot,
    ProviderCapabilities,
    ProviderChoice,
    ProviderConnectionStatus,
    RawTickArchive,
    RqDataProvider,
    TickBarAggregator,
    TickQualityGate,
    TickReplay,
    XtDataProvider,
    inspect_provider_selection,
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
    def __init__(self, capabilities: ProviderCapabilities | None = None) -> None:
        self.connected = False
        self.tick_handler = None
        self.bar_handler = None
        self.capabilities = capabilities or ProviderCapabilities(
            "RQData",
            "1",
            "rqdatac-test",
            "canonical-tick-v1",
            "session-1",
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            False,
        )

    def connect(self):
        self.connected = True

    def disconnect(self):
        self.connected = False

    def handshake(self):
        return self.capabilities

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


class MockXtBridge(MockRqBridge):
    def __init__(self) -> None:
        super().__init__(
            ProviderCapabilities(
                "XtData", "1", "xtquant-test", "canonical-tick-v1", "xt-session",
                True, True, True, True, True, True, True, True,
            )
        )


class FailingCleanupRqBridge(MockRqBridge):
    def handshake(self):
        raise ValueError("invalid handshake")

    def disconnect(self):
        raise OSError("cleanup failed")


def test_rqdata_adapter_uses_injected_bridge_and_never_contains_credentials():
    bridge = MockRqBridge()
    provider = RqDataProvider(bridge)
    assert provider.get_status().connection is ProviderConnectionStatus.DISCONNECTED
    with pytest.raises(RuntimeError, match="handshake"):
        _ = provider.capabilities
    with pytest.raises(RuntimeError, match="not connected"):
        provider.subscribe(("510300",))
    provider.connect()
    assert provider.capabilities.corporate_actions is False
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


def test_rqdata_failed_handshake_stays_disconnected_when_cleanup_also_fails():
    provider = RqDataProvider(FailingCleanupRqBridge())
    with pytest.raises(ValueError, match="invalid handshake"):
        provider.connect()
    assert provider.get_status().connection is ProviderConnectionStatus.DISCONNECTED
    with pytest.raises(RuntimeError, match="handshake"):
        _ = provider.capabilities


def test_rqdata_adapter_rejects_unverified_or_unentitled_capabilities():
    bridge = MockRqBridge(
        ProviderCapabilities(
            "XT",
            "1",
            "test",
            "canonical-tick-v1",
            "session",
            True,
            True,
            True,
            True,
            True,
            True,
            True,
            True,
        )
    )
    with pytest.raises(ValueError, match="identity mismatch"):
        RqDataProvider(bridge).connect()
    provider = RqDataProvider(
        MockRqBridge(
            ProviderCapabilities(
                "RQData",
                "1",
                "test",
                "canonical-tick-v1",
                "session",
                True,
                False,
                False,
                False,
                True,
                True,
                True,
                False,
            )
        )
    )
    provider.connect()
    with pytest.raises(RuntimeError, match="not entitled"):
        provider.subscribe(("510300",))


def test_xtdata_adapter_uses_data_only_verified_bridge_session():
    bridge = MockXtBridge()
    provider = XtDataProvider(bridge)
    provider.connect()
    provider.subscribe(("510300",))
    observed: list[CanonicalTick] = []
    provider.on_tick(observed.append)
    value = replace(
        tick(1), provider="XtData", session_nonce="xt-session"
    )
    bridge.tick_handler(value)
    assert observed == [value]
    with pytest.raises(ValueError, match="session"):
        bridge.tick_handler(replace(value, session_nonce="wrong"))


def test_provider_selection_reports_minimum_actions_without_secret_values(tmp_path):
    akshare = inspect_provider_selection(ProviderChoice.AKSHARE, environment={})
    assert akshare.ready_for_authorized_bridge is False
    assert akshare.missing_configuration == (
        "QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true",
    )
    assert inspect_provider_selection(
        ProviderChoice.AKSHARE,
        environment={"QTRADE_AKSHARE_NONCOMMERCIAL_ACK": "true"},
    ).operational_probe_required

    rq = inspect_provider_selection(ProviderChoice.RQDATA, environment={})
    assert rq.ready_for_authorized_bridge is False
    assert rq.missing_configuration == (
        "QTRADE_RQDATA_USERNAME", "QTRADE_RQDATA_TOKEN"
    )
    rq_configured = inspect_provider_selection(
        ProviderChoice.RQDATA,
        environment={
            "QTRADE_RQDATA_USERNAME": "configured",
            "QTRADE_RQDATA_TOKEN": "configured",
        },
    )
    assert rq_configured.ready_for_authorized_bridge is False
    assert rq_configured.operational_probe_required is True
    xt = inspect_provider_selection(
        ProviderChoice.XTDATA,
        environment={
            "QTRADE_XTDATA_PATH": str(tmp_path),
            "QTRADE_XTDATA_ENTITLEMENT_CONFIRMED": "true",
        },
    )
    assert xt.ready_for_authorized_bridge is False
    assert xt.operational_probe_required is True


@pytest.mark.parametrize("version", ("../escaped", "C:/absolute", "a/b", ".."))
def test_tick_store_rejects_path_like_data_versions_without_filesystem_effect(
    tmp_path, version
):
    store = CanonicalTickStore(tmp_path / "root")
    with pytest.raises(ValueError, match="opaque identifier"):
        store.write((replace(tick(1), data_version=version),))
    assert not (tmp_path / "escaped").exists()


def test_rejected_tick_does_not_poison_quality_cursor():
    gate = TickQualityGate()
    first = replace(tick(5), sequence=5, event_id="first")
    rejected = replace(tick(3), sequence=3, event_id="rejected")
    next_value = replace(tick(6), sequence=6, event_id="next")
    assert gate.validate((first,)) == ()
    assert {issue.code for issue in gate.validate((rejected,))} == {
        "SEQUENCE_DISORDER",
        "TIMESTAMP_DISORDER",
    }
    assert gate.validate((next_value,)) == ()


def test_raw_archive_refuses_to_extend_a_tampered_chain(tmp_path):
    path = tmp_path / "raw.jsonl"
    archive = RawTickArchive(path)
    archive.append("mock", {"value": 1}, datetime(2026, 8, 12, tzinfo=UTC))
    archive.append("mock", {"value": 2}, datetime(2026, 8, 12, tzinfo=UTC))
    lines = path.read_text(encoding="utf-8").splitlines()
    row = json.loads(lines[0])
    row["payload"]["value"] = 999
    lines[0] = json.dumps(row, sort_keys=True)
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    with pytest.raises(ValueError, match="verification"):
        RawTickArchive(path)


def test_tick_store_read_is_confined_to_its_root(tmp_path):
    outside = tmp_path / "outside.parquet"
    outside.write_bytes(b"not parquet")
    with pytest.raises(ValueError, match="escapes"):
        CanonicalTickStore(tmp_path / "root").read(outside, "unused")
