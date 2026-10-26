"""Actual rolling child failures cannot replace an explicitly approved research model."""

from __future__ import annotations

import json
import os
import shutil
import sqlite3
import sys
import time
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.model_registry import ModelRegistry
from qtrade.research.prediction_registry import active_approval
from qtrade.research.rolling_policy import default_policy
from scripts import run_qlib_worker as worker_module

NOW = datetime(2026, 10, 26, 15, tzinfo=UTC)
ACTIVE, FAILED_CANDIDATE, DATA, REVISION = 'a'*64, 'b'*64, 'd'*64, 'c'*40

# This child still runs the real rolling main/run_candidate/plan/config path. Only
# Provider verification and the expensive fit boundary are controlled fixtures.
FIT_FAILURE = '''
import json
import shutil
import time
from pathlib import Path
from types import SimpleNamespace
import qtrade.research.data_versions as versions
import scripts.run_qlib_baseline as baseline

versions.verify_version = lambda root: json.loads((root/'version.json').read_text())
shutil.disk_usage = lambda root: SimpleNamespace(free=200*1024**3)
def fail_fit(data, output, tracking, revision, **kwargs):
    output.mkdir(parents=True, exist_ok=True)
    (output/'status.json').write_text(json.dumps({'state': 'TRAINING_MODEL'}))
    (output/'fit-attempt.json').write_text(json.dumps({
        'rolling_plan': kwargs['rolling_plan'], 'restore_proof': kwargs['restore_proof']}))
    partial = output/'runs'/'failed-candidate'
    partial.mkdir(parents=True)
    (partial/'summary.json').write_text(json.dumps({'model_id': 'b'*64, 'state': 'TRAINED'}))
    time.sleep(.1)
    raise RuntimeError('TEST_ONLY_ROLLING_FIT_FAILURE')
baseline.run = fail_fit
import scripts.run_qlib_rolling as rolling
rolling.main()
'''


def signed_proof():
    contract = signed({**AUTHORITY, 'model_bundle_sha256': ACTIVE, 'data_version_id': DATA,
        'generated_at': (NOW-timedelta(minutes=1)).isoformat(),
        'expected_execution_at': (NOW+timedelta(hours=12)).isoformat()})
    return signed({**AUTHORITY, 'schema_version': 'qtrade.qlib-forward-validation.v1',
        'state': 'FORWARD_INPUT_VALIDATED_NOT_ACTIVATED', 'model_bundle_sha256': ACTIVE,
        'validation_id': '1'*64, 'contract': contract, 'fitted_processors_unchanged': True,
        'fit_methods_forbidden': True, 'model_activated': False, 'auto_predict_enabled': False})


def approved_model(registry):
    definition = signed({**AUTHORITY, 'schema_version': 'qtrade.online-model.v1',
        'model_id': ACTIVE, 'feature_count': 158, 'historical_pit_certified': False,
        'fitted_restore_verified': True})
    registry.register(definition, now=NOW)
    registry.record_validation(signed_proof(), now=NOW)
    command = {'request_id': '1'*32, 'operation': 'ACTIVATE', 'model_id': ACTIVE,
        'expected_active': None, 'data_version_id': DATA, 'validation_id': '1'*64,
        'actor': 'test-only-operator'}
    receipt = registry.apply(command, current_data_version=DATA, now=NOW)
    assert receipt['state'] == 'APPLIED'
    return command, receipt


def model_bytes(database):
    """Compare stored rows exactly; clocked snapshot envelopes are deliberately excluded."""
    tables = ('models', 'model_validations', 'active_model', 'model_commands', 'model_events',
              'prediction_tasks', 'prediction_events')
    with sqlite3.connect(database.resolve().as_uri()+'?mode=ro', uri=True) as connection:
        return {table: connection.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall()
                for table in tables}


