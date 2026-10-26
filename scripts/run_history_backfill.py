"""Bounded, low-priority day/5min historical execution; never publishes a Qlib version."""

from __future__ import annotations

import argparse
import base64
import contextlib
import json
import math
import os
import re
import shutil
import signal
import socket
import subprocess
import sys
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from hashlib import sha256
from importlib import import_module
from pathlib import Path
from threading import Event
from typing import Any, TextIO

from qtrade.data.storage import _fsync_directory
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts.atomic_json import write_json_atomic
from scripts.history_backfill_queue import HistoryQueue, read_signed
from scripts.history_backfill_store import HistoryStore, request_for
from scripts.history_source_gate import HistorySourceGate, history_backoff_reason
from scripts.vendor_session import baostock_session

REPO = Path(__file__).resolve().parents[1]
RESERVE = 100 * 1024**3
WRITE_HEADROOM = 16 * 1024**2
STREAM_LIMIT = 2_000_000


def pit_backoff_reason(base: Path, now: datetime) -> str | None:
    """Separate vendor-failure backoff from the PIT queue's normal scheduling clock."""
    path = base / "pit-repair/next-pass.json"
    try:
        with path.open("rb") as handle:
            payload = handle.read(64_001)
    except FileNotFoundError:
        return None
    except OSError:
        return "PIT_SOURCE_BACKOFF_INVALID"
    try:
        if len(payload) > 64_000:
            raise ValueError("PIT_SCHEDULE_TOO_LARGE")
        value = json.loads(payload)
        if not isinstance(value, dict):
            raise ValueError("PIT_SCHEDULE_NOT_OBJECT")
        outcome = value.get("outcome")
        failures = value.get("consecutive_failures", 0)
        returncode = value.get("returncode")
        receipt_valid = value.get("receipt_valid")
        if (outcome not in {"RUNNING", "FAILED", "PARTIAL", "SUCCEEDED"}
                or type(failures) is not int or failures < 0
                or "returncode" in value and type(returncode) is not int
                or "receipt_valid" in value and type(receipt_valid) is not bool):
            raise ValueError("PIT_SCHEDULE_OUTCOME_UNCLASSIFIABLE")
        deadline = (datetime.fromisoformat(value["next_attempt_at"])
                    if "next_attempt_at" in value else None)
        if now.tzinfo is None or deadline is not None and deadline.tzinfo is None:
            raise ValueError("PIT_SCHEDULE_TIMEZONE_REQUIRED")
        # RUNNING deliberately has no deadline and carries the prior failure count.
        # The shared vendor lease prevents an overlapping anonymous session.
        if outcome == "RUNNING":
            return None
        failed = (outcome == "FAILED" or failures > 0 or receipt_valid is False
                  or returncode is not None and returncode != 0)
        if failed:
            if deadline is None:
                raise ValueError("PIT_FAILURE_DEADLINE_MISSING")
            return "PIT_SOURCE_BACKOFF_ACTIVE" if now < deadline else None
        if returncode != 0 or outcome == "PARTIAL" and receipt_valid is not True:
            raise ValueError("PIT_NORMAL_SCHEDULE_RECEIPT_UNCLASSIFIABLE")
        # Successful passes wait six hours; valid partial batches can wait for
        # per-symbol retries. Neither interval is an endpoint-wide source failure.
    except (ValueError, TypeError):
        return "PIT_SOURCE_BACKOFF_INVALID"
    return None


