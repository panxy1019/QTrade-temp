from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class TradingStatus(StrEnum):
    OPEN = "OPEN"
    SUSPENDED = "SUSPENDED"
    CLOSED = "CLOSED"


@dataclass(frozen=True, slots=True)
class TradingDayStatus:
    venue: str
    trading_day: date
    status: TradingStatus
    known_from: date
    source: str


class TradingCalendar:
    def __init__(self, entries: tuple[TradingDayStatus, ...]) -> None:
        self._entries = entries

    def status(self, venue: str, trading_day: date, *, known_on: date) -> TradingStatus:
        matches = [
            entry
            for entry in self._entries
            if entry.venue == venue
            and entry.trading_day == trading_day
            and entry.known_from <= known_on
        ]
        if len(matches) != 1:
            raise LookupError("trading-day status is missing or ambiguous")
        return matches[0].status

    def advance_open_days(
        self, venue: str, trading_day: date, sessions: int, *, known_on: date
    ) -> date:
        if sessions < 0:
            raise ValueError("sessions cannot be negative")
        open_days = sorted(
            entry.trading_day
            for entry in self._entries
            if entry.venue == venue
            and entry.status is TradingStatus.OPEN
            and entry.known_from <= known_on
            and entry.trading_day >= trading_day
        )
        if trading_day not in open_days:
            raise LookupError("starting trading day is not a verified open session")
        index = open_days.index(trading_day) + sessions
        if index >= len(open_days):
            raise LookupError("calendar does not cover required sellability session")
        return open_days[index]
