from datetime import UTC, date, datetime

import pytest

from qtrade.reporting import DailyPaperReport, DailyReportStore


def test_daily_report_is_complete_immutable_and_hash_verified(tmp_path):
    report = DailyPaperReport(
        "paper-20260812",
        date(2026, 8, 12),
        datetime(2026, 8, 12, 15, 30, tzinfo=UTC),
        "rq-20260812-v1",
        ("ETF_MOM_V1@1.0.0",),
        (("ticks", "24000"),),
        ({"instrument": "510300", "target": 100},),
        ({"instrument": "510300", "quantity": 100},),
        ({"instrument": "510300", "quantity": 100},),
        (("total", "25.00"),),
        (("kill_state", "NORMAL"),),
        (("status", "PASSED"),),
        (),
        (("ETF_MOM_V1@1.0.0", "HEALTHY"),),
    )
    store = DailyReportStore(tmp_path)
    path, digest = store.save(report)
    assert len(digest) == 64 and store.verify(path) is True
    with pytest.raises(FileExistsError, match="immutable"):
        store.save(report)
