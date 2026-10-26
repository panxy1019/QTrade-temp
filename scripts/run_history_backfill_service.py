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
from scripts.run_history_backfill import run_pass, validate_cohort_roots, worker_lease

PERIOD_SECONDS = 300
HEARTBEAT_SECONDS = 15


def serve(
    base: Path, root: Path, plan: dict[str, Any], master: dict[str, Any], *, revision: str,
    stop: Event, seconds: float = 240, new_windows: int = 4, retry_windows: int = 1,
    query_seconds: float = 25, lease_dir: Path = Path("/root/qtrade-local"),
    extra_states: tuple[Path, ...] = (),
    probe_bundle: Path | None = None, probe_root: Path | None = None,
) -> dict[str, Any]:
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise RuntimeError("LIVE_TRADING_MUST_REMAIN_DISABLED")
    if os.environ.get("QTRADE_BAOSTOCK_FREE_DATA_ACK", "").lower() != "true":
        raise RuntimeError("BAOSTOCK_RESEARCH_ACK_REQUIRED")
    if (re.fullmatch(r"[a-f0-9]{40}", revision) is None or not 1 <= seconds <= 240
            or not 0 <= new_windows <= 100 or not 0 <= retry_windows <= 20
            or new_windows + retry_windows < 1 or not 1 <= query_seconds <= 120):
        raise ValueError("HISTORY_SERVICE_RESOURCE_BUDGET_INVALID")
    probe_identity = None
    if (probe_bundle is None) != (probe_root is None):
        raise ValueError("DELISTED_SERVICE_REQUIRES_BUNDLE_AND_SEPARATE_ROOT")
    if probe_bundle is not None and probe_root is not None:
        from scripts.delisted_history_queue import DelistedHistoryQueue

        validate_cohort_roots(root, probe_root)
        probe_identity = DelistedHistoryQueue(probe_root, probe_bundle, read_only=True).identity
    status: dict[str, Any] = {
        **AUTHORITY, "schema_version": "qtrade.history-service.v1", "state": "STARTING",
        "started_at": datetime.now(UTC).isoformat(), "code_revision": revision,
        "plan_sha256": plan["evidence_sha256"], "period_seconds": PERIOD_SECONDS,
        "pass_budget_seconds": seconds, "completed_passes": 0, "failed_passes": 0,
        "last_pass_state": None, "last_error_class": None, "next_pass_not_before": None,
        "last_execution_sha256": None,
        "delisted_probe_plan_sha256": probe_identity, "running_cohort": None,
        "scheduling_policy": "ALTERNATE_OBSERVED_DELISTED" if probe_identity else "OBSERVED_ONLY",
        "last_observed_execution_sha256": None, "last_delisted_execution_sha256": None,
        "last_pass_cohort": None,
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
        cycle = 0
        try:
            while not stop.is_set():
                is_probe = probe_bundle is not None and cycle % 2 == 1
                cohort = "DELISTED_PROBE" if is_probe else "OBSERVED"
                started = time.monotonic()
                due = datetime.now(UTC) + timedelta(seconds=PERIOD_SECONDS)
                publish(state="RUNNING", next_pass_not_before=due.isoformat(),
                        last_error_class=None, running_cohort=cohort)
                try:
                    result = run_pass(
                        base, probe_root if is_probe and probe_root is not None else root,
                        None if is_probe else plan, None if is_probe else master,
                        revision=revision, seconds=seconds,
                        new_windows=new_windows, retry_windows=retry_windows,
                        query_seconds=query_seconds, stop=stop, lease_dir=lease_dir,
                        extra_states=extra_states,
                        probe_bundle=probe_bundle if is_probe else None,
                        observed_root=root if is_probe else None,
                    )
                except BlockingIOError:
                    publish(state="WAITING", last_pass_state="EXISTING_HISTORY_WRITER_ACTIVE",
                            last_pass_cohort=cohort)
                except Exception as error:
                    publish(state="FAILED", last_pass_state="FAILED",
                            failed_passes=status["failed_passes"] + 1,
                            last_error_class=type(error).__name__, last_pass_cohort=cohort)
                else:
                    publish(state="STOPPING" if stop.is_set() else "WAITING",
                            completed_passes=status["completed_passes"] + 1,
                            last_pass_state=result["state"],
                            last_pass_cohort=cohort,
                            last_execution_sha256=result["evidence_sha256"],
                            **{"last_delisted_execution_sha256" if is_probe else
                               "last_observed_execution_sha256": result["evidence_sha256"]})
                cycle += 1
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
                    else status["last_error_class"], next_pass_not_before=None, running_cohort=None)
        if heartbeat_error:
            raise RuntimeError("HISTORY_SERVICE_HEARTBEAT_FAILED")
    return signed(status)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--master", type=Path)
    parser.add_argument("--delisted-probe-bundle", type=Path)
    parser.add_argument("--delisted-probe-root", type=Path)
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
        probe_bundle=args.delisted_probe_bundle, probe_root=args.delisted_probe_root,
    )
    print(json.dumps(result, ensure_ascii=False))


if __name__ == "__main__":
    main()
