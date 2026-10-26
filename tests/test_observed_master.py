from copy import deepcopy
from datetime import UTC, datetime

import pytest

from qtrade.instruments.observed import build_observed_master, instrument_id


def build(groups=None, vendor=None, target=3):
    return build_observed_master(
        vendor_rows=vendor if vendor is not None else [
            {"code": "sh.600036", "code_name": "招商银行", "type": "1", "status": "1",
             "ipoDate": "2002-04-09", "outDate": ""},
            {"code": "sz.000001", "code_name": "平安银行", "type": "1", "status": "1"},
            {"code": "sh.510300", "code_name": "ETF", "type": "5", "status": "1"},
            {"code": "sz.000002", "code_name": "退市样例", "type": "1", "status": "0",
             "ipoDate": "1991-01-01", "outDate": "2020-01-01"},
            {"code": "sh.900001", "type": "1", "status": "1"},
            {"code": "sh.512000", "type": "5", "status": "1"},
        ], vendor_sha256="a" * 64,
        source_groups=groups if groups is not None else [
            {"source_id": "etf30", "role": "legacy", "sha256": "b" * 64,
             "rows": [{"symbol": "510300", "security_type": "ETF"}]},
            {"source_id": "stock75", "role": "legacy", "sha256": "c" * 64,
             "rows": [{"symbol": "600036", "security_type": "STOCK"}]},
            {"source_id": "daily1000", "role": "legacy", "sha256": "d" * 64,
             "rows": [{"symbol": "600036", "security_type": "STOCK"}]},
            {"source_id": "candidates", "role": "candidate", "sha256": "e" * 64,
             "rows": [{"symbol": "000002", "security_type": "STOCK"},
                      {"symbol": "000001", "security_type": "STOCK"}]},
        ], observed_at=datetime(2026, 10, 26, tzinfo=UTC), target_count=target,
    )


def test_observed_master_deduplicates_lineage_tops_up_and_never_certifies_pit() -> None:
    result = build()
    assert tuple(result[k] for k in (
        "legacy_union_count", "top_up_count", "target_actual")) == (2, 1, 3)
    assert result["target_by_type"] == {"STOCK": 2, "ETF": 1}
    rows = {r["instrument_id"]: r for r in result["securities"]}
    assert rows["SSE.600036"]["legacy_sources"] == ["stock75", "daily1000"]
    assert len(rows["SSE.600036"]["source"]) == 3
    assert rows["SZSE.000002"]["in_target"] is False
    assert rows["SZSE.000002"]["delisting_date"] == "2020-01-01"
    assert "SSE.512000" not in rows  # vendor fund class alone cannot assert ETF
    assert all(r["available_at"] is None for r in rows.values())
    assert all(r["universe_version"] == result["universe_version"] for r in rows.values())
    assert result["point_in_time"] is False and result["historical_pit_certified_count"] == 0
    assert result == build()


def test_observed_master_reports_shortfall_and_retains_inactive_or_conflicting_identity() -> None:
    assert build(target=5)["state"] == "PARTIAL"
    vendor = [{"code": "sh.600036", "type": "1", "status": "1"}] * 2
    result = build(vendor=deepcopy(vendor))
    row = next(r for r in result["securities"] if r["symbol"] == "600036")
    assert row["data_quality_status"] == "QUARANTINED" and not row["in_target"]
    assert "DUPLICATE_VENDOR_IDENTITY" in row["issues"]
    assert "SSE.600036" in result["legacy_unresolved_or_inactive"]


@pytest.mark.parametrize("symbol,kind", [("900001", "STOCK"), ("600036", "ETF"),
                                         ("../bad", "STOCK"), ("000001", "INDEX")])
def test_observed_master_rejects_ambiguous_identity(symbol, kind) -> None:
    with pytest.raises(ValueError):
        instrument_id(symbol, kind)
