from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

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