def priority_reason(base: Path, now: datetime, extra_states: tuple[Path, ...] = (),
                    history_gate_required: bool = False) -> str | None:
    """Missing/stale current collector evidence cannot authorize expensive history work."""
    if reason := pit_backoff_reason(base, now):
        return reason
    if reason := history_backoff_reason(base, now, history_gate_required):
        return reason
    paths = (base / "unified-data/service-state.json", *extra_states)
    idle = {"WAITING", "IDLE", "SUCCEEDED", "PASS_FINISHED", "COMPLETE", "STOPPED"}
    for path in paths:
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
            stamp = datetime.fromisoformat(value.get("heartbeat_at") or value["updated_at"])
            age = (now - stamp).total_seconds()
            if stamp.tzinfo is None or not 0 <= age <= 300:
                return "CURRENT_PRIORITY_EVIDENCE_STALE_OR_FUTURE"
            if value.get("state") not in idle:
                return "CURRENT_PRIORITY_WORK_ACTIVE_OR_FAILED"
        except (OSError, ValueError, KeyError, TypeError):
            return "CURRENT_PRIORITY_EVIDENCE_UNAVAILABLE"
    return None


@contextlib.contextmanager
def worker_lease(directory: Path) -> Iterator[TextIO]:
    import fcntl

    directory.mkdir(parents=True, exist_ok=True)
    with (directory / "history-backfill.lock").open("a") as handle:
        fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        yield handle


def capture_vendor(
    store: HistoryStore, attempt: dict[str, Any], api: Any, *,
    before_login: Callable[[], str | None] | None = None,
) -> None:
    """Run only in a bounded child. Each decoded row is fsynced before the next read."""
    path = store.path(attempt["attempt_id"], "capture.jsonl")
    total = 0
    with path.open("xb") as stream:
        _fsync_directory(path.parent)
        def emit(kind: str, **value: Any) -> None:
            nonlocal total
            encoded = (json.dumps({"kind": kind, "observed_at": datetime.now(UTC).isoformat(),
                                  **value}, ensure_ascii=False,
                                  separators=(",", ":")) + "\n").encode()
            total += len(encoded)
            if total > STREAM_LIMIT:
                raise ValueError("HISTORY_CAPTURE_STREAM_LIMIT")
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())

        queried = False
        try:
            with baostock_session(blocking=False):
                if before_login is not None and (reason := before_login()):
                    emit("finished", state="YIELDED_TO_CURRENT_COLLECTION", body_complete=False,
                         vendor_error_code=None, error=reason)
                    return
                if api.login().error_code != "0":
                    emit("finished", state="LOGIN_FAILED", body_complete=False,
                         vendor_error_code=None, error="VENDOR_LOGIN_FAILED")
                    return
                try:
                    request = request_for(attempt["scope"])
                    queried = True
                    emit("query_started")
                    result = api.query_history_k_data_plus(request["code"], request["fields"], **{
                        k: request[k] for k in ("start_date", "end_date", "frequency", "adjustflag")
                    })
                    returned_fields = list(result.fields)
                    if len(returned_fields) > 20 or any(not isinstance(v, str) or len(v) > 100
                                                       for v in returned_fields):
                        raise ValueError("HISTORY_RESPONSE_FIELDS_LIMIT")
                    emit("fields", values=returned_fields)
                    count = 0
                    limit = 31 if attempt["scope"]["frequency"] == "day" else 1488
                    while result.error_code == "0" and result.next():
                        row = list(result.get_row_data())
                        if (count >= limit or len(row) > 20
                                or any(not isinstance(v, str) or len(v) > 100 for v in row)):
                            raise ValueError("HISTORY_RESPONSE_ROWS_LIMIT")
                        emit("row", values=row)
                        count += 1
                    code = str(result.error_code)
                    if re.fullmatch(r"[0-9-]{1,20}", code) is None:
                        code = "INVALID_VENDOR_CODE"
                    emit("finished", state="CAPTURED" if code == "0" else "SOURCE_QUERY_FAILED",
                         body_complete=code == "0", vendor_error_code=code, error=None)
                finally:
                    # Logout is bounded by the parent deadline too, and cannot erase a capture.
                    with contextlib.suppress(Exception):
                        api.logout()
        except BlockingIOError:
            emit("finished", state="YIELDED_TO_SHARED_VENDOR_SESSION", body_complete=False,
                 vendor_error_code=None, error=None)
        except Exception as error:
            emit("finished", state="SOURCE_QUERY_FAILED" if queried else "LOGIN_FAILED",
                 body_complete=False, vendor_error_code=None, error=type(error).__name__)


