from dataclasses import replace
from datetime import date, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pytest

from qtrade.data import Bar
from qtrade.data.minute_admission import admitted_checkpoint, validate_minute_window
from qtrade.data.storage import DatasetIntegrityError
from qtrade.market_data.akshare import SHANGHAI
from qtrade.market_data.sina_minute import SINA_FIVE_MINUTE_SESSION_TIMES
from scripts.expand_research_data import ingest_minute


def sample() -> list[Bar]:
    first = date(2023, 10, 24)
    days = [first + timedelta(days=i) for i in range(940)
            if (first + timedelta(days=i)).weekday() < 5]
    return [Bar("002384", "SZSE", datetime.combine(day, clock, SHANGHAI), day,
                *[Decimal("10")] * 4, Decimal("100"))
            for day in days for clock in SINA_FIVE_MINUTE_SESSION_TIMES]


def test_legacy_minute_coverage_does_not_trust_correct_hash_or_filename() -> None:
    bars = sample()[:24000]
    result = validate_minute_window(bars, symbol="002384", requested_end=date(2026, 10, 23))
    assert result["state"] == "QUARANTINED"
    assert {"STALE_END", "INSUFFICIENT_SESSIONS"} <= set(result["issues"])


def test_minute_contract_rejects_grid_identity_duplicate_and_partial_sessions() -> None:
    bars = sample()
    end = bars[-1].trading_day
    assert validate_minute_window(bars, symbol="002384", requested_end=end)["issues"] == []
    mutations = [
        (bars[:-1], "INCOMPLETE_SESSION"),
        ([*bars, bars[-1]], "DUPLICATE"),
        ([replace(bars[0], symbol="002385"), *bars[1:]], "SYMBOL_MISMATCH"),
        ([replace(bars[0], exchange="SSE"), *bars[1:]], "EXCHANGE_MISMATCH"),
        ([replace(bars[0], timestamp=bars[0].timestamp + timedelta(seconds=1)), *bars[1:]],
         "INVALID_SESSION_GRID"),
    ]
    for records, issue in mutations:
        result = validate_minute_window(records, symbol="002384", requested_end=end)
        assert issue in result["issues"]


def test_actual_cached_parquet_is_reread_and_original_checkpoint_is_preserved(
    tmp_path: Path,
) -> None:
    import json
    from hashlib import sha256

    import pyarrow as pa
    import pyarrow.parquet as pq

    import qtrade.data.storage as storage

    bars = sample()
    target = tmp_path / "bars/legacy/bars.parquet"
    target.parent.mkdir(parents=True)
    pq.write_table(pa.Table.from_pylist([
        {"symbol": b.symbol, "exchange": b.exchange, "timestamp": b.timestamp.isoformat(),
         "trading_day": b.trading_day, **{k: str(getattr(b, k)) for k in (
             "open", "high", "low", "close", "volume")}, "data_version": "legacy"}
        for b in bars
    ], schema=storage._BAR_PARQUET_SCHEMA), target)
    checkpoint = tmp_path / "minute/002384" / (str(bars[-1].trading_day) + ".json")
    checkpoint.parent.mkdir(parents=True)
    checkpoint.write_text(json.dumps({"symbol": "002384", "bar_count": 1, "manifest": {
        "data_version": "legacy", "checksum": sha256(target.read_bytes()).hexdigest(),
    }}))
    original = checkpoint.read_bytes()
    admitted = ingest_minute(tmp_path, {"symbol": "002384"}, bars[-1].trading_day)
    assert admitted["bar_count"] == len(bars)  # ignore false checkpoint counts
    assert admitted["admission"]["historical_calendar_certified"] is False
    assert admitted["admission"]["point_in_time"] is False
    assert checkpoint.read_bytes() == original
    with pytest.raises(ValueError, match="STALE_END"):
        admitted_checkpoint(tmp_path, checkpoint, symbol="002384", end=date(2026, 10, 23))
    target = tmp_path / "bars/legacy/bars.parquet"
    target.write_bytes(target.read_bytes() + b"tampered")
    with pytest.raises(DatasetIntegrityError, match="checksum"):
        ingest_minute(tmp_path, {"symbol": "002384"}, bars[-1].trading_day)
