from datetime import date, datetime

import pytest

from qtrade.market_data import (
    BAOSTOCK_FIVE_MINUTE_FIELDS,
    SINA_FIVE_MINUTE_SESSION_TIMES,
    fetch_baostock_five_minute_history,
)
from scripts.probe_baostock_forward_session import validate_forward_session


class FakeResult:
    def __init__(self, rows: list[list[str]], *, error_code: str = "0") -> None:
        self.error_code = error_code
        self.error_msg = "test"
        self.fields = list(BAOSTOCK_FIVE_MINUTE_FIELDS)
        self._rows = iter(rows)
        self._current: list[str] = []

    def next(self) -> bool:
        try:
            self._current = next(self._rows)
        except StopIteration:
            return False
        return True

    def get_row_data(self) -> list[str]:
        return self._current


class FakeApi:
    def __init__(self, result: FakeResult) -> None:
        self.result = result
        self.request: tuple[object, ...] | None = None

    def query_history_k_data_plus(
        self,
        code: str,
        fields: str,
        *,
        start_date: str,
        end_date: str,
        frequency: str,
        adjustflag: str,
    ) -> FakeResult:
        self.request = (code, fields, start_date, end_date, frequency, adjustflag)
        return self.result


def _row(
    timestamp: str,
    *,
    code: str = "sh.600000",
    trading_day: str = "2026-08-25",
) -> list[str]:
    return [
        trading_day,
        timestamp,
        code,
        "10.00",
        "10.20",
        "9.90",
        "10.10",
        "1000",
        "10100",
        "2",
    ]


def _suspended_row(
    timestamp: str, *, trading_day: str = "2026-08-25"
) -> list[str]:
    row = _row(timestamp, trading_day=trading_day)
    row[3:9] = ["0", "0", "0", "0", "0", "0"]
    return row


def test_fetches_provider_native_five_minute_rows() -> None:
    api = FakeApi(
        FakeResult([_row("20260825093500000"), _row("20260825094000000")])
    )

    bars = fetch_baostock_five_minute_history(
        api,
        symbol="600000",
        start_date="2026-08-25",
        end_date="2026-08-25",
    )

    assert [bar.timestamp.strftime("%H:%M") for bar in bars] == ["09:35", "09:40"]
    assert bars[0].exchange == "SSE"
    assert api.request is not None
    assert api.request[0] == "sh.600000"
    assert api.request[-2:] == ("5", "2")


def test_omits_all_zero_suspension_placeholders_without_filling_them() -> None:
    api = FakeApi(
        FakeResult(
            [_suspended_row("20260825093500000"), _row("20260825094000000")]
        )
    )

    bars = fetch_baostock_five_minute_history(
        api,
        symbol="600000",
        start_date="2026-08-25",
        end_date="2026-08-25",
    )

    assert len(bars) == 1
    assert bars[0].timestamp.strftime("%H:%M") == "09:40"


def test_zero_placeholder_makes_locked_forward_session_incomplete() -> None:
    anchor_day = date(2026, 8, 25)
    target_day = date(2026, 8, 26)
    anchor_rows = [
        _row(datetime.combine(anchor_day, value).strftime("%Y%m%d%H%M%S000"))
        for value in SINA_FIVE_MINUTE_SESSION_TIMES
    ]
    target_rows = [
        _row(
            datetime.combine(target_day, value).strftime("%Y%m%d%H%M%S000"),
            trading_day=target_day.isoformat(),
        )
        for value in SINA_FIVE_MINUTE_SESSION_TIMES
    ]
    target_rows[10] = _suspended_row(
        target_rows[10][1], trading_day=target_day.isoformat()
    )
    bars = fetch_baostock_five_minute_history(
        FakeApi(FakeResult([*anchor_rows, *target_rows])),
        symbol="600000",
        start_date=anchor_day.isoformat(),
        end_date=target_day.isoformat(),
    )

    assert len([bar for bar in bars if bar.trading_day == target_day]) == 47
    with pytest.raises(ValueError, match="incomplete"):
        validate_forward_session(
            symbols=("600000",),
            anchor_day=anchor_day,
            target_day=target_day,
            frozen_bars=[bar for bar in bars if bar.trading_day == anchor_day],
            fetched_by_symbol={"600000": bars},
        )


@pytest.mark.parametrize(
    ("row", "message"),
    [
        (_row("20260825093500000", code="sz.000001"), "identity"),
        (_row("20260825093600000"), "grid"),
    ],
)
def test_rejects_wrong_identity_or_non_native_timestamp(
    row: list[str], message: str
) -> None:
    with pytest.raises(ValueError, match=message):
        fetch_baostock_five_minute_history(
            FakeApi(FakeResult([row])),
            symbol="600000",
            start_date="2026-08-25",
            end_date="2026-08-25",
        )