def read_capture(store: HistoryStore, attempt: dict[str, Any]) -> dict[str, Any]:
    path = store.path(attempt["attempt_id"], "capture.jsonl")
    if path.exists():
        with path.open("rb") as handle:
            stream = handle.read(STREAM_LIMIT + 1)
    else:
        stream = b""
    if len(stream) > STREAM_LIMIT:
        raise ValueError("HISTORY_CAPTURE_STREAM_LIMIT")
    value: dict[str, Any] = {
        "request": request_for(attempt["scope"]), "fields": [], "rows": [],
        "body_complete": False, "vendor_error_code": None,
        "transport_error": "INTERRUPTED_CAPTURE", "state": "INTERRUPTED",
        "query_attempted": False,
        "capture_observed_at": None,
        "capture_basis": "UNMODIFIED_DECODED_BAOSTOCK_API_ROWS_NOT_TCP_WIRE_BYTES",
        "capture_stream_sha256": sha256(stream).hexdigest(),
        "capture_stream_base64": base64.b64encode(stream).decode(),
    }
    for line in stream.splitlines(keepends=True):
        if not line.endswith(b"\n"):
            break  # The exact interrupted tail remains in capture_stream_base64.
        event = json.loads(line)
        value["capture_observed_at"] = event["observed_at"]
        if event["kind"] == "fields":
            value["fields"] = event["values"]
        elif event["kind"] == "row":
            value["rows"].append(event["values"])
        elif event["kind"] == "query_started":
            value["query_attempted"] = True
        elif event["kind"] == "finished":
            value.update({k: event[k] for k in ("state", "body_complete", "vendor_error_code")})
            value["transport_error"] = event["error"]
    return value


def reap(child: subprocess.Popen[bytes]) -> None:
    if child.poll() is None:
        child.terminate()
        try:
            child.wait(timeout=3)
        except subprocess.TimeoutExpired:
            child.kill()
            child.wait(timeout=3)


def download(
    store: HistoryStore, attempt: dict[str, Any], *, base: Path, seconds: float,
    stop: Event, extra_states: tuple[Path, ...] = (),
    worker_lease_fd: int | None = None,
) -> tuple[dict[str, Any], str | None]:
    if stop.is_set():
        return read_capture(store, attempt), "INTERRUPTED"
    if priority_reason(base, datetime.now(UTC), extra_states, True):
        return read_capture(store, attempt), "YIELDED_TO_CURRENT_COLLECTION"
    child = subprocess.Popen([
        sys.executable, "-m", "scripts.run_history_backfill", "--capture-root", str(store.root),
        "--capture-attempt", attempt["attempt_id"], "--capture-seconds", str(seconds),
        "--base", str(base),
        "--history-gate-required",
        *[part for path in extra_states for part in ("--priority-state", str(path))],
    ], cwd=REPO, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        pass_fds=() if worker_lease_fd is None else (worker_lease_fd,))
    started = time.monotonic()
    state = None
    try:
        while child.poll() is None:
            if stop.is_set():
                state = "INTERRUPTED"
                break
            if priority_reason(base, datetime.now(UTC), extra_states, True):
                state = "YIELDED_TO_CURRENT_COLLECTION"
                break
            if shutil.disk_usage(base).free < RESERVE:
                state = "STORAGE_RESERVE_PAUSED"
                break
            if time.monotonic() - started >= seconds:
                state = "QUERY_TIMEOUT"
                break
            stop.wait(0.2)
    finally:
        reap(child)  # A killed child releases its OS vendor lease before return.
    raw = read_capture(store, attempt)
    # Supervisor may signal the whole process group, so the capture can exit
    # before the polling body sees stop. Intentional shutdown is not a vendor fault.
    if state is None and stop.is_set() and not raw["body_complete"]:
        state = "INTERRUPTED"
    if state is None and child.returncode != 0 and not raw["body_complete"]:
        state = "QUERY_TIMEOUT" if child.returncode == 124 else "SOURCE_QUERY_FAILED"
    if state is None and raw["state"] != "CAPTURED":
        state = raw["state"]
    return raw, state


