"""Build a quality-gated, sector-balanced A-share research dataset."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from hashlib import sha256
from pathlib import Path
from typing import Any

from qtrade.data import Bar, DataManifest, ParquetBarStore, QualityStatus
from qtrade.market_data import AkshareMarketDataProvider
from qtrade.persistence import SQLiteMetadataCatalog

_OPAQUE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")
_EXPECTED_SYMBOLS = 75
_EXPECTED_SECTORS = 15
_EXPECTED_PER_SECTOR = 5


def _canonical_sha256(value: dict[str, Any]) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _load_universe(path: Path) -> tuple[dict[str, Any], tuple[str, ...]]:
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    supplied_digest = payload.get("evidence_sha256")
    unsigned = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    if supplied_digest != _canonical_sha256(unsigned):
        raise ValueError("stock universe evidence signature mismatch")
    symbols = tuple(payload.get("symbols", ()))
    stocks = payload.get("stocks")
    if len(symbols) != _EXPECTED_SYMBOLS or len(set(symbols)) != _EXPECTED_SYMBOLS:
        raise ValueError("stock universe must contain 75 unique symbols")
    if any(not isinstance(symbol, str) or not re.fullmatch(r"\d{6}", symbol) for symbol in symbols):
        raise ValueError("stock universe contains a malformed symbol")
    if not isinstance(stocks, list) or len(stocks) != _EXPECTED_SYMBOLS:
        raise ValueError("stock universe metadata is incomplete")
    if tuple(item.get("symbol") for item in stocks) != symbols:
        raise ValueError("stock universe symbol metadata is inconsistent")
    sector_counts = Counter(str(item.get("sector_code", "")) for item in stocks)
    if len(sector_counts) != _EXPECTED_SECTORS or set(sector_counts.values()) != {
        _EXPECTED_PER_SECTOR
    }:
        raise ValueError("stock universe must contain 15 sectors with 5 stocks each")
    if payload.get("point_in_time") is not False:
        raise ValueError("current stock universe must explicitly disclose PIT=false")
    selection = payload.get("selection")
    if (
        not isinstance(selection, dict)
        or selection.get("candidate_data_access_before_freeze") is not False
    ):
        raise ValueError("stock universe must prove it was frozen before candidate data access")
    return payload, symbols


def _coverage(
    bars: list[Bar], symbols: tuple[str, ...], *, minimum_bars: int
) -> dict[str, dict[str, object]]:
    if minimum_bars <= 0:
        raise ValueError("minimum bars must be positive")
    grouped: dict[str, list[Bar]] = {symbol: [] for symbol in symbols}
    for bar in bars:
        if bar.symbol not in grouped:
            raise ValueError(f"history contains unexpected symbol {bar.symbol}")
        grouped[bar.symbol].append(bar)
    underfilled = [
        symbol for symbol, values in grouped.items() if len(values) < minimum_bars
    ]
    if underfilled:
        raise ValueError("stock history is underfilled: " + ",".join(underfilled))
    return {
        symbol: {
            "bar_count": len(values),
            "observed_start": min(bar.timestamp for bar in values).isoformat(),
            "observed_end": max(bar.timestamp for bar in values).isoformat(),
        }
        for symbol, values in grouped.items()
    }


def _remove_listing_price_discovery_sessions(
    bars: list[Bar],
    symbols: tuple[str, ...],
    *,
    requested_start: datetime,
) -> tuple[list[Bar], dict[str, dict[str, object]]]:
    grouped: dict[str, list[Bar]] = {symbol: [] for symbol in symbols}
    for bar in bars:
        if bar.symbol not in grouped:
            raise ValueError(f"history contains unexpected symbol {bar.symbol}")
        grouped[bar.symbol].append(bar)
    retained: list[Bar] = []
    removed: dict[str, dict[str, object]] = {}
    for symbol, values in grouped.items():
        values.sort(key=lambda bar: bar.timestamp)
        remove_count = (
            min(5, len(values))
            if values and values[0].timestamp >= requested_start + timedelta(days=30)
            else 0
        )
        if remove_count:
            removed_values = values[:remove_count]
            removed[symbol] = {
                "count": remove_count,
                "observed_start": removed_values[0].timestamp.isoformat(),
                "observed_end": removed_values[-1].timestamp.isoformat(),
            }
        retained.extend(values[remove_count:])
    retained.sort(key=lambda bar: (bar.timestamp, bar.exchange, bar.symbol))
    return retained, removed


def _remove_pre_long_suspension_history(
    bars: list[Bar], symbols: tuple[str, ...]
) -> tuple[list[Bar], dict[str, dict[str, object]]]:
    grouped: dict[str, list[Bar]] = {symbol: [] for symbol in symbols}
    for bar in bars:
        if bar.symbol not in grouped:
            raise ValueError(f"history contains unexpected symbol {bar.symbol}")
        grouped[bar.symbol].append(bar)
    retained: list[Bar] = []
    removed: dict[str, dict[str, object]] = {}
    for symbol, values in grouped.items():
        values.sort(key=lambda bar: bar.timestamp)
        reset_indices = [
            index
            for index in range(1, len(values))
            if (values[index].trading_day - values[index - 1].trading_day).days > 90
        ]
        reset_index = reset_indices[-1] if reset_indices else 0
        if reset_index:
            removed_values = values[:reset_index]
            removed[symbol] = {
                "count": len(removed_values),
                "observed_start": removed_values[0].timestamp.isoformat(),
                "observed_end": removed_values[-1].timestamp.isoformat(),
                "retained_start": values[reset_index].timestamp.isoformat(),
            }
        retained.extend(values[reset_index:])
    retained.sort(key=lambda bar: (bar.timestamp, bar.exchange, bar.symbol))
    return retained, removed


def _sign_evidence(evidence: dict[str, Any]) -> dict[str, Any]:
    return {**evidence, "evidence_sha256": _canonical_sha256(evidence)}


def _cached_bars(
    path: Path,
    *,
    symbol: str,
    adjustment: str,
    requested_start: str,
    requested_end_exclusive: str,
) -> list[Bar] | None:
    if not path.exists():
        return None
    payload: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    digest = payload.get("evidence_sha256")
    unsigned = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    if digest != _canonical_sha256(unsigned):
        raise ValueError(f"cached history signature mismatch for {symbol}")
    expected = (symbol, adjustment, requested_start, requested_end_exclusive)
    observed = (
        payload.get("symbol"),
        payload.get("adjustment"),
        payload.get("requested_start"),
        payload.get("requested_end_exclusive"),
    )
    if observed != expected:
        raise ValueError(f"cached history request identity mismatch for {symbol}")
    rows = payload.get("bars")
    if not isinstance(rows, list) or not rows:
        raise ValueError(f"cached history is empty for {symbol}")
    bars = [
        Bar(
            symbol=row["symbol"],
            exchange=row["exchange"],
            timestamp=datetime.fromisoformat(row["timestamp"]),
            trading_day=datetime.fromisoformat(row["trading_day"]).date(),
            open=Decimal(row["open"]),
            high=Decimal(row["high"]),
            low=Decimal(row["low"]),
            close=Decimal(row["close"]),
            volume=Decimal(row["volume"]),
        )
        for row in rows
    ]
    if any(bar.symbol != symbol for bar in bars):
        raise ValueError(f"cached history symbol mismatch for {symbol}")
    return bars


def _write_cached_bars(
    path: Path,
    bars: list[Bar],
    *,
    symbol: str,
    adjustment: str,
    requested_start: str,
    requested_end_exclusive: str,
) -> str:
    if not bars or any(bar.symbol != symbol for bar in bars):
        raise ValueError("cannot cache empty or mismatched stock history")
    payload = _sign_evidence(
        {
            "symbol": symbol,
            "adjustment": adjustment,
            "requested_start": requested_start,
            "requested_end_exclusive": requested_end_exclusive,
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
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(".tmp")
    temporary.write_text(
        json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":")),
        encoding="utf-8",
    )
    temporary.replace(path)
    return str(payload["evidence_sha256"])


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
        "--output-root", type=Path, default=Path("runtime/data/akshare-stocks")
    )
    parser.add_argument(
        "--cache-root",
        type=Path,
        default=Path("runtime/cache/akshare-stock-history"),
    )
    parser.add_argument("--start", default="2018-01-01")
    parser.add_argument("--end", default="2026-08-21")
    parser.add_argument("--minimum-bars", type=int, default=750)
    parser.add_argument("--data-version")
    args = parser.parse_args()

    if os.environ.get("QTRADE_AKSHARE_NONCOMMERCIAL_ACK", "").lower() != "true":
        raise RuntimeError(
            "set QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true only for accepted "
            "noncommercial research use"
        )
    universe, symbols = _load_universe(args.universe)
    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    end = datetime.fromisoformat(args.end).replace(tzinfo=UTC) + timedelta(days=1)
    if start >= end:
        raise ValueError("history window must be non-empty")
    data_version = args.data_version or (
        f"{universe['universe_id']}-hfq-daily-{args.start.replace('-', '')}"
        f"-{args.end.replace('-', '')}"
    )
    if _OPAQUE_ID.fullmatch(data_version) is None:
        raise ValueError("data-version must be a bounded opaque identifier")

    provider = AkshareMarketDataProvider(noncommercial_research_acknowledged=True)
    downloaded_bars: list[Bar] = []
    cache_hashes: dict[str, str] = {}
    try:
        provider.connect_history()
        cache_dir = args.cache_root / data_version
        for index, symbol in enumerate(symbols, start=1):
            cache_path = cache_dir / f"{symbol}.json"
            symbol_bars = _cached_bars(
                cache_path,
                symbol=symbol,
                adjustment="hfq",
                requested_start=start.isoformat(),
                requested_end_exclusive=end.isoformat(),
            )
            source = "cache"
            if symbol_bars is None:
                source = "upstream"
                symbol_bars = list(
                    provider.query_stock_history(
                        (symbol,), start=start, end=end, adjustment="hfq"
                    )
                )
                cache_hashes[symbol] = _write_cached_bars(
                    cache_path,
                    symbol_bars,
                    symbol=symbol,
                    adjustment="hfq",
                    requested_start=start.isoformat(),
                    requested_end_exclusive=end.isoformat(),
                )
            else:
                cached_payload = json.loads(cache_path.read_text(encoding="utf-8"))
                cache_hashes[symbol] = str(cached_payload["evidence_sha256"])
            downloaded_bars.extend(symbol_bars)
            print(
                json.dumps(
                    {
                        "stock": f"{index}/{len(symbols)}",
                        "symbol": symbol,
                        "bar_count": len(symbol_bars),
                        "source": source,
                    },
                    sort_keys=True,
                ),
                flush=True,
            )
    finally:
        provider.close()
    continuous_bars, suspension_history_removed = _remove_pre_long_suspension_history(
        downloaded_bars, symbols
    )
    bars, listing_warmup_removed = _remove_listing_price_discovery_sessions(
        continuous_bars, symbols, requested_start=start
    )
    coverage = _coverage(bars, symbols, minimum_bars=args.minimum_bars)

    ingestion_time = datetime.now(UTC)
    pending = DataManifest(
        data_version=data_version,
        source="AKShare 1.18.84 stock_zh_a_hist_tx hfq daily",
        schema_version="qtrade.bar.v1",
        ingestion_time=ingestion_time,
        checksum="pending",
        quality_status=QualityStatus.PENDING,
    )
    store = ParquetBarStore(args.output_root)
    manifest = store.write(bars, pending)
    SQLiteMetadataCatalog(args.output_root / "metadata.sqlite").register_dataset(manifest)
    dataset_dir = args.output_root.resolve() / data_version
    evidence = _sign_evidence(
        {
            "manifest": {
                "data_version": manifest.data_version,
                "source": manifest.source,
                "schema_version": manifest.schema_version,
                "ingestion_time": manifest.ingestion_time.isoformat(),
                "checksum": manifest.checksum,
                "quality_status": manifest.quality_status.value,
            },
            "universe": universe,
            "adjustment": "hfq",
            "requested_start": start.isoformat(),
            "requested_end_exclusive": end.isoformat(),
            "minimum_bars_per_symbol": args.minimum_bars,
            "downloaded_bar_count": len(downloaded_bars),
            "signed_request_cache": cache_hashes,
            "bar_count": len(bars),
            "coverage": coverage,
            "listing_price_discovery_sessions_removed": listing_warmup_removed,
            "pre_long_suspension_history_removed": suspension_history_removed,
            "observed_start": min(bar.timestamp for bar in bars).isoformat(),
            "observed_end": max(bar.timestamp for bar in bars).isoformat(),
            "classification": "noncommercial_research_historical_only",
            "point_in_time": False,
            "snapshot_readiness": False,
            "real_data_paper_readiness": False,
            "protocol_revision": {
                "made_before_strategy_outcomes": True,
                "reason": (
                    "QFQ rejected before backtesting because 300750 contained "
                    "negative OHLC values on 2018-06-11 and 2018-06-12"
                ),
                "replacement": "uniform HFQ for all 75 symbols",
            },
            "limitations": [
                "CURRENT_2026_CONSTITUENTS_PROJECTED_BACKWARD",
                "HFQ_SERIES_CAN_BE_REVISED_AND_IS_NOT_POINT_IN_TIME",
                "SINGLE_PUBLIC_DATA_SOURCE",
                "FIRST_FIVE_SESSIONS_REMOVED_FOR_STOCKS_LISTED_IN_WINDOW",
                "HISTORY_BEFORE_LAST_GAP_OVER_90_CALENDAR_DAYS_REMOVED",
            ],
        }
    )
    (dataset_dir / "manifest.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(
        json.dumps(
            {
                "data_version": data_version,
                "symbol_count": len(symbols),
                "bar_count": len(bars),
                "observed_start": evidence["observed_start"],
                "observed_end": evidence["observed_end"],
                "evidence_sha256": evidence["evidence_sha256"],
            },
            ensure_ascii=False,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
