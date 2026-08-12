from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from qtrade.backtest import BacktestEngine
from qtrade.data import Bar, DataManifest, QualityStatus
from qtrade.domain import Instrument
from qtrade.execution import FeeSchedule
from qtrade.instruments import TradingRule
from qtrade.risk import RiskLimits
from qtrade.strategies import BuyAndHoldStrategy, MovingAverageTrendStrategy


def bars(closes: list[str]) -> list[Bar]:
    return [
        Bar(
            symbol="510300",
            exchange="SSE",
            timestamp=datetime(2026, 1, index + 2, 7, tzinfo=UTC),
            trading_day=date(2026, 1, index + 2),
            open=Decimal(close),
            high=Decimal(close),
            low=Decimal(close),
            close=Decimal(close),
            volume=Decimal("10000"),
        )
        for index, close in enumerate(closes)
    ]


def engine(slippage: str = "0") -> BacktestEngine:
    return BacktestEngine(
        instrument=Instrument("510300", "SSE", "ETF", 100, Decimal("0.001")),
        opening_cash=Decimal("100000"),
        fees=FeeSchedule("test", Decimal("0.0003"), Decimal("0.0005")),
        limits=RiskLimits(Decimal("100000"), 10000, Decimal("100000")),
        trading_rule=TradingRule(
            "test-etf",
            "SSE",
            "DOMESTIC_EQUITY_ETF",
            date(2020, 1, 1),
            None,
            100,
            100,
            Decimal("0.001"),
            Decimal("0.10"),
            1,
            "https://example.test/rule",
            "test",
        ),
        slippage=Decimal(slippage),
    )


def passed_manifest() -> DataManifest:
    return DataManifest(
        "test-bars-v1",
        "fixture",
        "1",
        datetime(2026, 1, 1, tzinfo=UTC),
        "fixture-checksum",
        QualityStatus.PASSED,
    )


def test_buy_and_hold_runs_through_shared_oms_and_accounting():
    backtest = engine()
    result = backtest.run(bars(["10", "11"]), BuyAndHoldStrategy(100), manifest=passed_manifest())
    assert result.trades == 1
    assert result.final_position == 100
    assert result.final_cash == Decimal("98899.67")
    assert any(event.event_type == "RISK_PASSED" for event in backtest.audit.events)


def test_trend_strategy_sells_with_costs_and_slippage():
    backtest = engine("0.01")
    result = backtest.run(
        bars(["10", "11", "9", "9"]),
        MovingAverageTrendStrategy(window=2, quantity=100),
        manifest=passed_manifest(),
    )
    assert result.trades == 2
    assert result.final_position == 0
    assert result.total_fees == Decimal("0.99")
    assert result.final_cash == Decimal("99997.01")


def test_close_signal_executes_no_earlier_than_next_bar_open():
    backtest = engine()
    result = backtest.run(bars(["10"]), BuyAndHoldStrategy(100), manifest=passed_manifest())
    assert result.trades == 0
    assert result.final_position == 0


def test_backtest_rejects_reuse_and_unordered_bars():
    backtest = engine()
    data = bars(["10", "11"])
    backtest.run(data, BuyAndHoldStrategy(100), manifest=passed_manifest())
    with pytest.raises(ValueError, match="single-use"):
        backtest.run(data, BuyAndHoldStrategy(100), manifest=passed_manifest())

    unordered = engine()
    with pytest.raises(ValueError, match="chronological"):
        unordered.run(list(reversed(data)), BuyAndHoldStrategy(100), manifest=passed_manifest())


def test_backtest_rejects_unverified_data_manifest():
    pending = DataManifest(
        "pending",
        "fixture",
        "1",
        datetime(2026, 1, 1, tzinfo=UTC),
        "pending",
        QualityStatus.PENDING,
    )
    with pytest.raises(ValueError, match="PASSED"):
        engine().run(bars(["10"]), BuyAndHoldStrategy(100), manifest=pending)
