"""Single supervised outbound worker; durable claims and bounded research subprocesses."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import sqlite3
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.qlib_publication import verify_archive
from qtrade.research.source_constraints import PROTOCOL, capture_source
from qtrade.research.workflow import catalog_envelope, data_audit, digest, validate_spec
from qtrade.research.workflow_queue import GitQueue, unseal
from scripts.atomic_json import write_json_atomic
from scripts.online_model_control import OnlineModels

REPO = Path(__file__).resolve().parents[1]
TERMINAL = {"COMPLETE", "FAILED", "CANCELLED", "INTERRUPTED", "REJECTED"}


def clock() -> str:
    return datetime.now(UTC).isoformat()


def terminate(child: subprocess.Popen[Any]) -> None:
    if child.poll() is not None:
        return
    with contextlib.suppress(ProcessLookupError):
        os.killpg(child.pid, signal.SIGTERM)
    try:
        child.wait(15)
    except subprocess.TimeoutExpired:
        with contextlib.suppress(ProcessLookupError):
            os.killpg(child.pid, signal.SIGKILL)
        child.wait()


def recover_child(job: Path) -> None:
    path = job / "child.json"
    if not path.exists():
        return
    value = json.loads(path.read_text())
    proc = Path("/proc") / str(int(value["pid"]))
    if not proc.exists():
        return
    command = (proc / "cmdline").read_bytes()
    start = (proc / "stat").read_text().split()[21]
    if start != value["start_ticks"] or b"scripts.run_qlib_baseline" not in command or (
        str(job / "output").encode() not in command
    ):
        raise ValueError("UNCERTAIN_CHILD_IDENTITY_REQUIRES_HUMAN_RECONCILIATION")
    with contextlib.suppress(ProcessLookupError):
        os.killpg(int(value["pid"]), signal.SIGKILL)


class Worker:
    def __init__(self, base: Path, database: Path, python: str, revision: str) -> None:
        self.base, self.python, self.revision = base, python, revision
        self.root = base / "workflow"
        self.root.mkdir(exist_ok=True)
        self.public = self.root / "public"
        self.public.mkdir(exist_ok=True)
        database.parent.mkdir(parents=True, exist_ok=True)
        self.db = sqlite3.connect(database)
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS jobs "
            "(id TEXT PRIMARY KEY, request TEXT NOT NULL, state TEXT NOT NULL, "
            "updated TEXT NOT NULL, result TEXT, error TEXT)"
        )
        self.db.execute(
            "CREATE TABLE IF NOT EXISTS events "
            "(id TEXT NOT NULL, at TEXT NOT NULL, state TEXT NOT NULL)"
        )
        for (identity,) in self.db.execute(
            "SELECT id FROM jobs WHERE state NOT IN "
            "('COMPLETE','FAILED','CANCELLED','INTERRUPTED','REJECTED','QUEUED')"
        ).fetchall():
            recover_child(self.root / "jobs" / identity)
            self.db.execute("INSERT INTO events VALUES (?,?,?)",
                            (identity, clock(), "INTERRUPTED"))
        # Uncertain prior runs never silently restart or get claimed a second time.
        self.db.execute(
            "UPDATE jobs SET state='INTERRUPTED', error='WORKER_RESTART_REQUIRES_RESUBMIT', "
            "updated=? WHERE state NOT IN ('COMPLETE','FAILED','CANCELLED',"
            "'INTERRUPTED','REJECTED','QUEUED')", (clock(),),
        )
        self.db.commit()
        self.stop = False
        self.queue = GitQueue.from_environment()
        self.secret = os.environ["QTRADE_RESEARCH_QUEUE_SECRET"]
        self.cancellations: set[str] = set()
        self.references: list[dict[str, Any]] | None = None
        self._source_version: str | None = None
        self._source_identity: str | None = None
        self.online = OnlineModels(base, database.with_name('online-models.sqlite'),
                                   database.with_name('qlib-versions.sqlite'))

    def freeze_source(self) -> str:
        root = self.base / "pit-repair"
        path = root / "coverage.json"
        version = digest(json.loads(path.read_bytes())) if path.exists() else "missing"
        if version == self._source_version and self._source_identity:
            return self._source_identity
        snapshot = capture_source(root)
        folder = self.root / "source-snapshots" / snapshot.identity
        if folder.exists():
            if capture_source(folder).identity != snapshot.identity:
                raise ValueError("PERSISTED_SOURCE_SNAPSHOT_MISMATCH")
        else:
            partial = folder.with_name(folder.name + "." + str(os.getpid()) + ".partial")
            snapshot.freeze(partial)
            partial.rename(folder)
        self._source_identity, self._source_version = snapshot.identity, version
        return snapshot.identity

    def ingest(self) -> None:
        values = self.queue.read()
        for key, envelope in values.items():
            identity = key.split("/")[1]
            try:
                value = unseal(envelope, self.secret)
                if value["job_id"] != identity:
                    raise ValueError("JOB_ID_MISMATCH")
                if value.get('kind') == 'QLIB_MODEL_CONTROL':
                    if (not key.startswith('requests/')
                            or value.get('live_trading_enabled') is not False
                            or value['command']['request_id'] != identity):
                        raise ValueError('MODEL_CONTROL_ENVELOPE_INVALID')
                    self.online.execute(value['command'], now=datetime.now(UTC))
                    continue
                if key.startswith("cancellations/"):
                    if value["kind"] != "CANCEL_RESEARCH_JOB":
                        raise ValueError("INVALID_CANCELLATION")
                    self.cancellations.add(identity)
                    continue
                if value["kind"] != "QLIB_RESEARCH_JOB" or (
                    value["live_trading_enabled"] is not False
                ):
                    raise ValueError("JOB_AUTHORITY_MISMATCH")
                spec = validate_spec(value["spec"])
                if digest(spec) != value["spec_sha256"]:
                    raise ValueError("SPEC_HASH_MISMATCH")
                raw = json.dumps(value, sort_keys=True)
                old = self.db.execute("SELECT request FROM jobs WHERE id=?", (identity,)).fetchone()
                if old is not None and old[0] != raw:
                    raise ValueError("IMMUTABLE_JOB_CONFLICT")
                new = self.db.execute(
                    "INSERT OR IGNORE INTO jobs (id,request,state,updated) VALUES (?,?,?,?)",
                    (identity, raw, "QUEUED", clock()),
                )
                if new.rowcount:
                    self.db.execute("INSERT INTO events VALUES (?,?,?)",
                                    (identity, clock(), "QUEUED"))
                    active = self.db.execute(
                        "SELECT COUNT(*) FROM jobs WHERE state NOT IN "
                        "('COMPLETE','FAILED','CANCELLED','INTERRUPTED','REJECTED')"
                    ).fetchone()[0]
                    if active > 3:
                        self.update(identity, "REJECTED", error="QUEUE_CAPACITY_EXCEEDED")
                self.db.commit()
            except (ValueError, KeyError, TypeError):
                # Malformed queue items are not executable. Do not publish their body.
                continue

    def update(
        self, identity: str, state: str, *, result: dict[str, Any] | None = None,
        error: str | None = None,
    ) -> None:
        self.db.execute("INSERT INTO events VALUES (?,?,?)", (identity, clock(), state))
        self.db.execute(
            "UPDATE jobs SET state=?,updated=?,result=COALESCE(?,result),error=? WHERE id=?",
            (state, clock(), json.dumps(result) if result else None, error, identity),
        )
        self.db.commit()

    def archive_detail(self, summary: dict[str, Any], identity: str) -> dict[str, Any]:
        spec = summary.get("workflow_spec")
        audit = data_audit(aligned=bool(spec))
        audit["source_repair_coverage"] = summary.get("pit_repair_coverage", {
            "state": "NOT_CAPTURED_IN_ORIGINAL_RUN", "historical_pit_certified": False,
        })
        constraints = summary.get("execution_constraints")
        if constraints:
            # Publish bounded aggregate counts; the full per-symbol ledger stays in the archive.
            audit["execution_constraints"] = {
                k: v for k, v in constraints.items() if k != "per_symbol"
            }
            audit["historical_st_and_halts"] = {
                "status": "RECONCILED_BACKFILL_DIAGNOSTIC_ONLY_NOT_PIT",
            }
            audit["execution"] = {
                "status": "DAILY_PROXY_WITH_SOURCE_BOUND_ADDITIONAL_BLOCKS",
            }
            audit["limitations"] = [*audit["limitations"],
                                    "BACKFILLED_STATE_RESTRICTIONS_ARE_POST_HOC_NOT_PIT"]
        detail = signed({
            **AUTHORITY, "schema_version": "qtrade.workflow-result.v1",
            "experiment_id": identity, "run_id": summary["run_id"],
            "source_revision": summary["source_revision"],
            "summary_sha256": summary["evidence_sha256"],
            "source_snapshot_sha256": summary.get("source_snapshot_sha256"),
            "state": "COMPLETE", "engine": "QLIB", "spec": spec,
            "model": summary["model"], "segments": summary["segments"],
            "data_end": summary["data_end"], "dataset_sha256": summary["dataset_sha256"],
            "symbols": summary.get("effective_universe_size", summary["symbols"]),
            "data_rows": summary["data_rows"], "feature_count": summary["feature_count"],
            "metrics": summary["metrics"], "curve": summary["curve"],
            "signal_evaluation": summary["signal_evaluation"],
            "portfolio_evaluation": summary["portfolio_evaluation"],
            "cost_assumptions": summary["cost_assumptions"],
            "portfolio": summary["portfolio"],
            "audit": audit,
            "limitations": sorted(set(summary["limitations"])),
            "persisted": True, "completed_at": summary["completed_at"],
            "comparison_protocol": (
                PROTOCOL if constraints else "ALIGNED_SUPPORTED_UNIVERSE_TRAIN_NORMALIZATION_V1"
                if spec else "LEGACY_BASELINE_DIFFERENT_PREPROCESSING_AND_UNIVERSE"
            ),
        })
        relative = "workflow-results/" + detail["evidence_sha256"] + ".json"
        write_json_atomic(self.public / relative, detail)
        return {
            "run_id": summary["run_id"], "detail_file": relative,
            "detail_sha256": detail["evidence_sha256"], "metrics": summary["metrics"],
            "audit": detail["audit"], "completed_at": summary["completed_at"],
            "source_revision": summary["source_revision"],
            "comparison_protocol": detail["comparison_protocol"],
        }

    def publish(self, state: str = "IDLE") -> None:
        rows = []
        # Import the accepted baseline as a reference, without relabeling its protocol.
        if self.references is None:
            self.references = []
            for path in sorted((self.base / "qlib/runs").glob("*/summary.json")):
                try:
                    summary = verify_archive(path.parent)
                    info = self.archive_detail(summary, summary["run_id"])
                    self.references.append({
                        "experiment_id": summary["run_id"], "state": "COMPLETE",
                        "name": "原始固定基线（对照档案）", "model": "lightgbm",
                        "engine": "QLIB", "spec": None, **info,
                    })
                except Exception:
                    continue
        rows.extend(self.references)
        for identity, raw, job_state, updated, result, error in self.db.execute(
            "SELECT id,request,state,updated,result,error FROM jobs ORDER BY rowid DESC LIMIT 100"
        ):
            request = json.loads(raw)
            rows.append({
                "experiment_id": identity, "name": request["spec"]["name"],
                "model": request["spec"]["model"], "engine": "QLIB",
                "state": job_state, "stage": job_state, "updated_at": updated,
                "spec": request["spec"], "spec_sha256": request["spec_sha256"],
                "audit": data_audit(), "error_code": error,
                "events": [{"at": at, "state": phase} for at, phase in self.db.execute(
                    "SELECT at,state FROM events WHERE id=? ORDER BY rowid", (identity,)
                )],
                **(json.loads(result) if result else {}),
            })
        value = catalog_envelope(
            published_at=clock(), experiments=rows,
            source_snapshot_sha256=self.freeze_source(),
            worker={"state": state, "heartbeat_at": clock(), "concurrency": 1,
                    "source_revision": self.revision, "max_job_seconds": 3600},
        )
        write_json_atomic(self.public / "workflow.json", value)
        write_json_atomic(self.public/'online.json',
                          self.online.publication(self.revision, now=datetime.now(UTC),
                                                  worker_state=state))
        # A consistent local-SQLite backup on NFS, never the active database.
        with sqlite3.connect(self.root / "catalog-backup.sqlite.tmp") as backup:
            self.db.backup(backup)
        (self.root / "catalog-backup.sqlite.tmp").replace(self.root / "catalog-backup.sqlite")
        with (
            contextlib.closing(sqlite3.connect(self.online.registry.database)) as model_source,
            contextlib.closing(sqlite3.connect(
                self.online.root/'catalog-backup.sqlite.tmp')) as backup,
        ):
            model_source.backup(backup)
        (self.online.root/'catalog-backup.sqlite.tmp').replace(
            self.online.root/'catalog-backup.sqlite')

    def execute(self, identity: str, request: dict[str, Any]) -> None:
        if identity in self.cancellations:
            self.update(identity, "CANCELLED")
            return
        if __import__("shutil").disk_usage(self.base).free < 100 * 1024**3:
            self.update(identity, "REJECTED", error="DISK_RESERVE_BELOW_100_GIB")
            return
        job = self.root / "jobs" / identity
        job.mkdir(parents=True, exist_ok=True)
        write_json_atomic(job / "request.json", request)
        write_json_atomic(job / "spec.json", request["spec"])
        source_id = request["spec"].get("source_snapshot_sha256") or self.freeze_source()
        source_root = self.root / "source-snapshots" / source_id
        if capture_source(source_root).identity != source_id:
            raise ValueError("REQUESTED_SOURCE_SNAPSHOT_UNAVAILABLE")
        output = job / "output"
        self.update(identity, "RUNNING")
        self.publish("RUNNING")
        command = [
            self.python, "-m", "scripts.run_qlib_baseline",
            "--data-root", str(self.base / "qlib-data-20261023"),
            "--output", str(output),
            "--tracking", "/root/qtrade-local/workflow-mlruns",
            "--source-revision", self.revision,
            "--workflow-spec", str(job / "spec.json"),
            "--pit-evidence-root", str(source_root),
        ]
        with (job / "runner.log").open("ab") as log, contextlib.ExitStack() as cleanup:
            child = subprocess.Popen(
                command, cwd=REPO, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
                env={**{k: v for k, v in os.environ.items()
                        if not k.startswith("QTRADE_RESEARCH_")},
                     "PYTHONPATH": str(REPO / "src"),
                     "LIVE_TRADING_ENABLED": "false", "OMP_NUM_THREADS": "6"},
            )
            cleanup.callback(terminate, child)
            write_json_atomic(job / "child.json", {
                "pid": child.pid,
                "start_ticks": (Path("/proc") / str(child.pid) / "stat").read_text().split()[21],
            })
            started = time.monotonic()
            last_state = "RUNNING"
            while child.poll() is None:
                if self.stop or identity in self.cancellations or (
                    time.monotonic() - started > 3600
                ):
                    terminate(child)
                    state = "CANCELLED" if identity in self.cancellations else "INTERRUPTED"
                    self.update(identity, state, error="CANCEL_TIMEOUT_OR_WORKER_STOP")
                    return
                status = output / "status.json"
                if status.exists():
                    phase = json.loads(status.read_text()).get("state")
                    if phase and phase not in TERMINAL and phase != last_state:
                        last_state = phase
                        self.update(identity, phase)
                self.publish("RUNNING")
                time.sleep(10)
                with contextlib.suppress(Exception):
                    self.ingest()
        if child.returncode:
            self.update(identity, "FAILED", error="QLIB_RUN_FAILED_SEE_PRIVATE_CLUSTER_LOG")
            return
        status = json.loads((output / "status.json").read_text())
        archive = output / "runs" / status["run_id"]
        summary = verify_archive(archive)
        self.update(identity, "COMPLETE", result=self.archive_detail(summary, identity))

    def once(self) -> None:
        self.ingest()
        row = self.db.execute(
            "SELECT id,request FROM jobs WHERE state='QUEUED' ORDER BY rowid LIMIT 1"
        ).fetchone()
        if row:
            try:
                self.execute(row[0], json.loads(row[1]))
            except Exception:
                self.update(row[0], "FAILED", error="WORKER_JOB_VALIDATION_FAILED")
        self.publish()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--base", type=Path, required=True)
    parser.add_argument("--database", type=Path, required=True)
    parser.add_argument("--python", required=True)
    parser.add_argument("--source-revision", required=True)
    parser.add_argument("--once", action="store_true")
    args = parser.parse_args()
    # Container-local exclusive process lease; a second supervisor cannot run jobs.
    import fcntl
    args.database.parent.mkdir(parents=True, exist_ok=True)
    lease = args.database.with_suffix(".lock").open("a")
    fcntl.flock(lease, fcntl.LOCK_EX | fcntl.LOCK_NB)
    if os.environ.get("LIVE_TRADING_ENABLED", "false").lower() != "false":
        raise RuntimeError("live trading must remain disabled")
    worker = Worker(args.base, args.database, args.python, args.source_revision)
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: setattr(worker, "stop", True))
    while not worker.stop:
        try:
            worker.once()
        except Exception as error:
            worker.publish("QUEUE_UNAVAILABLE")
            print("WORKFLOW_POLL_FAILED:" + type(error).__name__, flush=True)
        if args.once:
            break
        time.sleep(15)


if __name__ == "__main__":
    main()
