import json
from datetime import UTC, date, datetime, time
from decimal import Decimal

import pytest

from qtrade.audit import AuditEvent
from qtrade.domain import Instrument, OrderRequest, RuntimeMode, Side
from qtrade.execution import FeeSchedule
from qtrade.instruments import TradingCalendar, TradingDayStatus, TradingStatus
from qtrade.reporting import (
    DailyPaperReport,
    DailyReportScheduleConfig,
    DailyReportScheduler,
    DailyReportStore,
)
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
    # 16:30 UTC on the prior date is 00:30 on this Asia/Shanghai report day.
    now = datetime(2026, 8, 11, 16, 30, tzinfo=UTC)
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
    restarted.audit.record(
        AuditEvent(
            event_type="POST_RESTART_INCIDENT",
            service="test",
            severity="ERROR",
            message="durable incident",
            timestamp=datetime(2026, 8, 12, 15, 45, tzinfo=UTC),
        )
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
    assert restored["report"]["incidents"] == ["durable incident"]


def test_scheduler_generates_once_after_verified_open_session_cutoff(tmp_path):
    day = date(2026, 8, 26)
    bundle = build_paper_runtime(
        mode=RuntimeMode.PAPER,
        opening_cash=Decimal("100000"),
        limits=RiskLimits(Decimal("50000"), 10000, Decimal("100000")),
        fees=FeeSchedule("test", Decimal("0"), Decimal("0")),
        journal_path=tmp_path / "runtime.db",
    )
    scheduler = DailyReportScheduler(
        config=DailyReportScheduleConfig(
            venue="SSE",
            data_version="rq-session-20260826-v1",
            strategy_versions=("minute-risk@1.0.0",),
            report_root=tmp_path / "reports",
        ),
        calendar=TradingCalendar(
            (TradingDayStatus("SSE", day, TradingStatus.OPEN, day, "fixture"),)
        ),
        generate=bundle.generate_daily_report,
        clock=lambda: datetime(2026, 8, 26, 7, 29, tzinfo=UTC),
    )

    before = scheduler.poll()
    assert before["state"] == "WAITING_FOR_CUTOFF"
    assert not (tmp_path / "reports").exists()

    generated = scheduler.poll(datetime(2026, 8, 26, 7, 30, tzinfo=UTC))
    assert generated["state"] == "GENERATED"
    assert generated["last_report_id"] == "paper-2026-08-26"
    assert generated["caller_supplied_identity"] is False
    target = tmp_path / "reports" / "paper-2026-08-26.json"
    assert DailyReportStore(tmp_path / "reports").verify(target)

    repeated = scheduler.poll(datetime(2026, 8, 26, 8, tzinfo=UTC))
    assert repeated["state"] == "ALREADY_GENERATED"
    assert list((tmp_path / "reports").glob("*.json")) == [target]


def test_scheduler_fails_closed_without_calendar_or_with_conflicting_identity(tmp_path):
    day = date(2026, 8, 26)
    incidents: list[str] = []

    def unexpected_generate(**_: object):
        raise AssertionError("generator must not run")

    config = DailyReportScheduleConfig(
        venue="SSE",
        data_version="trusted-data-v1",
        strategy_versions=("trusted-strategy@1",),
        report_root=tmp_path / "reports",
        cutoff=time(15, 30),
    )
    missing = DailyReportScheduler(
        config=config,
        calendar=TradingCalendar(()),
        generate=unexpected_generate,
        clock=lambda: datetime(2026, 8, 26, 8, tzinfo=UTC),
    )
    assert missing.poll()["state"] == "CALENDAR_SESSION_UNAVAILABLE"

    DailyReportStore(tmp_path / "reports").save(
        DailyPaperReport(
            "paper-2026-08-26",
            day,
            datetime(2026, 8, 26, 8, tzinfo=UTC),
            "different-data-v1",
            ("trusted-strategy@1",),
            (), (), (), (), (), (), (), (), (),
        )
    )
    conflicting = DailyReportScheduler(
        config=config,
        calendar=TradingCalendar(
            (TradingDayStatus("SSE", day, TradingStatus.OPEN, day, "fixture"),)
        ),
        generate=unexpected_generate,
        clock=lambda: datetime(2026, 8, 26, 8, tzinfo=UTC),
        incident_handler=incidents.append,
    )
    status = conflicting.poll()
    assert status["state"] == "FAILED"
    assert status["error_code"] == "EXISTING_REPORT_IDENTITY_INVALID"
    assert incidents == [
        "PAPER daily report scheduler: EXISTING_REPORT_IDENTITY_INVALID"
    ]
