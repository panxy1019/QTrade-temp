from pathlib import Path

from scripts.sina_minute_history_ingest import _load_universe, _sign


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