@pytest.mark.parametrize('failure', ['native_provider_rejection', 'controlled_fit_exception'])
def test_failed_rolling_child_retains_approved_active_and_durable_failure(
    tmp_path, monkeypatch, failure,
):
    monkeypatch.setenv('LIVE_TRADING_ENABLED', 'false')
    monkeypatch.setenv('QTRADE_RESEARCH_QUEUE_SECRET', 'test-only-queue-signing-key'*2)
    queue = type('Queue', (), {'read': lambda self: {}})()
    monkeypatch.setattr(worker_module.GitQueue, 'from_environment', lambda: queue)
    class Clock(datetime):
        @classmethod
        def now(cls, tz=UTC):
            return NOW.astimezone(tz)
    monkeypatch.setattr(worker_module, 'datetime', Clock)
    monkeypatch.setattr(shutil, 'disk_usage', lambda root: type('Disk', (), {
        'free': 200*1024**3})())
    # Preserve real subprocesses and bound the worker's ten-second polling interval
    # without modifying either SQLite transaction or the child failure handler.
    original_popen, original_sleep = worker_module.subprocess.Popen, time.sleep
    monkeypatch.setattr(worker_module.time, 'sleep', lambda seconds: original_sleep(.01))
    def atomic(path, value):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(value))
    if os.name == 'nt':
        monkeypatch.setattr(worker_module, 'write_json_atomic', atomic)
        fake_proc = tmp_path/'proc'
        monkeypatch.setattr(worker_module, 'Path',
            lambda value: fake_proc if str(value) == '/proc' else Path(value))
    worker = worker_module.Worker(tmp_path, tmp_path/'workflow.sqlite', sys.executable, REVISION)
    publications, children, launches = [], [], []
    monkeypatch.setattr(worker, 'publish', publications.append)
    monkeypatch.setattr(worker.predictor, 'tick', lambda **kwargs: None)
    registry = worker.online.registry
    command, approval = approved_model(registry)
    model_database = registry.database
    before = model_bytes(model_database)
    with registry.connect() as connection:
        assert active_approval(connection) == approval
    # Real model/data/workflow files, isolated from every production source and ledger.
    provider = tmp_path/'unified-data/qlib-versions/versions'/DATA
    (provider/'provider/calendars').mkdir(parents=True)
    first, last = date(2017, 4, 5), NOW.date()
    calendar = [(first+timedelta(days=i)).isoformat()
                for i in range((last-first).days+1) if (first+timedelta(days=i)).weekday() < 5]
    (provider/'provider/calendars/day.txt').write_text('\n'.join(calendar)+'\n')
    # The native failure case must reject this unverified-inventory Provider fixture;
    # it is never falsely admitted as a real verified training data version.
    version = signed({**AUTHORITY, 'schema_version': 'qtrade.qlib-data-version.v1',
        'frequency': 'day', 'state': 'RESEARCH_READY_QUALIFIED_SUBSET', 'version_id': DATA,
        'actual_end': str(last), 'historical_pit_certified': False,
        'external_execution_ready': False, 'files': {}, 'bindings': {}})
    (provider/'version.json').write_text(json.dumps(version))
    with sqlite3.connect(worker.online.data_database) as data:
        data.executescript('CREATE TABLE data_versions(version_id TEXT,manifest_sha256 TEXT);'
                           'CREATE TABLE data_heads(frequency TEXT,version_id TEXT);')
        data.execute('INSERT INTO data_versions VALUES(?,?)', (DATA, version['evidence_sha256']))
        data.execute('INSERT INTO data_heads VALUES(?,?)', ('day', DATA))
    worker.rolling.apply({'request_id': '2'*32,
        'expected_policy_id': worker.rolling.current()['policy_id'],
        'policy': {**default_policy(), 'mode': 'DAILY', 'train_sessions': 252,
                   'valid_sessions': 60, 'test_sessions': 20},
        'actor': 'test-only-operator'}, now=NOW)
    identity = worker.rolling.schedule(calendar, str(last), DATA, REVISION, now=NOW)
    assert identity
    request = json.loads(worker.db.execute('SELECT request FROM jobs WHERE id=?',
                                          (identity,)).fetchone()[0])
    assert request['kind'] == 'QLIB_ROLLING_JOB'

    def launch(args, **kwargs):
        launches.append(args)
        assert args[:3] == [sys.executable, '-m', 'scripts.run_qlib_rolling']
        assert kwargs['env']['LIVE_TRADING_ENABLED'] == 'false'
        assert not any(key.startswith('QTRADE_RESEARCH_') for key in kwargs['env'])
        assert args[args.index('--data-version-root')+1] == str(provider)
        actual = ([sys.executable, '-c', FIT_FAILURE, *args[3:]]
                  if failure == 'controlled_fit_exception' else args)
        child = original_popen(actual, **kwargs)
        children.append(child)
        if os.name == 'nt':
            proc = fake_proc/str(child.pid)
            proc.mkdir(parents=True)
            (proc/'stat').write_text(' '.join(['0']*22))
        return child
    monkeypatch.setattr(worker_module.subprocess, 'Popen', launch)
    try:
        worker.once()
        assert len(launches) == 1 and children[0].returncode != 0
        assert publications[0] == 'RUNNING' and publications[-1] == 'IDLE'
        row = worker.db.execute('SELECT state,result,error FROM jobs WHERE id=?',
                                (identity,)).fetchone()
        assert row == ('FAILED', None, 'QLIB_RUN_FAILED_SEE_PRIVATE_CLUSTER_LOG')
        states = [row[0] for row in worker.db.execute('SELECT state FROM events WHERE id=? '
                                                     'ORDER BY rowid', (identity,))]
        assert states[0:2] == ['QUEUED', 'RUNNING'] and states[-1] == 'FAILED'
        assert 'COMPLETE' not in states
        task = worker.rolling.publication()['tasks'][0]
        assert task['state'] == 'FAILED' and task['result'] is None
        assert task['error_code'] == row[2]
        assert worker.rolling.publication()['automatic_activation'] is False
        job = worker.root/'jobs'/identity
        assert json.loads((job/'output/status.json').read_text())['state'] == 'FAILED'
        log = (job/'runner.log').read_text()
        if failure == 'controlled_fit_exception':
            assert 'TRAINING_MODEL' in states and 'TEST_ONLY_ROLLING_FIT_FAILURE' in log
            fit = json.loads((job/'output/fit-attempt.json').read_text())
            assert fit['rolling_plan'] == request['plan'] and fit['restore_proof'] is True
            assert (job/'output/runs/failed-candidate/summary.json').exists()
        else:
            assert ('DATA_VERSION_MANIFEST_CONTRACT_INVALID' in log
                    or 'DISK_RESERVE_BELOW_100_GIB' in log)
        assert model_bytes(model_database) == before
        assert registry.receipt(command) == approval
        assert registry.snapshot()['active_model'] == ACTIVE
        assert FAILED_CANDIDATE not in {row['definition']['model_id']
                                        for row in registry.snapshot()['models']}
        # The same mature day cannot silently retry a failed task.
        assert worker.rolling.schedule(calendar, str(last), DATA, REVISION, now=NOW) is None
    finally:
        for child in children:
            if child.poll() is None:
                child.kill()
            child.wait(timeout=10)
        worker.db.close()
    # Reopen the real worker and independent SQLite connections after completion.
    restarted = worker_module.Worker(tmp_path, tmp_path/'workflow.sqlite', sys.executable, REVISION)
    try:
        assert restarted.db.execute('SELECT state,result,error FROM jobs WHERE id=?',
                                    (identity,)).fetchone() == row
        assert restarted.rolling.current()['last_attempt_day'] == str(last)
        assert restarted.rolling.schedule(calendar, str(last), DATA, REVISION, now=NOW) is None
        assert model_bytes(model_database) == before
        with restarted.online.registry.connect() as connection:
            assert active_approval(connection) == approval
        assert ModelRegistry(model_database).receipt(command) == approval
    finally:
        restarted.db.close()
