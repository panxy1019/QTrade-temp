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

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.workflow import digest
from scripts.atomic_json import write_json_atomic, write_json_immutable
from scripts.collect_pit_evidence import record_attempt

REPO = Path(__file__).resolve().parents[1]
QUEUE_MODE = "BOUNDED_UNIVERSE_QUEUE_V2"


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
    if previous.get("outcome") == "SUCCEEDED" and previous.get("mode") != QUEUE_MODE:
        maximum = min(990, maximum + 20)
    if type(maximum) is not int or not 1 <= maximum <= 990:
        raise ValueError("INVALID_SOURCE_PASS_BOUND")
    return max(0, (deadline - now).total_seconds()), maximum


def finish_pass(
    maximum: int, returncode: int, now: datetime, failures: int, *, partial: bool = False,
) -> dict[str, Any]:
    complete = returncode == 0 and not partial
    failures = 0 if complete else min(5, failures + 1)
    delay = 21600 if complete else min(21600, 1800 * 2 ** (failures - 1))
    return {
        "maximum": maximum, "at": now.isoformat(), "returncode": returncode,
        "outcome": "SUCCEEDED" if complete else "PARTIAL" if returncode == 0 else "FAILED",
        "consecutive_failures": failures,
        "next_attempt_at": (now + timedelta(seconds=delay)).isoformat(),
    }


def finish_batch(
    maximum: int, returncode: int, now: datetime, failures: int,
    attempt: dict[str, Any], started_at: datetime,
) -> dict[str, Any]:
    """Only an actual current receipt can establish coverage; partial never means success."""
    progress = attempt.get("collection_progress")
    retry = None
    try:
        updated = datetime.fromisoformat(attempt["updated_at"])
        valid = isinstance(progress, dict) and (
            signed({k: v for k, v in attempt.items() if k != "evidence_sha256"}) == attempt
            and all(attempt.get(k) == v for k, v in AUTHORITY.items())
            and attempt.get("schema_version") == "qtrade.pit-source-attempt.v1"
            and attempt.get("requested_maximum") == maximum
            and attempt.get("historical_pit_certified") is False
            and progress["mode"] == QUEUE_MODE and progress["scope_symbols"] == maximum
            and progress["completed_symbols"] + progress["unresolved_symbols"] == maximum
            and sum(progress[k] for k in (
                "unattempted_symbols", "due_retry_symbols", "waiting_retry_symbols"
            )) == progress["unresolved_symbols"]
            and all(type(progress[k]) is int and progress[k] >= 0 for k in (
                "completed_symbols", "unresolved_symbols", "unattempted_symbols",
                "due_retry_symbols", "waiting_retry_symbols",
            )) and progress["historical_pit_certified"] is False
            and updated.tzinfo is not None and started_at <= updated <= now
            and attempt["state"] == ("PARTIAL" if progress["unresolved_symbols"] else "COMPLETE")
        )
        if valid:
            assert isinstance(progress, dict)
            retry = (datetime.fromisoformat(progress["next_retry_at"])
                     if progress["next_retry_at"] is not None else None)
            valid = (retry is not None and retry.tzinfo is not None
                     and (retry <= now) == bool(progress["due_retry_symbols"])) if (
                progress["due_retry_symbols"] or progress["waiting_retry_symbols"]
            ) else retry is None
    except (KeyError, TypeError, ValueError):
        valid = False
    if returncode != 0 or not valid:
        value = finish_pass(maximum, returncode or 1, now, failures)
        return {**value, "mode": QUEUE_MODE, "collector_returncode": returncode,
                "receipt_valid": valid}
    assert isinstance(progress, dict)
    delay = 21600.0
    if progress["unattempted_symbols"] or progress["due_retry_symbols"]:
        delay = 60
    elif progress["unresolved_symbols"]:
        assert retry is not None
        delay = max(60, min(21600, (retry - now).total_seconds()))
    return {
        "mode": QUEUE_MODE, "maximum": maximum, "at": now.isoformat(),
        "returncode": returncode, "receipt_valid": True,
        "outcome": "PARTIAL" if progress["unresolved_symbols"] else "SUCCEEDED",
        "consecutive_failures": 0, "collection_progress": progress,
        "next_attempt_at": (now + timedelta(seconds=delay)).isoformat(),
    }


def migrate_schedule(base: Path, previous: dict[str, Any], now: datetime) -> dict[str, Any]:
    if previous.get("mode") == QUEUE_MODE:
        return previous
    if previous:
        path = base / "pit-repair" / ("legacy-next-pass-" + digest(previous) + ".json")
        if not path.exists():
            write_json_immutable(path, previous)
    dataset = json.loads((base / "qlib-data-20261023/dataset.json").read_text())
    maximum = len(dataset["stocks"])
    if not 1 <= maximum <= 990 or dataset["accepted_symbols"] != maximum:
        raise ValueError("SOURCE_QUEUE_UNIVERSE_BOUND_INVALID")
    return {
        "mode": QUEUE_MODE, "maximum": maximum, "outcome": "MIGRATED",
        "consecutive_failures": previous.get("consecutive_failures", 0),
        "next_attempt_at": (now + timedelta(seconds=plan_pass(previous, now)[0])).isoformat(),
        "legacy_schedule_identity": digest(previous),
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
            prior = migrate_schedule(args.base, prior, datetime.now(UTC))
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
                "mode": QUEUE_MODE, "outcome": "RUNNING", "consecutive_failures": prior.get(
                    "consecutive_failures", 0
                ),
            })
            started_at = datetime.now(UTC)
            with (args.base / "pit-collection.log").open("ab") as log:
                child = subprocess.Popen(
                    [sys.executable, "-m", "scripts.collect_pit_evidence", "--base",
                     str(args.base), "--maximum", str(maximum), "--batch-size", "20"],
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
            attempt_path = args.base / "pit-repair/attempt.json"
            attempt = json.loads(attempt_path.read_text()) if attempt_path.exists() else {}
            write_json_atomic(marker, finish_batch(
                maximum, child.returncode, datetime.now(UTC), prior.get("consecutive_failures", 0),
                attempt, started_at,
            ))
            if child.returncode and attempt.get("state") == "RUNNING":
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
