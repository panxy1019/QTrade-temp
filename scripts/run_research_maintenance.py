"""Supervised bounded refreshes and resumable expansion, using the exchange calendar."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import time
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from typing import Any

from qtrade.monitoring.data_freshness import Calendar
from scripts.atomic_json import write_json_atomic

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--unified", action="store_true")
    parser.add_argument("--database", type=Path)
    parser.add_argument("--source-revision")
    args = parser.parse_args()
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise RuntimeError("live trading must remain disabled")
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    if args.unified:
        if not args.database or not args.source_revision:
            raise ValueError("UNIFIED_DATABASE_AND_SOURCE_REVISION_REQUIRED")
        if os.environ.get("QTRADE_AKSHARE_NONCOMMERCIAL_ACK", "").lower() != "true":
            raise RuntimeError("noncommercial research acknowledgement required")
        unified_loop(args.base, args.database, args.source_revision, stop)
        return
    calendar = Calendar(REPO / "configs/observer_calendar_2026.json")
    while not stop.is_set():
        try:
            end = str(calendar.expected(datetime.now(UTC), "daily").date())
            progress_path = args.base / "expansion/progress.json"
            progress = json.loads(progress_path.read_text()) if progress_path.exists() else {}
            # Expansion is expensive: one bounded, resumable pass per service start/day.
            # Failures retry after six hours; successful same-day passes are not repeated.
            marker = args.base / "expansion/last-attempt.json"
            last = json.loads(marker.read_text()) if marker.exists() else {}
            age = (
                (datetime.now(UTC) - datetime.fromisoformat(last["at"])).total_seconds()
                if last
                else 99_999
            )
            complete = (
                progress.get("requested_end") == end
                and progress.get("daily", {}).get("state") == "COMPLETE"
                and progress.get("minute", {}).get("state") == "COMPLETE"
            )
            if not complete and (last.get("end") != end or age >= 21600):
                write_json_atomic(marker, {"end": end, "at": datetime.now(UTC).isoformat()})
                # Keep refreshing the website while the larger job runs.
                with (args.base / "expansion.log").open("a") as log:
                    child = subprocess.Popen(
                        [
                            sys.executable,
                            "-m",
                            "scripts.expand_research_data",
                            "--root",
                            str(args.base / "expansion"),
                            "--end",
                            end,
                        ],
                        cwd=REPO,
                        stdout=log,
                        stderr=subprocess.STDOUT,
                    )
                    started = datetime.now(UTC)
                    while child.poll() is None and not stop.is_set():
                        refresh(args.base)
                        if (datetime.now(UTC) - started).total_seconds() > 7200:
                            child.terminate()
                        stop.wait(300)
                    if child.poll() is None:
                        child.terminate()
                        try:
                            child.wait(timeout=15)
                        except subprocess.TimeoutExpired:
                            child.kill()
                            child.wait()
            refresh(args.base)
        except Exception as error:
            print("MAINTENANCE_FAILED:" + type(error).__name__, flush=True)
        stop.wait(300)


def unified_loop(base: Path, database: Path, revision: str, stop: Event) -> None:
    from scripts.collect_unified_data import run_pass
    from scripts.publish_unified_collection import publish_collection

    pending: Future[Any] | None = None
    next_refresh = 0.0

    def publish(value: dict[str, Any]) -> None:
        publish_collection(base, value)

    with ThreadPoolExecutor(max_workers=1) as compatibility:
        while not stop.is_set():
            if (pending is None or pending.done()) and time.monotonic() >= next_refresh:
                if pending is not None:
                    try:
                        pending.result()
                    except Exception as error:
                        print("LEGACY_CURRENT_REFRESH_FAILED:" + type(error).__name__, flush=True)
                pending = compatibility.submit(refresh, base)
                next_refresh = time.monotonic() + 300
            try:
                write_json_atomic(base / "unified-data/service-state.json", {
                    "state": "COLLECTING", "heartbeat_at": datetime.now(UTC).isoformat(),
                    "code_revision": revision, "live_trading_enabled": False,
                })
                result = run_pass(base, database, code_revision=revision, seconds=220, stop=stop,
                                  publish_progress=publish)
                write_json_atomic(base / "unified-data/service-state.json", {
                    "state": "WAITING", "heartbeat_at": datetime.now(UTC).isoformat(),
                    "code_revision": revision, "live_trading_enabled": False,
                    "last_pass_state": result["state"],
                })
            except Exception as error:
                write_json_atomic(base / "unified-data/service-state.json", {
                    "state": "FAILED", "heartbeat_at": datetime.now(UTC).isoformat(),
                    "error_type": type(error).__name__, "code_revision": revision,
                    "live_trading_enabled": False,
                })
                print("UNIFIED_COLLECTION_FAILED:" + type(error).__name__, flush=True)
            stop.wait(60)


def refresh(base: Path) -> None:
    with (base / "current-refresh.log").open("a") as log:
        subprocess.run(
            [
                sys.executable,
                "-m",
                "scripts.refresh_current_datasets",
                "--root",
                str(base / "current-data"),
                "--expansion",
                str(base / "expansion"),
            ],
            cwd=REPO,
            stdout=log,
            stderr=subprocess.STDOUT,
            timeout=900,
            check=True,
        )


if __name__ == "__main__":
    main()
