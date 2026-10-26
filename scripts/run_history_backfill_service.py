"""Independent Supervisor loop for bounded low-priority historical passes."""

from __future__ import annotations

import argparse
import json
import os
import re
import signal
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from threading import Event, Lock, Thread
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts.atomic_json import write_json_atomic
from scripts.history_backfill_queue import read_signed
from scripts.run_history_backfill import run_pass, worker_lease

PERIOD_SECONDS = 300
HEARTBEAT_SECONDS = 15


def serve(
    base: Path, root: Path, plan: dict[str, Any], master: dict[str, Any], *, revision: str,
    stop: Event, seconds: float = 240, new_windows: int = 4, retry_windows: int = 1,
    query_seconds: float = 25, lease_dir: Path = Path("/root/qtrade-local"),
    extra_states: tuple[Path, ...] = (),
) -> dict[str, Any]:
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise RuntimeError("LIVE_TRADING_MUST_REMAIN_DISABLED")
    if os.environ.get("QTRADE_BAOSTOCK_FREE_DATA_ACK", "").lower() != "true":
        raise RuntimeError("BAOSTOCK_RESEARCH_ACK_REQUIRED")
    if (re.fullmatch(r"[a-f0-9]{40}", revision) is None or not 1 <= seconds <= 240
            or not 0 <= new_windows <= 100 or not 0 <= retry_windows <= 20
            or new_windows + retry_windows < 1 or not 1 <= query_seconds <= 120):
        raise ValueError("HISTORY_SERVICE_RESOURCE_BUDGET_INVALID")
    status: dict[str, Any] = {
        **AUTHORITY, "schema_version": "qtrade.history-service.v1", "state": "STARTING",
        "started_at": datetime.now(UTC).isoformat(), "code_revision": revision,
        "plan_sha256": plan["evidence_sha256"], "period_seconds": PERIOD_SECONDS,
        "pass_budget_seconds": seconds, "completed_passes": 0, "failed_passes": 0,
        "last_pass_state": None, "last_error_class": None, "next_pass_not_before": None,
        "last_execution_sha256": None,
    }
    mutex = Lock()
    heartbeat_error: list[str] = []
    heartbeat_done = Event()
    path = base / "unified-data/backfill/service-state.json"

    def publish(**updates: Any) -> None:
        with mutex:
            status.update(updates, heartbeat_at=datetime.now(UTC).isoformat())
            write_json_atomic(path, signed(status))

    def heartbeats() -> None:
        while not heartbeat_done.wait(HEARTBEAT_SECONDS):
            try:
                publish()
            except Exception as error:
                # An unobservable service cannot keep issuing source requests.
                heartbeat_error.append(type(error).__name__)
                stop.set()
                break

    # A service lease spans idle periods; each run_pass retains its own writer
    # lease and inherited child lease, also excluding one-off CLI writers.
    with worker_lease(lease_dir / "history-service"):
        publish()
        heartbeat = Thread(target=heartbeats, name="history-heartbeat", daemon=True)
        heartbeat.start()
        try:
            while not stop.is_set():
                started = time.monotonic()
                due = datetime.now(UTC) + timedelta(seconds=PERIOD_SECONDS)
                publish(state="RUNNING", next_pass_not_before=due.isoformat(),
                        last_error_class=None)
                try:
                    result = run_pass(
                        base, root, plan, master, revision=revision, seconds=seconds,
                        new_windows=new_windows, retry_windows=retry_windows,
                        query_seconds=query_seconds, stop=stop, lease_dir=lease_dir,
                        extra_states=extra_states,
                    )
                except BlockingIOError:
                    publish(state="WAITING", last_pass_state="EXISTING_HISTORY_WRITER_ACTIVE")
                except Exception as error:
                    publish(state="FAILED", last_pass_state="FAILED",
                            failed_passes=status["failed_passes"] + 1,
                            last_error_class=type(error).__name__)
                else:
                    publish(state="STOPPING" if stop.is_set() else "WAITING",
                            completed_passes=status["completed_passes"] + 1,
                            last_pass_state=result["state"],
                            last_execution_sha256=result["evidence_sha256"])
                # Start-to-start cadence; long passes never build a catch-up queue.
                remaining = PERIOD_SECONDS - (time.monotonic() - started)
                if remaining > 0:
                    stop.wait(remaining)
        finally:
            # run_pass has reaped every capture before reaching this point.
            heartbeat_done.set()
            heartbeat.join(timeout=HEARTBEAT_SECONDS + 1)
            publish(state="FAILED" if heartbeat_error else "STOPPED",
                    last_error_class=heartbeat_error[0] if heartbeat_error
                    else status["last_error_class"], next_pass_not_before=None)
        if heartbeat_error:
            raise RuntimeError("HISTORY_SERVICE_HEARTBEAT_FAILED")
    return signed(status)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--master", type=Path)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--seconds", type=float, default=240)
    parser.add_argument("--new-windows", type=int, default=4)
    parser.add_argument("--retry-windows", type=int, default=1)
    parser.add_argument("--query-seconds", type=float, default=25)
    parser.add_argument("--priority-state", type=Path, action="append", default=[])
    parser.add_argument("--lease-dir", type=Path, default=Path("/root/qtrade-local"))
    args = parser.parse_args()
    stop = Event()
    for sig in (signal.SIGTERM, signal.SIGINT):
        signal.signal(sig, lambda *_: stop.set())
    result = serve(
        args.base, args.root, read_signed(args.plan or args.root / "plan.json"),
        read_signed(args.master or args.root / "master.json"), revision=args.source_revision,
        stop=stop, seconds=args.seconds, new_windows=args.new_windows,
        retry_windows=args.retry_windows, query_seconds=args.query_seconds,
        lease_dir=args.lease_dir, extra_states=tuple(args.priority_state),
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
