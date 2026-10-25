"""Supervised bounded refreshes and resumable expansion, using the exchange calendar."""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

from qtrade.monitoring.data_freshness import Calendar
from scripts.atomic_json import write_json_atomic

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise RuntimeError("live trading must remain disabled")
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
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
