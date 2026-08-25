from __future__ import annotations

import time
from collections import defaultdict
from collections.abc import Callable, Mapping
from datetime import date, datetime
from datetime import time as datetime_time
from decimal import Decimal, InvalidOperation
from importlib import import_module
from typing import Literal, Protocol, cast

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
    start_hour: int,
    start_minute: int,
    end_hour: int,
    end_minute: int,
    *,
    step: int = 1,
) -> tuple[datetime_time, ...]:
    start = start_hour * 60 + start_minute
    end = end_hour * 60 + end_minute
    return tuple(
        datetime_time(value // 60, value % 60)
        for value in range(start, end + 1, step)
    )


# The source exposes 238 stable observations per completed A-share session. It
# omits 14:58 and 14:59 and publishes the closing auction at 15:00. QTrade does
# not synthesize the omitted timestamps.
SINA_COMPLETE_SESSION_TIMES = (
    *_minute_range(9, 31, 11, 30),
    *_minute_range(13, 1, 14, 57),
    datetime_time(15, 0),
)

# The provider's native 5-minute bars close on the five-minute marks. Unlike
# its 1-minute response, the afternoon grid is continuous through the 15:00
# close and contains 48 observations per completed session.
SINA_FIVE_MINUTE_SESSION_TIMES = (
    *_minute_range(9, 35, 11, 30, step=5),
    *_minute_range(13, 5, 15, 0, step=5),
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
        scale_minutes: Literal[1, 5] = 1,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        if timeout_seconds <= 0 or initial_backoff_seconds < 0:
            raise ValueError("Sina minute request timings are invalid")
        if maximum_attempts < 1:
            raise ValueError("Sina minute request attempts must be positive")
        if scale_minutes not in {1, 5}:
            raise ValueError("Sina minute scale must be 1 or 5")
        minimum = len(
            SINA_COMPLETE_SESSION_TIMES
            if scale_minutes == 1
            else SINA_FIVE_MINUTE_SESSION_TIMES
        )
        if not minimum <= datalen <= 1970:
            raise ValueError(
                f"Sina minute datalen must be between {minimum} and provider limit 1970"
            )
        self._client = client or NativeHttpxClient()
        self._timeout = timeout_seconds
        self._maximum_attempts = maximum_attempts
        self._initial_backoff = initial_backoff_seconds
        self._datalen = datalen
        self._scale_minutes = scale_minutes
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
                        "scale": str(self._scale_minutes),
                        "ma": "no",
                        "datalen": str(self._datalen),
                    },
                    timeout=self._timeout,
                )
                if response.status_code != 200:
                    raise RuntimeError(
                        f"Sina minute endpoint returned HTTP {response.status_code}"
                    )
                expected_times = (
                    SINA_COMPLETE_SESSION_TIMES
                    if self._scale_minutes == 1
                    else SINA_FIVE_MINUTE_SESSION_TIMES
                )
                bars = _parse_rows(
                    symbol,
                    exchange,
                    response.json(),
                    allowed_times=frozenset(expected_times),
                )
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


def complete_session_days(
    bars: tuple[Bar, ...],
    *,
    expected_times: tuple[datetime_time, ...] = SINA_COMPLETE_SESSION_TIMES,
) -> tuple[date, ...]:
    by_day: dict[date, list[datetime_time]] = defaultdict(list)
    for bar in bars:
        by_day[bar.trading_day].append(bar.timestamp.astimezone(SHANGHAI).time())
    return tuple(
        day
        for day, observed in sorted(by_day.items())
        if tuple(observed) == expected_times
    )


def _parse_rows(
    symbol: str,
    exchange: str,
    payload: object,
    *,
    allowed_times: frozenset[datetime_time] = frozenset(SINA_COMPLETE_SESSION_TIMES),
) -> tuple[Bar, ...]:
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
        if timestamp.second != 0 or timestamp.time() not in allowed_times:
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
