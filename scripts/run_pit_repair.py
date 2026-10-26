"""Bounded resumable historical-source passes alongside the research worker."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path
from threading import Event

from scripts.atomic_json import write_json_atomic

REPO = Path(__file__).resolve().parents[1]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
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
            age = ((datetime.now(UTC) - datetime.fromisoformat(prior["at"])).total_seconds()
                   if prior else 100000)
            if age < 21600:
                stop.wait(min(60, 21600 - age))
                continue
            if shutil.disk_usage(args.base).free < 100 * 1024**3:
                raise ValueError("DISK_RESERVE_BELOW_100_GIB")
            maximum = min(990, prior.get("maximum", 20) + 20)
            write_json_atomic(marker, {"at": datetime.now(UTC).isoformat(), "maximum": maximum})
            with (args.base / "pit-collection.log").open("ab") as log:
                child = subprocess.Popen(
                    [sys.executable, "-m", "scripts.collect_pit_evidence", "--base",
                     str(args.base), "--maximum", str(maximum)],
                    cwd=REPO, stdout=log, stderr=subprocess.STDOUT,
                )
                try:
                    child.wait(1800)
                except subprocess.TimeoutExpired:
                    child.terminate()
                    try:
                        child.wait(10)
                    except subprocess.TimeoutExpired:
                        child.kill()
                        child.wait()
            print("PIT_SOURCE_PASS_EXIT=" + str(child.returncode), flush=True)
        except Exception as error:
            print("PIT_SOURCE_PASS_FAILED:" + type(error).__name__, flush=True)
        stop.wait(60)


if __name__ == "__main__":
    main()
