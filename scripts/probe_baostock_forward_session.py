"""Collect one immutable complete BaoStock session under the forward lock."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from datetime import UTC, date, datetime, time
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from typing import Any, cast
from zoneinfo import ZoneInfo

from qtrade.data import Bar, DataManifest, ParquetBarStore, QualityStatus
from qtrade.market_data import SINA_FIVE_MINUTE_SESSION_TIMES
from qtrade.market_data.baostock_five_minute import BaoStockHistoryApi
from scripts.baostock_five_minute_history_ingest import _fetch_with_retry
from scripts.run_sina_minute_overlay_backtest import _load_dataset

SHANGHAI = ZoneInfo("Asia/Shanghai")
EXPECTED_PREREGISTRATION_ID = "baostock-locked-forward-observation-v1"


def _canonical_sha256(value: object) -> str:
    from hashlib import sha256

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
    unsigned = {
        key: item
        for key, item in value.items()
        if key != "preregistration_sha256"
    } if isinstance(value, dict) else {}
    if (
        not isinstance(value, dict)
        or digest != _canonical_sha256(unsigned)
        or value.get("preregistration_id") != EXPECTED_PREREGISTRATION_ID
    ):
        raise ValueError("forward preregistration verification failed")
    return cast(dict[str, Any], value)


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


def validate_forward_session(
    *,
    symbols: tuple[str, ...],
    anchor_day: date,
    target_day: date,
    frozen_bars: list[Bar],
    fetched_by_symbol: dict[str, tuple[Bar, ...]],
) -> tuple[list[Bar], str]:
    if target_day <= anchor_day:
        raise ValueError("forward target day must follow its anchor day")
    if set(fetched_by_symbol) != set(symbols):
        raise ValueError("forward fetch does not cover the frozen symbol set")
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
    accepted: list[Bar] = []
    anchor_payload: list[tuple[object, ...]] = []
    for symbol in symbols:
        frozen = frozen_anchor[symbol]
        fetched = fetched_by_symbol[symbol]
        anchor = tuple(bar for bar in fetched if bar.trading_day == anchor_day)
        target = tuple(bar for bar in fetched if bar.trading_day == target_day)
        if (
            len(frozen) != 48
            or len(anchor) != 48
            or len(target) != 48
            or {bar.timestamp.time() for bar in anchor} != expected_times
            or {bar.timestamp.time() for bar in target} != expected_times
        ):
            raise ValueError(f"incomplete forward or anchor session for {symbol}")
        if tuple(map(_bar_identity, frozen)) != tuple(map(_bar_identity, anchor)):
            raise ValueError(f"frozen anchor revision detected for {symbol}")
        anchor_payload.extend(map(_bar_identity, anchor))
        accepted.extend(target)
    accepted.sort(key=lambda bar: (bar.timestamp, bar.exchange, bar.symbol))
    counts = Counter((bar.symbol, bar.trading_day) for bar in accepted)
    if len(accepted) != len(symbols) * 48 or set(counts.values()) != {48}:
        raise ValueError("forward synchronized bar count is inconsistent")
    return accepted, _canonical_sha256(anchor_payload)


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
        "--output-root",
        type=Path,
        default=Path("runtime/data/baostock-forward-sessions"),
    )
    args = parser.parse_args()
    preregistration = _load_preregistration(args.preregistration)
    lock = cast(dict[str, Any], preregistration["collection_lock"])
    if args.target_day < date.fromisoformat(str(lock["first_eligible_trading_day"])):
        raise ValueError("forward target day predates the lock")
    now = datetime.now(SHANGHAI)
    if now.date() < args.target_day or (
        now.date() == args.target_day and now.time() < time(15, 10)
    ):
        raise RuntimeError("forward session cannot be collected before 15:10 China time")
    if os.environ.get("QTRADE_BAOSTOCK_FREE_DATA_ACK", "").lower() != "true":
        raise RuntimeError(
            "set QTRADE_BAOSTOCK_FREE_DATA_ACK=true only for accepted free historical research use"
        )
    warmup = cast(dict[str, Any], preregistration["warmup_dataset"])
    evidence, base_manifest, frozen_bars = _load_dataset(
        args.dataset_root,
        str(warmup["data_version"]),
        expected_classification="free_historical_qfq_5m_current_non_pit_not_eligible",
        expected_interval="5m",
    )
    if (
        base_manifest.checksum != warmup["checksum"]
        or _canonical_sha256(evidence) != warmup["evidence_sha256"]
        or args.anchor_day.isoformat() != warmup["last_trading_day"]
    ):
        raise ValueError("frozen warmup dataset does not match the forward lock")
    symbols = tuple(cast(list[str], evidence["eligible_symbols"]))
    module = import_module("baostock")
    provider_version = version("baostock")
    login = module.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_code}")
    fetched: dict[str, tuple[Bar, ...]] = {}
    try:
        api = cast(BaoStockHistoryApi, module)
        for position, symbol in enumerate(symbols, start=1):
            fetched[symbol] = _fetch_with_retry(
                api,
                symbol=symbol,
                start_date=args.anchor_day.isoformat(),
                end_date=args.target_day.isoformat(),
                adjustflag="2",
            )
            print(
                json.dumps(
                    {"progress": f"{position}/{len(symbols)}", "symbol": symbol},
                    sort_keys=True,
                ),
                flush=True,
            )
    finally:
        logout = module.logout()
        if logout.error_code != "0":
            raise RuntimeError(f"BaoStock logout failed: {logout.error_code}")
    bars, anchor_sha256 = validate_forward_session(
        symbols=symbols,
        anchor_day=args.anchor_day,
        target_day=args.target_day,
        frozen_bars=frozen_bars,
        fetched_by_symbol=fetched,
    )
    data_version = f"baostock-forward-{args.target_day.isoformat()}-qfq-v1"
    target = args.output_root / data_version
    if target.exists():
        raise FileExistsError(f"refusing to overwrite forward session: {target}")
    pending = DataManifest(
        data_version=data_version,
        source=f"BaoStock {provider_version} native 5-minute; adjustflag=2",
        schema_version="qtrade.bar.v1",
        ingestion_time=datetime.now(UTC),
        checksum="pending",
        quality_status=QualityStatus.PENDING,
    )
    manifest = ParquetBarStore(args.output_root).write(bars, pending)
    unsigned: dict[str, Any] = {
        "schema_version": 1,
        "report_id": "baostock-complete-forward-session-v1",
        "collected_at": datetime.now(SHANGHAI).isoformat(),
        "preregistration_id": preregistration["preregistration_id"],
        "preregistration_sha256": preregistration["preregistration_sha256"],
        "manifest": {
            "data_version": manifest.data_version,
            "source": manifest.source,
            "schema_version": manifest.schema_version,
            "ingestion_time": manifest.ingestion_time.isoformat(),
            "checksum": manifest.checksum,
            "quality_status": manifest.quality_status.value,
        },
        "target_day": args.target_day.isoformat(),
        "anchor_day": args.anchor_day.isoformat(),
        "anchor_exact_match": True,
        "anchor_sha256": anchor_sha256,
        "provider": "BaoStock",
        "provider_version": provider_version,
        "frequency": "5",
        "adjustflag": "2",
        "symbol_count": len(symbols),
        "sector_count": 15,
        "bars_per_symbol": 48,
        "bar_count": len(bars),
        "complete_session": True,
        "partial_session": False,
        "point_in_time": False,
        "future_adjustment_knowledge_possible": True,
        "realtime_readiness": False,
        "real_data_paper_readiness": False,
        "promotion": "NOT_ELIGIBLE",
        "external_order_transmission": False,
    }
    report = {**unsigned, "evidence_sha256": _canonical_sha256(unsigned)}
    (target / "manifest.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "status": "ACCEPTED_COMPLETE_SESSION",
                "target_day": args.target_day.isoformat(),
                "bar_count": len(bars),
                "evidence_sha256": report["evidence_sha256"],
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