def run_pass(
    base: Path, root: Path, plan: dict[str, Any] | None, master: dict[str, Any] | None,
    *, revision: str,
    seconds: float = 240, new_windows: int = 4, retry_windows: int = 1,
    query_seconds: float = 25, stop: Event | None = None,
    lease_dir: Path = Path("/root/qtrade-local"), extra_states: tuple[Path, ...] = (),
    downloader: Callable[..., tuple[dict[str, Any], str | None]] = download,
    probe_bundle: Path | None = None, observed_root: Path | None = None,
) -> dict[str, Any]:
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise RuntimeError("LIVE_TRADING_MUST_REMAIN_DISABLED")
    if (not 1 <= seconds <= 1800 or not 0 <= new_windows <= 100
            or not 0 <= retry_windows <= 20 or new_windows + retry_windows < 1
            or not 1 <= query_seconds <= 120 or re.fullmatch(r"[a-f0-9]{40}", revision) is None):
        raise ValueError("HISTORY_RESOURCE_BUDGET_INVALID")
    stop = stop or Event()
    started = time.monotonic()
    with worker_lease(lease_dir) as lease:
        if probe_bundle is not None:
            from scripts.delisted_history_queue import DelistedHistoryQueue

            if plan is not None or master is not None or observed_root is None:
                raise ValueError("DELISTED_EXECUTION_REQUIRES_OBSERVED_ROOT_AND_SEPARATE_BUNDLE")
            validate_cohort_roots(observed_root, root)
            observed = HistoryQueue(observed_root, read_signed(observed_root / "plan.json"),
                                    read_signed(observed_root / "master.json"))
            queue: HistoryQueue = DelistedHistoryQueue(root, probe_bundle, now=datetime.now(UTC))
        else:
            if plan is None or master is None or observed_root is not None:
                raise ValueError("OBSERVED_EXECUTION_REQUIRES_FROZEN_PLAN_AND_MASTER")
            queue = observed = HistoryQueue(root, plan, master)
        source_gate = HistorySourceGate(base, observed, now=datetime.now(UTC))
        source_recovery = source_gate.recover_pending(now=datetime.now(UTC))
        source_gate.bind(observed, HistoryStore(observed.root / "lake"), now=datetime.now(UTC))
        source_gate.prepare_queue_recovery(observed, HistoryStore(observed.root / "lake"),
                                           now=datetime.now(UTC))
        if queue is not observed:
            # A pre-upgrade active receipt can contain a source failure absent from
            # its old scheduler. Replay it before granting a second cohort access.
            observed.recover(HistoryStore(observed.root / "lake"), now=datetime.now(UTC))
            source_gate.bind(queue, HistoryStore(root / "lake"), now=datetime.now(UTC))
        store = HistoryStore(root / "lake")
        if queue is not observed:
            source_gate.prepare_queue_recovery(queue, store, now=datetime.now(UTC))
        recovery = queue.recover(store, now=datetime.now(UTC))
        attempted, state, reason = 0, "PASS_FINISHED", None
        pass_counts = {key: 0 for key in ("requested", "downloaded", "downloaded_rows",
                                         "parsed_rows",
                                         "qualified_rows_including_reobservations",
                                         "newly_qualified_rows", "independent_source_reconciled",
                                         "published_qlib_versions")}
        for retry in [False] * new_windows + [True] * retry_windows:
            remaining = seconds - (time.monotonic() - started)
            if stop.is_set() or remaining < 1:
                state = "STOPPED" if stop.is_set() else "PASS_BUDGET_REACHED"
                break
            reason = priority_reason(base, datetime.now(UTC), extra_states, True)
            if reason:
                state = ("WAITING_FOR_GLOBAL_SOURCE_BACKOFF" if reason.startswith(
                    "HISTORY_SOURCE_BACKOFF") else "YIELDED_TO_CURRENT_COLLECTION")
                break
            if shutil.disk_usage(base).free < RESERVE + WRITE_HEADROOM:
                state = "STORAGE_RESERVE_PAUSED"
                break
            attempt = queue.claim(now=datetime.now(UTC), revision=revision, retry=retry)
            if attempt is None:
                if queue.meta["global_next_attempt_at"]:
                    state = "WAITING_FOR_GLOBAL_SOURCE_BACKOFF"
                continue
            store.begin(attempt)
            source_gate.begin(root, attempt, now=datetime.now(UTC))
            raw, override = downloader(store, attempt, base=base,
                                       seconds=min(query_seconds, remaining), stop=stop,
                                       extra_states=extra_states,
                                       worker_lease_fd=lease.fileno() if lease else None)
            receipt = store.seal(attempt, raw, state=override)
            task = queue.tasks[attempt["scope"]["task_id"]]
            previous_rows = task["windows"].get(attempt["scope"]["start"], {}).get(
                "qualified_rows", 0)
            queue.finish(store, receipt, now=datetime.now(UTC))
            pass_counts["qualified_rows_including_reobservations"] += receipt["quality"][
                "qualified_rows"]
            pass_counts["newly_qualified_rows"] += queue.tasks[attempt["scope"]["task_id"]][
                "windows"][attempt["scope"]["start"]]["qualified_rows"] - previous_rows
            pass_counts["downloaded_rows"] += receipt["downloaded_rows"]
            for key in ("requested", "downloaded", "parsed_rows"):
                pass_counts[key] += receipt["stage_counts"][key]
            attempted += 1
            if override in {"YIELDED_TO_CURRENT_COLLECTION", "YIELDED_TO_SHARED_VENDOR_SESSION",
                            "STORAGE_RESERVE_PAUSED", "INTERRUPTED"}:
                state = override
                if override == "YIELDED_TO_CURRENT_COLLECTION":
                    reason = priority_reason(base, datetime.now(UTC), extra_states, True)
                break
            if receipt["quality"]["state"] in {
                    "LOGIN_FAILED", "SOURCE_QUERY_FAILED", "QUERY_TIMEOUT"}:
                state, reason = "WAITING_FOR_GLOBAL_SOURCE_BACKOFF", "HISTORY_SOURCE_BACKOFF_ACTIVE"
                break
        report = queue.snapshot(now=datetime.now(UTC))
        report = signed({**{k: v for k, v in report.items() if k != "evidence_sha256"},
                         "state": state, "priority_reason": reason, "attempted_windows": attempted,
                         "stage_counts_this_pass": pass_counts,
                         "recovery": recovery, "code_revision": revision,
                         "shared_source_recovery": source_recovery,
                         "shared_source_backoff_sha256": source_gate.value["evidence_sha256"],
                         "global_next_attempt_at": source_gate.value["next_attempt_at"],
                         "free_bytes": shutil.disk_usage(base).free,
                         "elapsed_seconds": round(time.monotonic() - started, 3)})
        write_json_atomic(root / "progress.json", report)
        # The small pointer is safe for a publisher; full gaps remain in progress.json.
        summary = "delisted-execution-latest.json" if probe_bundle else "execution-latest.json"
        write_json_atomic(base / "unified-data/backfill" / summary, signed({
            **{k: v for k, v in report.items() if k not in {"tasks", "evidence_sha256"}},
            "progress_sha256": report["evidence_sha256"],
        }))
        return report


