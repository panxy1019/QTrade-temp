from datetime import date, datetime, timedelta, timezone
from decimal import Decimal

import pytest

from qtrade.data import Bar
from qtrade.research import run_minute_overlay_backtest
from qtrade.runtime import MinuteOverlayPolicy
from scripts.run_sina_minute_overlay_backtest import _stock_sector_groups

SHANGHAI = timezone(timedelta(hours=8))


def policy() -> MinuteOverlayPolicy:
    return MinuteOverlayPolicy(
        short_window=2,
        long_window=3,
        volatility_window=2,
        maximum_minute_volatility=Decimal("1"),
        maximum_intraday_drawdown=Decimal("0.05"),
        reduction_multiplier=Decimal("0.5"),
        cooldown_bars=0,
    )


def bars_for(symbol: str, closes: list[str]) -> list[Bar]:
    trading_day = date(2026, 8, 25)
    result = []
    previous = Decimal(closes[0])
    for index, raw_close in enumerate(closes):
        close = Decimal(raw_close)
        timestamp = datetime(2026, 8, 25, 9, 31, tzinfo=SHANGHAI) + timedelta(
            minutes=index
        )
        result.append(
            Bar(
                symbol=symbol,
                exchange="SSE",
                timestamp=timestamp,
                trading_day=trading_day,
                open=previous,
                high=max(previous, close),
                low=min(previous, close),
                close=close,
                volume=Decimal("100"),
            )
        )
        previous = close
    return result


def test_signal_at_close_only_changes_exposure_at_next_open() -> None:
    closes = ["10"] * 20 + ["9", "8"]
    result = run_minute_overlay_backtest(
        bars_for("510300", closes),
        policy_id="causal-test",
        policy=policy(),
        cost_bps=0,
    )

    assert result.overlay_equity[19] == pytest.approx(1)
    assert result.overlay_equity[20] == pytest.approx(0.9)
    assert result.overlay_equity[21] == pytest.approx(0.9)
    assert result.base_equity[21] == pytest.approx(0.8)
    assert result.maximum_exposure <= 1
    assert result.reason_counts["INTRADAY_DRAWDOWN_LIMIT"] >= 1


def test_multi_symbol_curves_are_equal_weighted_and_cost_bound() -> None:
    first = bars_for("510300", ["10"] * 20 + ["10.1", "10.2"])
    second = bars_for("510500", ["20"] * 20 + ["20.1", "20.2"])
    records = sorted([*first, *second], key=lambda bar: (bar.timestamp, bar.symbol))
    free = run_minute_overlay_backtest(
        records, policy_id="free", policy=policy(), cost_bps=0
    )
    costly = run_minute_overlay_backtest(
        records, policy_id="costly", policy=policy(), cost_bps=10
    )

    assert len(free.symbols) == 2
    assert costly.base_equity[-1] < free.base_equity[-1]
    assert costly.overlay_equity[-1] < free.overlay_equity[-1]
    assert costly.metrics()["overlay"].point_count == len(first)


def test_backtest_rejects_unsynchronized_symbols() -> None:
    first = bars_for("510300", ["10"] * 5)
    second = bars_for("510500", ["20"] * 4)

    with pytest.raises(ValueError, match="synchronized"):
        run_minute_overlay_backtest(
            [*first, *second], policy_id="bad", policy=policy(), cost_bps=0
        )


def test_stock_sector_groups_remove_every_stock_in_each_sector() -> None:
    evidence = {
        "universe": {
            "stocks": [
                {"symbol": "600001", "sector_code": "s1", "sector_name": "one"},
                {"symbol": "600002", "sector_code": "s1", "sector_name": "one"},
                {"symbol": "600003", "sector_code": "s2", "sector_name": "two"},
            ]
        }
    }

    groups = _stock_sector_groups(evidence, {"510300", "600001", "600002", "600003"})

    assert groups == (
        ("s1", "one", ("600001", "600002")),
        ("s2", "two", ("600003",)),
    )
