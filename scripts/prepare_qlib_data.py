"""Recover Sina cash turnover/HFQ alongside checksum-verified QTrade daily bars.

Run in the existing ingestion environment. No BaoStock session or gateway is used.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.qlib_adapter import normalize_daily, verify_reused_bars
from scripts.atomic_json import write_json_atomic


def download(url: str) -> str:
    import requests

    error: Exception | None = None
    for attempt in range(3):
        try:
            response = requests.get(url, timeout=(10, 35))
            response.raise_for_status()
            return str(response.text)
        except Exception as exc:
            error = exc
            time.sleep(1 + attempt)
    raise RuntimeError("SINA_SOURCE_DOWNLOAD_FAILED") from error


def source_history(code: str, target: Path) -> list[dict[str, Any]]:
    import py_mini_racer
    from akshare.stock.cons import hk_js_decode

    path = target / (code + "-raw.js")
    if not path.exists():
        path.write_text(
            download(
                f"https://finance.sina.com.cn/realstock/company/{code}/hisdata_klc2/klc_kl.js"
            ),
            encoding="utf-8",
        )
    text = path.read_text(encoding="utf-8")
    decoder = py_mini_racer.MiniRacer()
    decoder.eval(hk_js_decode)  # Fixed installed AKShare decoder, not remote JavaScript.
    rows = decoder.call("d", text.split("=", 1)[1].split(";")[0].replace('"', ""))
    del decoder  # The pinned MiniRacer version has no context manager API.
    if not isinstance(rows, list) or not rows:
        raise ValueError("INVALID_SINA_RAW_HISTORY")
    return rows


def prepare_symbol(expansion: str, root: str, stock: dict[str, Any], end: str) -> dict[str, Any]:
    import pyarrow as pa
    import pyarrow.parquet as pq

    base, output = Path(expansion), Path(root)
    symbol = stock["symbol"]
    target = output / "stocks" / symbol
    target.mkdir(parents=True, exist_ok=True)
    checkpoint = target / "manifest.json"
    if checkpoint.exists():
        value = json.loads(checkpoint.read_text())
        for name, digest in value["files"].items():
            if sha256((target / name).read_bytes()).hexdigest() != digest:
                raise ValueError("QLIB_CHECKPOINT_CHECKSUM_MISMATCH")
        if value["source_bar_checksum"] != stock["manifest"]["checksum"]:
            raise ValueError("QLIB_CHECKPOINT_SOURCE_CHANGED")
        return value
    if shutil.disk_usage(output).free < 100 * 1024**3:
        raise RuntimeError("PERSISTENT_STORAGE_RESERVE_100_GIB")
    code = ("sh" if symbol.startswith("6") else "sz") + symbol
    rows = source_history(code, target)
    rows = [r for r in rows if "2016-10-24" <= str(r["date"])[:10] <= end]
    source_path = base / "bars" / stock["manifest"]["data_version"] / "bars.parquet"
    if sha256(source_path.read_bytes()).hexdigest() != stock["manifest"]["checksum"]:
        raise ValueError("REUSED_SOURCE_CHECKSUM_MISMATCH")
    count = verify_reused_bars(rows, pq.read_table(source_path).to_pylist())
    factor_path = target / (code + "-hfq.js")
    if not factor_path.exists():
        factor_path.write_text(
            download(f"https://finance.sina.com.cn/realstock/company/{code}/hfq.js"),
            encoding="utf-8",
        )
    # Sina appends JavaScript metadata after the JSON object; decode only the
    # first object and never execute the downloaded script.
    factor_object, _ = json.JSONDecoder().raw_decode(
        factor_path.read_text().split("=", 1)[1].lstrip()
    )
    factors = factor_object["data"]
    normalized = normalize_daily(rows, factors, symbol=symbol)
    if normalized[-1]["date"] != end:
        raise ValueError("STALE_QLIB_NORMALIZED_HISTORY")
    pq.write_table(pa.Table.from_pylist(normalized), target / "daily.parquet", compression="zstd")
    value = signed(
        {
            "symbol": symbol,
            "name": stock["name"],
            "rows": len(normalized),
            "start": normalized[0]["date"],
            "end": normalized[-1]["date"],
            "reused_rows_verified": count,
            "source_bar_checksum": stock["manifest"]["checksum"],
            "observed_at": datetime.now(UTC).isoformat(),
            "files": {
                p.name: sha256(p.read_bytes()).hexdigest()
                for p in sorted(target.iterdir())
                if p.suffix in (".parquet", ".js")
            },
        }
    )
    write_json_atomic(checkpoint, value)
    return value


def run(expansion: Path, root: Path, workers: int) -> None:
    root.mkdir(parents=True, exist_ok=True)
    if (root / "dataset.json").exists():
        raise ValueError("IMMUTABLE_DATASET_ALREADY_COMPLETE_USE_NEW_ROOT")
    universe = json.loads((expansion / "universe.json").read_text())
    if signed({k: v for k, v in universe.items() if k != "evidence_sha256"}) != universe:
        raise ValueError("UNIVERSE_CHECKSUM_MISMATCH")
    stocks = universe["stocks"]
    end = min(s["observed_end"] for s in stocks)
    benchmark = root / "benchmark"
    benchmark.mkdir(exist_ok=True)
    index = source_history("sh000300", benchmark)
    index = [r for r in index if "2016-10-24" <= str(r["date"])[:10] <= end]
    if not index or str(index[-1]["date"])[:10] != end:
        raise ValueError("BENCHMARK_NOT_CURRENT")
    write_json_atomic(benchmark / "raw.json", {"rows": index})
    accepted: list[dict[str, Any]] = []
    failures: list[dict[str, str]] = []
    started = datetime.now(UTC).isoformat()
    with ProcessPoolExecutor(max_workers=workers) as pool:
        futures = {
            pool.submit(prepare_symbol, str(expansion), str(root), s, end): s for s in stocks
        }
        for future in as_completed(futures):
            try:
                accepted.append(future.result())
            except Exception as exc:
                failures.append(
                    {
                        "symbol": futures[future]["symbol"],
                        "error": type(exc).__name__ + ":" + str(exc)[:120],
                    }
                )
            write_json_atomic(
                root / "progress.json",
                {
                    "state": "RUNNING",
                    "started_at": started,
                    "updated_at": datetime.now(UTC).isoformat(),
                    "target": len(stocks),
                    "completed": len(accepted),
                    "failed": len(failures),
                    "failures": failures,
                },
            )
            print(f"QLIB_DATA completed={len(accepted)} failed={len(failures)}", flush=True)
    accepted.sort(key=lambda r: r["symbol"])
    document = signed(
        {
            **AUTHORITY,
            "schema_version": "qtrade.qlib-daily.v1",
            "end": end,
            "created_at": datetime.now(UTC).isoformat(),
            "universe_sha256": universe["evidence_sha256"],
            "requested_symbols": len(stocks),
            "accepted_symbols": len(accepted),
            "rows": sum(r["rows"] for r in accepted),
            "stocks": accepted,
            "failures": failures,
            "benchmark_sha256": sha256((benchmark / "raw.json").read_bytes()).hexdigest(),
            "adjustment": (
                "Sina effective-date HFQ multiplier; normalized initial close=1; inverse volume"
            ),
            "selection_bias": (
                "current liquidity universe; survivorship and pre-screen bias; non-PIT"
            ),
            "suspensions": (
                "No forward-filled bars; missing observations non-tradable; reason uncertified"
            ),
            "execution": (
                "Next-session open; conservative 4.5% directional gap blocks; STAR buys excluded"
            ),
            "limitations": [
                "Historical ST/limit-price and halt reason not independently certified",
                "Historical vendor revisions not point-in-time snapshots",
                "Vendor HFQ multipliers not independently reconciled to corporate-action ledger",
            ],
        }
    )
    write_json_atomic(root / "dataset.json", document)
    write_json_atomic(
        root / "progress.json",
        {
            "state": "COMPLETE" if len(accepted) == len(stocks) else "PARTIAL",
            "completed": len(accepted),
            "failed": len(failures),
            "target": len(stocks),
            "updated_at": datetime.now(UTC).isoformat(),
            "failures": failures,
        },
    )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--expansion", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--workers", type=int, choices=(1, 2, 3), default=2)
    args = parser.parse_args()
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise RuntimeError("live trading must remain disabled")
    run(args.expansion, args.root, args.workers)
