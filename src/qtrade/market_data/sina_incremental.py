"""Parse captured bounded observations; no synthetic amounts, clocks or price-basis joins."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import date, datetime
from datetime import time as wall_time
from decimal import Decimal, InvalidOperation
from typing import Any, Literal

from qtrade.data import Bar, BarQualityGate
from qtrade.instruments.observed import instrument_id
from qtrade.market_data.akshare import SHANGHAI
from qtrade.market_data.sina_minute import SINA_FIVE_MINUTE_SESSION_TIMES
from qtrade.monitoring.data_freshness import Calendar

PRICE_BASIS = "SINA_NATIVE_ADJUSTMENT_UNCERTIFIED"


def number(value: Any, *, allow_zero: bool = False) -> Decimal:
    try:
        result = Decimal(str(value))
    except InvalidOperation as error:
        raise ValueError("INVALID_SOURCE_NUMBER") from error
    if not result.is_finite() or result < 0 or (result == 0 and not allow_zero):
        raise ValueError("INVALID_SOURCE_NUMBER")
    return result


@dataclass(frozen=True)
class IncrementalBar:
    bar: Bar
    frequency: Literal["day", "5min"]
    amount: Decimal | None
    vwap: Decimal | None
    source_clock: datetime | None = None
    amount_basis: str = "VENDOR_CLAIM_UNVERIFIED"
    price_basis: str = PRICE_BASIS

    def row(self) -> dict[str, Any]:
        bar = self.bar
        return {
            "symbol": bar.symbol, "exchange": bar.exchange, "frequency": self.frequency,
            "timestamp": bar.timestamp.isoformat(), "trading_day": str(bar.trading_day),
            **{key: str(getattr(bar, key)) for key in (
                "open", "high", "low", "close", "volume",
            )},
            "amount": str(self.amount) if self.amount is not None else None,
            "vwap": str(self.vwap) if self.vwap is not None else None,
            "source_clock": self.source_clock.isoformat() if self.source_clock else None,
            "amount_basis": self.amount_basis, "price_basis": self.price_basis,
            "completion_status": "CLOSED", "historical_available_at": None,
        }


def _amount(bar: Bar, amount: Decimal | None, kind: str) -> Decimal | None:
    if amount is None:
        return None
    if bar.volume == 0:
        if amount != 0:
            raise ValueError("AMOUNT_WITH_ZERO_VOLUME")
        return None
    if amount <= 0:
        raise ValueError("NONPOSITIVE_TRADED_AMOUNT")
    vwap = amount / bar.volume
    tolerance = Decimal("0.001") if kind == "ETF" else Decimal("0.01")
    if not bar.low - tolerance <= vwap <= bar.high + tolerance:
        raise ValueError("VWAP_OR_AMOUNT_VOLUME_UNIT_MISMATCH")
    return vwap


def completed_klines(
    raw: bytes, *, symbol: str, security_type: str,
    frequency: Literal["day", "5min"], requested_day: date, observed_at: datetime,
    calendar: Calendar, require_complete_session: bool = False,
) -> tuple[IncrementalBar, ...]:
    """Admit only the requested day; a missing bar never supplies a suspension reason."""
    identity, cutoff = _kline_context(symbol, security_type, frequency, requested_day,
                                      observed_at, calendar)
    return _decode_klines(raw, symbol, security_type, frequency, requested_day, identity, cutoff,
                          require_complete_session)


def _decode_klines(raw: bytes, symbol: str, security_type: str,
                   frequency: Literal['day', '5min'], requested_day: date, identity: str,
                   cutoff: datetime, require_complete_session: bool
                   ) -> tuple[IncrementalBar, ...]:
    if len(raw) > 2_000_000:
        raise ValueError("BOUNDED_SOURCE_RESPONSE_TOO_LARGE")
    return _completed_kline_rows(json.loads(raw), symbol, security_type, frequency,
                                 requested_day, identity, cutoff, require_complete_session)


class _CapturedKlineReader:
    """One export's bounded immutable calendar/identity memo; never caches source admission."""

    def __init__(self, calendar: Calendar):
        self._calendar = calendar
        self._identities: dict[tuple[str, str], str] = {}
        self._days: dict[date, bool] = {}
        self._cutoffs: dict[tuple[datetime, str], datetime] = {}

    def identity(self, symbol: str, security_type: str) -> str:
        key = (symbol, security_type)
        if key not in self._identities:
            value = instrument_id(symbol, security_type)
            if len(self._identities) >= 4096:
                return value
            self._identities[key] = value
        return self._identities[key]

    def is_open(self, day: date) -> bool:
        if day not in self._days:
            value = self._calendar.is_open(day)
            if len(self._days) >= 4096:
                return value
            self._days[day] = value
        return self._days[day]

    def expected(self, now: datetime, kind: str) -> datetime:
        # The concrete Calendar's cutoff is constant throughout one Shanghai
        # minute (including its 60-second lag). Custom calendars retain full calls.
        if type(self._calendar) is not Calendar:
            return self._calendar.expected(now, kind)
        key = (now.astimezone(SHANGHAI).replace(second=0, microsecond=0), kind)
        if key not in self._cutoffs:
            value = self._calendar.expected(now, kind)
            if len(self._cutoffs) >= 4096:
                return value
            self._cutoffs[key] = value
        return self._cutoffs[key]

    def parse(self, raw: bytes, *, symbol: str, security_type: str,
              frequency: Literal['day', '5min'], requested_day: date, observed_at: datetime,
              calendar: Calendar, require_complete_session: bool = False
              ) -> tuple[IncrementalBar, ...]:
        reader = self if calendar is self._calendar else None
        identity, cutoff = _kline_context(symbol, security_type, frequency, requested_day,
                                          observed_at, calendar, _reader=reader)
        return _decode_klines(raw, symbol, security_type, frequency, requested_day, identity,
                              cutoff, require_complete_session)


