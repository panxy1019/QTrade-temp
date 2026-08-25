from dataclasses import replace
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from threading import Barrier, Thread

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
    CanonicalTick,
    CanonicalTickStore,
    MarketDataReadinessAttestation,
    MarketDataReadinessGate,
    RecordedPaperSession,
)
from qtrade.risk import RiskLimits
from qtrade.runtime import RealDataPaperRunner, build_paper_runtime


class Target100:
    def on_bar(self, bar: Bar, current_quantity: int) -> int:
        return 100


def build_session(tmp_path, name: str):
    day = date(2026, 8, 12)
    decision_time = datetime(2026, 8, 12, 1, 31, tzinfo=UTC)
    bundle = build_paper_runtime(
        mode=RuntimeMode.PAPER,
        opening_cash=Decimal("100000"),
        limits=RiskLimits(Decimal("50000"), 10000, Decimal("100000")),
        fees=FeeSchedule("test", Decimal("0"), Decimal("0")),
        journal_path=tmp_path / f"{name}.db",
    )
    bundle.reconcile_and_recover(actor="operator")
    bundle.runtime.start(actor="operator")
    gate = MarketDataReadinessGate()
    gate.attest(
        MarketDataReadinessAttestation(
            "mock",
            "session-1",
            "initial",
            "ticks-v1",
            decision_time - timedelta(minutes=1),
            decision_time + timedelta(minutes=10),
        )
    )
    runner = RealDataPaperRunner(
        bundle=bundle,
        instrument=Instrument("510300", "SSE", "ETF", 100, Decimal("0.001")),
        trading_rule=TradingRule(
            "etf",
            "SSE",
            "ETF",
            day,
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
            (TradingDayStatus("SSE", day, TradingStatus.OPEN, day, "fixture"),)
        ),
        strategy=Target100(),
        strategy_id="target100",
        strategy_version="1.0.0",
        strategy_spec_hash="strategy-spec-hash",
        portfolio_id="paper",
        data_version="ticks-v1",
        provider="mock",
        bar_interval="1m",
        market_readiness=gate,
        clock=lambda: decision_time + timedelta(seconds=2),
    )
    return bundle, RecordedPaperSession(root=tmp_path / name, runner=runner), runner


def tick(event_id: str, sequence: int, timestamp: datetime) -> CanonicalTick:
    return CanonicalTick(
        event_id,
        "mock",
        "510300",
        "SSE",
        timestamp,
        timestamp,
        sequence,
        Decimal("10"),
        Decimal(sequence * 100),
        Decimal(sequence * 1000),
        Decimal("9.999"),
        Decimal("10.001"),
        Decimal("100"),
        Decimal("100"),
        "ticks-v1",
        timestamp,
        ingest_ordinal=sequence,
        session_nonce="session-1",
    )


def test_record_quality_bar_and_causal_paper_replay_converge(tmp_path):
    first_time = datetime(2026, 8, 12, 1, 30, 59, tzinfo=UTC)
    second_time = datetime(2026, 8, 12, 1, 31, 1, tzinfo=UTC)

    outcomes = []
    for name in ("online", "replay"):
        bundle, session, _ = build_session(tmp_path, name)
        first = session.on_tick(
            tick(f"{name}-1", 1, first_time),
            watermark=datetime(2026, 8, 12, 1, 31, tzinfo=UTC),
        )
        assert len(first.bars) == 1
        assert first.result.trade is None
        assert first.canonical_path.is_file()

        second = session.on_tick(
            tick(f"{name}-2", 2, second_time),
            watermark=datetime(2026, 8, 12, 1, 31, 30, tzinfo=UTC),
        )
        assert second.bars == ()
        assert second.result.trade is not None
        assert second.result.trade.timestamp > first.bars[0].available_at
        assert session.verify_raw_chain()
        outcomes.append(
            (
                bundle.ledger.account.cash,
                bundle.ledger.account.position_quantity("510300"),
                second.result.trade.price,
                second.result.trade.quantity,
            )
        )

    assert outcomes == [
        (Decimal("99000"), 100, Decimal("10"), 100),
        (Decimal("99000"), 100, Decimal("10"), 100),
    ]


def test_recording_failure_latches_paper_runtime(tmp_path):
    bundle, session, _ = build_session(tmp_path, "failure")
    value = tick("bad", 1, datetime(2026, 8, 12, 1, 30, 59, tzinfo=UTC))
    session.on_tick(
        value,
        watermark=datetime(2026, 8, 12, 1, 31, tzinfo=UTC),
    )

    try:
        session.on_tick(
            value,
            watermark=datetime(2026, 8, 12, 1, 31, 1, tzinfo=UTC),
        )
    except ValueError as error:
        assert "DUPLICATE" in str(error)
    else:
        raise AssertionError("duplicate market event must be rejected")
    assert bundle.risk.state.value == "HALTED"
    assert bundle.metrics.reconciliation_status == "FAILED"


