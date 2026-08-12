from __future__ import annotations

from dataclasses import dataclass
from datetime import date

from qtrade.domain import Instrument
from qtrade.instruments.rules import TradingRule, TradingRuleBook


@dataclass(frozen=True, slots=True)
class InstrumentRecord:
    instrument: Instrument
    name: str
    listed_on: date
    delisted_on: date | None
    rule_class: str
    known_from: date


class InstrumentMaster:
    def __init__(self, rules: TradingRuleBook) -> None:
        self._rules = rules
        self._records: dict[str, list[InstrumentRecord]] = {}

    def add(self, record: InstrumentRecord) -> None:
        self._records.setdefault(record.instrument.symbol, []).append(record)

    def resolve(self, symbol: str, on_date: date, *, known_on: date) -> InstrumentRecord:
        matches = [
            item
            for item in self._records.get(symbol, [])
            if item.known_from <= known_on
            and item.listed_on <= on_date
            and (item.delisted_on is None or on_date <= item.delisted_on)
        ]
        if len(matches) != 1:
            raise LookupError(f"expected one point-in-time instrument record; found {len(matches)}")
        return matches[0]

    def trading_rule(self, symbol: str, on_date: date, *, known_on: date) -> TradingRule:
        record = self.resolve(symbol, on_date, known_on=known_on)
        return self._rules.resolve(record.instrument.exchange, record.rule_class, on_date)
