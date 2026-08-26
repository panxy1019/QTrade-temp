from __future__ import annotations

import argparse
import json
import statistics
import time
import urllib.parse
import urllib.request
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo


def _fetch_json(url: str) -> tuple[dict[str, Any], str | None, float]:
    started = time.perf_counter()
    with urllib.request.urlopen(url, timeout=30) as response:  # noqa: S310
        payload = json.load(response)
        cache = response.headers.get("x-vercel-cache")
    if not isinstance(payload, dict):
        raise TypeError("expected a JSON object")
    return payload, cache, time.perf_counter() - started


def _select_sector_instruments(report: dict[str, Any]) -> list[dict[str, str]]:
    selected: list[dict[str, str]] = []
    seen: set[str] = set()
    for row in report["minute_dataset"]["instruments"]:
        sector_code = str(row["sector_code"])
        if sector_code in seen:
            continue
        seen.add(sector_code)
        selected.append(
            {
                "symbol": str(row["symbol"]),
                "sector_code": sector_code,
                "sector_name": str(row["sector_name"]),
            }
        )
    return selected


def _validate_series(
    payload: dict[str, Any],
    *,
    symbol: str,
    trading_day: str,
    checksum: str,
) -> list[str]:
    failures: list[str] = []
    bars = payload.get("bars")
    if payload.get("symbol") != symbol:
        failures.append("SYMBOL_MISMATCH")
    if payload.get("selected_trading_day") != trading_day:
        failures.append("TRADING_DAY_MISMATCH")
    if payload.get("dataset_checksum") != checksum:
        failures.append("CHECKSUM_MISMATCH")
    if payload.get("total_count") != 48 or not isinstance(bars, list) or len(bars) != 48:
        failures.append("INCOMPLETE_SESSION")
        return failures
    if {row.get("trading_day") for row in bars} != {trading_day}:
        failures.append("BAR_TRADING_DAY_MISMATCH")
    timestamps = [str(row.get("timestamp")) for row in bars]
    if len(set(timestamps)) != 48:
        failures.append("DUPLICATE_TIMESTAMP")
    try:
        local_times = [
            datetime.fromisoformat(value)
            .astimezone(ZoneInfo("Asia/Shanghai"))
            .strftime("%H:%M")
            for value in timestamps
        ]
    except ValueError:
        failures.append("INVALID_TIMESTAMP")
    else:
        if local_times[0] != "09:35" or local_times[-1] != "15:00":
            failures.append("SESSION_CLOCK_MISMATCH")
    if any(
        row.get("observation_type") != "HISTORICAL_FIVE_MINUTE_BAR"
        for row in bars
    ):
        failures.append("OBSERVATION_TYPE_MISMATCH")
    return failures


def _percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    return ordered[max(0, min(len(ordered) - 1, int(len(ordered) * fraction) - 1))]


def _run_round(
    *,
    api_root: str,
    checksum: str,
    instruments: list[dict[str, str]],
    trading_days: tuple[str, str, str],
    cache_key: str,
    workers: int,
) -> dict[str, Any]:
    items = [
        (instrument, trading_day)
        for instrument in instruments
        for trading_day in trading_days
    ]

    def probe(item: tuple[dict[str, str], str]) -> dict[str, Any]:
        instrument, trading_day = item
        symbol = instrument["symbol"]
        query = urllib.parse.urlencode(
            {"trading_day": trading_day, "audit": cache_key}
        )
        url = f"{api_root}/long-five-minute-bars/{symbol}?{query}"
        payload, cache, elapsed = _fetch_json(url)
        return {
            **instrument,
            "trading_day": trading_day,
            "latency_seconds": round(elapsed, 6),
            "vercel_cache": cache,
            "failures": _validate_series(
                payload,
                symbol=symbol,
                trading_day=trading_day,
                checksum=checksum,
            ),
        }

    with ThreadPoolExecutor(max_workers=workers) as pool:
        samples = list(pool.map(probe, items))
    latencies = [float(row["latency_seconds"]) for row in samples]
    failures = [row for row in samples if row["failures"]]
    return {
        "request_count": len(samples),
        "all_valid": not failures,
        "latency_seconds": {
            "minimum": round(min(latencies), 3),
            "median": round(statistics.median(latencies), 3),
            "p95": round(_percentile(latencies, 0.95), 3),
            "maximum": round(max(latencies), 3),
        },
        "vercel_cache_counts": dict(
            sorted(Counter(str(row["vercel_cache"]) for row in samples).items())
        ),
        "failures": failures,
    }


def _signed(payload: dict[str, Any]) -> dict[str, Any]:
    canonical = json.dumps(
        payload,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    return {
        **payload,
        "evidence_sha256": sha256(canonical.encode("utf-8")).hexdigest(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Audit public checksum-bound long-history 5-minute bars."
    )
    parser.add_argument("--base-url", default="https://qtrade.erapi0neer.xyz")
    parser.add_argument("--cache-key", required=True)
    parser.add_argument("--workers", type=int, default=8)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.workers < 1 or args.workers > 16:
        parser.error("--workers must be between 1 and 16")
    api_root = f"{args.base_url.rstrip('/')}/api"
    metadata_url = (
        f"{api_root}/long-five-minute-research?"
        f"{urllib.parse.urlencode({'audit': args.cache_key})}"
    )
    research, _, _ = _fetch_json(metadata_url)
    instruments = _select_sector_instruments(research)
    days = research["minute_dataset"]["trading_days"]
    trading_days = (str(days[0]), str(days[len(days) // 2]), str(days[-1]))
    checksum = str(research["minute_dataset"]["checksum"])
    first = _run_round(
        api_root=api_root,
        checksum=checksum,
        instruments=instruments,
        trading_days=trading_days,
        cache_key=args.cache_key,
        workers=args.workers,
    )
    repeated = _run_round(
        api_root=api_root,
        checksum=checksum,
        instruments=instruments,
        trading_days=trading_days,
        cache_key=args.cache_key,
        workers=args.workers,
    )
    payload = _signed(
        {
            "schema_version": 1,
            "classification": "PUBLIC_READ_ONLY_MINUTE_BAR_AVAILABILITY_AUDIT",
            "generated_at": datetime.now(ZoneInfo("Asia/Shanghai")).isoformat(),
            "base_url": args.base_url.rstrip("/"),
            "cache_key": args.cache_key,
            "data_version": research["minute_dataset"]["data_version"],
            "dataset_checksum": checksum,
            "sector_count": len(instruments),
            "instruments": instruments,
            "sampled_trading_days": list(trading_days),
            "first_round": first,
            "repeat_round": repeated,
            "all_valid": bool(first["all_valid"] and repeated["all_valid"]),
            "promotion": "NOT_ELIGIBLE",
            "external_order_transmission": False,
        }
    )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(payload, ensure_ascii=False, indent=2))
    return 0 if payload["all_valid"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
