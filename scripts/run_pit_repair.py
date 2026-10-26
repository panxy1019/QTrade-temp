"""Bounded resumable historical-source passes alongside the research worker."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event
from typing import Any

from scripts.atomic_json import write_json_atomic
from scripts.collect_pit_evidence import record_attempt

REPO = Path(__file__).resolve().parents[1]


def plan_pass(previous: dict[str, Any], now: datetime) -> tuple[float, int]:
    """Legacy/incomplete attempts retry their existing scope, without claiming progress."""
    if previous.get("next_attempt_at"):
        deadline = datetime.fromisoformat(previous["next_attempt_at"])
    elif previous.get("at"):
        deadline = datetime.fromisoformat(previous["at"]) + timedelta(minutes=30)
    else:
        deadline = now
    if deadline.tzinfo is None:
        raise ValueError("SCHEDULE_TIMEZONE_REQUIRED")
    maximum = previous.get("maximum", 20)
    if previous.get("outcome") == "SUCCEEDED":
        maximum = min(990, maximum + 20)
    if type(maximum) is not int or not 1 <= maximum <= 990:
        raise ValueError("INVALID_SOURCE_PASS_BOUND")
    return max(0, (deadline - now).total_seconds()), maximum


def finish_pass(maximum: int, returncode: int, now: datetime, failures: int) -> dict[str, Any]:
    failures = 0 if returncode == 0 else min(5, failures + 1)
    delay = 21600 if returncode == 0 else min(21600, 1800 * 2 ** (failures - 1))
    return {
        "maximum": maximum, "at": now.isoformat(), "returncode": returncode,
        "outcome": "SUCCEEDED" if returncode == 0 else "FAILED",
        "consecutive_failures": failures,
        "next_attempt_at": (now + timedelta(seconds=delay)).isoformat(),
    }


def stop_child(child: subprocess.Popen[Any]) -> None:
    if child.poll() is not None:
        return
    child.terminate()
    try:
        child.wait(10)
    except subprocess.TimeoutExpired:
        child.kill()
        child.wait()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise RuntimeError("live trading must remain disabled")
    import fcntl
    # Keep the exclusive lease alive for the entire supervised process lifetime.
    lease = Path("/root/qtrade-local/pit-worker.lock").open("a")  # noqa: SIM115
    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
    stop = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    marker = args.base / "pit-repair/next-pass.json"
    while not stop.is_set():
        try:
            prior = json.loads(marker.read_text()) if marker.exists() else {}
            delay, maximum = plan_pass(prior, datetime.now(UTC))
            if delay:
                if args.once:
                    return
                stop.wait(min(60, delay))
                continue
            if shutil.disk_usage(args.base).free < 100 * 1024**3:
                raise ValueError("DISK_RESERVE_BELOW_100_GIB")
            write_json_atomic(marker, {
                "at": datetime.now(UTC).isoformat(), "maximum": maximum,
                "outcome": "RUNNING", "consecutive_failures": prior.get(
                    "consecutive_failures", 0
                ),
            })
            with (args.base / "pit-collection.log").open("ab") as log:
                child = subprocess.Popen(
                    [sys.executable, "-m", "scripts.collect_pit_evidence", "--base",
                     str(args.base), "--maximum", str(maximum)],
                    cwd=REPO, stdout=log, stderr=subprocess.STDOUT,
                )
                started = time.monotonic()
                try:
                    while child.poll() is None and not stop.is_set():
                        if time.monotonic() - started >= 1800:
                            break
                        stop.wait(1)
                finally:
                    stop_child(child)
            write_json_atomic(marker, finish_pass(
                maximum, child.returncode, datetime.now(UTC), prior.get("consecutive_failures", 0)
            ))
            attempt_path = args.base / "pit-repair/attempt.json"
            if child.returncode and attempt_path.exists() and json.loads(
                attempt_path.read_text()
            ).get("state") == "RUNNING":
                record_attempt(args.base, maximum, "FAILED", stage="PROCESS_EXIT",
                               returncode=child.returncode)
            print("PIT_SOURCE_PASS_EXIT=" + str(child.returncode), flush=True)
        except Exception as error:
            print("PIT_SOURCE_PASS_FAILED:" + type(error).__name__, flush=True)
        if args.once:
            break
        stop.wait(60)


if __name__ == "__main__":
    main()
