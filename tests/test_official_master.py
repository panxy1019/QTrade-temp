from copy import deepcopy

import pytest

from qtrade.monitoring.data_freshness import signed
from qtrade.research.official_master import reconcile
from scripts.reconcile_official_master import verified_raw


def inputs():
    return ([{"COMPANY_CODE": "600001", "A_STOCK_CODE": "600001", "STOCK_TYPE": "1",
              "LIST_DATE": "19980122", "DELIST_DATE": "20091229"}],
            [{"证券代码": "000001", "上市日期": "1991-04-03", "终止上市日期": "2020-01-02"}],
            [{"code": "sh.600001", "type": "1", "status": "0",
              "ipoDate": "1998-01-22", "outDate": "2009-12-29"},
             {"code": "sz.000001", "type": "1", "status": "0",
              "ipoDate": "1991-04-03", "outDate": "2020-01-02"}])


def test_exact_dates_remain_uncertified_and_inputs_are_unchanged():
    sse, szse, master = inputs()
    original = deepcopy((sse, szse, master))
    result = reconcile(sse, szse, master, {"000001", "600002"})
    assert (sse, szse, master) == original
    assert result["state_counts"] == {"DATES_AGREE_UNCERTIFIED": 2}
    assert result["records"][1]["in_current_research_sample"] is True
    assert result["historical_available_at"] is None
    assert result["historical_pit_certified"] is result["point_in_time"] is False
    assert result["used_in_qlib"] is result["live_trading_enabled"] is False
    assert signed({k: v for k, v in result.items() if k != "evidence_sha256"}) == result


@pytest.mark.parametrize("mutation,issue", [
    (lambda s, z, m: s[0].update(STOCK_TYPE="2"), "SHARE_CLASS_DATE_SEMANTICS_UNVERIFIED"),
    (lambda s, z, m: s.append(deepcopy(s[0])), "DUPLICATE_OFFICIAL_IDENTITY"),
    (lambda s, z, m: m.append(deepcopy(m[0])), "DUPLICATE_VENDOR_IDENTITY"),
    (lambda s, z, m: m.pop(0), "MISSING_VENDOR_IDENTITY"),
    (lambda s, z, m: m[0].update(outDate="2009-12-28"), "CONFLICT_OUTDATE"),
    (lambda s, z, m: m[0].update(outDate=""), "MISSING_VENDOR_OUTDATE"),
    (lambda s, z, m: m[0].update(status="1"), "VENDOR_NOT_MARKED_INACTIVE"),
    (lambda s, z, m: m[0].update(type="2"), "VENDOR_SECURITY_TYPE_MISMATCH"),
    (lambda s, z, m: s[0].update(DELIST_DATE="20090230"), "OFFICIAL_DATE_INVALID"),
    (lambda s, z, m: s[0].update(DELIST_DATE="19900101"), "OFFICIAL_DATE_ORDER_INVALID"),
    (lambda s, z, m: z[0].update(证券代码="200001"), "NON_A_OR_UNRESOLVED_IDENTITY"),
    (lambda s, z, m: s[0].update(A_STOCK_CODE="600002"), "NON_A_OR_UNRESOLVED_IDENTITY"),
])
def test_conflicting_unknown_and_ambiguous_records_are_retained(mutation, issue):
    sse, szse, master = inputs()
    mutation(sse, szse, master)
    result = reconcile(sse, szse, master, set())
    assert result["issue_counts"][issue] >= 1
    assert result["official_rows"] == len(sse) + len(szse)
    assert any(row["state"] != "DATES_AGREE_UNCERTIFIED" for row in result["records"])


def test_unhashed_raw_input_is_rejected_before_parsing(tmp_path):
    (tmp_path / ("a" * 64 + ".json")).write_bytes(b"{}")
    with pytest.raises(ValueError, match="OFFICIAL_SOURCE_BYTES_MISMATCH"):
        verified_raw(tmp_path, {"raw_sha256": "a" * 64, "raw_bytes": 2}, ".json")


def test_receipt_cannot_escape_source_directory(tmp_path):
    with pytest.raises(ValueError, match="INVALID_OFFICIAL_SOURCE_HASH"):
        verified_raw(tmp_path, {"raw_sha256": "../" + "a" * 61}, ".json")
