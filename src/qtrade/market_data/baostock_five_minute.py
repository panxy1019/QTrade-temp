from __future__ import annotations

from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from typing import Protocol

from qtrade.data import Bar, BarQualityGate
from qtrade.market_data.akshare import SHANGHAI
from qtrade.market_data.sina_minute import SINA_FIVE_MINUTE_SESSION_TIMES

BAOSTOCK_FIVE_MINUTE_FIELDS = (
    "date",
    "time",
    "code",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "amount",
    "adjustflag",
)


class BaoStockQueryResult(Protocol):
    error_code: str
    error_msg: str
    fields: list[str]

    def next(self) -> bool: ...

    def get_row_data(self) -> list[str]: ...


class BaoStockHistoryApi(Protocol):
    def query_history_k_data_plus(
        self,
        code: str,
        fields: str,
        *,
        start_date: str,
        end_date: str,
        frequency: str,
        adjustflag: str,
    ) -> BaoStockQueryResult: ...


def fetch_baostock_five_minute_history(
    api: BaoStockHistoryApi,
    *,
    symbol: str,
    start_date: str,
    end_date: str,
    adjustflag: str = "2",
) -> tuple[Bar, ...]:
    source_code, exchange = _source_identity(symbol)
    start = date.fromisoformat(start_date)
    end = date.fromisoformat(end_date)
    if start > end:
        raise ValueError("BaoStock history start must not follow end")
    if adjustflag not in {"2", "3"}:
        raise ValueError("BaoStock 5-minute adjustment must be qfq=2 or raw=3")
    result = api.query_history_k_data_plus(
        source_code,
        ",".join(BAOSTOCK_FIVE_MINUTE_FIELDS),
        start_date=start_date,
        end_date=end_date,
        frequency="5",
        adjustflag=adjustflag,
    )
    if result.error_code != "0":
        raise RuntimeError(
            f"BaoStock history request failed for {symbol}: {result.error_code}"
        )
    if tuple(result.fields) != BAOSTOCK_FIVE_MINUTE_FIELDS:
        raise ValueError("BaoStock 5-minute response fields are inconsistent")

    bars: list[Bar] = []
    allowed_times = set(SINA_FIVE_MINUTE_SESSION_TIMES)
    while result.next():
        row = result.get_row_data()
        if len(row) != len(BAOSTOCK_FIVE_MINUTE_FIELDS):
            raise ValueError("BaoStock 5-minute row has an invalid field count")
        (
            trading_day_raw,
            timestamp_raw,
            observed_code,
            open_raw,
            high_raw,
            low_raw,
            close_raw,
            volume_raw,
            _amount_raw,
            observed_adjustflag,
        ) = row
        if observed_code != source_code or observed_adjustflag != adjustflag:
            raise ValueError("BaoStock 5-minute row identity is inconsistent")
        try:
            trading_day = date.fromisoformat(trading_day_raw)
            if len(timestamp_raw) != 17 or not timestamp_raw.isdigit():
                raise ValueError
            timestamp = datetime.strptime(timestamp_raw, "%Y%m%d%H%M%S%f").replace(
                tzinfo=SHANGHAI
            )
            values = tuple(
                Decimal(value)
                for value in (open_raw, high_raw, low_raw, close_raw, volume_raw)
            )
        except (InvalidOperation, ValueError) as error:
            raise ValueError("BaoStock 5-minute row is malformed") from error
        if (
            timestamp.date() != trading_day
            or not start <= trading_day <= end
            or timestamp.time() not in allowed_times
        ):
            raise ValueError("BaoStock 5-minute timestamp is outside its request grid")
        if all(value == 0 for value in values):
            # BaoStock emits a full 48-row all-zero placeholder grid on some
            # suspended days. It is not a tradable bar and is never carried
            # forward or filled; omitting it makes the session incomplete.
            continue
        bars.append(
            Bar(
                symbol=symbol,
                exchange=exchange,
                timestamp=timestamp,
                trading_day=trading_day,
                open=values[0],
                high=values[1],
                low=values[2],
                close=values[3],
                volume=values[4],
            )
        )
    if result.error_code != "0":
        raise RuntimeError(
            f"BaoStock history iteration failed for {symbol}: {result.error_code}"
        )
    if not bars:
        raise ValueError("BaoStock 5-minute response is empty")
    issues = BarQualityGate().validate(bars, expected_symbol=symbol)
    if issues:
        raise ValueError(
            "BaoStock 5-minute history failed quality gate: "
            + ",".join(sorted({issue.code for issue in issues}))
        )
    return tuple(bars)


def _source_identity(symbol: str) -> tuple[str, str]:
    if len(symbol) != 6 or not symbol.isdigit():
        raise ValueError("BaoStock symbol must be a six-digit A-share code")
    if symbol.startswith(("5", "6", "9")):
        return f"sh.{symbol}", "SSE"
    if symbol.startswith(("0", "1", "2", "3")):
        return f"sz.{symbol}", "SZSE"
    raise ValueError("BaoStock symbol exchange is unsupported")
