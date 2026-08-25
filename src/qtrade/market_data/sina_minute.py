from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from datetime import date, datetime
from datetime import time as datetime_time
from decimal import Decimal, InvalidOperation
from importlib import import_module
from typing import Protocol, cast

from qtrade.data import Bar, BarQualityGate
from qtrade.market_data.akshare import SHANGHAI

SINA_MINUTE_URL = (
    "https://quotes.sina.cn/cn/api/json_v2.php/"
    "CN_MarketDataService.getKLineData"
)


class HttpResponse(Protocol):
    status_code: int

    def json(self) -> object: ...


class HttpClient(Protocol):
    def get(
        self, url: str, *, params: Mapping[str, str], timeout: float
    ) -> HttpResponse: ...


class NativeHttpxClient:
    """Optional HTTP boundary so importing QTrade does not import httpx."""

    def __init__(self) -> None:
        try:
            self._module = import_module("httpx")
        except ImportError as error:
            raise RuntimeError(
                "httpx is not installed; install the pinned qtrade akshare extra"
            ) from error

    def get(
        self, url: str, *, params: Mapping[str, str], timeout: float
    ) -> HttpResponse:
        return cast(
            HttpResponse,
            self._module.get(
                url,
                params=params,
                timeout=timeout,
                headers={
                    "User-Agent": "QTrade/0.1 noncommercial historical research",
                    "Accept": "application/json",
                },
            ),
        )


def _minute_range(
    start_hour: int, start_minute: int, end_hour: int, end_minute: int
) -> tuple[datetime_time, ...]:
    start = start_hour * 60 + start_minute
    end = end_hour * 60 + end_minute
    return tuple(datetime_time(value // 60, value % 60) for value in range(start, end + 1))


# The source exposes 238 stable observations per completed A-share session. It
# omits 14:58 and 14:59 and publishes the closing auction at 15:00. QTrade does
# not synthesize the omitted timestamps.
SINA_COMPLETE_SESSION_TIMES = (
    *_minute_range(9, 31, 11, 30),
    *_minute_range(13, 1, 14, 57),
    datetime_time(15, 0),
)


class SinaMinuteHistoryClient:
    """Bounded historical 1-minute reader with no streaming or order capability."""

    def __init__(
        self,
        client: HttpClient | None = None,
        *,
        timeout_seconds: float = 20,
        maximum_attempts: int = 3,
        initial_backoff_seconds: float = 0.5,
        datalen: int = 1970,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if timeout_seconds <= 0 or initial_backoff_seconds < 0:
            raise ValueError("Sina minute request timings are invalid")
        if maximum_attempts < 1:
            raise ValueError("Sina minute request attempts must be positive")
        if not 238 <= datalen <= 10000:
            raise ValueError("Sina minute datalen must be between 238 and 10000")
        self._client = client or NativeHttpxClient()
        self._timeout = timeout_seconds
        self._maximum_attempts = maximum_attempts
        self._initial_backoff = initial_backoff_seconds
        self._datalen = datalen
        self._sleep = sleep

    def fetch(self, symbol: str) -> tuple[Bar, ...]:
        source_symbol, exchange = _source_identity(symbol)
        failure: Exception | None = None
        for attempt in range(self._maximum_attempts):
            try:
                response = self._client.get(
                    SINA_MINUTE_URL,
                    params={
                        "symbol": source_symbol,
                        "scale": "1",
                        "ma": "no",
                        "datalen": str(self._datalen),
                    },
                    timeout=self._timeout,
                )
                if response.status_code != 200:
                    raise RuntimeError(
                        f"Sina minute endpoint returned HTTP {response.status_code}"
                    )
                bars = _parse_rows(symbol, exchange, response.json())
                issues = BarQualityGate().validate(bars, expected_symbol=symbol)
                if issues:
                    raise ValueError(
                        "Sina minute history failed quality gate: "
                        + ",".join(sorted({issue.code for issue in issues}))
                    )
                return bars
            except Exception as error:
                failure = error
                if attempt + 1 < self._maximum_attempts:
                    self._sleep(self._initial_backoff * (2**attempt))
        assert failure is not None
        raise RuntimeError(f"Sina minute history request failed for {symbol}") from failure


def complete_session_days(bars: tuple[Bar, ...]) -> tuple[date, ...]:
    by_day: dict[date, list[datetime_time]] = defaultdict(list)
    for bar in bars:
        by_day[bar.trading_day].append(bar.timestamp.astimezone(SHANGHAI).time())
    return tuple(
        day
        for day, observed in sorted(by_day.items())
        if tuple(observed) == SINA_COMPLETE_SESSION_TIMES
    )


def _parse_rows(symbol: str, exchange: str, payload: object) -> tuple[Bar, ...]:
    if not isinstance(payload, list) or not payload:
        raise ValueError("Sina minute response must contain rows")
    result: list[Bar] = []
    for row in payload:
        if not isinstance(row, dict):
            raise ValueError("Sina minute response row is malformed")
        try:
            timestamp = datetime.strptime(str(row["day"]), "%Y-%m-%d %H:%M:%S").replace(
                tzinfo=SHANGHAI
            )
        except (KeyError, ValueError) as error:
            raise ValueError("Sina minute timestamp is malformed") from error
        if timestamp.second != 0 or not _in_source_session(timestamp.time()):
            raise ValueError("Sina minute timestamp is outside the disclosed session grid")
        result.append(
            Bar(
                symbol=symbol,
                exchange=exchange,
                timestamp=timestamp,
                trading_day=timestamp.date(),
                open=_decimal(row, "open"),
                high=_decimal(row, "high"),
                low=_decimal(row, "low"),
                close=_decimal(row, "close"),
                volume=_decimal(row, "volume", allow_zero=True),
            )
        )
    return tuple(result)


def _decimal(
    row: Mapping[object, object], key: str, *, allow_zero: bool = False
) -> Decimal:
    try:
        value = Decimal(str(row[key]))
    except (KeyError, InvalidOperation) as error:
        raise ValueError(f"Sina minute {key} is malformed") from error
    if not value.is_finite() or value < 0 or (value == 0 and not allow_zero):
        raise ValueError(f"Sina minute {key} is invalid")
    return value


def _in_source_session(value: datetime_time) -> bool:
    return (
        datetime_time(9, 31) <= value <= datetime_time(11, 30)
        or datetime_time(13, 1) <= value <= datetime_time(14, 57)
        or value == datetime_time(15, 0)
    )


def _source_identity(symbol: str) -> tuple[str, str]:
    if len(symbol) != 6 or not symbol.isdigit():
        raise ValueError("Sina minute symbol must contain six digits")
    if symbol.startswith(("5", "6")):
        return f"sh{symbol}", "SSE"
    if symbol.startswith(("0", "1", "3")):
        return f"sz{symbol}", "SZSE"
    if symbol.startswith(("4", "8")):
        return f"bj{symbol}", "BSE"
    raise ValueError("Sina minute symbol has an unsupported exchange prefix")
