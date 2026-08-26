"""Capture a bounded same-day minute observation without claiming realtime readiness."""

from __future__ import annotations

import argparse
import json
import os
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime, time, timedelta
from hashlib import sha256
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from qtrade.data import Bar
from qtrade.market_data import SinaMinuteHistoryClient

SHANGHAI = ZoneInfo("Asia/Shanghai")


def _canonical_sha256(value: object) -> str:
    canonical = json.dumps(
        value,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
        default=str,
    )
    return sha256(canonical.encode("utf-8")).hexdigest()


def _load_sector_representatives(path: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(manifest, dict):
        raise ValueError("minute dataset manifest must be an object")
    supplied = manifest.get("evidence_sha256")
    unsigned = {key: value for key, value in manifest.items() if key != "evidence_sha256"}
    universe = manifest.get("universe")
    instruments = universe.get("stocks") if isinstance(universe, dict) else None
    if supplied != _canonical_sha256(unsigned):
        raise ValueError("minute dataset manifest integrity check failed")
    if not isinstance(instruments, list):
        raise ValueError("minute dataset instrument inventory is missing")
    selected: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in instruments:
        if not isinstance(row, dict):
            raise ValueError("minute dataset instrument is malformed")
        sector_code = str(row.get("sector_code"))
        if sector_code in seen:
            continue
        seen.add(sector_code)
        selected.append(
            {
                "symbol": str(row.get("symbol")),
                "name": str(row.get("name")),
                "sector_code": sector_code,
                "sector_name": str(row.get("sector_name")),
            }
        )
    if len(selected) != int(manifest.get("sector_count", 0)):
        raise ValueError("minute dataset sector representatives are incomplete")
    return manifest, selected


def _session_phase(instant: datetime) -> str:
    local = instant.astimezone(SHANGHAI).time()
    if local < time(9, 30):
        return "PRE_OPEN"
    if local <= time(11, 30):
        return "MORNING_SESSION"
    if local < time(13, 0):
        return "MIDDAY_BREAK"
    if local <= time(15, 0):
        return "AFTERNOON_SESSION"
    return "POST_CLOSE"


def _finalized_bars(
    bars: tuple[Bar, ...],
    *,
    observed_at: datetime,
    guard_seconds: int,
) -> tuple[Bar, ...]:
    if observed_at.tzinfo is None or guard_seconds < 0:
        raise ValueError("finalization boundary is invalid")
    cutoff = observed_at - timedelta(seconds=guard_seconds)
    return tuple(bar for bar in bars if bar.timestamp <= cutoff)


def _bar_payload(bar: Bar) -> dict[str, str]:
    return {
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


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--manifest",
        type=Path,
        default=Path(
            "deployment/evidence/long-five-minute-market-data/manifest.json"
        ),
    )
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--workers", type=int, default=5)
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
    manifest, instruments = _load_sector_representatives(args.manifest)
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
            "latest_same_day_finalized_age_seconds": (
                round((observed_at - latest_same_day.timestamp).total_seconds(), 3)
                if latest_same_day is not None
                else None
            ),
            "forming_or_uncertain_bar_excluded": (
                latest_finalized is None
                or latest_observed.timestamp > latest_finalized.timestamp
            ),
            "bars": [_bar_payload(bar) for bar in same_day],
        }

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        observations = list(pool.map(fetch, instruments))
    completed_at = datetime.now(UTC)
    timestamp_sets = [
        {str(bar["timestamp"]) for bar in row["bars"]}
        for row in observations
        if row["bars"]
    ]
    synchronized = sorted(set.intersection(*timestamp_sets)) if timestamp_sets else []
    same_day_ages = [
        float(row["latest_same_day_finalized_age_seconds"])
        for row in observations
        if row["latest_same_day_finalized_age_seconds"] is not None
    ]
    unsigned: dict[str, Any] = {
        "schema_version": 2,
        "classification": (
            "FREE_HISTORICAL_SOURCE_INTRADAY_OBSERVATION_NOT_REALTIME_ELIGIBLE"
        ),
        "provider": "Sina CN_MarketDataService.getKLineData",
        "bar_interval": "1m",
        "started_at": started_at.isoformat(),
        "completed_at": completed_at.isoformat(),
        "session_phase": _session_phase(completed_at),
        "data_version": manifest["manifest"]["data_version"],
        "dataset_checksum": manifest["manifest"]["checksum"],
        "symbol_count": len(observations),
        "sector_count": len({row["sector_code"] for row in observations}),
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
        "realtime_readiness": False,
        "real_data_paper_readiness": False,
        "promotion": "NOT_ELIGIBLE",
        "external_order_transmission": False,
    }
    report = {**unsigned, "evidence_sha256": _canonical_sha256(unsigned)}
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
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
