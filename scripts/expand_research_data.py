"""Resumable, capacity-bounded public-source research ingestion; no trading authority."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import socket
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import UTC, date, datetime
from datetime import time as wall_time
from decimal import Decimal
from pathlib import Path
from typing import Any

from qtrade.data import Bar, DataManifest, ParquetBarStore, QualityStatus
from qtrade.data.minute_admission import admitted_checkpoint, validate_minute_window
from qtrade.market_data.akshare import SHANGHAI
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts.atomic_json import write_json_atomic


def reserve_space(root: Path) -> None:
    # Leave other cluster users and immutable research archives ample headroom.
    if shutil.disk_usage(root).free < 100 * 1024**3:
        raise RuntimeError("PERSISTENT_STORAGE_RESERVE_100_GIB")


def daily_bars(frame: Any, symbol: str) -> list[Bar]:
    return [
        Bar(
            symbol,
            "SSE" if symbol.startswith("6") else "SZSE",
            datetime.combine(row.date, wall_time(15), SHANGHAI),
            row.date,
            *[
                Decimal(str(getattr(row, key)))
                for key in ("open", "high", "low", "close", "volume")
            ],
        )
        for row in frame.itertuples()
        if row.volume > 0 and row.close > 0
    ]


def liquid_candidates() -> list[dict[str, str]]:
    import requests

    rows: dict[str, dict[str, str]] = {}
    url = "https://vip.stock.finance.sina.com.cn/quotes_service/api/json_v2.php/Market_Center.getHQNodeData"
    # Bounded latest-turnover pre-screen, followed by actual 20-session median ranking.
    for page in range(1, 23):
        for attempt in range(3):
            try:
                response = requests.get(
                    url,
                    params={"page": page, "num": 80, "sort": "amount", "asc": 0, "node": "hs_a"},
                    timeout=15,
                )
                response.raise_for_status()
                values = response.json()
                if not isinstance(values, list) or not values:
                    raise ValueError("EMPTY_LIQUIDITY_PAGE")
                for row in values:
                    symbol, name = str(row["code"]), str(row["name"])
                    if (
                        symbol.startswith(("0", "3", "6"))
                        and "ST" not in name.upper()
                        and "退" not in name
                    ):
                        rows[symbol] = {"symbol": symbol, "name": name}
                break
            except Exception:
                if attempt == 2:
                    raise
                time.sleep(2)
        time.sleep(0.2)
    if len(rows) < 1000:
        raise ValueError("LIQUIDITY_CANDIDATES_BELOW_TARGET")
    return list(rows.values())


def ingest_daily(root: Path, row: dict[str, str], end: date) -> dict[str, Any]:
    import akshare as ak

    symbol = row["symbol"]
    checkpoint = root / "daily" / symbol / (str(end) + ".json")
    if checkpoint.is_file():
        value = json.loads(checkpoint.read_text())
        manifest = value["manifest"]
        target = root / "bars" / manifest["data_version"] / "bars.parquet"
        from hashlib import sha256

        if sha256(target.read_bytes()).hexdigest() != manifest["checksum"]:
            raise ValueError("CHECKPOINT_CHECKSUM_MISMATCH")
        return value
    reserve_space(root)
    frame = ak.stock_zh_a_daily(
        symbol=("sh" if symbol.startswith("6") else "sz") + symbol,
        start_date="20161024",
        end_date=end.strftime("%Y%m%d"),
        adjust="",
    )
    bars = daily_bars(frame, symbol)
    if len(bars) < 1200 or bars[-1].trading_day != end:
        raise ValueError("INSUFFICIENT_FIVE_YEAR_HISTORY_OR_STALE")
    version = f"sina-raw-daily-{symbol}-20161024-{end:%Y%m%d}"
    manifest = ParquetBarStore(root / "bars").write(
        bars,
        DataManifest(
            version,
            "AKShare stock_zh_a_daily Sina unadjusted; volume shares; amount CNY",
            "qtrade.bar.v1",
            datetime.now(UTC),
            "pending",
            QualityStatus.PENDING,
        ),
    )
    value = signed(
        {
            **AUTHORITY,
            **row,
            "bar_count": len(bars),
            "observed_start": str(bars[0].trading_day),
            "observed_end": str(bars[-1].trading_day),
            "median_amount_20": str(frame.tail(20)["amount"].median()),
            "manifest": {
                "data_version": version,
                "checksum": manifest.checksum,
                "bytes": (root / "bars" / version / "bars.parquet").stat().st_size,
            },
        }
    )
    write_json_atomic(checkpoint, value)
    return value


def run(root: Path, end: date) -> None:
    root.mkdir(parents=True, exist_ok=True)
    reserve_space(root)
    state: dict[str, Any] = {
        **AUTHORITY,
        "target_daily_symbols": 1000,
        "target_daily_years": 10,
        "minimum_daily_sessions": 1200,
        "target_minute_symbols": 400,
        "target_minute_years": 3,
        "minute_interval": "5m",
        "selection_bias": "current liquid universe; survivorship and pre-screen bias; non-PIT",
        "requested_end": str(end),
        "started_at": datetime.now(UTC).isoformat(),
        "daily": {"state": "RUNNING", "completed": 0, "failed": 0},
        "minute": {"state": "WAITING_FOR_DAILY_UNIVERSE"},
    }

    def save() -> None:
        state["updated_at"] = datetime.now(UTC).isoformat()
        state["free_gib"] = round(shutil.disk_usage(root).free / 1024**3, 2)
        write_json_atomic(root / "progress.json", state)

    cached_path = root / "universe.json"
    cached = json.loads(cached_path.read_text()) if cached_path.exists() else None
    if cached is not None:
        unsigned = {k: v for k, v in cached.items() if k != "evidence_sha256"}
        if signed(unsigned)["evidence_sha256"] != cached.get("evidence_sha256"):
            raise ValueError("DAILY_UNIVERSE_CHECKSUM_MISMATCH")
    reuse = (
        cached is not None
        and len(cached.get("stocks", [])) == 1000
        and all(r["observed_end"] == str(end) for r in cached["stocks"])
    )
    if reuse:
        selected = cached["stocks"]
        state["daily"] = {
            "state": "COMPLETE",
            "completed": len(selected),
            "selected": len(selected),
            "selected_bars": sum(r["bar_count"] for r in selected),
            "selected_bytes": sum(r["manifest"]["bytes"] for r in selected),
            "selection_reused_for_same_complete_day": True,
        }
        save()
    else:
        save()
        candidate_file = root / ("candidates-" + str(end) + ".json")
        if not candidate_file.exists():
            write_json_atomic(candidate_file, {"rows": liquid_candidates()})
        candidates = json.loads(candidate_file.read_text())["rows"]
        state["daily"]["candidate_count"] = len(candidates)
        accepted, failures = [], []
        # AKShare's underlying requests have no timeout parameter. The supervisor's
        # outer subprocess timeout bounds a pass; completed symbol checkpoints survive.
        socket.setdefaulttimeout(25)
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = {pool.submit(ingest_daily, root, row, end): row for row in candidates}
            for future in as_completed(futures):
                try:
                    accepted.append(future.result())
                except Exception as error:
                    failures.append(
                        {
                            "symbol": futures[future]["symbol"],
                            "error": type(error).__name__ + ":" + str(error)[:100],
                        }
                    )
                state["daily"].update(completed=len(accepted), failed=len(failures))
                save()
        selected = sorted(
            accepted, key=lambda row: (-Decimal(row["median_amount_20"]), row["symbol"])
        )[:1000]
        write_json_atomic(
            root / "universe.json",
            signed(
                {
                    **AUTHORITY,
                    "known_at": datetime.now(UTC).isoformat(),
                    "selection": (
                        "latest-turnover top-1760 pre-screen, exclude ST, >=1200 sessions, "
                        "rank 20-session median CNY amount"
                    ),
                    "point_in_time": False,
                    "stocks": selected,
                }
            ),
        )
        state["daily"].update(
            state="COMPLETE" if len(selected) == 1000 else "PARTIAL",
            selected=len(selected),
            selected_bars=sum(row["bar_count"] for row in selected),
            selected_bytes=sum(row["manifest"]["bytes"] for row in selected),
        )
        write_json_atomic(root / "failures.json", {"failures": failures})
        save()
    minute_rows, minute_failures = [], []

    # BaoStock owns a process-global socket; use isolated bounded subprocesses,
    # never concurrent threads sharing a vendor session. Checkpoints survive timeout.
    def fetch_minute(row: dict[str, Any]) -> tuple[dict[str, Any] | None, str | None]:
        import subprocess
        import sys

        try:
            reserve_space(root)
            log_path = root / "minute-logs" / (row["symbol"] + ".log")
            log_path.parent.mkdir(parents=True, exist_ok=True)
            with log_path.open("a") as log:
                subprocess.run(
                    [
                        sys.executable,
                        "-m",
                        "scripts.expand_research_data",
                        "--root",
                        str(root),
                        "--end",
                        str(end),
                        "--minute-symbol",
                        row["symbol"],
                    ],
                    stdout=log,
                    stderr=subprocess.STDOUT,
                    timeout=120,
                    check=True,
                )
            value = admitted_checkpoint(
                root, root / "minute" / row["symbol"] / (str(end) + ".json"),
                symbol=row["symbol"], end=end,
            )
            return value, None
        except Exception as error:
            return None, type(error).__name__

    with ThreadPoolExecutor(max_workers=1) as pool:
        for offset in range(0, len(selected), 24):
            if len(minute_rows) >= 400:
                break
            group = selected[offset : offset + 24]
            for row, (value, error) in zip(group, pool.map(fetch_minute, group), strict=True):
                if value is not None and len(minute_rows) < 400:
                    minute_rows.append(value)
                elif error:
                    minute_failures.append({"symbol": row["symbol"], "error": error})
                state["minute"] = {
                    "state": "RUNNING",
                    "completed": len(minute_rows),
                    "failed": len(minute_failures),
                }
                write_json_atomic(root / "minute-failures.json", {"failures": minute_failures})
                save()
    write_json_atomic(root / "minute-universe.json", signed({**AUTHORITY, "stocks": minute_rows}))
    state["minute"] = {
        "state": "COMPLETE" if len(minute_rows) == 400 else "PARTIAL",
        "completed": len(minute_rows),
        "bar_count": sum(r["bar_count"] for r in minute_rows),
        "bytes": sum(r["manifest"].get("bytes", 0) for r in minute_rows),
    }
    save()


def ingest_minute(root: Path, row: dict[str, Any], end: date) -> dict[str, Any]:
    version = f"baostock-qfq-5m-{row['symbol']}-20231024-{end:%Y%m%d}"
    checkpoint = root / "minute" / row["symbol"] / (str(end) + ".json")
    if checkpoint.exists():
        return admitted_checkpoint(root, checkpoint, symbol=row["symbol"], end=end)

    import baostock as bs

    from qtrade.market_data.baostock_five_minute import fetch_baostock_five_minute_history
    bars = list(
        fetch_baostock_five_minute_history(
            bs,
            symbol=row["symbol"],
            start_date="2023-10-24",
            end_date=str(end),
            adjustflag="2",
        )
    )
    coverage = validate_minute_window(bars, symbol=row["symbol"], requested_end=end)
    if coverage["state"] != "CONTENT_COVERAGE_VERIFIED":
        raise ValueError("THREE_YEAR_MINUTE_COVERAGE_FAILED:" + ",".join(coverage["issues"]))
    manifest = ParquetBarStore(root / "bars").write(
        bars,
        DataManifest(
            version,
            "BaoStock qfq 5m full-window rebuild; future adjustment knowledge possible",
            "qtrade.bar.v1",
            datetime.now(UTC),
            "pending",
            QualityStatus.PENDING,
        ),
    )
    value = {
        "symbol": row["symbol"],
        "bar_count": len(bars),
        "session_count": coverage["session_count"],
        "observed_start": coverage["observed_start"],
        "observed_end": coverage["observed_end"],
        "missing_sessions_policy": coverage["missing_sessions_policy"],
        "manifest": {
            "data_version": version,
            "checksum": manifest.checksum,
            "bytes": (root / "bars" / version / "bars.parquet").stat().st_size,
        },
    }
    write_json_atomic(checkpoint, value)
    return value


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--end", type=date.fromisoformat, required=True)
    parser.add_argument("--minute-symbol")
    args = parser.parse_args()
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise RuntimeError("live trading must remain disabled")
    if args.minute_symbol:
        import baostock as bs

        from scripts.vendor_session import baostock_session

        socket.setdefaulttimeout(20)
        with baostock_session():
            login = bs.login()
            if login.error_code != "0":
                raise RuntimeError("BAOSTOCK_LOGIN_FAILED")
            try:
                ingest_minute(args.root, {"symbol": args.minute_symbol}, args.end)
            finally:
                bs.logout()
    else:
        run(args.root, args.end)


if __name__ == "__main__":
    main()
