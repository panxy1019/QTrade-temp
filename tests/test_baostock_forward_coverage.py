from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import Decimal
from zoneinfo import ZoneInfo

from qtrade.data import Bar
from qtrade.market_data import SINA_FIVE_MINUTE_SESSION_TIMES
from scripts.probe_baostock_forward_coverage import (
    _canonical_sha256,
    build_coverage_report,
)

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _session(
    symbol: str,
    trading_day: date,
    *,
    close_offset: str = "0",
) -> tuple[Bar, ...]:
    return tuple(
        Bar(
            symbol=symbol,
            exchange="SSE",
            timestamp=datetime.combine(trading_day, value, SHANGHAI),
            trading_day=trading_day,
            open=Decimal("10"),
            high=Decimal("10.2"),
            low=Decimal("9.9"),
            close=Decimal("10.1") + Decimal(close_offset),
            volume=Decimal("1000"),
        )
        for value in SINA_FIVE_MINUTE_SESSION_TIMES
    )


def _preregistration() -> dict[str, object]:
    return {
        "preregistration_id": "baostock-locked-forward-observation-v1",
        "preregistration_sha256": "a" * 64,
        "warmup_dataset": {
            "data_version": "warmup-v1",
            "checksum": "b" * 64,
            "evidence_sha256": "c" * 64,
            "last_trading_day": "2026-08-25",
        },
        "collection_lock": {
            "first_eligible_trading_day": "2026-08-26",
            "provider": "BaoStock",
            "provider_frequency": "5",
            "adjustflag": "2",
            "required_symbol_count": 2,
            "required_bars_per_symbol_session": 48,
        },
    }


def test_forward_coverage_reports_missing_target_without_entering_metrics() -> None:
    symbols = ("600036", "601166")
    anchor_day = date(2026, 8, 25)
    target_day = date(2026, 8, 26)
    frozen = [
        replace(bar, timestamp=bar.timestamp.astimezone(UTC))
        for symbol in symbols
        for bar in _session(symbol, anchor_day)
    ]
    fetched = {
        "600036": (*_session("600036", anchor_day), *_session("600036", target_day)),
        "601166": _session("601166", anchor_day),
    }

    report = build_coverage_report(
        preregistration=_preregistration(),
        symbols=symbols,
        frozen_bars=frozen,
        fetched_by_symbol=fetched,
        fetch_error_symbols=(),
        anchor_day=anchor_day,
        target_day=target_day,
        provider_version="0.9.3",
        generated_at=datetime(2026, 8, 26, 18, tzinfo=SHANGHAI),
    )

    assert report["status"] == "SOURCE_INCOMPLETE"
    assert report["target_complete_symbol_count"] == 1
    assert report["ready_symbol_count"] == 1
    assert report["incomplete_symbols"] == ["601166"]
    assert report["target_incomplete_symbols"] == ["601166"]
    assert report["ready_for_formal_collection"] is False
    assert report["enters_forward_metrics"] is False
    assert report["real_data_paper_readiness"] is False
    assert report["external_order_transmission"] is False
    unsigned = {key: value for key, value in report.items() if key != "evidence_sha256"}
    assert report["evidence_sha256"] == _canonical_sha256(unsigned)


def test_forward_coverage_requires_exact_anchor_before_ready() -> None:
    symbols = ("600036", "601166")
    anchor_day = date(2026, 8, 25)
    target_day = date(2026, 8, 26)
    frozen = [
        replace(bar, timestamp=bar.timestamp.astimezone(UTC))
        for symbol in symbols
        for bar in _session(symbol, anchor_day)
    ]
    fetched = {
        symbol: (*_session(symbol, anchor_day), *_session(symbol, target_day))
        for symbol in symbols
    }
    fetched["601166"] = (
        *_session("601166", anchor_day, close_offset="0.01"),
        *_session("601166", target_day),
    )

    revised = build_coverage_report(
        preregistration=_preregistration(),
        symbols=symbols,
        frozen_bars=frozen,
        fetched_by_symbol=fetched,
        fetch_error_symbols=(),
        anchor_day=anchor_day,
        target_day=target_day,
        provider_version="0.9.3",
        generated_at=datetime(2026, 8, 26, 18, tzinfo=SHANGHAI),
    )
    assert revised["target_complete_symbol_count"] == 2
    assert revised["ready_symbol_count"] == 1
    assert revised["incomplete_symbols"] == ["601166"]
    assert revised["target_incomplete_symbols"] == []

    ready_fetched = {
        symbol: (*_session(symbol, anchor_day), *_session(symbol, target_day))
        for symbol in symbols
    }
    ready = build_coverage_report(
        preregistration=_preregistration(),
        symbols=symbols,
        frozen_bars=frozen,
        fetched_by_symbol=ready_fetched,
        fetch_error_symbols=(),
        anchor_day=anchor_day,
        target_day=target_day,
        provider_version="0.9.3",
        generated_at=datetime(2026, 8, 26, 18, tzinfo=SHANGHAI),
    )
    assert ready["status"] == "READY_FOR_FORMAL_COLLECTION"
    assert ready["ready_for_formal_collection"] is True
    assert ready["promotion"] == "NOT_ELIGIBLE"
    assert ready["enters_forward_metrics"] is False
