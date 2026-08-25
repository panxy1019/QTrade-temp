"""Fetch a synchronized multi-asset historical 1-minute dataset from Sina.

The command is research-only. It keeps only complete provider-native sessions,
does not synthesize missing timestamps, and does not attest realtime readiness.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import Counter
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from qtrade.data import DataManifest, ParquetBarStore, QualityStatus
from qtrade.market_data import (
    SINA_COMPLETE_SESSION_TIMES,
    SinaMinuteHistoryClient,
    complete_session_days,
)
from qtrade.persistence import SQLiteMetadataCatalog

_OPAQUE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


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


def _load_universe(path: Path) -> tuple[dict[str, Any], tuple[str, ...]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    etfs = payload.get("etfs")
    stocks = payload.get("stocks")
    if not isinstance(etfs, list) or not isinstance(stocks, list):
        raise ValueError("minute universe must contain ETF and stock lists")
    stock_symbols = [row.get("symbol") for row in stocks if isinstance(row, dict)]
    symbols = tuple([*etfs, *stock_symbols])
    if len(etfs) < 20 or len(stocks) < 15 or len(symbols) != len(etfs) + len(stocks):
        raise ValueError("minute universe must span at least 20 ETFs and 15 stocks")
    if any(not isinstance(symbol, str) or not re.fullmatch(r"\d{6}", symbol) for symbol in symbols):
        raise ValueError("minute universe contains a malformed symbol")
    if len(set(symbols)) != len(symbols):
        raise ValueError("minute universe contains duplicate symbols")
    sectors = {
        row.get("sector_code")
        for row in stocks
        if isinstance(row, dict) and isinstance(row.get("sector_code"), str)
    }
    if len(sectors) < 15:
        raise ValueError("minute stock universe must span at least 15 sectors")
    if payload.get("point_in_time") is not False:
        raise ValueError("minute universe must explicitly disclose PIT=false")
    return payload, symbols


def _load_balanced_stock_universe(
    path: Path,
) -> tuple[dict[str, Any], tuple[str, ...]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("balanced stock universe must be an object")
    supplied_digest = payload.get("evidence_sha256")
    unsigned = {key: value for key, value in payload.items() if key != "evidence_sha256"}
    if supplied_digest != _canonical_sha256(unsigned):
        raise ValueError("balanced stock universe evidence signature mismatch")
    symbols = tuple(payload.get("symbols", ()))
    stocks = payload.get("stocks")
    if (
        not isinstance(stocks, list)
        or len(symbols) != 75
        or len(stocks) != 75
        or not all(isinstance(row, dict) for row in stocks)
        or len(set(symbols)) != 75
        or tuple(row.get("symbol") for row in stocks) != symbols
    ):
        raise ValueError("balanced stock universe must contain 75 ordered stocks")
    if any(
        not isinstance(symbol, str) or re.fullmatch(r"\d{6}", symbol) is None for symbol in symbols
    ):
        raise ValueError("balanced stock universe contains a malformed symbol")
    if any(
        not isinstance(row.get(key), str) or not row[key]
        for row in stocks
        for key in ("name", "sector_code", "sector_name")
    ):
        raise ValueError("balanced stock universe metadata is malformed")
    sector_counts = Counter(row["sector_code"] for row in stocks)
    selection = payload.get("selection")
    if (
        len(sector_counts) != 15
        or set(sector_counts.values()) != {5}
        or payload.get("point_in_time") is not False
        or not isinstance(selection, dict)
        or selection.get("candidate_data_access_before_freeze") is not False
    ):
        raise ValueError("balanced stock universe must be frozen, non-PIT, and contain 15x5 stocks")
    return payload, symbols


def _combine_universes(base: dict[str, Any], stock_universe: dict[str, Any]) -> dict[str, Any]:
    return {
        **base,
        "universe_id": "sina-minute-etf30-stock-sw15x5-current-v2",
        "classification": "current_etf30_csi300_sw15x5_non_pit_historical_research",
        "survivorship_bias_warning": (
            "Current ETF candidates and current CSI 300 sector-balanced stocks "
            "are not point-in-time membership."
        ),
        "stocks": stock_universe["stocks"],
        "stock_universe_provenance": {
            "universe_id": stock_universe["universe_id"],
            "evidence_sha256": stock_universe["evidence_sha256"],
            "known_at": stock_universe["known_at"],
            "constituent_as_of": stock_universe["constituent_as_of"],
            "selection": stock_universe["selection"],
        },
    }


def _sign(value: dict[str, Any]) -> dict[str, Any]:
    return {**value, "evidence_sha256": _canonical_sha256(value)}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--universe",
        type=Path,
        default=Path("configs/sina_minute_universe_v1.json"),
    )
    parser.add_argument(
        "--stock-universe",
        type=Path,
        help="optional signed CSI 300 SW 15x5 universe replacing the 15 representatives",
    )
    parser.add_argument(
        "--output-root", type=Path, default=Path("runtime/data/sina-minute")
    )
    parser.add_argument("--sessions", type=int, default=5)
    parser.add_argument("--data-version")
    args = parser.parse_args()

    if os.environ.get("QTRADE_AKSHARE_NONCOMMERCIAL_ACK", "").lower() != "true":
        raise ValueError("minute ingest requires explicit noncommercial research acknowledgement")
    if not 2 <= args.sessions <= 8:
        raise ValueError("minute ingest requires between 2 and 8 complete sessions")
    universe, symbols = _load_universe(args.universe)
    if args.stock_universe is not None:
        stock_universe, stock_symbols = _load_balanced_stock_universe(args.stock_universe)
        universe = _combine_universes(universe, stock_universe)
        symbols = (*tuple(universe["etfs"]), *stock_symbols)
        if len(set(symbols)) != len(symbols):
            raise ValueError("combined minute universe contains duplicate symbols")
    data_version = args.data_version or (
        f"{universe['universe_id']}-sina-raw-1m-{args.sessions}s"
    )
    if _OPAQUE_ID.fullmatch(data_version) is None:
        raise ValueError("data-version must be a bounded opaque identifier")

    client = SinaMinuteHistoryClient()
    raw_by_symbol = {symbol: client.fetch(symbol) for symbol in symbols}
    complete_by_symbol = {
        symbol: set(complete_session_days(bars)) for symbol, bars in raw_by_symbol.items()
    }
    requested_etfs = tuple(universe["etfs"])
    requested_stocks = tuple(row["symbol"] for row in universe["stocks"])
    incomplete_stocks = {
        symbol: len(complete_by_symbol[symbol])
        for symbol in requested_stocks
        if len(complete_by_symbol[symbol]) < args.sessions
    }
    if incomplete_stocks:
        raise RuntimeError(
            "minute source returned incomplete sector stock histories: "
            + json.dumps(incomplete_stocks, sort_keys=True)
        )
    stock_common_days = sorted(
        set.intersection(*(complete_by_symbol[symbol] for symbol in requested_stocks))
    )
    if len(stock_common_days) < args.sessions:
        raise RuntimeError("sector stocks have too few synchronized complete sessions")
    selected_days = tuple(stock_common_days[-args.sessions :])
    selected = set(selected_days)
    excluded_etfs = {
        symbol: {
            "complete_session_count": len(complete_by_symbol[symbol]),
            "missing_selected_days": sorted(
                day.isoformat() for day in selected - complete_by_symbol[symbol]
            ),
        }
        for symbol in requested_etfs
        if not selected.issubset(complete_by_symbol[symbol])
    }
    eligible_etfs = tuple(
        symbol for symbol in requested_etfs if symbol not in excluded_etfs
    )
    if len(eligible_etfs) < 20:
        raise RuntimeError("minute source returned fewer than 20 synchronized ETFs")
    eligible_symbols = (*eligible_etfs, *requested_stocks)
    bars = sorted(
        (
            bar
            for symbol in eligible_symbols
            for bar in raw_by_symbol[symbol]
            if bar.trading_day in selected
        ),
        key=lambda bar: (bar.timestamp, bar.exchange, bar.symbol),
    )
    expected_count = (
        len(eligible_symbols) * len(selected_days) * len(SINA_COMPLETE_SESSION_TIMES)
    )
    if len(bars) != expected_count:
        raise RuntimeError("minute synchronized dataset bar count is inconsistent")
    counts = Counter((bar.symbol, bar.trading_day) for bar in bars)
    if set(counts.values()) != {len(SINA_COMPLETE_SESSION_TIMES)}:
        raise RuntimeError("minute synchronized dataset contains an incomplete session")

    ingestion_time = datetime.now(UTC)
    store = ParquetBarStore(args.output_root)
    pending = DataManifest(
        data_version=data_version,
        source=(
            "Sina CN_MarketDataService.getKLineData raw 1-minute; "
            "238 provider-native timestamps per completed session"
        ),
        schema_version="qtrade.bar.v1",
        ingestion_time=ingestion_time,
        checksum="pending",
        quality_status=QualityStatus.PENDING,
    )
    manifest = store.write(bars, pending)
    SQLiteMetadataCatalog(args.output_root / "metadata.sqlite").register_dataset(manifest)
    dataset_dir = args.output_root.resolve() / data_version
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
            "universe": universe,
            "universe_sha256": _canonical_sha256(universe),
            "requested_symbol_count": len(symbols),
            "symbol_count": len(eligible_symbols),
            "eligible_symbols": list(eligible_symbols),
            "etf_count": len(eligible_etfs),
            "stock_count": len(universe["stocks"]),
            "sector_count": len({row["sector_code"] for row in universe["stocks"]}),
            "stock_count_by_sector": dict(
                sorted(Counter(row["sector_code"] for row in universe["stocks"]).items())
            ),
            "excluded_etfs": [
                {
                    "symbol": symbol,
                    **detail,
                    "reason": "MISSING_LATEST_STOCK_ANCHORED_SESSIONS",
                }
                for symbol, detail in sorted(excluded_etfs.items())
            ],
            "bar_count": len(bars),
            "session_count": len(selected_days),
            "session_bar_count_per_symbol": len(SINA_COMPLETE_SESSION_TIMES),
            "trading_days": [trading_day.isoformat() for trading_day in selected_days],
            "observed_start": bars[0].timestamp.isoformat(),
            "observed_end": bars[-1].timestamp.isoformat(),
            "source_interval_disclosure": {
                "morning": "09:31-11:30",
                "afternoon": "13:01-14:57 plus 15:00 close",
                "synthetic_bars": False,
                "omitted_by_source": ["14:58", "14:59"],
            },
            "classification": "noncommercial_recent_historical_1m_non_pit_not_eligible",
            "snapshot_readiness": False,
            "realtime_readiness": False,
            "real_data_paper_readiness": False,
            "external_order_transmission": False,
        }
    )
    (dataset_dir / "manifest.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
