from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from decimal import Decimal

import pytest

from qtrade.data import Bar
from scripts.akshare_stock_history_ingest import (
    _cached_bars,
    _coverage,
    _load_universe,
    _remove_listing_price_discovery_sessions,
    _remove_pre_long_suspension_history,
    _sign_evidence,
    _write_cached_bars,
)


def _bar(symbol: str, day: int) -> Bar:
    timestamp = datetime(2025, 1, day, tzinfo=UTC)
    return Bar(
        symbol=symbol,
        exchange="XSHG",
        timestamp=timestamp,
        trading_day=timestamp.date(),
        open=Decimal("10"),
        high=Decimal("11"),
        low=Decimal("9"),
        close=Decimal("10"),
        volume=Decimal("100"),
    )


def _universe() -> dict[str, object]:
    stocks = [
        {"symbol": f"{sector:02d}{offset:04d}", "sector_code": f"s{sector:02d}"}
        for sector in range(15)
        for offset in range(5)
    ]
    return _sign_evidence(
        {
            "symbols": [item["symbol"] for item in stocks],
            "stocks": stocks,
            "point_in_time": False,
            "selection": {"candidate_data_access_before_freeze": False},
        }
    )


def test_load_universe_requires_valid_signature_and_balanced_shape(tmp_path):
    target = tmp_path / "universe.json"
    target.write_text(json.dumps(_universe()), encoding="utf-8")

    payload, symbols = _load_universe(target)

    assert len(symbols) == 75
    assert payload["point_in_time"] is False
    tampered = _universe()
    tampered["symbols"] = list(reversed(tampered["symbols"]))
    target.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="signature mismatch"):
        _load_universe(target)


def test_coverage_rejects_any_underfilled_symbol():
    symbols = ("600000", "000001")
    bars = [_bar("600000", 2), _bar("600000", 3), _bar("000001", 2)]

    with pytest.raises(ValueError, match="000001"):
        _coverage(bars, symbols, minimum_bars=2)

    coverage = _coverage(bars, symbols, minimum_bars=1)
    assert coverage["600000"]["bar_count"] == 2
    assert coverage["000001"]["bar_count"] == 1


def test_listing_price_discovery_removal_only_affects_stocks_starting_late():
    requested_start = datetime(2024, 1, 1, tzinfo=UTC)
    bars = [
        replace(
            _bar("600000", day),
            timestamp=datetime(2024, 1, day, tzinfo=UTC),
            trading_day=datetime(2024, 1, day, tzinfo=UTC).date(),
        )
        for day in range(2, 8)
    ]
    late = [
        replace(
            _bar("688000", day),
            timestamp=datetime(2025, 1, day, tzinfo=UTC),
            trading_day=datetime(2025, 1, day, tzinfo=UTC).date(),
        )
        for day in range(2, 8)
    ]

    retained, removed = _remove_listing_price_discovery_sessions(
        [*bars, *late], ("600000", "688000"), requested_start=requested_start
    )

    assert sum(bar.symbol == "600000" for bar in retained) == 6
    assert sum(bar.symbol == "688000" for bar in retained) == 1
    assert removed["688000"]["count"] == 5


def test_signed_stock_cache_round_trips_and_rejects_tampering(tmp_path):
    target = tmp_path / "600000.json"
    values = [_bar("600000", 2), _bar("600000", 3)]
    identity = {
        "symbol": "600000",
        "adjustment": "hfq",
        "requested_start": "2025-01-01T00:00:00+00:00",
        "requested_end_exclusive": "2025-01-05T00:00:00+00:00",
    }
    _write_cached_bars(target, values, **identity)
    assert _cached_bars(target, **identity) == values

    payload = json.loads(target.read_text(encoding="utf-8"))
    payload["bars"][0]["close"] = "999"
    target.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="signature mismatch"):
        _cached_bars(target, **identity)


def test_long_suspension_resets_history_before_storage():
    old = [_bar("000792", 2), _bar("000792", 3)]
    resumed = [
        replace(
            _bar("000792", day),
            timestamp=datetime(2025, 8, day, tzinfo=UTC),
            trading_day=datetime(2025, 8, day, tzinfo=UTC).date(),
        )
        for day in (2, 3)
    ]

    retained, removed = _remove_pre_long_suspension_history(
        [*old, *resumed], ("000792",)
    )

    assert retained == resumed
    assert removed["000792"]["count"] == 2
