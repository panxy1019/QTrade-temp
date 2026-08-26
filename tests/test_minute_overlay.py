import json
from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from qtrade.data import Bar
from qtrade.domain import Instrument, RuntimeMode
from qtrade.execution import FeeSchedule
from qtrade.instruments import (
    TradingCalendar,
    TradingDayStatus,
    TradingRule,
    TradingStatus,
)
from qtrade.market_data import (
    AvailableBar,
    CanonicalTick,
    MultiAssetRecordedPaperSession,
    RecordedPaperEvent,
    TickReplay,
)
from qtrade.market_data.readiness import (
    MarketDataReadinessAttestation,
    MarketDataReadinessGate,
)
from qtrade.risk import RiskLimits
from qtrade.runtime import (
    CausalDailyTargetBook,
    DailyTarget,
    MinuteOverlayMonitor,
    MinuteOverlayPolicy,
    MinuteRiskOverlayStrategy,
    RealDataPaperResult,
    RealDataPaperRunner,
    build_paper_runtime,
)


class MemoryStateStore:
    def __init__(self) -> None:
        self.values: dict[str, dict[str, object]] = {}

    def load_state(self, key: str) -> dict[str, object] | None:
        value = self.values.get(key)
        return None if value is None else json.loads(json.dumps(value))

    def save_state(self, key: str, value: dict[str, object]) -> None:
        self.values[key] = json.loads(json.dumps(value))


def policy(
    *,
    maximum_volatility: str = "0.5",
    maximum_drawdown: str = "0.20",
    cooldown: int = 2,
) -> MinuteOverlayPolicy:
    return MinuteOverlayPolicy(
        short_window=2,
        long_window=3,
        volatility_window=2,
        maximum_minute_volatility=Decimal(maximum_volatility),
        maximum_intraday_drawdown=Decimal(maximum_drawdown),
        reduction_multiplier=Decimal("0.5"),
        cooldown_bars=cooldown,
    )


def bar(minute: int, close: str, *, symbol: str = "510300", day: int = 12) -> Bar:
    trading_day = date(2026, 8, day)
    timestamp = datetime(2026, 8, day, 1, minute, tzinfo=UTC)
    price = Decimal(close)
    return Bar(
        symbol,
        "SSE",
        timestamp,
        trading_day,
        price,
        price,
        price,
        price,
        Decimal("100"),
    )


def published_target(*, known_at: datetime, quantity: int = 550) -> DailyTarget:
    return DailyTarget(
        "510300",
        date(2026, 8, 12),
        quantity,
        "daily-reversal-v1",
        "daily-spec-hash",
        known_at,
    )


def test_daily_target_is_causal_immutable_and_restartable() -> None:
    store = MemoryStateStore()
    book = CausalDailyTargetBook(state_store=store)
    target = published_target(known_at=datetime(2026, 8, 12, 1, 29, tzinfo=UTC))
    book.publish(target)

    assert (
        book.target_for(
            "510300",
            target.trading_day,
            bar_started_at=datetime(2026, 8, 12, 1, 28, tzinfo=UTC),
        )
        is None
    )
    assert (
        book.target_for(
            "510300",
            target.trading_day,
            bar_started_at=datetime(2026, 8, 12, 1, 29, tzinfo=UTC),
        )
        == target
    )
    book.publish(target)
    with pytest.raises(ValueError, match="immutable"):
        book.publish(replace(target, quantity=600))
    assert (
        CausalDailyTargetBook(state_store=store).target_for(
            "510300",
            target.trading_day,
            bar_started_at=datetime(2026, 8, 12, 1, 30, tzinfo=UTC),
        )
        == target
    )


def test_daily_target_restore_rejects_boolean_quantity() -> None:
    store = MemoryStateStore()
    book = CausalDailyTargetBook(state_store=store)
    book.publish(published_target(known_at=datetime(2026, 8, 11, 7, tzinfo=UTC)))
    store.values["minute_overlay:daily_targets:v1"]["targets"][0]["quantity"] = True  # type: ignore[index]

    with pytest.raises(ValueError, match="row is malformed"):
        CausalDailyTargetBook(state_store=store)


def test_minute_overlay_warms_up_only_reduces_and_holds_recovery_cooldown() -> None:
    book = CausalDailyTargetBook()
    book.publish(published_target(known_at=datetime(2026, 8, 11, 7, tzinfo=UTC)))
    strategy = MinuteRiskOverlayStrategy(
        symbol="510300",
        lot_size=100,
        overlay_id="risk-overlay-v1",
        targets=book,
        policy=policy(),
    )

    assert strategy.on_bar(bar(30, "10"), 0) == 0
    assert strategy.on_bar(bar(31, "10.1"), 0) == 0
    assert strategy.on_bar(bar(32, "10.2"), 0) == 500
    assert strategy.snapshot is not None and strategy.snapshot.ready is True

    assert strategy.on_bar(bar(33, "9.8"), 500) == 200
    assert strategy.snapshot is not None
    assert "MINUTE_TREND_WEAK" in strategy.snapshot.reasons
    assert strategy.snapshot.corrected_target_quantity <= 550

    assert strategy.on_bar(bar(34, "10.5"), 200) == 200
    assert strategy.on_bar(bar(35, "10.8"), 200) == 200
    assert strategy.on_bar(bar(36, "11"), 200) == 200
    assert strategy.on_bar(bar(37, "11.1"), 200) == 500


