"""Refresh actual chart/backtest datasets independently of frozen experiment evidence."""

from __future__ import annotations

import argparse
import json
import os
import shutil
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time
from hashlib import sha256
from pathlib import Path
from typing import Any

from qtrade.data import Bar, DataManifest, ParquetBarStore, QualityStatus
from qtrade.market_data import AkshareMarketDataProvider, SinaMinuteHistoryClient
from qtrade.market_data.akshare import SHANGHAI
from qtrade.market_data.sina_minute import SINA_FIVE_MINUTE_SESSION_TIMES
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from scripts.atomic_json import write_json_atomic

REPO = Path(__file__).resolve().parents[1]


def persist(
    root: Path, kind: str, bars: list[Bar], instruments: list[dict[str, Any]], source: str
) -> dict[str, Any]:
    bars.sort(key=lambda b: (b.timestamp, b.symbol))
    identity = sha256(
        "\n".join(
            f"{b.symbol}|{b.timestamp}|{b.open}|{b.high}|{b.low}|{b.close}|{b.volume}" for b in bars
        ).encode()
    ).hexdigest()[:20]
    version = f"current-{kind}-{identity}"
    path = root / "datasets" / version / "bars.parquet"
    if not path.exists():
        manifest = ParquetBarStore(root / "datasets").write(
            bars,
            DataManifest(
                version,
                source,
                "qtrade.bar.v1",
                datetime.now(UTC),
                "pending",
                QualityStatus.PENDING,
            ),
        )
        checksum = manifest.checksum
    else:
        checksum = sha256(path.read_bytes()).hexdigest()
    target = root / "public" / "datasets" / (checksum + ".parquet")
    target.parent.mkdir(parents=True, exist_ok=True)
    if not target.exists():
        shutil.copyfile(path, target)
    latest = {
        symbol: max(b.trading_day for b in bars if b.symbol == symbol).isoformat()
        for symbol in {b.symbol for b in bars}
    }
    return {
        "provider": source,
        "provider_version": "1.18.84",
        "classification": AUTHORITY["classification"],
        "data_version": version,
        "checksum": checksum,
        "quality_status": "PASSED",
        "bar_count": len(bars),
        "bytes": path.stat().st_size,
        "observed_start": min(b.timestamp for b in bars).astimezone(SHANGHAI).isoformat(),
        "observed_end": max(b.timestamp for b in bars).astimezone(SHANGHAI).isoformat(),
        "minimum_latest_day": min(latest.values()),
        "latest_by_symbol": latest,
        "trading_days": sorted({str(b.trading_day) for b in bars}),
        "instruments": instruments,
        "interval": "1d" if kind == "daily" else "5m",
        "adjustment": "raw" if kind == "daily" else "source_native_not_independently_certified",
        "snapshot_readiness": False,
        "real_data_paper_readiness": False,
        "universe": {"point_in_time": False, "symbols": sorted(latest)},
    }


def refresh(root: Path, expansion: Path) -> None:
    calendar = Calendar(REPO / "configs/observer_calendar_2026.json")
    expected = calendar.expected(datetime.now(UTC), "daily")
    previous_path = root / "public/current.json"
    previous = json.loads(previous_path.read_text()) if previous_path.exists() else None
    if previous and all(
        previous[k]["minimum_latest_day"] >= str(expected.date()) for k in ("daily", "minute")
    ):
        document = {key: value for key, value in previous.items() if key != "evidence_sha256"}
    else:
        symbols = json.loads((REPO / "configs/akshare_etf_universe_v1.json").read_text())["symbols"]
        provider = AkshareMarketDataProvider(noncommercial_research_acknowledged=True)
        try:
            provider.connect_history(probe_symbol=symbols[0])
            daily = list(
                provider.query_history(
                    tuple(symbols),
                    start=datetime(2022, 9, 6, tzinfo=SHANGHAI),
                    end=datetime.combine(expected.date(), time(23, 59), SHANGHAI),
                    interval="1d",
                )
            )
        finally:
            provider.close()
        stocks = json.loads(
            (REPO / "deployment/evidence/long-five-minute-market-data/manifest.json").read_text()
        )["universe"]["stocks"]

        def fetch(row: dict[str, Any]) -> list[Bar]:
            bars = SinaMinuteHistoryClient(
                datalen=1970, scale_minutes=5, timeout_seconds=12, maximum_attempts=2
            ).fetch(row["symbol"])
            counts = Counter(b.trading_day for b in bars)
            complete = {
                day for day, count in counts.items() if count == 48 and day <= expected.date()
            }
            return [b for b in bars if b.trading_day in complete]

        with ThreadPoolExecutor(max_workers=4) as pool:
            groups = list(pool.map(fetch, stocks))
        common = set.intersection(*[{b.trading_day for b in group} for group in groups])
        if expected.date() not in common:
            raise ValueError("LATEST_COMPLETE_MINUTE_SESSION_MISSING")
        minute = [b for group in groups for b in group if b.trading_day in common]
        if any(
            {
                b.timestamp.astimezone(SHANGHAI).time()
                for b in group
                if b.trading_day == expected.date()
            }
            != set(SINA_FIVE_MINUTE_SESSION_TIMES)
            for group in groups
        ):
            raise ValueError("INVALID_MINUTE_SESSION_GRID")
        daily_desc = persist(
            root,
            "daily",
            daily,
            [{"symbol": s, "name": s} for s in symbols],
            "AKShare Sina raw ETF daily",
        )
        minute_desc = persist(
            root, "minute", minute, stocks, "Sina native 5m; adjustment not independently certified"
        )
        if daily_desc["minimum_latest_day"] != str(expected.date()):
            raise ValueError("LATEST_DAILY_SESSION_MISSING")
        document = {
            **AUTHORITY,
            "schema_version": "qtrade.current-datasets.v1",
            "daily": daily_desc,
            "minute": minute_desc,
            "calendar": calendar.document,
        }
    document["published_at"] = datetime.now(UTC).isoformat()
    document["expansion"] = (
        json.loads((expansion / "progress.json").read_text())
        if (expansion / "progress.json").exists()
        else None
    )
    write_json_atomic(previous_path, signed(document))
    print(
        json.dumps(
            {
                "daily_end": document["daily"]["observed_end"],
                "minute_end": document["minute"]["observed_end"],
            }
        ),
        flush=True,
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--expansion", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise RuntimeError("live trading must remain disabled")
    refresh(args.root, args.expansion)


if __name__ == "__main__":
    main()
