from datetime import date
from decimal import Decimal

import pytest

from qtrade.instruments import TradingRule, TradingRuleBook

SOURCE = "https://www.sse.com.cn/lawandrules/example"


def rule(rule_id: str, start: date, end: date | None, limit: str) -> TradingRule:
    return TradingRule(
        rule_id=rule_id,
        venue="SSE",
        instrument_class="MAIN_RISK_WARNING",
        effective_from=start,
        effective_to=end,
        buy_lot=100,
        quantity_step=100,
        price_tick=Decimal("0.01"),
        price_limit_fraction=Decimal(limit),
        sellability_lag_days=1,
        source_url=SOURCE,
        source_version="official-rulebook",
    )


def test_rulebook_resolves_across_effective_date_boundary():
    book = TradingRuleBook(
        (
            rule("pre-2026", date(2023, 1, 1), date(2026, 7, 5), "0.05"),
            rule("2026-current", date(2026, 7, 6), None, "0.10"),
        )
    )
    assert book.resolve("SSE", "MAIN_RISK_WARNING", date(2026, 7, 5)).rule_id == "pre-2026"
    assert book.resolve("SSE", "MAIN_RISK_WARNING", date(2026, 7, 6)).rule_id == "2026-current"


def test_rulebook_fails_closed_for_missing_or_overlapping_rules():
    book = TradingRuleBook((rule("a", date(2026, 1, 1), None, "0.10"),))
    with pytest.raises(LookupError):
        book.resolve("SZSE", "MAIN_RISK_WARNING", date(2026, 1, 1))
    overlap = TradingRuleBook(
        (
            rule("a", date(2026, 1, 1), None, "0.10"),
            rule("b", date(2026, 2, 1), None, "0.10"),
        )
    )
    with pytest.raises(LookupError):
        overlap.resolve("SSE", "MAIN_RISK_WARNING", date(2026, 3, 1))