def test_drawdown_kills_target_and_overlay_state_survives_restart() -> None:
    store = MemoryStateStore()
    targets = CausalDailyTargetBook(state_store=store)
    targets.publish(
        published_target(known_at=datetime(2026, 8, 11, 7, tzinfo=UTC), quantity=500)
    )
    strategy = MinuteRiskOverlayStrategy(
        symbol="510300",
        lot_size=100,
        overlay_id="durable-overlay-v1",
        targets=targets,
        policy=policy(maximum_drawdown="0.05"),
        state_store=store,
    )
    strategy.on_bar(bar(30, "10"), 0)
    strategy.on_bar(bar(31, "10"), 0)
    assert strategy.on_bar(bar(32, "10"), 0) == 500
    assert strategy.on_bar(bar(33, "9.4"), 500) == 0
    assert strategy.snapshot is not None
    assert "INTRADAY_DRAWDOWN_LIMIT" in strategy.snapshot.reasons

    restored_targets = CausalDailyTargetBook(state_store=store)
    restored = MinuteRiskOverlayStrategy(
        symbol="510300",
        lot_size=100,
        overlay_id="durable-overlay-v1",
        targets=restored_targets,
        policy=policy(maximum_drawdown="0.05"),
        state_store=store,
    )
    assert restored.snapshot == strategy.snapshot
    with pytest.raises(ValueError, match="strictly chronological"):
        restored.on_bar(bar(33, "9.4"), 0)
    assert restored.on_bar(bar(34, "9.5"), 0) == 0


def test_minute_overlay_restore_rejects_string_ready_state() -> None:
    store = MemoryStateStore()
    targets = CausalDailyTargetBook(state_store=store)
    targets.publish(published_target(known_at=datetime(2026, 8, 11, 7, tzinfo=UTC)))
    strategy = MinuteRiskOverlayStrategy(
        symbol="510300",
        lot_size=100,
        overlay_id="malformed-overlay-v1",
        targets=targets,
        policy=policy(),
        state_store=store,
    )
    strategy.on_bar(bar(30, "10"), 0)
    state_key = next(key for key in store.values if key.startswith("minute_overlay:strategy:"))
    snapshot = store.values[state_key]["snapshot"]
    assert isinstance(snapshot, dict)
    snapshot["ready"] = "false"

    with pytest.raises(ValueError, match="snapshot"):
        MinuteRiskOverlayStrategy(
            symbol="510300",
            lot_size=100,
            overlay_id="malformed-overlay-v1",
            targets=targets,
            policy=policy(),
            state_store=store,
        )


def test_minute_overlay_monitor_requires_provider_and_all_symbols_ready() -> None:
    book = CausalDailyTargetBook()
    book.publish(published_target(known_at=datetime(2026, 8, 11, 7, tzinfo=UTC)))
    strategy = MinuteRiskOverlayStrategy(
        symbol="510300",
        lot_size=100,
        overlay_id="monitored-overlay-v1",
        targets=book,
        policy=policy(),
    )
    connected = [False]
    monitor = MinuteOverlayMonitor(
        provider="mock",
        strategies={"510300": strategy},
        provider_connected=lambda: connected[0],
    )
    assert monitor.snapshot()["readiness"] == "NOT_READY"
    strategy.on_bar(bar(30, "10"), 0)
    strategy.on_bar(bar(31, "10.1"), 0)
    strategy.on_bar(bar(32, "10.2"), 0)
    assert monitor.snapshot()["readiness"] == "NOT_READY"
    connected[0] = True
    value = monitor.snapshot()
    assert value["readiness"] == "READY"
    assert value["ready_symbol_count"] == 1
    assert value["external_order_transmission"] is False


