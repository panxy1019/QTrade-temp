import json
from pathlib import Path

import pytest

from scripts.sina_minute_history_ingest import (
    _combine_universes,
    _load_balanced_stock_universe,
    _load_universe,
    _sign,
)


def test_minute_universe_spans_etfs_stocks_and_sectors() -> None:
    universe, symbols = _load_universe(Path("configs/sina_minute_universe_v1.json"))

    assert len(universe["etfs"]) == 30
    assert len(universe["stocks"]) == 15
    assert len({row["sector_code"] for row in universe["stocks"]}) == 15
    assert len(symbols) == len(set(symbols)) == 45
    assert universe["point_in_time"] is False


def test_minute_evidence_signature_is_deterministic_and_content_bound() -> None:
    first = _sign({"data_version": "minute-v1", "bar_count": 10})
    second = _sign({"bar_count": 10, "data_version": "minute-v1"})
    changed = _sign({"data_version": "minute-v1", "bar_count": 11})

    assert first == second
    assert first["evidence_sha256"] != changed["evidence_sha256"]


def _balanced_stock_universe() -> dict[str, object]:
    stocks = [
        {
            "symbol": f"{sector + 1:02d}{offset:04d}",
            "name": f"stock-{sector}-{offset}",
            "sector_code": f"s{sector:02d}",
            "sector_name": f"sector-{sector}",
        }
        for sector in range(15)
        for offset in range(5)
    ]
    return _sign(
        {
            "universe_id": "balanced-v1",
            "known_at": "2026-08-24T00:00:00+00:00",
            "constituent_as_of": "2026-07-31",
            "symbols": [row["symbol"] for row in stocks],
            "stocks": stocks,
            "point_in_time": False,
            "selection": {"candidate_data_access_before_freeze": False},
        }
    )


def test_signed_balanced_stock_universe_replaces_single_representatives(
    tmp_path: Path,
) -> None:
    target = tmp_path / "stock-universe.json"
    target.write_text(json.dumps(_balanced_stock_universe()), encoding="utf-8")

    stock_universe, symbols = _load_balanced_stock_universe(target)
    base, _ = _load_universe(Path("configs/sina_minute_universe_v1.json"))
    combined = _combine_universes(base, stock_universe)

    assert len(symbols) == 75
    assert len(combined["stocks"]) == 75
    assert combined["stock_universe_provenance"]["universe_id"] == "balanced-v1"
    tampered = _balanced_stock_universe()
    tampered["symbols"] = list(reversed(tampered["symbols"]))
    target.write_text(json.dumps(tampered), encoding="utf-8")
    with pytest.raises(ValueError, match="signature mismatch"):
        _load_balanced_stock_universe(target)
