from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

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
from qtrade.market_data import AvailableBar, CanonicalTick
from qtrade.risk import RiskLimits
from qtrade.runtime import RealDataPaperRunner, build_paper_runtime


class Target100:
    def on_bar(self, bar: Bar, current_quantity: int) -> int:
        return 100


def test_finalized_bar_signal_executes_only_on_later_observed_tick(tmp_path):
    day = date(2026, 8, 12)
    calendar = TradingCalendar(
        (TradingDayStatus("SSE", day, TradingStatus.OPEN, day, "fixture"),)
    )
    bundle = build_paper_runtime(
        mode=RuntimeMode.PAPER,
        opening_cash=Decimal("100000"),
        limits=RiskLimits(Decimal("50000"), 10000, Decimal("100000")),
        fees=FeeSchedule("test", Decimal("0"), Decimal("0")),
        journal_path=tmp_path / "runtime.db",
    )
    bundle.reconcile_and_recover(actor="operator")
    bundle.runtime.start(actor="operator")
    runner = RealDataPaperRunner(
        bundle=bundle,
        instrument=Instrument("510300", "SSE", "ETF", 100, Decimal("0.001")),
        trading_rule=TradingRule(
            "etf", "SSE", "ETF", day, None, 100, 100, Decimal("0.001"),
            Decimal("0.1"), 0, "fixture://rule", "1"
        ),
        calendar=calendar,
        strategy=Target100(),
        strategy_id="target100",
        portfolio_id="paper",
    )
    bar = Bar(
        "510300", "SSE", datetime(2026, 8, 12, 1, 30, tzinfo=UTC), day,
        Decimal("10"), Decimal("11"), Decimal("9"), Decimal("10.5"), Decimal("100")
    )
    decided_at = datetime(2026, 8, 12, 1, 31, tzinfo=UTC)
    runner.on_finalized_bar(AvailableBar(bar, decided_at, 1, 9))
    def make_tick(event_id: str, received_at: datetime, ordinal: int) -> CanonicalTick:
        return CanonicalTick(
            event_id,
            "mock",
            "510300",
            "SSE",
            datetime(2026, 8, 12, 1, 30, 59, tzinfo=UTC),
            received_at,
            ordinal,
            Decimal("10.6"),
            Decimal("100"),
            Decimal("1060"),
            Decimal("10.599"),
            Decimal("10.601"),
            Decimal("10"),
            Decimal("10"),
            "ticks-v1",
            received_at,
            ingest_ordinal=ordinal,
        )

    before = make_tick("before", decided_at - timedelta(milliseconds=1), 10)
    assert runner.on_tick(before).trade is None
    after = make_tick("after", decided_at + timedelta(milliseconds=1), 11)
    result = runner.on_tick(after)
    assert result.trade is not None and result.trade.price == Decimal("10.6")
    assert result.trade.timestamp >= decided_at
    assert bundle.ledger.account.position_quantity("510300") == 100


def test_post_submit_fill_start_failure_halts_and_blocks_restart_recovery(
    tmp_path, monkeypatch
):
    day = date(2026, 8, 12)
    calendar = TradingCalendar(
        (TradingDayStatus("SSE", day, TradingStatus.OPEN, day, "fixture"),)
    )

    def build():
        return build_paper_runtime(
            mode=RuntimeMode.PAPER,
            opening_cash=Decimal("100000"),
            limits=RiskLimits(Decimal("50000"), 10000, Decimal("100000")),
            fees=FeeSchedule("test", Decimal("0"), Decimal("0")),
            journal_path=tmp_path / "failure.db",
        )

    def runner(bundle):
        return RealDataPaperRunner(
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
            calendar=calendar,
            strategy=Target100(),
            strategy_id="target100",
            portfolio_id="paper",
        )

    bundle = build()
    bundle.reconcile_and_recover(actor="operator")
    bundle.runtime.start(actor="operator")
    value = runner(bundle)
    bar = Bar(
        "510300",
        "SSE",
        datetime(2026, 8, 12, 1, 30, tzinfo=UTC),
        day,
        Decimal("10"),
        Decimal("11"),
        Decimal("9"),
        Decimal("10.5"),
        Decimal("100"),
    )
    decided_at = datetime(2026, 8, 12, 1, 31, tzinfo=UTC)
    value.on_finalized_bar(AvailableBar(bar, decided_at, 1, 9))
    tick_value = CanonicalTick(
        "after",
        "mock",
        "510300",
        "SSE",
        decided_at,
        decided_at + timedelta(milliseconds=1),
        10,
        Decimal("10.6"),
        Decimal("100"),
        Decimal("1060"),
        Decimal("10.599"),
        Decimal("10.601"),
        Decimal("10"),
        Decimal("10"),
        "ticks-v1",
        decided_at + timedelta(milliseconds=1),
        ingest_ordinal=10,
    )
    monkeypatch.setattr(
        bundle.journal,
        "begin_fill",
        lambda *_args: (_ for _ in ()).throw(OSError("fill journal unavailable")),
    )
    with pytest.raises(OSError, match="fill journal unavailable"):
        value.on_tick(tick_value)
    assert bundle.risk.state.value == "HALTED"
    assert bundle.metrics.reconciliation_status == "FAILED"
    assert bundle.oms.orders[0].status.value == "ACCEPTED"

    restarted = build()
    with pytest.raises(ValueError, match="uncertain"):
        restarted.reconcile_and_recover(actor="operator")


def test_realtime_checkpoint_failure_cannot_skip_incident_latch(tmp_path, monkeypatch):
    day = date(2026, 8, 12)
    bundle = build_paper_runtime(
        mode=RuntimeMode.PAPER,
        opening_cash=Decimal("100000"),
        limits=RiskLimits(Decimal("50000"), 10000, Decimal("100000")),
        fees=FeeSchedule("test", Decimal("0"), Decimal("0")),
        journal_path=tmp_path / "checkpoint.db",
    )
    bundle.reconcile_and_recover(actor="operator")
    bundle.runtime.start(actor="operator")
    value = RealDataPaperRunner(
        bundle=bundle,
        instrument=Instrument("510300", "SSE", "ETF", 100, Decimal("0.001")),
        trading_rule=TradingRule(
            "etf", "SSE", "ETF", day, None, 100, 100, Decimal("0.001"),
            Decimal("0.1"), 0, "fixture://rule", "1"
        ),
        calendar=TradingCalendar(
            (TradingDayStatus("SSE", day, TradingStatus.OPEN, day, "fixture"),)
        ),
        strategy=Target100(),
        strategy_id="target100",
        portfolio_id="paper",
    )
    monkeypatch.setattr(
        bundle.journal,
        "save_state",
        lambda *_args: (_ for _ in ()).throw(OSError("checkpoint unavailable")),
    )
    with pytest.raises(OSError, match="checkpoint unavailable"):
        value.on_finalized_bar(
            AvailableBar(
                Bar(
                    "510300", "SSE", datetime(2026, 8, 12, 1, 30, tzinfo=UTC), day,
                    Decimal("10"), Decimal("10"), Decimal("10"), Decimal("10"),
                    Decimal("100")
                ),
                datetime(2026, 8, 12, 1, 31, tzinfo=UTC),
                1,
                1,
            )
        )
    assert bundle.risk.state.value == "HALTED"
    assert bundle.metrics.reconciliation_status == "FAILED"
