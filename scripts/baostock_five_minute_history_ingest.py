"""Build a synchronized 75-stock native 5-minute BaoStock research dataset."""

from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import Counter
from datetime import UTC, datetime
from decimal import Decimal
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from typing import Any, cast

from qtrade.data import Bar, BarQualityGate, DataManifest, ParquetBarStore, QualityStatus
from qtrade.market_data import (
    SINA_FIVE_MINUTE_SESSION_TIMES,
    complete_session_days,
    fetch_baostock_five_minute_history,
)
from qtrade.market_data.baostock_five_minute import BaoStockHistoryApi
from qtrade.persistence import SQLiteMetadataCatalog

try:
    from scripts.sina_minute_history_ingest import (
        _canonical_sha256,
        _load_balanced_stock_universe,
    )
except ModuleNotFoundError:
    from sina_minute_history_ingest import (  # type: ignore[no-redef]
        _canonical_sha256,
        _load_balanced_stock_universe,
    )

_OPAQUE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _sign(value: dict[str, Any]) -> dict[str, Any]:
    return {**value, "evidence_sha256": _canonical_sha256(value)}


def _bars_payload(
    bars: tuple[Bar, ...],
    *,
    symbol: str,
    provider_version: str,
    start_date: str,
    end_date: str,
    adjustflag: str,
) -> dict[str, Any]:
    return _sign(
        {
            "schema_version": 1,
            "provider": "BaoStock",
            "provider_version": provider_version,
            "symbol": symbol,
            "start_date": start_date,
            "end_date": end_date,
            "frequency": "5",
            "adjustflag": adjustflag,
            "bars": [
                {
                    "symbol": bar.symbol,
                    "exchange": bar.exchange,
                    "timestamp": bar.timestamp.isoformat(),
                    "trading_day": bar.trading_day.isoformat(),
                    "open": str(bar.open),
                    "high": str(bar.high),
                    "low": str(bar.low),
                    "close": str(bar.close),
                    "volume": str(bar.volume),
                }
                for bar in bars
            ],
        }
    )


def _write_cache(
    path: Path,
    bars: tuple[Bar, ...],
    *,
    symbol: str,
    provider_version: str,
    start_date: str,
    end_date: str,
    adjustflag: str,
) -> str:
    payload = _bars_payload(
        bars,
        symbol=symbol,
        provider_version=provider_version,
        start_date=start_date,
        end_date=end_date,
        adjustflag=adjustflag,
    )
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)
    return str(payload["evidence_sha256"])


