from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from qtrade.data import Bar
from scripts.probe_sina_intraday_observation import (
    _finalized_bars,
    _session_phase,
)


def _bar(minute: int) -> Bar:
    timestamp = datetime(2026, 8, 26, 1, minute, tzinfo=UTC)
    return Bar(
        symbol="600036",
        exchange="SSE",
        timestamp=timestamp,
        trading_day=date(2026, 8, 26),
        open=Decimal("1"),
        high=Decimal("1"),
        low=Decimal("1"),
        close=Decimal("1"),
        volume=Decimal("1"),
    )


def test_intraday_probe_excludes_uncertain_latest_bar() -> None:
    bars = (_bar(31), _bar(32), _bar(33))
    observed_at = datetime(2026, 8, 26, 1, 33, 30, tzinfo=UTC)
    assert _finalized_bars(
        bars,
        observed_at=observed_at,
        guard_seconds=60,
    ) == bars[:2]


def test_intraday_probe_rejects_naive_observation_time() -> None:
    with pytest.raises(ValueError, match="finalization boundary"):
        _finalized_bars(
            (_bar(31),),
            observed_at=datetime(2026, 8, 26, 1, 33),
            guard_seconds=60,
        )


def test_intraday_probe_classifies_china_session_phase() -> None:
    assert _session_phase(datetime(2026, 8, 26, 1, 0, tzinfo=UTC)) == "PRE_OPEN"
    assert (
        _session_phase(datetime(2026, 8, 26, 2, 0, tzinfo=UTC))
        == "MORNING_SESSION"
    )
    assert (
        _session_phase(datetime(2026, 8, 26, 4, 0, tzinfo=UTC))
        == "MIDDAY_BREAK"
    )