def completed_kline_rows(
    rows: list[dict[str, Any]], *, symbol: str, security_type: str,
    frequency: Literal["day", "5min"], requested_day: date, observed_at: datetime,
    calendar: Calendar, require_complete_session: bool = False,
) -> tuple[IncrementalBar, ...]:
    """Validate bounded constructed rows; captured responses must use completed_klines.

    The market-shard validator constructs one row after bounding every numeric
    string. It can reuse the same admission checks without a JSON round trip.
    This entry point does not replace raw-response byte, JSON or source replay checks.
    """
    identity, cutoff = _kline_context(symbol, security_type, frequency, requested_day,
                                      observed_at, calendar)
    return _completed_kline_rows(rows, symbol, security_type, frequency, requested_day,
                                 identity, cutoff, require_complete_session)


class _ConstructedKlineRows:
    """One shard's fixed context; rows still receive independent parser/quality checks."""

    def __init__(self, symbol: str, security_type: str, frequency: Literal['day', '5min'],
                 observed_at: datetime, calendar: Calendar):
        self._symbol, self._security_type, self._frequency = symbol, security_type, frequency
        self._observed_at, self._calendar = observed_at, calendar
        self._contexts: dict[date, tuple[str, datetime]] = {}

    def parse(self, rows: list[dict[str, Any]], requested_day: date
              ) -> tuple[IncrementalBar, ...]:
        if requested_day not in self._contexts:
            self._contexts[requested_day] = _kline_context(
                self._symbol, self._security_type, self._frequency, requested_day,
                self._observed_at, self._calendar)
        identity, cutoff = self._contexts[requested_day]
        return _completed_kline_rows(rows, self._symbol, self._security_type, self._frequency,
                                     requested_day, identity, cutoff, False)


def _kline_context(symbol: str, security_type: str, frequency: str, requested_day: date,
                   observed_at: datetime, calendar: Calendar, *,
                   _reader: _CapturedKlineReader | None = None) -> tuple[str, datetime]:
    identity = (_reader.identity(symbol, security_type) if _reader is not None else
                instrument_id(symbol, security_type))
    if observed_at.tzinfo is None or not (
            _reader.is_open(requested_day) if _reader is not None
            else calendar.is_open(requested_day)):
        raise ValueError("INVALID_INCREMENTAL_OBSERVATION_CLOCK_OR_DAY")
    if frequency not in {"day", "5min"}:
        raise ValueError("INVALID_INCREMENTAL_FREQUENCY")
    kind = 'daily' if frequency == 'day' else 'minute'
    cutoff = (_reader.expected(observed_at, kind) if _reader is not None else
              calendar.expected(observed_at, kind))
    if requested_day > cutoff.date():
        raise ValueError("REQUESTED_DAY_NOT_COMPLETE")
    return identity, cutoff


