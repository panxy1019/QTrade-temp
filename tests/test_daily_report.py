from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from qtrade.domain import RuntimeMode
from qtrade.execution import FeeSchedule
from qtrade.reporting import DailyPaperReport, DailyReportStore
from qtrade.risk import RiskLimits
from qtrade.runtime import build_paper_runtime


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


def test_runtime_generates_traceable_daily_report(tmp_path):
    bundle = build_paper_runtime(
        mode=RuntimeMode.PAPER,
        opening_cash=Decimal("100000"),
        limits=RiskLimits(Decimal("50000"), 10000, Decimal("100000")),
        fees=FeeSchedule("test", Decimal("0"), Decimal("0")),
        journal_path=tmp_path / "runtime.db",
    )
    bundle.reconcile_and_recover(actor="operator")
    report_root = tmp_path / "reports"
    path, digest = bundle.generate_daily_report(
        trading_day=date(2026, 8, 12),
        data_version="ticks-v1",
        strategy_versions=("trend@1.0.0",),
        report_root=report_root,
        generated_at=datetime(2026, 8, 12, 16, tzinfo=UTC),
    )
    assert len(digest) == 64
    assert DailyReportStore(report_root).verify(path)
