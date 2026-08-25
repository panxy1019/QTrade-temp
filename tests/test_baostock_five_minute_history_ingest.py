import json
from datetime import date, datetime
from decimal import Decimal
from pathlib import Path

import pytest

from qtrade.data import Bar
from qtrade.market_data.akshare import SHANGHAI
from scripts.baostock_five_minute_history_ingest import _load_cache, _write_cache


def test_baostock_symbol_cache_is_identity_and_content_bound(tmp_path: Path) -> None:
    cache = tmp_path / "600000.json"
    bars = (
        Bar(
            symbol="600000",
            exchange="SSE",
            timestamp=datetime(2026, 8, 25, 9, 35, tzinfo=SHANGHAI),
            trading_day=date(2026, 8, 25),
            open=Decimal("10"),
            high=Decimal("10.2"),
            low=Decimal("9.9"),
            close=Decimal("10.1"),
            volume=Decimal("1000"),
        ),
    )
    _write_cache(
        cache,
        bars,
        symbol="600000",
        provider_version="0.9.3",
        start_date="2026-08-25",
        end_date="2026-08-25",
        adjustflag="2",
    )

    loaded = _load_cache(
        cache,
        symbol="600000",
        provider_version="0.9.3",
        start_date="2026-08-25",
        end_date="2026-08-25",
        adjustflag="2",
    )
    assert loaded == bars
    with pytest.raises(ValueError, match="verification"):
        _load_cache(
            cache,
            symbol="600000",
            provider_version="0.9.3",
            start_date="2026-08-25",
            end_date="2026-08-25",
            adjustflag="3",
        )

    payload = json.loads(cache.read_text(encoding="utf-8"))
    payload["bars"][0]["close"] = "99"
    cache.write_text(json.dumps(payload), encoding="utf-8")
    with pytest.raises(ValueError, match="verification"):
        _load_cache(
            cache,
            symbol="600000",
            provider_version="0.9.3",
            start_date="2026-08-25",
            end_date="2026-08-25",
            adjustflag="2",
        )
