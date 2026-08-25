"""Run the PAPER control plane locally; real trading is unavailable."""

import argparse
from datetime import UTC, date, datetime, time
from decimal import Decimal
from pathlib import Path
from threading import Event, Thread

from qtrade.api import context_from_runtime, create_app
from qtrade.domain import RuntimeMode
from qtrade.execution import FeeSchedule
from qtrade.instruments import TradingCalendar, TradingDayStatus, TradingStatus
from qtrade.reporting import (
    DailyReportScheduleConfig,
    DailyReportScheduler,
    run_daily_report_worker,
)
from qtrade.risk import RiskLimits
from qtrade.runtime import build_paper_runtime


def build_app(
    *,
    historical_manifest: Path | None = None,
    report_data_version: str | None = None,
    report_strategy_versions: tuple[str, ...] = (),
    report_open_days: tuple[date, ...] = (),
    report_cutoff: time = time(15, 30),
):
    bundle = build_paper_runtime(
        mode=RuntimeMode.PAPER,
        opening_cash=Decimal("100000"),
        limits=RiskLimits(Decimal("50000"), 10000, Decimal("100000")),
        fees=FeeSchedule("local-unconfigured", Decimal("0"), Decimal("0")),
        journal_path=Path("runtime/control-plane.db"),
    )
    context = context_from_runtime(bundle, historical_manifest_path=historical_manifest)
    scheduler: DailyReportScheduler | None = None
    requested = bool(report_data_version or report_strategy_versions or report_open_days)
    if requested:
        if not report_data_version or not report_strategy_versions or not report_open_days:
            raise ValueError(
                "automatic reports require data version, strategy versions and open days"
            )
        if len(report_open_days) > 366 or len(set(report_open_days)) != len(report_open_days):
            raise ValueError("automatic report open days must be unique and bounded")
        calendar = TradingCalendar(
            tuple(
                TradingDayStatus(
                    "SSE", day, TradingStatus.OPEN, day, "operator-pinned-open-session"
                )
                for day in report_open_days
            )
        )
        scheduler = DailyReportScheduler(
            config=DailyReportScheduleConfig(
                venue="SSE",
                data_version=report_data_version,
                strategy_versions=report_strategy_versions,
                report_root=Path("runtime/reports"),
                cutoff=report_cutoff,
            ),
            calendar=calendar,
            generate=bundle.generate_daily_report,
            clock=lambda: datetime.now(UTC),
            incident_handler=bundle.report_incident,
        )
        context.reporting_status_reader = scheduler.snapshot
    application = create_app(context)
    application.state.daily_report_scheduler = scheduler
    return application


def _wall_clock_time(value: str) -> time:
    try:
        parsed = time.fromisoformat(value)
    except ValueError as exc:
        raise argparse.ArgumentTypeError("expected HH:MM") from exc
    if parsed.tzinfo is not None or parsed.second or parsed.microsecond:
        raise argparse.ArgumentTypeError("expected local HH:MM without seconds or timezone")
    return parsed

if __name__ == "__main__":
    import uvicorn

    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--historical-manifest",
        type=Path,
        help="checksum-signed manifest.json produced by akshare_history_ingest.py",
    )
    parser.add_argument(
        "--report-data-version",
        help="trusted PAPER market-data identity bound at process startup",
    )
    parser.add_argument(
        "--report-strategy-version",
        action="append",
        default=[],
        help="trusted PAPER strategy identity; repeat for multiple strategies",
    )
    parser.add_argument(
        "--report-open-day",
        action="append",
        default=[],
        type=date.fromisoformat,
        help="verified SSE open session (YYYY-MM-DD); repeat for the configured horizon",
    )
    parser.add_argument(
        "--report-cutoff",
        type=_wall_clock_time,
        default=time(15, 30),
        help="Asia/Shanghai automatic report cutoff (default: 15:30)",
    )
    args = parser.parse_args()
    application = build_app(
        historical_manifest=args.historical_manifest,
        report_data_version=args.report_data_version,
        report_strategy_versions=tuple(args.report_strategy_version),
        report_open_days=tuple(args.report_open_day),
        report_cutoff=args.report_cutoff,
    )
    scheduler = application.state.daily_report_scheduler
    stop = Event()
    worker = None
    if scheduler is not None:
        worker = Thread(
            target=run_daily_report_worker,
            args=(scheduler, stop),
            name="qtrade-daily-report",
            daemon=True,
        )
        worker.start()
    try:
        uvicorn.run(application, host="127.0.0.1", port=8000)
    finally:
        stop.set()
        if worker is not None:
            worker.join(timeout=5)
else:
    app = build_app()