def test_minute_overlay_executes_only_on_tick_after_finalized_bar(tmp_path: Path) -> None:
    trading_day = date(2026, 8, 12)
    targets = CausalDailyTargetBook()
    targets.publish(
        published_target(known_at=datetime(2026, 8, 11, 7, tzinfo=UTC), quantity=500)
    )
    overlay = MinuteRiskOverlayStrategy(
        symbol="510300",
        lot_size=100,
        overlay_id="integrated-overlay-v1",
        targets=targets,
        policy=policy(cooldown=0),
    )
    bundle = build_paper_runtime(
        mode=RuntimeMode.PAPER,
        opening_cash=Decimal("100000"),
        limits=RiskLimits(Decimal("100000"), 10000, Decimal("100000")),
        fees=FeeSchedule("test", Decimal("0"), Decimal("0")),
        journal_path=tmp_path / "runtime.db",
    )
    bundle.reconcile_and_recover(actor="operator")
    bundle.runtime.start(actor="operator")
    readiness = MarketDataReadinessGate()
    readiness.attest(
        MarketDataReadinessAttestation(
            "mock",
            "session-1",
            "initial",
            "ticks-v1",
            datetime(2026, 8, 12, 1, 29, tzinfo=UTC),
            datetime(2026, 8, 12, 2, tzinfo=UTC),
        )
    )
    now = [datetime(2026, 8, 12, 1, 31, 1, tzinfo=UTC)]
    runner = RealDataPaperRunner(
        bundle=bundle,
        instrument=Instrument("510300", "SSE", "ETF", 100, Decimal("0.001")),
        trading_rule=TradingRule(
            "etf",
            "SSE",
            "ETF",
            trading_day,
            None,
            100,
            100,
            Decimal("0.001"),
            Decimal("0.1"),
            0,
            "fixture://rule",
            "1",
        ),
        calendar=TradingCalendar(
            (TradingDayStatus("SSE", trading_day, TradingStatus.OPEN, trading_day, "fixture"),)
        ),
        strategy=overlay,
        strategy_id="daily-plus-minute-overlay",
        strategy_version="1.0.0",
        strategy_spec_hash="overlay-spec-hash",
        portfolio_id="paper",
        data_version="ticks-v1",
        provider="mock",
        bar_interval="1m",
        market_readiness=readiness,
        clock=lambda: now[0],
    )

    for ordinal, (minute, close) in enumerate(((30, "10"), (31, "10.1")), start=1):
        available_at = datetime(2026, 8, 12, 1, minute + 1, tzinfo=UTC)
        runner.on_finalized_bar(AvailableBar(bar(minute, close), available_at, ordinal, ordinal))
        now[0] = available_at + timedelta(seconds=1)
        result = runner.on_tick(_tick(ordinal, now[0], close))
        assert result.trade is None

    available_at = datetime(2026, 8, 12, 1, 33, tzinfo=UTC)
    runner.on_finalized_bar(AvailableBar(bar(32, "10.2"), available_at, 3, 3))
    before = _tick(3, available_at - timedelta(microseconds=1), "10.2")
    assert runner.on_tick(before).trade is None
    now[0] = available_at + timedelta(seconds=1)
    result = runner.on_tick(_tick(4, now[0], "10.3"))
    assert result.trade is not None
    assert result.trade.timestamp > available_at
    assert bundle.ledger.account.position_quantity("510300") == 500


class FakeRecordedSession:
    def on_tick(self, tick: CanonicalTick, *, watermark: datetime) -> RecordedPaperEvent:
        return RecordedPaperEvent(
            (), RealDataPaperResult(None, None, None), Path(tick.instrument), watermark.isoformat()
        )


def test_multi_asset_session_routes_known_symbols_and_fails_closed() -> None:
    first = FakeRecordedSession()
    second = FakeRecordedSession()
    session = MultiAssetRecordedPaperSession(
        {"510300": first, "510500": second}  # type: ignore[arg-type]
    )
    observed_at = datetime(2026, 8, 12, 1, 31, tzinfo=UTC)
    routed = session.on_tick(_tick(1, observed_at, "10"), watermark=observed_at)
    assert routed.instrument == "510300"
    assert routed.event.canonical_path == Path("510300")
    replayed: list[str] = []
    other = replace(
        _tick(2, observed_at + timedelta(seconds=1), "20"),
        event_id="second-symbol",
        instrument="510500",
        ingest_ordinal=2,
    )
    first_tick = replace(_tick(1, observed_at, "10"), ingest_ordinal=1)
    TickReplay((other, first_tick)).run(
        lambda value: replayed.append(
            session.on_tick(value, watermark=value.timestamp_received).instrument
        )
    )
    assert replayed == ["510300", "510500"]
    unknown = replace(_tick(2, observed_at, "10"), instrument="159915")
    with pytest.raises(ValueError, match="no configured"):
        session.on_tick(unknown, watermark=observed_at)


def _tick(ordinal: int, observed_at: datetime, price: str) -> CanonicalTick:
    return CanonicalTick(
        f"tick-{ordinal}-{observed_at.isoformat()}",
        "mock",
        "510300",
        "SSE",
        observed_at,
        observed_at,
        ordinal,
        Decimal(price),
        Decimal("100"),
        Decimal("1000"),
        Decimal(price) - Decimal("0.001"),
        Decimal(price) + Decimal("0.001"),
        Decimal("10"),
        Decimal("10"),
        "ticks-v1",
        observed_at,
        ingest_ordinal=ordinal,
        session_nonce="session-1",
    )
