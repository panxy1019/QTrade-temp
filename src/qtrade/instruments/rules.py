from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from decimal import Decimal


@dataclass(frozen=True, slots=True)
class TradingRule:
    rule_id: str
    venue: str
    instrument_class: str
    effective_from: date
    effective_to: date | None
    buy_lot: int
    quantity_step: int
    price_tick: Decimal
    price_limit_fraction: Decimal | None
    sellability_lag_days: int
    source_url: str
    source_version: str

    def __post_init__(self) -> None:
        if not all((self.rule_id, self.venue, self.instrument_class, self.source_url)):
            raise ValueError("rule identity, class, venue, and source are required")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("rule effective interval is invalid")
        if self.buy_lot <= 0 or self.quantity_step <= 0 or self.price_tick <= 0:
            raise ValueError("lot, step, and tick must be positive")
        if self.sellability_lag_days < 0:
            raise ValueError("sellability lag cannot be negative")


class TradingRuleBook:
    def __init__(self, rules: tuple[TradingRule, ...]) -> None:
        self._rules = rules

    def resolve(self, venue: str, instrument_class: str, on_date: date) -> TradingRule:
        matches = [
            rule
            for rule in self._rules
            if rule.venue == venue
            and rule.instrument_class == instrument_class
            and rule.effective_from <= on_date
            and (rule.effective_to is None or on_date <= rule.effective_to)
        ]
        if len(matches) != 1:
            raise LookupError(
                f"expected one trading rule for {venue}/{instrument_class}/{on_date}; "
                f"found {len(matches)}"
            )
        return matches[0]
