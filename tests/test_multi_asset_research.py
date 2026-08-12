from datetime import UTC, date, datetime, timedelta
from decimal import Decimal

import pytest

from qtrade.data import Bar
from qtrade.research import (
    DailyMultiAssetBacktester,
    EqualWeightBuyHold,
    MomentumAllocation,
    write_experiment_artifacts,
)


def bars() -> list[Bar]:
    result = []
    start = date(2026, 1, 1)
    for index in range(8):
        for symbol, base, step in (
            ("510300", Decimal("10"), Decimal("1")),
            ("510500", Decimal("10"), Decimal("-0.2")),
        ):
            close = base + step * index
            result.append(
                Bar(
                    symbol,
                    "SSE",
                    datetime(2026, 1, 1, 7, tzinfo=UTC) + timedelta(days=index),
                    start + timedelta(days=index),
                    close,
                    close,
                    close,
                    close,
                    Decimal("10000"),
                )
            )
    result.sort(key=lambda value: (value.timestamp, value.symbol))
    return result


def test_buy_and_hold_signal_executes_only_at_following_open():
    result = DailyMultiAssetBacktester(
        initial_cash=Decimal("100000"),
        transaction_cost_rate=Decimal("0"),
        slippage_rate=Decimal("0"),
    ).run(bars(), EqualWeightBuyHold(("510300",), reserve=Decimal("0")))
    assert result.trades[0].trading_day == date(2026, 1, 2)
    assert result.equity_curve[0] == Decimal("100000")
    assert result.terminal_positions == (("510300", 9000),)


def test_momentum_rotation_uses_prior_closes_and_writes_required_artifacts(tmp_path):
    model = MomentumAllocation((2, 3), top_k=1, rebalance_every=1, reserve=Decimal("0"))
    result = DailyMultiAssetBacktester(
        initial_cash=Decimal("100000"),
        transaction_cost_rate=Decimal("0"),
        slippage_rate=Decimal("0"),
    ).run(bars(), model)
    assert result.trades[0].trading_day == date(2026, 1, 5)
    assert {trade.symbol for trade in result.trades} == {"510300"}
    artifacts = write_experiment_artifacts(result, tmp_path / "artifacts")
    assert {path.stem for path in artifacts} == {
        "equity", "drawdown", "trades", "positions", "risk", "logs"
    }


def test_missing_bar_carries_last_close_without_fabricating_a_trade():
    records = [
        bar
        for bar in bars()
        if not (bar.symbol == "510300" and bar.trading_day == date(2026, 1, 3))
    ]
    result = DailyMultiAssetBacktester(
        initial_cash=Decimal("100000"),
        transaction_cost_rate=Decimal("0"),
        slippage_rate=Decimal("0"),
    ).run(records, EqualWeightBuyHold(("510300",), reserve=Decimal("0")))
    assert result.equity_curve[2] == result.equity_curve[1]
    assert all(trade.trading_day != date(2026, 1, 3) for trade in result.trades)


def test_stale_holding_beyond_policy_fails_research_result():
    records = [
        bar
        for bar in bars()
        if not (
            bar.symbol == "510300"
            and bar.trading_day in {date(2026, 1, 3), date(2026, 1, 4)}
        )
    ]
    with pytest.raises(ValueError, match="stale valuation policy"):
        DailyMultiAssetBacktester(
            initial_cash=Decimal("100000"),
            transaction_cost_rate=Decimal("0"),
            slippage_rate=Decimal("0"),
            maximum_stale_sessions=1,
        ).run(records, EqualWeightBuyHold(("510300",), reserve=Decimal("0")))


def test_higher_transaction_cost_reduces_terminal_equity():
    model = EqualWeightBuyHold(("510300",), reserve=Decimal("0"))
    low = DailyMultiAssetBacktester(
        initial_cash=Decimal("100000"),
        transaction_cost_rate=Decimal("0.001"),
        slippage_rate=Decimal("0"),
    ).run(bars(), model)
    high = DailyMultiAssetBacktester(
        initial_cash=Decimal("100000"),
        transaction_cost_rate=Decimal("0.01"),
        slippage_rate=Decimal("0"),
    ).run(bars(), model)
    assert high.equity_curve[-1] < low.equity_curve[-1]
