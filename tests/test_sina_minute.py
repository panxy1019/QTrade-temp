from datetime import date, datetime
from decimal import Decimal

import pytest

from qtrade.market_data import (
    SINA_COMPLETE_SESSION_TIMES,
    SinaMinuteHistoryClient,
    complete_session_days,
)
from qtrade.market_data.akshare import SHANGHAI


class FakeResponse:
    def __init__(self, payload, *, status_code=200):
        self.status_code = status_code
        self._payload = payload

    def json(self):
        return self._payload


class FakeClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def get(self, url, *, params, timeout):
        self.calls.append((url, params, timeout))
        value = self.responses.pop(0)
        if isinstance(value, Exception):
            raise value
        return value


def source_row(timestamp: datetime, close: str = "4.10") -> dict[str, str]:
    return {
        "day": timestamp.strftime("%Y-%m-%d %H:%M:%S"),
        "open": close,
        "high": close,
        "low": close,
        "close": close,
        "volume": "100",
    }


def complete_rows(day: date) -> list[dict[str, str]]:
    return [
        source_row(datetime.combine(day, minute, tzinfo=SHANGHAI))
        for minute in SINA_COMPLETE_SESSION_TIMES
    ]


def test_fetch_retries_and_canonicalizes_a_complete_etf_session() -> None:
    client = FakeClient(
        [TimeoutError("temporary"), FakeResponse(complete_rows(date(2026, 8, 25)))]
    )
    sleeps = []
    value = SinaMinuteHistoryClient(
        client,
        maximum_attempts=2,
        initial_backoff_seconds=0.25,
        sleep=sleeps.append,
    )

    bars = value.fetch("510300")

    assert len(bars) == 238
    assert bars[0].exchange == "SSE"
    assert bars[0].timestamp.tzinfo is not None
    assert bars[0].close == Decimal("4.10")
    assert complete_session_days(bars) == (date(2026, 8, 25),)
    assert client.calls[-1][1]["symbol"] == "sh510300"
    assert client.calls[-1][1]["scale"] == "1"
    assert sleeps == [0.25]


def test_fetch_supports_stock_exchange_prefix_and_excludes_partial_session() -> None:
    rows = complete_rows(date(2026, 8, 25))
    rows.extend(complete_rows(date(2026, 8, 26))[:10])
    client = FakeClient([FakeResponse(rows)])

    bars = SinaMinuteHistoryClient(client).fetch("300059")

    assert all(bar.exchange == "SZSE" for bar in bars)
    assert complete_session_days(bars) == (date(2026, 8, 25),)
    assert client.calls[0][1]["symbol"] == "sz300059"


def test_fetch_rejects_out_of_session_or_non_positive_prices() -> None:
    outside = source_row(datetime(2026, 8, 25, 12, 0, tzinfo=SHANGHAI))
    with pytest.raises(RuntimeError, match="request failed"):
        SinaMinuteHistoryClient(FakeClient([FakeResponse([outside])])).fetch("510300")

    invalid = source_row(datetime(2026, 8, 25, 9, 31, tzinfo=SHANGHAI), "0")
    with pytest.raises(RuntimeError, match="request failed"):
        SinaMinuteHistoryClient(FakeClient([FakeResponse([invalid])])).fetch("510300")


def test_fetch_is_bounded_and_rejects_unknown_symbols() -> None:
    with pytest.raises(ValueError, match="datalen"):
        SinaMinuteHistoryClient(FakeClient([]), datalen=100)
    with pytest.raises(ValueError, match="exchange prefix"):
        SinaMinuteHistoryClient(FakeClient([])).fetch("900000")
