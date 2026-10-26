"""Single supervised outbound worker; durable claims and bounded research subprocesses."""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.qlib_publication import verify_archive
from qtrade.research.rolling_control import RollingControl
from qtrade.research.source_constraints import capture_source, constraint_protocol
from qtrade.research.workflow import catalog_envelope, data_audit, digest, validate_spec
from qtrade.research.workflow_queue import GitQueue, unseal
from scripts.atomic_json import write_json_atomic, write_json_immutable
from scripts.online_model_control import OnlineModels
from scripts.online_prediction_worker import OnlinePredictor

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
        request = job/'request.json'
        if request.exists() and json.loads(request.read_text()).get('kind') in {
                'QLIB_ROLLING_JOB', 'QLIB_MODEL_VALIDATION', 'QLIB_SIGNAL_JOB'}:
            # Cover a crash after spawn but before child.json is sealed.
            for proc in Path('/proc').iterdir():
                if not proc.name.isdigit():
                    continue
                try:
                    args = (proc/'cmdline').read_bytes().split(b'\0')
                    match = (any(m in args for m in (b'scripts.run_qlib_rolling',
                                b'scripts.run_qlib_candidate_validation',
                                b'scripts.run_qlib_signal_baselines')) and b'--output' in args
                             and args[args.index(b'--output')+1] == str(job/'output').encode())
                    if match:
                        if os.getpgid(int(proc.name)) != int(proc.name):
                            raise ValueError('ROLLING_ORPHAN_PROCESS_GROUP_UNCERTAIN')
                        os.killpg(int(proc.name), signal.SIGKILL)
                except (FileNotFoundError, ProcessLookupError):
                    continue
        return
    value = json.loads(path.read_text())
    proc = Path("/proc") / str(int(value["pid"]))
    if not proc.exists():
        return
    command = (proc / "cmdline").read_bytes()
    start = (proc / "stat").read_text().split()[21]
    if start != value["start_ticks"] or not any(module in command for module in (
        b"scripts.run_qlib_baseline", b"scripts.run_qlib_rolling",
        b'scripts.run_qlib_candidate_validation', b'scripts.run_qlib_signal_baselines')) or (
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
        self.predictor = OnlinePredictor(self.online, python, revision)
        self.rolling = RollingControl(self.db)

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
                if value.get('kind') == 'QLIB_ROLLING_POLICY':
                    if (not key.startswith('requests/')
                            or value.get('live_trading_enabled') is not False
                            or value['command']['request_id'] != identity):
                        raise ValueError('ROLLING_CONTROL_ENVELOPE_INVALID')
                    self.rolling.apply(value['command'], now=datetime.now(UTC))
                    continue
                if value.get('kind') == 'QLIB_DATA_ROLLBACK':
                    if (not key.startswith('requests/')
                            or value.get('live_trading_enabled') is not False
                            or value['command']['request_id'] != identity):
                        raise ValueError('DATA_CONTROL_ENVELOPE_INVALID')
                    self.execute_data_rollback(value['command'])
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

    def execute_data_rollback(self, value: dict[str, Any]) -> None:
        from qtrade.research.data_version_control import command
        from scripts.prepare_qlib_incremental import load_signed

        value = command(value)
        if self.stop or self.predictor.child is not None:
            return
        folder = self.root/'data-control'/value['request_id']
        folder.mkdir(parents=True, exist_ok=True)
        request = folder/'request.json'
        if request.exists():
            if json.loads(request.read_bytes()) != value:
                raise ValueError('DATA_CONTROL_REQUEST_ID_CONFLICT')
        else:
            write_json_immutable(request, value)
        receipt = folder/'receipt.json'
        if receipt.exists():
            saved = load_signed(receipt)
            if (saved['request_id'] != value['request_id']
                    or saved['command_sha256'] != signed(value)['evidence_sha256']):
                raise ValueError('DATA_CONTROL_RECEIPT_CONFLICT')
            return
        now = datetime.now(UTC)
        attempt = folder/'attempt.json'
        if attempt.exists() and datetime.fromisoformat(load_signed(attempt)['not_before']) > now:
            return
        write_json_atomic(attempt, signed({**AUTHORITY,
            'not_before': (now+timedelta(minutes=5)).isoformat()}))
        # A short-lived sole writer shares the builder lock; parent never claims its DB.
        with (folder/'runner.log').open('ab') as log:
            try:
                subprocess.run([sys.executable, '-m', 'scripts.data_version_control', 'rollback',
                    '--database', str(self.online.data_database), '--archive-root',
                    str(self.base/'unified-data/qlib-versions'), '--request', str(request),
                    '--output', str(receipt)], cwd=REPO, timeout=180, check=False,
                    stdout=log, stderr=subprocess.STDOUT,
                    env={**{k: v for k, v in os.environ.items()
                           if not k.startswith('QTRADE_RESEARCH_')},
                         'PYTHONPATH': str(REPO/'src'), 'LIVE_TRADING_ENABLED': 'false'})
            except subprocess.TimeoutExpired:
                return

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
        rolling = summary.get('rolling_plan')
        audit = data_audit(aligned=bool(spec or rolling))
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
            **({'rolling_plan': rolling} if rolling else {}),
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
                'MATURE_ROLLING_SOURCE_BOUND_V1' if rolling else
                constraint_protocol(constraints) if constraints
                else "ALIGNED_SUPPORTED_UNIVERSE_TRAIN_NORMALIZATION_V1"
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
            if request.get('kind') in {'QLIB_MODEL_VALIDATION', 'QLIB_SIGNAL_JOB'}:
                continue  # Listed with candidate controls, not as a portfolio experiment.
            rolling = request.get('kind') == 'QLIB_ROLLING_JOB'
            rows.append({
                "experiment_id": identity,
                "name": request['name'] if rolling else request["spec"]["name"],
                "model": request['model'] if rolling else request["spec"]["model"],
                "engine": "QLIB",
                "state": job_state, "stage": job_state, "updated_at": updated,
                "spec": request.get("spec"), "spec_sha256": request.get("spec_sha256"),
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
        online_value = self.online.publication(self.revision, now=datetime.now(UTC),
                                                worker_state=state)
        online_value['auto_predict'] = self.predictor.publication(now=datetime.now(UTC))
        online_value['auto_train'] = self.rolling.publication()
        write_json_atomic(self.public/'online.json', signed({
            k: v for k, v in online_value.items() if k != 'evidence_sha256'}))
        self.publish_signals()
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
        output = job / "output"
        signal_job = request.get('kind') == 'QLIB_SIGNAL_JOB'
        rolling = request.get('kind') == 'QLIB_ROLLING_JOB'
        validation = request.get('kind') == 'QLIB_MODEL_VALIDATION'
        if signal_job:
            from qtrade.research.data_epoch import current_epoch

            data = self.online.data_head(verify=False)
            if (request['data_version_id'] != data['version_id']
                    or request['source_revision'] != self.revision
                    or request.get('data_event_id') != current_epoch(
                        self.online.data_database, data['version_id'])):
                self.update(identity, 'REJECTED', error='SIGNAL_CONTEXT_CHANGED')
                return
            command = [self.python, '-m', 'scripts.run_qlib_signal_baselines',
                '--data-version-root', str(self.base/'unified-data/qlib-versions/versions'/
                                           data['version_id']), '--output', str(output),
                '--source-revision', self.revision, '--data-event-id', request['data_event_id']]
        elif validation:
            from scripts.prepare_qlib_incremental import load_signed

            data = self.online.data_head(verify=False)
            if (request['data_version_id'] != data['version_id']
                    or request['source_revision'] != self.revision):
                self.update(identity, 'REJECTED', error='CANDIDATE_VALIDATION_CONTEXT_CHANGED')
                return
            index = load_signed(self.online.root/'models'/(request['model_id']+'.json'))
            archive = self.online.local(index['archive'])
            command = [self.python, '-m', 'scripts.run_qlib_candidate_validation',
                '--archive', str(archive), '--data-root',
                str(self.base/'unified-data/qlib-versions/versions'/data['version_id']),
                '--output', str(output), '--model-id', request['model_id'],
                '--data-version-id', data['version_id']]
        elif rolling:
            from qtrade.monitoring.data_freshness import Calendar

            planned = request['plan']
            data = self.online.data_head(verify=False)
            if (request['policy_id'] != self.rolling.current()['policy_id']
                    or planned['source_revision'] != self.revision
                    or planned['data_version_id'] != data['version_id']
                    or data['actual_end'] != Calendar(REPO/'configs/observer_calendar_2026.json'
                        ).expected(datetime.now(UTC), 'daily').date().isoformat()):
                self.update(identity, 'REJECTED', error='ROLLING_CONTEXT_CHANGED')
                return
            write_json_atomic(job/'policy.json', planned['policy'])
            command = [self.python, '-m', 'scripts.run_qlib_rolling',
                '--data-version-root', str(self.base/'unified-data/qlib-versions/versions'/
                                            data['version_id']), '--output', str(output),
                '--tracking', '/root/qtrade-local/workflow-mlruns',
                '--source-revision', self.revision, '--policy', str(job/'policy.json')]
        else:
            write_json_atomic(job / "spec.json", request["spec"])
            source_id = request["spec"].get("source_snapshot_sha256") or self.freeze_source()
            source_root = self.root / "source-snapshots" / source_id
            if capture_source(source_root).identity != source_id:
                raise ValueError("REQUESTED_SOURCE_SNAPSHOT_UNAVAILABLE")
            command = [
                self.python, "-m", "scripts.run_qlib_baseline",
                "--data-root", str(self.base / "qlib-data-20261023"),
                "--output", str(output),
                "--tracking", "/root/qtrade-local/workflow-mlruns",
                "--source-revision", self.revision,
                "--workflow-spec", str(job / "spec.json"),
                "--pit-evidence-root", str(source_root),
            ]
        self.update(identity, "RUNNING")
        self.publish("RUNNING")
        with (job / "runner.log").open("ab") as log, contextlib.ExitStack() as cleanup:
            child = subprocess.Popen(
                command, cwd=REPO, stdout=log, stderr=subprocess.STDOUT,
                start_new_session=True,
                env={**{k: v for k, v in os.environ.items()
                        if not k.startswith("QTRADE_RESEARCH_")},
                     "PYTHONPATH": str(REPO / "src"),
                     "LIVE_TRADING_ENABLED": "false",
                     "OMP_NUM_THREADS": '2' if rolling or validation or signal_job else '6'},
            )
            cleanup.callback(terminate, child)
            write_json_atomic(job / "child.json", {
                "pid": child.pid,
                "start_ticks": (Path("/proc") / str(child.pid) / "stat").read_text().split()[21],
            })
            started = time.monotonic()
            last_state = "RUNNING"
            while child.poll() is None:
                self.predictor.tick(stop=self.stop)
                if self.stop or identity in self.cancellations or (
                    time.monotonic() - started > (900 if validation or signal_job else 3600)
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
        if signal_job:
            from qtrade.research.signal_publication import verify_signal_archive

            paths = list(output.glob('*/manifest.json'))
            if len(paths) != 1:
                raise ValueError('SIGNAL_ONE_SEALED_ARCHIVE_REQUIRED')
            manifest = verify_signal_archive(paths[0].parent)['manifest']
            if (manifest['bindings']['data_version_id'] != request['data_version_id']
                    or manifest['bindings']['code_revision'] != self.revision):
                raise ValueError('SIGNAL_COMPLETED_CONTEXT_CHANGED')
            self.update(identity, 'COMPLETE', result={'signal_family_id':
                manifest['signal_family_id'], 'data_version_id': request['data_version_id']})
            return
        if validation:
            proof = json.loads((output/'result.json').read_text())
            self.online.import_candidate(archive, output/'validations'/proof['validation_id'],
                                          now=datetime.now(UTC))
            self.update(identity, 'COMPLETE', result={'validation_id': proof['validation_id'],
                        'model_id': request['model_id'], 'data_version_id': data['version_id']})
            return
        status = json.loads((output / "status.json").read_text())
        archive = output / "runs" / status["run_id"]
        summary = verify_archive(archive)
        if rolling and summary.get('rolling_plan') != request['plan']:
            raise ValueError('ROLLING_COMPLETED_PLAN_CHANGED')
        if rolling:
            self.online.import_trained(archive, now=datetime.now(UTC))
        self.update(identity, "COMPLETE", result=self.archive_detail(summary, identity))

    def schedule_rolling(self) -> None:
        if (self.stop or self.predictor.child is not None
                or self.predictor.state not in {'COMPLETE', 'WAITING_FOR_APPROVAL'}
                or self.rolling.current()['policy']['mode'] == 'OFF'):
            return
        from qtrade.monitoring.data_freshness import Calendar

        now = datetime.now(UTC)
        data = self.online.data_head(verify=False)
        if (data['actual_end'] != Calendar(REPO/'configs/observer_calendar_2026.json').expected(
                now, 'daily').date().isoformat()
                or __import__('shutil').disk_usage(self.base).free < 100*1024**3):
            return
        root = self.base/'unified-data/qlib-versions/versions'/data['version_id']
        calendar = (root/'provider/calendars/day.txt').read_text().splitlines()
        self.rolling.schedule(calendar, data['actual_end'], data['version_id'],
                               self.revision, now=now)

    def schedule_candidate_validation(self) -> None:
        if (self.stop or self.predictor.child is not None
                or self.predictor.state not in {'COMPLETE', 'WAITING_FOR_APPROVAL'}):
            return
        if self.db.execute("SELECT count(*) FROM jobs WHERE state NOT IN "
            "('COMPLETE','FAILED','CANCELLED','INTERRUPTED','REJECTED')").fetchone()[0]:
            return
        from qtrade.monitoring.data_freshness import Calendar

        now = datetime.now(UTC)
        data = self.online.data_head(verify=False)
        if data['actual_end'] != Calendar(REPO/'configs/observer_calendar_2026.json').expected(
                now, 'daily').date().isoformat():
            return
        for model in self.online.registry.snapshot()['models']:
            definition = model['definition']
            if model['state'] != 'TRAINED' or data['actual_end'] <= definition['training_data_end']:
                continue
            bindings = {'kind': 'QLIB_MODEL_VALIDATION', 'model_id': definition['model_id'],
                        'data_version_id': data['version_id'], 'source_revision': self.revision}
            identity = signed(bindings)['evidence_sha256'][:32]
            request = signed({**AUTHORITY, **bindings, 'job_id': identity})
            with self.db:
                created = self.db.execute('INSERT OR IGNORE INTO jobs VALUES(?,?,?,?,NULL,NULL)',
                    (identity, json.dumps(request), 'QUEUED', now.isoformat()))
                if created.rowcount:
                    self.db.execute('INSERT INTO events VALUES(?,?,?)',
                                     (identity, now.isoformat(), 'QUEUED'))
                    return

    def once(self) -> None:
        self.ingest()
        self.predictor.tick(stop=self.stop)
        self.schedule_candidate_validation()
        self.schedule_signals()
        self.schedule_rolling()
        row = self.db.execute(
            "SELECT id,request FROM jobs WHERE state='QUEUED' ORDER BY rowid LIMIT 1"
        ).fetchone()
        if row and self.predictor.child is None:
            try:
                self.execute(row[0], json.loads(row[1]))
            except Exception:
                self.update(row[0], "FAILED", error="WORKER_JOB_VALIDATION_FAILED")
        self.publish('PREDICTING' if self.predictor.child is not None else 'IDLE')

    def schedule_signals(self) -> None:
        from qtrade.research.data_epoch import current_epoch

        if (self.stop or self.predictor.child is not None
                or self.predictor.state not in {'COMPLETE', 'WAITING_FOR_APPROVAL'}):
            return
        if self.db.execute("SELECT count(*) FROM jobs WHERE state NOT IN "
            "('COMPLETE','FAILED','CANCELLED','INTERRUPTED','REJECTED')").fetchone()[0]:
            return
        from qtrade.monitoring.data_freshness import Calendar

        now = datetime.now(UTC)
        data = self.online.data_head(verify=False)
        if data['actual_end'] != Calendar(REPO/'configs/observer_calendar_2026.json').expected(
                now, 'daily').date().isoformat():
            return
        bindings = {'kind': 'QLIB_SIGNAL_JOB', 'data_version_id': data['version_id'],
                    'data_event_id': current_epoch(self.online.data_database, data['version_id']),
                    'source_revision': self.revision}
        identity = signed(bindings)['evidence_sha256'][:32]
        with self.db:
            created = self.db.execute('INSERT OR IGNORE INTO jobs VALUES(?,?,?,?,NULL,NULL)',
                (identity, json.dumps(signed({**AUTHORITY, **bindings, 'job_id': identity})),
                 'QUEUED', now.isoformat()))
            if created.rowcount:
                self.db.execute('INSERT INTO events VALUES(?,?,?)',
                                 (identity, now.isoformat(), 'QUEUED'))

    def publish_signals(self) -> None:
        from qtrade.research.signal_publication import verify_signal_archive

        publication = signed({**AUTHORITY, 'state': 'NO_VERIFIED_SIGNAL_ARCHIVE'})
        try:
            for identity, raw, result in self.db.execute(
                    "SELECT id,request,result FROM jobs WHERE state='COMPLETE' "
                    "ORDER BY rowid DESC"):
                if json.loads(raw).get('kind') != 'QLIB_SIGNAL_JOB':
                    continue
                family_id = json.loads(result)['signal_family_id']
                publication = verify_signal_archive(self.root/'jobs'/identity/'output'/family_id)
                break
        except (OSError, ValueError, KeyError, TypeError):
            publication = signed({**AUTHORITY, 'state': 'SIGNAL_ARCHIVE_INVALID'})
        write_json_atomic(self.public/'signals.json', publication)


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
    worker.predictor.tick(stop=True)
    worker.publish('STOPPED')


if __name__ == "__main__":
    main()