def validate_cohort_roots(observed: Path, probe: Path) -> None:
    if (observed.resolve().is_relative_to(probe.resolve())
            or probe.resolve().is_relative_to(observed.resolve())):
        raise ValueError("HISTORY_COHORT_ROOTS_MUST_BE_SEPARATE")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path)
    parser.add_argument("--root", type=Path)
    parser.add_argument("--plan", type=Path)
    parser.add_argument("--master", type=Path)
    parser.add_argument("--delisted-probe-bundle", type=Path)
    parser.add_argument("--observed-root", type=Path)
    parser.add_argument("--source-revision")
    parser.add_argument("--seconds", type=float, default=240)
    parser.add_argument("--new-windows", type=int, default=4)
    parser.add_argument("--retry-windows", type=int, default=1)
    parser.add_argument("--query-seconds", type=float, default=25)
    parser.add_argument("--priority-state", type=Path, action="append", default=[])
    parser.add_argument("--lease-dir", type=Path, default=Path("/root/qtrade-local"))
    parser.add_argument("--capture-root", type=Path, help=argparse.SUPPRESS)
    parser.add_argument("--capture-attempt", help=argparse.SUPPRESS)
    parser.add_argument("--capture-seconds", type=float, default=25, help=argparse.SUPPRESS)
    parser.add_argument("--history-gate-required", action="store_true", help=argparse.SUPPRESS)
    args = parser.parse_args()
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise RuntimeError("LIVE_TRADING_MUST_REMAIN_DISABLED")
    if os.environ.get("QTRADE_BAOSTOCK_FREE_DATA_ACK", "").lower() != "true":
        raise RuntimeError("BAOSTOCK_RESEARCH_ACK_REQUIRED")
    if args.capture_attempt:
        if args.capture_root is None:
            parser.error("capture root required")
        store = HistoryStore(args.capture_root)
        request = read_signed(store.path(args.capture_attempt, "request.json"))
        attempt = {k: v for k, v in request.items() if k != "evidence_sha256"}
        # Request authority is added only to the immutable envelope, not the queue claim.
        attempt = {k: v for k, v in attempt.items() if k not in AUTHORITY}
        if not 0 < args.capture_seconds <= 120:
            raise ValueError("HISTORY_CAPTURE_DEADLINE_INVALID")
        # Keep the inherited worker lease if the parent is SIGKILLed. An independent
        # hard child deadline then releases both leases without an orphan writer.
        signal.signal(signal.SIGALRM, lambda *_: os._exit(124))
        signal.alarm(math.ceil(args.capture_seconds))
        socket.setdefaulttimeout(15)
        capture_vendor(store, attempt, import_module("baostock"),
                       before_login=(lambda: priority_reason(
                           args.base, datetime.now(UTC), tuple(args.priority_state),
                           args.history_gate_required))
                       if args.base else None)
        return
    if not args.base or not args.root or not args.source_revision:
        parser.error("--base, --root and --source-revision required")
    if args.delisted_probe_bundle and (args.plan or args.master):
        parser.error("delisted bundle requires its own source inputs; omit --plan and --master")
    plan_path = args.plan or args.root / "plan.json"
    master_path = args.master or args.root / "master.json"
    stop = Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    result = run_pass(
        args.base, args.root, None if args.delisted_probe_bundle else read_signed(plan_path),
        None if args.delisted_probe_bundle else read_signed(master_path),
        revision=args.source_revision, seconds=args.seconds, new_windows=args.new_windows,
        retry_windows=args.retry_windows, query_seconds=args.query_seconds, stop=stop,
        extra_states=tuple(args.priority_state), lease_dir=args.lease_dir,
        probe_bundle=args.delisted_probe_bundle, observed_root=args.observed_root,
    )
    print(json.dumps({k: v for k, v in result.items() if k != "tasks"}, ensure_ascii=False))


if __name__ == "__main__":
    main()
