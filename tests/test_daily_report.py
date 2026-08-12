import json
from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from qtrade.domain import Instrument, OrderRequest, RuntimeMode, Side
from qtrade.execution import FeeSchedule
from qtrade.reporting import DailyPaperReport, DailyReportStore
from qtrade.risk import RiskContext, RiskLimits
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
    journal_path = tmp_path / "runtime.db"
    bundle = build_paper_runtime(
        mode=RuntimeMode.PAPER,
        opening_cash=Decimal("100000"),
        limits=RiskLimits(Decimal("50000"), 10000, Decimal("100000")),
        fees=FeeSchedule("test", Decimal("0"), Decimal("0")),
        journal_path=journal_path,
    )
    bundle.reconcile_and_recover(actor="operator")
    bundle.runtime.start(actor="operator")
    now = datetime(2026, 8, 12, 1, 30, tzinfo=UTC)
    instrument = Instrument("510300", "SSE", "ETF", 100, Decimal("0.001"))
    order, _ = bundle.service.submit(
        OrderRequest(
            "daily-report-order", "trend", "paper", "510300", Side.BUY,
            Decimal("10"), 100, now,
        ),
        RiskContext(
            bundle.ledger.account, {"510300": instrument}, {"510300": Decimal("10")},
            now, True, True, now,
        ),
        now=now,
    )
    bundle.gateway.fill(order, fill_key="daily-fill", price=Decimal("10"), quantity=100, now=now)
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
    first = json.loads(path.read_text(encoding="utf-8"))
    assert len(first["report"]["trades"]) == 1

    restarted = build_paper_runtime(
        mode=RuntimeMode.PAPER,
        opening_cash=Decimal("100000"),
        limits=RiskLimits(Decimal("50000"), 10000, Decimal("100000")),
        fees=FeeSchedule("test", Decimal("0"), Decimal("0")),
        journal_path=journal_path,
    )
    restarted_path, _ = restarted.generate_daily_report(
        trading_day=date(2026, 8, 12),
        data_version="ticks-v1",
        strategy_versions=("trend@1.0.0",),
        report_root=tmp_path / "restarted-reports",
        generated_at=datetime(2026, 8, 12, 16, tzinfo=UTC),
    )
    restored = json.loads(restarted_path.read_text(encoding="utf-8"))
    assert restored["report"]["trades"] == first["report"]["trades"]
