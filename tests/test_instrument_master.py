from datetime import date
from decimal import Decimal

import pytest

from qtrade.accounting import SellabilityLedger
from qtrade.domain import Instrument
from qtrade.instruments import (
    InstrumentMaster,
    InstrumentRecord,
    TradingCalendar,
    TradingDayStatus,
    TradingRule,
    TradingRuleBook,
    TradingStatus,
)


def domestic_etf_rule() -> TradingRule:
    return TradingRule(
        "sse-domestic-etf-2026",
        "SSE",
        "DOMESTIC_EQUITY_ETF",
        date(2026, 7, 6),
        None,
        100,
        100,
        Decimal("0.001"),
        Decimal("0.10"),
        1,
        "https://www.sse.com.cn/official",
        "2026",
    )


def test_instrument_master_resolves_point_in_time_rule():
    master = InstrumentMaster(TradingRuleBook((domestic_etf_rule(),)))
    master.add(
        InstrumentRecord(
            Instrument("510300", "SSE", "ETF", 100, Decimal("0.001")),
            "ETF",
            date(2012, 1, 1),
            None,
            "DOMESTIC_EQUITY_ETF",
            date(2011, 12, 1),
        )
    )
    assert (
        master.trading_rule(
            "510300", date(2026, 8, 12), known_on=date(2026, 8, 12)
        ).sellability_lag_days
        == 1
    )
    with pytest.raises(LookupError):
        master.resolve("510300", date(2010, 1, 1), known_on=date(2010, 1, 1))


def test_calendar_fails_closed_and_sellability_distinguishes_t0_t1():
    calendar = TradingCalendar(
        (
            TradingDayStatus(
                "SSE",
                date(2026, 8, 12),
                TradingStatus.OPEN,
                date(2025, 12, 20),
                "exchange-calendar",
            ),
        )
    )
    assert (
        calendar.status("SSE", date(2026, 8, 12), known_on=date(2026, 8, 12)) is TradingStatus.OPEN
    )
    with pytest.raises(LookupError):
        calendar.status("SSE", date(2026, 8, 13), known_on=date(2026, 8, 13))

    lots = SellabilityLedger()
    lots.acquire("DOMESTIC", 100, date(2026, 8, 12), sellability_lag_days=1)
    lots.acquire("MONEY_ETF", 100, date(2026, 8, 12), sellability_lag_days=0)
    assert lots.sellable_quantity("DOMESTIC", date(2026, 8, 12)) == 0
    assert lots.sellable_quantity("DOMESTIC", date(2026, 8, 13)) == 100
    assert lots.sellable_quantity("MONEY_ETF", date(2026, 8, 12)) == 100