def _load_cache(
    path: Path,
    *,
    symbol: str,
    provider_version: str,
    start_date: str,
    end_date: str,
    adjustflag: str,
) -> tuple[Bar, ...] | None:
    if not path.is_file():
        return None
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"BaoStock cache is malformed for {symbol}")
    digest = payload.get("evidence_sha256")
    unsigned = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    expected_identity = (
        1,
        "BaoStock",
        provider_version,
        symbol,
        start_date,
        end_date,
        "5",
        adjustflag,
    )
    observed_identity = (
        payload.get("schema_version"),
        payload.get("provider"),
        payload.get("provider_version"),
        payload.get("symbol"),
        payload.get("start_date"),
        payload.get("end_date"),
        payload.get("frequency"),
        payload.get("adjustflag"),
    )
    rows = payload.get("bars")
    if (
        digest != _canonical_sha256(unsigned)
        or observed_identity != expected_identity
        or not isinstance(rows, list)
        or not rows
    ):
        raise ValueError(f"BaoStock cache verification failed for {symbol}")
    bars = tuple(
        Bar(
            symbol=str(row["symbol"]),
            exchange=str(row["exchange"]),
            timestamp=datetime.fromisoformat(str(row["timestamp"])),
            trading_day=datetime.fromisoformat(str(row["trading_day"])).date(),
            open=Decimal(str(row["open"])),
            high=Decimal(str(row["high"])),
            low=Decimal(str(row["low"])),
            close=Decimal(str(row["close"])),
            volume=Decimal(str(row["volume"])),
        )
        for row in rows
        if isinstance(row, dict)
    )
    issues = BarQualityGate().validate(bars, expected_symbol=symbol)
    if len(bars) != len(rows) or issues:
        raise ValueError(f"BaoStock cached bars failed quality checks for {symbol}")
    return bars


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
                time.sleep(0.5 * (2**attempt))
    assert failure is not None
    raise RuntimeError(f"BaoStock history failed after retries for {symbol}") from failure


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--universe",
        type=Path,
        default=Path(
            "runtime/universes/akshare-csi300-sw15x5-current-20260731-v1.json"
        ),
    )
    parser.add_argument(
        "--output-root", type=Path, default=Path("runtime/data/baostock-five-minute")
    )
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=Path("runtime/cache/baostock-five-minute"),
    )
    parser.add_argument("--start", default="2025-01-02")
    parser.add_argument("--end", default="2026-08-25")
    parser.add_argument("--adjustflag", choices=("2", "3"), default="2")
    parser.add_argument("--minimum-common-sessions", type=int, default=250)
    parser.add_argument("--data-version", required=True)
    args = parser.parse_args()

    if os.environ.get("QTRADE_BAOSTOCK_FREE_DATA_ACK", "").lower() != "true":
        raise RuntimeError(
            "set QTRADE_BAOSTOCK_FREE_DATA_ACK=true only for accepted free "
            "historical research use"
        )
    if _OPAQUE_ID.fullmatch(args.data_version) is None:
        raise ValueError("data-version must be a bounded opaque identifier")
    if args.minimum_common_sessions < 120:
        raise ValueError("long 5-minute study requires at least 120 common sessions")
    if (args.output_root / args.data_version).exists():
        raise FileExistsError(f"refusing to overwrite dataset {args.data_version}")
    universe, symbols = _load_balanced_stock_universe(args.universe)
    provider_version = version("baostock")
    module = import_module("baostock")
    login = module.login()
    if login.error_code != "0":
        raise RuntimeError(f"BaoStock login failed: {login.error_code}")

    raw_by_symbol: dict[str, tuple[Bar, ...]] = {}
    cache_hashes: dict[str, str] = {}
    try:
        api = cast(BaoStockHistoryApi, module)
        for position, symbol in enumerate(symbols, start=1):
            cache_path = args.cache_root / f"{symbol}.json"
            bars = _load_cache(
                cache_path,
                symbol=symbol,
                provider_version=provider_version,
                start_date=args.start,
                end_date=args.end,
                adjustflag=args.adjustflag,
            )
            source = "CACHE"
            if bars is None:
                bars = _fetch_with_retry(
                    api,
                    symbol=symbol,
                    start_date=args.start,
                    end_date=args.end,
                    adjustflag=args.adjustflag,
                )
                cache_hashes[symbol] = _write_cache(
                    cache_path,
                    bars,
                    symbol=symbol,
                    provider_version=provider_version,
                    start_date=args.start,
                    end_date=args.end,
                    adjustflag=args.adjustflag,
                )
                source = "NETWORK"
            else:
                cache_payload = json.loads(cache_path.read_text(encoding="utf-8"))
                cache_hashes[symbol] = str(cache_payload["evidence_sha256"])
            raw_by_symbol[symbol] = bars
            print(
                json.dumps(
                    {
                        "progress": f"{position}/{len(symbols)}",
                        "symbol": symbol,
                        "bar_count": len(bars),
                        "source": source,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
    finally:
        logout = module.logout()
        if logout.error_code != "0":
            raise RuntimeError(f"BaoStock logout failed: {logout.error_code}")

    complete_by_symbol = {
        symbol: set(
            complete_session_days(
                bars, expected_times=SINA_FIVE_MINUTE_SESSION_TIMES
            )
        )
        for symbol, bars in raw_by_symbol.items()
    }
    common_days = sorted(set.intersection(*complete_by_symbol.values()))
    if len(common_days) < args.minimum_common_sessions:
        raise RuntimeError(
            f"BaoStock returned only {len(common_days)} common complete sessions"
        )
    selected_days = set(common_days)
    bars = sorted(
        (
            bar
            for symbol in symbols
            for bar in raw_by_symbol[symbol]
            if bar.trading_day in selected_days
        ),
        key=lambda bar: (bar.timestamp, bar.exchange, bar.symbol),
    )
    expected_count = len(symbols) * len(common_days) * len(
        SINA_FIVE_MINUTE_SESSION_TIMES
    )
    if len(bars) != expected_count:
        raise RuntimeError("BaoStock synchronized 5-minute bar count is inconsistent")
    counts = Counter((bar.symbol, bar.trading_day) for bar in bars)
    if set(counts.values()) != {len(SINA_FIVE_MINUTE_SESSION_TIMES)}:
        raise RuntimeError("BaoStock synchronized dataset contains an incomplete session")

    ingestion_time = datetime.now(UTC)
    pending = DataManifest(
        data_version=args.data_version,
        source=(
            f"BaoStock {provider_version} query_history_k_data_plus native 5-minute; "
            f"adjustflag={args.adjustflag}"
        ),
        schema_version="qtrade.bar.v1",
        ingestion_time=ingestion_time,
        checksum="pending",
        quality_status=QualityStatus.PENDING,
    )
    store = ParquetBarStore(args.output_root)
    manifest = store.write(bars, pending)
    SQLiteMetadataCatalog(args.output_root / "metadata.sqlite").register_dataset(manifest)
    stock_rows = cast(list[dict[str, Any]], universe["stocks"])
    evidence = _sign(
        {
            "manifest": {
                "data_version": manifest.data_version,
                "source": manifest.source,
                "schema_version": manifest.schema_version,
                "ingestion_time": manifest.ingestion_time.isoformat(),
                "checksum": manifest.checksum,
                "quality_status": manifest.quality_status.value,
            },
            "provider": "BaoStock",
            "provider_version": provider_version,
            "request": {
                "start_date": args.start,
                "end_date": args.end,
                "frequency": "5",
                "adjustflag": args.adjustflag,
                "adjustment": "qfq" if args.adjustflag == "2" else "raw",
            },
            "universe": universe,
            "universe_sha256": _canonical_sha256(universe),
            "eligible_symbols": list(symbols),
            "symbol_count": len(symbols),
            "etf_count": 0,
            "stock_count": len(symbols),
            "sector_count": len({row["sector_code"] for row in stock_rows}),
            "stock_count_by_sector": dict(
                sorted(Counter(row["sector_code"] for row in stock_rows).items())
            ),
            "bar_count": len(bars),
            "bar_interval": "5m",
            "session_count": len(common_days),
            "session_bar_count_per_symbol": len(SINA_FIVE_MINUTE_SESSION_TIMES),
            "trading_days": [day.isoformat() for day in common_days],
            "observed_start": bars[0].timestamp.isoformat(),
            "observed_end": bars[-1].timestamp.isoformat(),
            "raw_coverage": [
                {
                    "symbol": symbol,
                    "raw_bar_count": len(raw_by_symbol[symbol]),
                    "complete_session_count": len(complete_by_symbol[symbol]),
                    "excluded_non_common_session_count": len(
                        complete_by_symbol[symbol] - selected_days
                    ),
                    "cache_evidence_sha256": cache_hashes[symbol],
                }
                for symbol in symbols
            ],
            "source_interval_disclosure": {
                "morning": "09:35-11:30 at provider-native 5-minute closes",
                "afternoon": "13:05-15:00 at provider-native 5-minute closes",
                "synthetic_bars": False,
                "common_complete_session_filter": True,
            },
            "classification": (
                "free_historical_qfq_5m_current_non_pit_not_eligible"
                if args.adjustflag == "2"
                else "free_historical_raw_5m_current_non_pit_not_eligible"
            ),
            "point_in_time": False,
            "future_adjustment_knowledge_possible": args.adjustflag == "2",
            "snapshot_readiness": False,
            "realtime_readiness": False,
            "real_data_paper_readiness": False,
            "external_order_transmission": False,
        }
    )
    dataset_dir = args.output_root.resolve() / args.data_version
    (dataset_dir / "manifest.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
