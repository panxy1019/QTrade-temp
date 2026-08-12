"""Build a canonical, quality-gated ETF daily-bar dataset from AKShare.

This command is deliberately historical-only. It does not attest snapshot or
REAL_DATA_PAPER readiness and it never starts a trading runtime.
"""

from __future__ import annotations

import argparse
import json
import os
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from qtrade.data import DataManifest, ParquetBarStore, QualityStatus
from qtrade.market_data import AkshareMarketDataProvider
from qtrade.persistence import SQLiteMetadataCatalog

_OPAQUE_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}")


def _load_universe(path: Path) -> tuple[dict[str, Any], tuple[str, ...]]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    symbols = tuple(payload.get("symbols", ()))
    if not 20 <= len(symbols) <= 50:
        raise ValueError("AKShare ETF universe must contain 20-50 symbols")
    if len(set(symbols)) != len(symbols):
        raise ValueError("AKShare ETF universe contains duplicate symbols")
    if any(not isinstance(symbol, str) or len(symbol) != 6 for symbol in symbols):
        raise ValueError("AKShare ETF universe contains a malformed symbol")
    if payload.get("point_in_time") is not False:
        raise ValueError("current-universe ingest must explicitly disclose PIT=false")
    return payload, symbols


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--universe",
        type=Path,
        default=Path("configs/akshare_etf_universe_v1.json"),
    )
    parser.add_argument("--output-root", type=Path, default=Path("runtime/data/akshare"))
    parser.add_argument("--start", default="2020-01-01")
    parser.add_argument("--end", default=datetime.now(UTC).date().isoformat())
    parser.add_argument("--data-version")
    args = parser.parse_args()

    acknowledged = (
        os.environ.get("QTRADE_AKSHARE_NONCOMMERCIAL_ACK", "").lower() == "true"
    )
    universe, symbols = _load_universe(args.universe)
    start = datetime.fromisoformat(args.start).replace(tzinfo=UTC)
    end = datetime.fromisoformat(args.end).replace(tzinfo=UTC) + timedelta(days=1)
    if start >= end:
        raise ValueError("history window must be non-empty")
    data_version = args.data_version or (
        f"{universe['universe_id']}-raw-daily-{args.start.replace('-', '')}"
        f"-{args.end.replace('-', '')}"
    )
    if _OPAQUE_ID.fullmatch(data_version) is None:
        raise ValueError("data-version must be a bounded opaque identifier")

    provider = AkshareMarketDataProvider(
        noncommercial_research_acknowledged=acknowledged
    )
    try:
        provider.connect_history(probe_symbol=symbols[0])
        bars = list(
            provider.query_history(symbols, start=start, end=end, interval="1d")
        )
    finally:
        provider.close()

    ingestion_time = datetime.now(UTC)
    pending = DataManifest(
        data_version=data_version,
        source="AKShare 1.18.84 fund_etf_hist_sina raw daily",
        schema_version="qtrade.bar.v1",
        ingestion_time=ingestion_time,
        checksum="pending",
        quality_status=QualityStatus.PENDING,
    )
    store = ParquetBarStore(args.output_root)
    manifest = store.write(bars, pending)
    SQLiteMetadataCatalog(args.output_root / "metadata.sqlite").register_dataset(manifest)
    dataset_dir = args.output_root.resolve() / data_version
    evidence = {
        "manifest": {
            "data_version": manifest.data_version,
            "source": manifest.source,
            "schema_version": manifest.schema_version,
            "ingestion_time": manifest.ingestion_time.isoformat(),
            "checksum": manifest.checksum,
            "quality_status": manifest.quality_status.value,
        },
        "universe": universe,
        "requested_start": start.isoformat(),
        "requested_end_exclusive": end.isoformat(),
        "bar_count": len(bars),
        "observed_start": min(bar.timestamp for bar in bars).isoformat(),
        "observed_end": max(bar.timestamp for bar in bars).isoformat(),
        "classification": "noncommercial_research_historical_only",
        "snapshot_readiness": False,
        "real_data_paper_readiness": False,
    }
    (dataset_dir / "manifest.json").write_text(
        json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    print(json.dumps(evidence, ensure_ascii=False, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