def test_recorded_session_serializes_concurrent_provider_callbacks(tmp_path):
    bundle, session, _ = build_session(tmp_path, "concurrent")
    start = Barrier(3)
    errors = []

    def invoke(value, watermark):
        start.wait()
        try:
            session.on_tick(value, watermark=watermark)
        except Exception as error:
            errors.append(error)

    first_time = datetime(2026, 8, 12, 1, 30, 59, tzinfo=UTC)
    second_time = datetime(2026, 8, 12, 1, 31, 1, tzinfo=UTC)
    threads = (
        Thread(
            target=invoke,
            args=(
                tick("concurrent-1", 1, first_time),
                datetime(2026, 8, 12, 1, 31, tzinfo=UTC),
            ),
        ),
        Thread(
            target=invoke,
            args=(
                tick("concurrent-2", 2, second_time),
                datetime(2026, 8, 12, 1, 31, 30, tzinfo=UTC),
            ),
        ),
    )
    for thread in threads:
        thread.start()
    start.wait()
    for thread in threads:
        thread.join()

    # Either callback may acquire the lock first. Out-of-ingest-order delivery is
    # rejected and halts safely; ordered delivery creates at most one target fill.
    assert bundle.ledger.account.position_quantity("510300") in {0, 100}
    assert len(bundle.gateway.applied_trades) <= 1
    if errors:
        assert bundle.risk.state.value == "HALTED"


def test_recorder_owns_ingest_ordinal_across_session_restart(tmp_path):
    bundle, session, runner = build_session(tmp_path, "restart")
    first_time = datetime(2026, 8, 12, 1, 30, 59, tzinfo=UTC)
    first = session.on_tick(
        tick("restart-1", 0, first_time),
        watermark=datetime(2026, 8, 12, 1, 31, tzinfo=UTC),
    )
    assert "ingest-00000000000000000000" in str(first.canonical_path)

    restarted_session = RecordedPaperSession(root=tmp_path / "restart", runner=runner)
    second = restarted_session.on_tick(
        tick("restart-2", 1, datetime(2026, 8, 12, 1, 31, 1, tzinfo=UTC)),
        watermark=datetime(2026, 8, 12, 1, 31, 30, tzinfo=UTC),
    )
    assert "ingest-00000000000000000001" in str(second.canonical_path)
    assert bundle.ledger.account.position_quantity("510300") == 100


def test_recorder_preserves_provider_global_ingest_order(tmp_path):
    _, session, _ = build_session(tmp_path, "global-order")
    observed_at = datetime(2026, 8, 12, 1, 30, 59, tzinfo=UTC)
    result = session.on_tick(
        replace(tick("global-order-1", 1, observed_at), ingest_ordinal=42),
        watermark=datetime(2026, 8, 12, 1, 31, tzinfo=UTC),
    )
    restored = CanonicalTickStore(
        tmp_path / "global-order" / "canonical"
    ).read(result.canonical_path, result.canonical_checksum)
    assert restored[0].ingest_ordinal == 42


def test_restart_replays_raw_event_crash_before_session_state(tmp_path, monkeypatch):
    bundle, session, runner = build_session(tmp_path, "raw-crash")
    original = session._set_state  # type: ignore[attr-defined]
    crashed = False

    def crash_once(*args, **kwargs):
        nonlocal crashed
        if not crashed:
            crashed = True
            raise SystemExit("simulated process death after raw fsync")
        return original(*args, **kwargs)

    monkeypatch.setattr(session, "_set_state", crash_once)
    first_time = datetime(2026, 8, 12, 1, 30, 59, tzinfo=UTC)
    with pytest.raises(SystemExit, match="process death"):
        session.on_tick(
            tick("raw-crash-1", 0, first_time),
            watermark=datetime(2026, 8, 12, 1, 31, tzinfo=UTC),
        )

    recovered = RecordedPaperSession(root=tmp_path / "raw-crash", runner=runner)
    result = recovered.on_tick(
        tick("raw-crash-2", 1, datetime(2026, 8, 12, 1, 31, 1, tzinfo=UTC)),
        watermark=datetime(2026, 8, 12, 1, 31, 30, tzinfo=UTC),
    )
    assert result.result.trade is not None
    assert bundle.ledger.account.position_quantity("510300") == 100


def test_restart_refuses_ambiguous_crash_inside_runner(tmp_path, monkeypatch):
    bundle, session, runner = build_session(tmp_path, "runner-crash")
    monkeypatch.setattr(
        runner,
        "on_tick",
        lambda _tick: (_ for _ in ()).throw(SystemExit("runner process death")),
    )
    with pytest.raises(SystemExit, match="runner process death"):
        session.on_tick(
            tick(
                "runner-crash-1",
                0,
                datetime(2026, 8, 12, 1, 30, 59, tzinfo=UTC),
            ),
            watermark=datetime(2026, 8, 12, 1, 31, tzinfo=UTC),
        )

    with pytest.raises(RuntimeError, match="explicit runtime reconciliation"):
        RecordedPaperSession(root=tmp_path / "runner-crash", runner=runner)
    assert bundle.risk.state.value == "HALTED"
    assert bundle.metrics.reconciliation_status == "FAILED"
