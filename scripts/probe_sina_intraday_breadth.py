"""Capture signed same-day minute breadth across the frozen 15x5 stock universe."""

from __future__ import annotations

import argparse
import json
import os
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qtrade.market_data import SinaMinuteHistoryClient
from scripts.atomic_json import write_json_atomic
from scripts.probe_sina_intraday_observation import (
    SHANGHAI,
    _canonical_sha256,
    _finalized_bars,
    _session_phase,
)


def _load_frozen_stocks(path: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("minute dataset manifest must be an object")
    supplied = manifest.get("evidence_sha256")
    unsigned = {key: value for key, value in manifest.items() if key != "evidence_sha256"}
    universe = manifest.get("universe")
    rows = universe.get("stocks") if isinstance(universe, dict) else None
    if supplied != _canonical_sha256(unsigned):
        raise ValueError("minute dataset manifest integrity check failed")
    if not isinstance(rows, list):
        raise ValueError("minute dataset stock inventory is missing")
    stocks: list[dict[str, str]] = []
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError("minute dataset stock is malformed")
        stocks.append(
            {
                "symbol": str(row.get("symbol")),
                "name": str(row.get("name")),
                "sector_code": str(row.get("sector_code")),
                "sector_name": str(row.get("sector_name")),
            }
        )
    sector_counts = Counter(row["sector_code"] for row in stocks)
    if (
        len(stocks) != 75
        or len({row["symbol"] for row in stocks}) != 75
        or len(sector_counts) != 15
        or set(sector_counts.values()) != {5}
    ):
        raise ValueError("minute dataset must contain the frozen 15x5 stock universe")
    return manifest, stocks


def _sector_summaries(observations: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    sector_order = list(dict.fromkeys(row["sector_code"] for row in observations))
    for sector_code in sector_order:
        rows = [row for row in observations if row["sector_code"] == sector_code]
        timestamp_sets = [set(row["same_day_finalized_timestamps"]) for row in rows]
        synchronized = sorted(set.intersection(*timestamp_sets)) if timestamp_sets else []
        ages = [
            float(row["latest_same_day_finalized_age_seconds"])
            for row in rows
            if row["latest_same_day_finalized_age_seconds"] is not None
        ]
        result.append(
            {
                "sector_code": sector_code,
                "sector_name": rows[0]["sector_name"],
                "symbol_count": len(rows),
                "same_day_symbol_count": sum(
                    int(row["same_day_finalized_bar_count"] > 0) for row in rows
                ),
                "synchronized_finalized_timestamp_count": len(synchronized),
                "latest_synchronized_finalized_timestamp": (
                    synchronized[-1] if synchronized else None
                ),
                "maximum_same_day_latest_age_seconds": max(ages) if ages else None,
            }
        )
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path("deployment/evidence/long-five-minute-market-data/manifest.json"),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--finalization-guard-seconds", type=int, default=60)
    args = parser.parse_args()
    if os.environ.get("QTRADE_AKSHARE_NONCOMMERCIAL_ACK", "").lower() != "true":
        raise RuntimeError(
            "set QTRADE_AKSHARE_NONCOMMERCIAL_ACK=true only for accepted "
            "noncommercial historical research use"
        )
    if not 1 <= args.workers <= 8:
        parser.error("--workers must be between 1 and 8")
    if not 0 <= args.finalization_guard_seconds <= 300:
        parser.error("--finalization-guard-seconds must be between 0 and 300")
    manifest, instruments = _load_frozen_stocks(args.manifest)
    started_at = datetime.now(UTC)

    def fetch(instrument: dict[str, str]) -> dict[str, Any]:
        bars = SinaMinuteHistoryClient(datalen=476, scale_minutes=1).fetch(
            instrument["symbol"]
        )
        observed_at = datetime.now(UTC)
        finalized = _finalized_bars(
            bars,
            observed_at=observed_at,
            guard_seconds=args.finalization_guard_seconds,
        )
        today = observed_at.astimezone(SHANGHAI).date()
        same_day = tuple(bar for bar in finalized if bar.trading_day == today)
        latest_observed = bars[-1]
        latest_finalized = finalized[-1] if finalized else None
        latest_same_day = same_day[-1] if same_day else None
        return {
            **instrument,
            "observed_at": observed_at.isoformat(),
            "session_phase": _session_phase(observed_at),
            "source_row_count": len(bars),
            "latest_observed_timestamp": latest_observed.timestamp.isoformat(),
            "latest_finalized_timestamp": (
                latest_finalized.timestamp.isoformat()
                if latest_finalized is not None
                else None
            ),
            "same_day_finalized_bar_count": len(same_day),
            "latest_same_day_finalized_timestamp": (
                latest_same_day.timestamp.isoformat()
                if latest_same_day is not None
                else None
            ),
            "latest_same_day_finalized_age_seconds": (
                round((observed_at - latest_same_day.timestamp).total_seconds(), 3)
                if latest_same_day is not None
                else None
            ),
            "forming_or_uncertain_bar_excluded": (
                latest_finalized is None
                or latest_observed.timestamp > latest_finalized.timestamp
            ),
            "same_day_finalized_timestamps": [
                bar.timestamp.isoformat() for bar in same_day
            ],
        }

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        observations = list(pool.map(fetch, instruments))
    completed_at = datetime.now(UTC)
    timestamp_sets = [set(row["same_day_finalized_timestamps"]) for row in observations]
    synchronized = sorted(set.intersection(*timestamp_sets)) if timestamp_sets else []
    same_day_ages = [
        float(row["latest_same_day_finalized_age_seconds"])
        for row in observations
        if row["latest_same_day_finalized_age_seconds"] is not None
    ]
    sector_summaries = _sector_summaries(observations)
    unsigned: dict[str, Any] = {
        "schema_version": 1,
        "classification": (
            "FREE_HISTORICAL_SOURCE_INTRADAY_BREADTH_NOT_REALTIME_ELIGIBLE"
        ),
        "coverage_scope": "ALL_FROZEN_STOCKS_15_SECTORS_X5",
        "provider": "Sina CN_MarketDataService.getKLineData",
        "bar_interval": "1m",
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "session_phase": _session_phase(completed_at),
        "data_version": manifest["manifest"]["data_version"],
        "dataset_checksum": manifest["manifest"]["checksum"],
        "symbol_count": len(observations),
        "sector_count": len(sector_summaries),
        "symbols_per_sector": 5,
        "same_day_symbol_count": sum(
            int(row["same_day_finalized_bar_count"] > 0) for row in observations
        ),
        "synchronized_finalized_timestamp_count": len(synchronized),
        "latest_synchronized_finalized_timestamp": (
            synchronized[-1] if synchronized else None
        ),
        "source_request_window_seconds": round(
            (completed_at - started_at).total_seconds(), 3
        ),
        "all_symbols_same_day_coverage": all(
            row["same_day_finalized_bar_count"] > 0 for row in observations
        ),
        "maximum_same_day_latest_age_seconds": (
            max(same_day_ages) if same_day_ages else None
        ),
        "finalization_guard_seconds": args.finalization_guard_seconds,
        "observations": observations,
        "sector_summaries": sector_summaries,
        "realtime_readiness": False,
        "real_data_paper_readiness": False,
        "promotion": "NOT_ELIGIBLE",
        "external_order_transmission": False,
    }
    report = {**unsigned, "evidence_sha256": _canonical_sha256(unsigned)}
    write_json_atomic(args.output, report)
    print(
        json.dumps(
            {
                key: report[key]
                for key in (
                    "completed_at",
                    "session_phase",
                    "symbol_count",
                    "sector_count",
                    "same_day_symbol_count",
                    "synchronized_finalized_timestamp_count",
                    "latest_synchronized_finalized_timestamp",
                    "source_request_window_seconds",
                    "maximum_same_day_latest_age_seconds",
                    "evidence_sha256",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