def _completed_kline_rows(
    payload: Any, symbol: str, security_type: str, frequency: Literal["day", "5min"],
    requested_day: date, identity: str, cutoff: datetime, require_complete_session: bool,
) -> tuple[IncrementalBar, ...]:
    if not isinstance(payload, list) or not 1 <= len(payload) <= 1970:
        raise ValueError("INVALID_BOUNDED_KLINE_RESPONSE")
    result = []
    for value in payload:
        if not isinstance(value, dict) or not isinstance(value.get("day"), str):
            raise ValueError("INVALID_KLINE_ROW")
        day = date.fromisoformat(value["day"][:10])
        if day != requested_day:
            continue
        if frequency == "day":
            if value["day"] != str(day):
                raise ValueError("INVALID_DAILY_SOURCE_CLOCK")
            stamp = datetime.combine(day, wall_time(15), SHANGHAI)
        else:
            stamp = datetime.fromisoformat(value["day"])
            if stamp.tzinfo is not None:
                raise ValueError("UNEXPECTED_VENDOR_MINUTE_TIMEZONE")
            stamp = stamp.replace(tzinfo=SHANGHAI)
            if stamp.time() not in SINA_FIVE_MINUTE_SESSION_TIMES:
                raise ValueError("INVALID_INCREMENTAL_SESSION_GRID")
            if stamp > cutoff:
                continue  # A source may include the still-forming candle.
        if not {"open", "high", "low", "close", "volume"} <= set(value):
            raise ValueError("MISSING_KLINE_FIELDS")
        bar = Bar(symbol, identity.split(".")[0], stamp, day,
                  open=number(value["open"]), high=number(value["high"]),
                  low=number(value["low"]), close=number(value["close"]),
                  volume=number(value["volume"], allow_zero=True))
        amount = (number(value["amount"], allow_zero=True)
                  if value.get("amount") is not None else None)
        result.append(IncrementalBar(bar, frequency, amount, _amount(bar, amount, security_type)))
    if not result:
        raise ValueError("NO_COMPLETED_REQUESTED_BARS_REASON_UNKNOWN")
    issues = BarQualityGate().validate([r.bar for r in result], expected_symbol=symbol)
    if issues:
        raise ValueError("INCREMENTAL_OHLCV_FAILED:" + ",".join(sorted({q.code for q in issues})))
    if frequency == "day" and len(result) != 1:
        raise ValueError("DUPLICATE_DAILY_SOURCE_BAR")
    if frequency == "5min" and require_complete_session and {
        r.bar.timestamp.time() for r in result
    } != set(SINA_FIVE_MINUTE_SESSION_TIMES):
        raise ValueError("INCOMPLETE_REQUESTED_MINUTE_SESSION")
    return tuple(result)


def completed_quotes(
    raw: bytes, *, requested: dict[str, str], requested_day: date,
    observed_at: datetime, calendar: Calendar,
) -> dict[str, IncrementalBar]:
    """A quote's update time is not a trade time; daily bars use the close boundary."""
    if (observed_at.tzinfo is None or not 1 <= len(requested) <= 50
            or requested_day > calendar.expected(observed_at, "daily").date()
            or not calendar.is_open(requested_day)):
        raise ValueError("INVALID_COMPLETED_QUOTE_REQUEST")
    if len(raw) > 200_000:
        raise ValueError("BOUNDED_QUOTE_RESPONSE_TOO_LARGE")
    result = {}
    for line in raw.decode("gb18030").splitlines():
        match = re.fullmatch(r'var hq_str_(sh|sz)(\d{6})="([^"\r\n]*)";', line.strip())
        if match is None:
            raise ValueError("UNKNOWN_QUOTE_RESPONSE_SCHEMA")
        market, symbol, text = match.groups()
        if symbol not in requested or symbol in result:
            raise ValueError("UNEXPECTED_OR_DUPLICATE_QUOTE_IDENTITY")
        kind = requested[symbol]
        identity = instrument_id(symbol, kind)
        if (market == "sh") != identity.startswith("SSE."):
            raise ValueError("QUOTE_EXCHANGE_MISMATCH")
        values = text.split(",")
        if len(values) != 34:
            raise ValueError("UNKNOWN_QUOTE_FIELD_COUNT")
        stamp = datetime.fromisoformat(values[30] + "T" + values[31]).replace(tzinfo=SHANGHAI)
        if stamp.date() != requested_day or stamp > observed_at or stamp.time() < wall_time(15):
            raise ValueError("QUOTE_DAY_OR_COMPLETION_CLOCK_MISMATCH")
        bar = Bar(symbol, identity.split(".")[0],
                  datetime.combine(requested_day, wall_time(15), SHANGHAI), requested_day,
                  open=number(values[1]), high=number(values[4]), low=number(values[5]),
                  close=number(values[3]), volume=number(values[8]))
        issues = BarQualityGate().validate([bar], expected_symbol=symbol)
        if issues:
            raise ValueError("QUOTE_OHLCV_FAILED")
        amount = number(values[9])
        result[symbol] = IncrementalBar(bar, "day", amount, _amount(bar, amount, kind), stamp)
    if set(result) != set(requested):
        raise ValueError("MISSING_QUOTE_IDENTITIES")
    return result


def reconcile_day(day: IncrementalBar, quote: IncrementalBar) -> IncrementalBar:
    """Same vendor, different endpoints: a consistency check, not PIT/source certification."""
    if day.frequency != "day" or quote.frequency != "day" or day.bar != quote.bar:
        raise ValueError("SAME_VENDOR_DAILY_QUOTE_MISMATCH")
    if quote.amount is None or quote.vwap is None or quote.source_clock is None:
        raise ValueError("MISSING_QUOTE_AMOUNT_OR_CLOCK")
    return IncrementalBar(day.bar, "day", quote.amount, quote.vwap, quote.source_clock,
                          "SAME_VENDOR_ENDPOINT_MATCH_NOT_INDEPENDENT_RECONCILIATION")
