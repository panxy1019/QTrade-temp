"""Nonblocking daily inference inside the existing exclusive research worker."""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import signal
import sqlite3
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import Calendar
from qtrade.research.forward_prediction import next_execution
from qtrade.research.model_registry import checked_document
from qtrade.research.prediction_registry import PredictionRegistry
from scripts.atomic_json import write_json_atomic, write_json_immutable
from scripts.online_model_control import OnlineModels
from scripts.prepare_qlib_incremental import load_signed
from scripts.run_qlib_prediction import verify_prediction

REPO = Path(__file__).resolve().parents[1]


class OnlinePredictor:
    def __init__(self, online: OnlineModels, python: str, revision: str):
        self.online, self.python, self.revision = online, python, revision
        self.ledger = PredictionRegistry(online.registry)
        self.root = online.root/'predictions'
        self.root.mkdir(parents=True, exist_ok=True)
        self.child: subprocess.Popen[Any] | None = None
        self.running: str | None = None
        self.started = 0.0
        self.state = 'WAITING_FOR_APPROVAL'
        self.calendar = Calendar(REPO/'configs/observer_calendar_2026.json')
        self.recover()

    def terminate(self) -> None:
        if self.child is None or self.child.poll() is not None:
            return
        with contextlib.suppress(ProcessLookupError):
            os.killpg(self.child.pid, signal.SIGTERM)
        try:
            self.child.wait(10)
        except subprocess.TimeoutExpired:
            with contextlib.suppress(ProcessLookupError):
                os.killpg(self.child.pid, signal.SIGKILL)
            self.child.wait()

    def recover(self) -> None:
        for task in self.ledger.tasks(limit=200):
            if task['state'] not in {'RUNNING', 'INTERRUPTED'}:
                continue
            identity = task['prediction_id']
            folder = self.root/identity
            child_file = folder/'child.json'
            candidates = []
            if child_file.exists():
                child = json.loads(child_file.read_text())
                proc = Path('/proc')/str(child['pid'])
                if proc.exists() and (proc/'stat').read_text().split()[2] != 'Z':
                    arguments = (proc/'cmdline').read_bytes().split(b'\0')
                    if ((proc/'stat').read_text().split()[21] != child['start_ticks']
                            or b'scripts.run_qlib_prediction' not in arguments
                            or str(folder/'request.json').encode() not in arguments):
                        raise RuntimeError('UNCERTAIN_PREDICTION_CHILD_REQUIRES_RECONCILIATION')
                    candidates.append(proc)
            elif Path('/proc').exists():
                # A crash can occur after spawn but before child.json is durable.
                # Match the exact immutable request, never a broad process name.
                for proc in Path('/proc').iterdir():
                    if not proc.name.isdigit():
                        continue
                    with contextlib.suppress(FileNotFoundError, ProcessLookupError):
                        arguments = (proc/'cmdline').read_bytes().split(b'\0')
                        if (b'scripts.run_qlib_prediction' in arguments
                                and str(folder/'request.json').encode() in arguments):
                            candidates.append(proc)
            for proc in candidates:
                pid = int(proc.name)
                with contextlib.suppress(ProcessLookupError):
                    if os.getpgid(pid) != pid:
                        raise RuntimeError('PREDICTION_CHILD_GROUP_REQUIRES_RECONCILIATION')
                    os.killpg(pid, signal.SIGKILL)
                deadline = time.monotonic()+5
                while proc.exists():
                    with contextlib.suppress(FileNotFoundError):
                        if (proc/'stat').read_text().split()[2] == 'Z':
                            break
                    if time.monotonic() >= deadline:
                        raise RuntimeError('PREDICTION_CHILD_STOP_NOT_CONFIRMED')
                    time.sleep(0.05)
            self.ledger.fail(identity, 'WORKER_RESTART_RECONCILIATION_REQUIRED',
                             now=datetime.now(UTC), interrupted=True)
            if (folder/'output'/identity/'manifest.json').exists():
                self.finish(identity)

    def context(self, now: datetime) -> tuple[dict[str, Any] | None, str]:
        approval = self.ledger.active()
        if approval is None:
            return None, 'WAITING_FOR_APPROVAL'
        version = self.online.data_head(verify=False)
        if self.calendar.expected(now, 'daily').date().isoformat() != version['actual_end']:
            return None, 'BLOCKED_STALE_DATA'
        if next_execution(version['actual_end'], self.calendar) <= now:
            return None, 'BLOCKED_EXECUTION_WINDOW_PASSED'
        with contextlib.closing(sqlite3.connect(
            self.online.data_database.resolve().as_uri()+'?mode=ro', uri=True,
        )) as c:
            row = c.execute('SELECT event_id,payload FROM data_events WHERE version_id=? '
                            'ORDER BY sequence DESC LIMIT 1', (version['version_id'],)).fetchone()
        if row is None:
            return None, 'BLOCKED_DATA_READY_EVENT_MISSING'
        event = json.loads(row[1])
        checked_document(event)
        if event['evidence_sha256'] != row[0] or event['kind'] not in {
                'QLIB_DATA_READY', 'QLIB_DATA_ROLLBACK'}:
            raise ValueError('PREDICTION_DATA_EVENT_CHANGED')
        return {'model_id': approval['model_id'], 'approval_sha256': approval['evidence_sha256'],
                'data_version_id': version['version_id'], 'data_event_id': row[0],
                'data_end': version['actual_end'], 'code_revision': self.revision,
                'observation_universe_version': version['bindings'][
                    'observation_universe_version']}, 'READY'

    def finish(self, identity: str) -> None:
        try:
            proof = verify_prediction(self.root/identity/'output'/identity)
            version = self.online.data_head(verify=True)
            with contextlib.closing(sqlite3.connect(self.online.data_database, timeout=10)) as c:
                c.execute('BEGIN IMMEDIATE')
                head = c.execute("SELECT version_id FROM data_heads "
                                 "WHERE frequency='day'").fetchone()
                if head is None:
                    raise ValueError('PREDICTION_DATA_HEAD_MISSING')
                if head[0] != version['version_id']:
                    raise ValueError('PREDICTION_DATA_HEAD_CHANGED_DURING_VERIFICATION')
                self.state = self.ledger.finish(proof, current_data_version=head[0],
                                                now=datetime.now(UTC))
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error):
            self.ledger.fail(identity, 'PREDICTION_FINAL_VERIFICATION_FAILED',
                             now=datetime.now(UTC))
            self.state = 'FAILED'

    def tick(self, *, stop: bool = False) -> None:
        now = datetime.now(UTC)
        if self.child is not None:
            if stop or time.monotonic()-self.started > 600:
                self.terminate()
                if self.running:
                    self.ledger.fail(self.running, 'PREDICTION_STOPPED_OR_TIMED_OUT',
                                     now=now, interrupted=True)
                self.child = None
                self.running = None
                self.state = 'INTERRUPTED'
                return
            if self.child.poll() is None:
                self.state = 'RUNNING'
                return
            if self.running:
                if self.child.returncode == 0:
                    self.finish(self.running)
                else:
                    self.ledger.fail(self.running, 'PREDICTION_SUBPROCESS_FAILED', now=now)
                    self.state = 'FAILED'
            self.child, self.running = None, None
            return
        if stop:
            return
        try:
            bindings, self.state = self.context(now)
            if bindings is None:
                return
            if shutil.disk_usage(self.online.base).free < 100*1024**3:
                self.state = 'BLOCKED_STORAGE_RESERVE'
                return
            request = self.ledger.claim(bindings, now=now)
            identity = request['prediction_id']
            state = self.ledger.state(identity)
            if state != 'QUEUED':
                self.state = state
                return
            folder = self.root/identity
            request_file = folder/'request.json'
            if request_file.exists():
                if load_signed(request_file) != request:
                    raise ValueError('PREDICTION_LOCAL_REQUEST_CHANGED')
            else:
                write_json_immutable(request_file, request)
            index = load_signed(self.online.root/'models'/(bindings['model_id']+'.json'))
            archive = self.online.local(index['archive'])
            data_root = self.online.local('unified-data/qlib-versions/versions/'
                                          +bindings['data_version_id'])
            if not self.ledger.start(identity, now=now):
                return
            self.running = identity
            args = [self.python, '-m', 'scripts.run_qlib_prediction',
                    '--request', str(request_file),
                    '--archive', str(archive), '--data-root', str(data_root),
                    '--output', str(folder/'output'), '--source-revision', self.revision]
            with (folder/'runner.log').open('ab') as log:
                self.child = subprocess.Popen(args, cwd=REPO, stdout=log, stderr=subprocess.STDOUT,
                    start_new_session=True, env={**{k: v for k, v in os.environ.items()
                        if not k.startswith('QTRADE_RESEARCH_')},
                        'PYTHONPATH': str(REPO/'src'), 'LIVE_TRADING_ENABLED': 'false',
                        'OMP_NUM_THREADS': '2'})
            self.running, self.started = identity, time.monotonic()
            process = Path('/proc')/str(self.child.pid)/'stat'
            write_json_atomic(folder/'child.json', {'pid': self.child.pid,
                'start_ticks': process.read_text().split()[21] if process.exists() else None})
            self.state = 'RUNNING'
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error):
            # No valid input becomes a signal; keep failed attempts and safe diagnostics.
            if self.running is not None:
                self.terminate()
                self.ledger.fail(self.running, 'PREDICTION_LAUNCH_FAILED', now=now)
                self.child, self.running = None, None
            self.state = 'BLOCKED_INPUT_OR_LAUNCH_FAILURE'

    def publication(self, *, now: datetime) -> dict[str, Any]:
        try:
            bindings, context_state = self.context(now)
        except (ValueError, KeyError, TypeError, OSError, sqlite3.Error):
            bindings, context_state = None, 'BLOCKED_INPUT_UNAVAILABLE'
        current = None
        rows = []
        for task in self.ledger.tasks(limit=50):
            proof = task['result']
            row = {k: task[k] for k in ('prediction_id', 'state', 'updated_at', 'error_code')}
            row['bindings'] = task['request']['bindings']
            row['claimed_at'] = task['request']['claimed_at']
            if proof is not None:
                row.update({k: proof[k] for k in ('contract', 'score_count', 'feature_count',
                                                'limitations', 'evidence_sha256')})
                deadline = datetime.fromisoformat(proof['contract']['expected_execution_at'])
                if task['state'] == 'COMPLETE' and row['bindings'] == bindings and now < deadline:
                    current = task['prediction_id']
            rows.append(row)
        return {'schema_version': 'qtrade.auto-prediction.v1',
                'enabled': True, 'state': self.state if bindings else context_state,
                'current_prediction_id': current, 'predictions': rows,
                'forward_return_observed': False}
