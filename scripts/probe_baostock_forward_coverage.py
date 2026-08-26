"""Diagnose BaoStock coverage without creating forward-session evidence."""

from __future__ import annotations

import argparse
import json
import os
from datetime import date, datetime, time
from hashlib import sha256
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from time import sleep
from typing import Any, cast
from zoneinfo import ZoneInfo

from qtrade.data import Bar, DataManifest, ParquetBarStore, QualityStatus
from qtrade.market_data import (
    SINA_FIVE_MINUTE_SESSION_TIMES,
    fetch_baostock_five_minute_history,
)
from qtrade.market_data.baostock_five_minute import BaoStockHistoryApi
from scripts.atomic_json import write_json_atomic

SHANGHAI = ZoneInfo("Asia/Shanghai")
EXPECTED_PREREGISTRATION_ID = "baostock-locked-forward-observation-v1"


def _canonical_sha256(value: object) -> str:
    return sha256(
        json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            default=str,
        ).encode("utf-8")
    ).hexdigest()


def _load_preregistration(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    digest = value.get("preregistration_sha256") if isinstance(value, dict) else None
    unsigned = (
        {key: item for key, item in value.items() if key != "preregistration_sha256"}
        if isinstance(value, dict)
        else {}
    )
    if (
        not isinstance(value, dict)
        or digest != _canonical_sha256(unsigned)
        or value.get("preregistration_id") != EXPECTED_PREREGISTRATION_ID
    ):
        raise ValueError("forward preregistration verification failed")
    return cast(dict[str, Any], value)


def _load_dataset(
    root: Path,
    version: str,
    *,
    expected_classification: str,
    expected_interval: str,
) -> tuple[dict[str, Any], DataManifest, list[Bar]]:
    signed = json.loads((root / version / "manifest.json").read_text(encoding="utf-8"))
    digest = signed.get("evidence_sha256") if isinstance(signed, dict) else None
    unsigned = (
        {key: item for key, item in signed.items() if key != "evidence_sha256"}
        if isinstance(signed, dict)
        else {}
    )
    value = unsigned.get("manifest")
    if (
        not isinstance(digest, str)
        or _canonical_sha256(unsigned) != digest
        or not isinstance(value, dict)
    ):
        raise ValueError("coverage warmup dataset signature is invalid")
    manifest = DataManifest(
        data_version=str(value["data_version"]),
        source=str(value["source"]),
        schema_version=str(value["schema_version"]),
        ingestion_time=datetime.fromisoformat(str(value["ingestion_time"])),
        checksum=str(value["checksum"]),
        quality_status=QualityStatus(str(value["quality_status"])),
    )
    if (
        manifest.data_version != version
        or manifest.quality_status is not QualityStatus.PASSED
        or unsigned.get("classification") != expected_classification
        or unsigned.get("bar_interval") != expected_interval
    ):
        raise ValueError("coverage warmup dataset identity is invalid")
    return cast(dict[str, Any], unsigned), manifest, ParquetBarStore(root).read(manifest)


def _fetch_with_retry(
    api: BaoStockHistoryApi,
    *,
    symbol: str,
    start_date: str,
    end_date: str,
    adjustflag: str,
    maximum_attempts: int = 3,
) -> tuple[Bar, ...]:
    failure: Exception | None = None
    for attempt in range(maximum_attempts):
        try:
            return fetch_baostock_five_minute_history(
                api,
                symbol=symbol,
                start_date=start_date,
                end_date=end_date,
                adjustflag=adjustflag,
            )
        except Exception as error:
            failure = error
            if attempt + 1 < maximum_attempts:
                sleep(0.5 * (2**attempt))
    assert failure is not None
    raise RuntimeError(f"BaoStock history failed after retries for {symbol}") from failure


def _bar_identity(bar: Bar) -> tuple[object, ...]:
    return (
        bar.symbol,
        bar.exchange,
        bar.timestamp,
        bar.trading_day,
        bar.open,
        bar.high,
        bar.low,
        bar.close,
        bar.volume,
    )


def build_coverage_report(
    *,
    preregistration: dict[str, object],
    symbols: tuple[str, ...],
    frozen_bars: list[Bar],
    fetched_by_symbol: dict[str, tuple[Bar, ...]],
    fetch_error_symbols: tuple[str, ...],
    anchor_day: date,
    target_day: date,
    provider_version: str,
    generated_at: datetime,
) -> dict[str, object]:
    if target_day <= anchor_day:
        raise ValueError("coverage target day must follow its anchor day")
    if generated_at.tzinfo is None:
        raise ValueError("coverage generation time must be timezone-aware")
    if set(fetched_by_symbol) != set(symbols):
        raise ValueError("coverage fetch does not match the frozen symbol set")
    fetch_errors = set(fetch_error_symbols)
    if not fetch_errors.issubset(symbols):
        raise ValueError("coverage fetch errors do not match the frozen symbol set")
    lock = cast(dict[str, object], preregistration["collection_lock"])
    warmup = cast(dict[str, object], preregistration["warmup_dataset"])
    required_symbol_count = cast(int, lock["required_symbol_count"])
    required_bar_count = cast(int, lock["required_bars_per_symbol_session"])
    if (
        required_symbol_count != len(symbols)
        or required_bar_count != len(SINA_FIVE_MINUTE_SESSION_TIMES)
        or lock.get("provider") != "BaoStock"
        or lock.get("provider_frequency") != "5"
        or lock.get("adjustflag") != "2"
        or warmup.get("last_trading_day") != anchor_day.isoformat()
        or target_day < date.fromisoformat(str(lock["first_eligible_trading_day"]))
    ):
        raise ValueError("coverage request does not match the forward lock")

    expected_times = set(SINA_FIVE_MINUTE_SESSION_TIMES)
    frozen_anchor = {
        symbol: tuple(
            sorted(
                (
                    bar
                    for bar in frozen_bars
                    if bar.symbol == symbol and bar.trading_day == anchor_day
                ),
                key=lambda bar: bar.timestamp,
            )
        )
        for symbol in symbols
    }
    rows: list[dict[str, object]] = []
    for symbol in symbols:
        fetched = fetched_by_symbol[symbol]
        anchor = tuple(bar for bar in fetched if bar.trading_day == anchor_day)
        target = tuple(bar for bar in fetched if bar.trading_day == target_day)
        frozen = frozen_anchor[symbol]
        frozen_anchor_complete = (
            len(frozen) == required_bar_count
            and {
                bar.timestamp.astimezone(SHANGHAI).time() for bar in frozen
            }
            == expected_times
        )
        anchor_grid_complete = (
            len(anchor) == required_bar_count
            and {
                bar.timestamp.astimezone(SHANGHAI).time() for bar in anchor
            }
            == expected_times
        )
        target_grid_complete = (
            len(target) == required_bar_count
            and {
                bar.timestamp.astimezone(SHANGHAI).time() for bar in target
            }
            == expected_times
        )
        anchor_exact_match = (
            frozen_anchor_complete
            and anchor_grid_complete
            and tuple(map(_bar_identity, frozen)) == tuple(map(_bar_identity, anchor))
        )
        reasons: list[str] = []
        if symbol in fetch_errors:
            reasons.append("FETCH_FAILED")
        if not frozen_anchor_complete:
            reasons.append("FROZEN_ANCHOR_INCOMPLETE")
        if not anchor_grid_complete:
            reasons.append("FETCHED_ANCHOR_INCOMPLETE")
        if not target_grid_complete:
            reasons.append("TARGET_SESSION_INCOMPLETE")
        if anchor_grid_complete and frozen_anchor_complete and not anchor_exact_match:
            reasons.append("ANCHOR_REVISION")
        rows.append(
            {
                "symbol": symbol,
                "fetch_status": "ERROR" if symbol in fetch_errors else "OK",
                "frozen_anchor_bar_count": len(frozen),
                "anchor_bar_count": len(anchor),
                "target_bar_count": len(target),
                "frozen_anchor_grid_complete": frozen_anchor_complete,
                "anchor_grid_complete": anchor_grid_complete,
                "target_grid_complete": target_grid_complete,
                "anchor_exact_match": anchor_exact_match,
                "ready": not reasons,
                "reasons": reasons,
            }
        )

    ready_symbols = [cast(str, row["symbol"]) for row in rows if row["ready"] is True]
    target_complete_symbols = [
        cast(str, row["symbol"])
        for row in rows
        if row["target_grid_complete"] is True
    ]
    incomplete_symbols = [
        cast(str, row["symbol"]) for row in rows if row["ready"] is False
    ]
    target_incomplete_symbols = [
        cast(str, row["symbol"])
        for row in rows
        if row["target_grid_complete"] is False
    ]
    ready = len(ready_symbols) == required_symbol_count
    unsigned: dict[str, object] = {
        "schema_version": 1,
        "report_id": "baostock-forward-source-coverage-diagnostic-v1",
        "generated_at": generated_at.astimezone(SHANGHAI).isoformat(),
        "classification": "SOURCE_COVERAGE_DIAGNOSTIC_NOT_FORWARD_EVIDENCE",
        "provider": "BaoStock",
        "provider_version": provider_version,
        "frequency": "5",
        "adjustflag": "2",
        "anchor_day": anchor_day.isoformat(),
        "target_day": target_day.isoformat(),
        "preregistration_id": preregistration["preregistration_id"],
        "preregistration_sha256": preregistration["preregistration_sha256"],
        "warmup_data_version": warmup["data_version"],
        "warmup_checksum": warmup["checksum"],
        "warmup_evidence_sha256": warmup["evidence_sha256"],
        "required_symbol_count": required_symbol_count,
        "required_bars_per_symbol_session": required_bar_count,
        "target_complete_symbol_count": len(target_complete_symbols),
        "ready_symbol_count": len(ready_symbols),
        "incomplete_symbol_count": len(incomplete_symbols),
        "incomplete_symbols": incomplete_symbols,
        "target_incomplete_symbols": target_incomplete_symbols,
        "fetch_error_symbols": sorted(fetch_errors),
        "coverage": rows,
        "status": "READY_FOR_FORMAL_COLLECTION" if ready else "SOURCE_INCOMPLETE",
        "ready_for_formal_collection": ready,
        "enters_forward_metrics": False,
        "promotion": "NOT_ELIGIBLE",
        "realtime_readiness": False,
        "real_data_paper_readiness": False,
        "external_order_transmission": False,
    }
    return {**unsigned, "evidence_sha256": _canonical_sha256(unsigned)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target-day", type=date.fromisoformat, required=True)
    parser.add_argument("--anchor-day", type=date.fromisoformat, required=True)
    parser.add_argument(
        "--preregistration",
        type=Path,
        default=Path(
            "docs/preregistrations/baostock-locked-forward-observation-v1.json"
        ),
    )
    parser.add_argument(
        "--dataset-root",
        type=Path,
        default=Path("runtime/data/baostock-five-minute"),
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("deployment/evidence/forward-source-coverage/report.json"),
    )
    args = parser.parse_args()
    preregistration = _load_preregistration(args.preregistration)
    lock = cast(dict[str, Any], preregistration["collection_lock"])
    now = datetime.now(SHANGHAI)
    if args.target_day < date.fromisoformat(str(lock["first_eligible_trading_day"])):
        raise ValueError("coverage target day predates the forward lock")
    if now.date() < args.target_day or (
        now.date() == args.target_day and now.time() < time(15, 10)
    ):
        raise RuntimeError("coverage cannot be probed before 15:10 China time")
    if os.environ.get("QTRADE_BAOSTOCK_FREE_DATA_ACK", "").lower() != "true":
        raise RuntimeError("set QTRADE_BAOSTOCK_FREE_DATA_ACK=true for the free source")
    warmup = cast(dict[str, Any], preregistration["warmup_dataset"])
    evidence, manifest, frozen_bars = _load_dataset(
        args.dataset_root,
        str(warmup["data_version"]),
        expected_classification="free_historical_qfq_5m_current_non_pit_not_eligible",
        expected_interval="5m",
    )
    if (
        manifest.checksum != warmup["checksum"]
        or _canonical_sha256(evidence) != warmup["evidence_sha256"]
        or args.anchor_day.isoformat() != warmup["last_trading_day"]
    ):
        raise ValueError("coverage warmup dataset does not match the forward lock")
    symbols = tuple(cast(list[str], evidence["eligible_symbols"]))
    module = import_module("baostock")
    provider_version = version("baostock")
    login = module.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_code}")
    fetched: dict[str, tuple[Bar, ...]] = {}
    fetch_errors: list[str] = []
    try:
        api = cast(BaoStockHistoryApi, module)
        for position, symbol in enumerate(symbols, start=1):
            try:
                fetched[symbol] = _fetch_with_retry(
                    api,
                    symbol=symbol,
                    start_date=args.anchor_day.isoformat(),
                    end_date=args.target_day.isoformat(),
                    adjustflag="2",
                )
                fetch_status = "OK"
            except Exception:
                fetched[symbol] = ()
                fetch_errors.append(symbol)
                fetch_status = "ERROR"
            print(
                json.dumps(
                    {
                        "progress": f"{position}/{len(symbols)}",
                        "symbol": symbol,
                        "fetch_status": fetch_status,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
    finally:
        logout = module.logout()
        if logout.error_code != "0":
            raise RuntimeError(f"BaoStock logout failed: {logout.error_code}")
    report = build_coverage_report(
        preregistration=cast(dict[str, object], preregistration),
        symbols=symbols,
        frozen_bars=frozen_bars,
        fetched_by_symbol=fetched,
        fetch_error_symbols=tuple(fetch_errors),
        anchor_day=args.anchor_day,
        target_day=args.target_day,
        provider_version=provider_version,
        generated_at=datetime.now(SHANGHAI),
    )
    write_json_atomic(args.output, report)
    print(
        json.dumps(
            {
                "output": str(args.output),
                "status": report["status"],
                "ready_symbol_count": report["ready_symbol_count"],
                "required_symbol_count": report["required_symbol_count"],
                "target_incomplete_symbols": report["target_incomplete_symbols"],
                "enters_forward_metrics": False,
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
