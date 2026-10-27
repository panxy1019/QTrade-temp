import json
import os
import signal
import subprocess
import sys
import time
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts import run_qlib_data_service as service

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    current = {'version_id': 'a'*64, 'actual_end': '2026-10-23'}
    monkeypatch.setattr(service, 'head', lambda *a: (tmp_path/'old', current))
    monkeypatch.setattr(service, 'current_epoch', lambda *a: 'e'*64)
    monkeypatch.setattr(service, 'collection_idle', lambda *a: True)
    monkeypatch.setattr(service.shutil, 'disk_usage', lambda p: type('Disk', (), {
        'free': 200*1024**3}))
    if os.name == 'nt':
        monkeypatch.setattr(service, 'exclusive', lambda *a: nullcontext())
        def write(path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value))
        monkeypatch.setattr(service, 'write_json_atomic', write)
        monkeypatch.setattr(service, 'write_json_immutable', write)
    path = tmp_path/'unified-data/collection-progress.json'
    path.parent.mkdir()
    path.write_text(json.dumps(signed({**AUTHORITY, 'required_through': {
        'day': '2026-10-26T15:00:00+08:00'}})))
    prepared = {'request_sha256': 'b'*64, 'prepared_symbols': 987,
                'qualified_current_daily_inputs': 987, 'retry_queue': {'unattempted_symbols': 0}}
    calls = []
    def prepare(*a, **kw):
        calls.append('prepare')
        return prepared
    def build(*a, **kw):
        calls.append('build')
        current.update(version_id='c'*64, actual_end='2026-10-26')
        return {**current, 'state': 'RESEARCH_READY_QUALIFIED_SUBSET'}
    monkeypatch.setattr(service, 'prepare', prepare)
    monkeypatch.setattr(service, 'build', build)
    monkeypatch.setattr(service, 'ready_identity', lambda p: 'd'*64)
    def run(now=NOW, revision='f'*40):
        return service.cycle(tmp_path, tmp_path/'db.sqlite', tmp_path/'python', revision, now=now)
    return run, current, prepared, calls, tmp_path


def test_one_day_publishes_once_and_adopts_durable_head_after_restart(runtime):
    run, current, _, calls, root = runtime
    assert run()['state'] == 'DAILY_VERSION_PUBLISHED'
    assert calls == ['prepare', 'build']
    assert run(now=NOW+timedelta(minutes=10))['state'] == 'CURRENT_DAY_ALREADY_READY'
    assert calls == ['prepare', 'build']
    assert (root/'unified-data/qlib-schedule/days/2026-10-26/completed.json').exists()


@pytest.mark.parametrize('gate', ['current', 'collection', 'storage', 'gap', 'expired', 'pending'])
def test_incomplete_or_unsafe_inputs_never_publish(runtime, monkeypatch, gate):
    run, current, prepared, calls, _ = runtime
    now = NOW
    if gate == 'current':
        current['actual_end'] = '2026-10-26'
    elif gate == 'collection':
        monkeypatch.setattr(service, 'collection_idle', lambda *a: False)
    elif gate == 'storage':
        monkeypatch.setattr(service.shutil, 'disk_usage', lambda p: type('D', (), {'free': 1}))
    elif gate == 'gap':
        current['actual_end'] = '2026-10-22'
    elif gate == 'expired':
        now = datetime(2026, 10, 27, 2, tzinfo=UTC)
    else:
        prepared['retry_queue']['unattempted_symbols'] = 20
    assert run(now=now)['provider_published'] is False
    assert 'build' not in calls


def test_crash_deadline_and_code_identity_prevent_uncontrolled_retries(runtime, monkeypatch):
    run, _, _, calls, _ = runtime
    def fail(*a, **kw):
        calls.append('prepare')
        raise OSError('test-only-interruption')
    monkeypatch.setattr(service, 'prepare', fail)
    with pytest.raises(OSError):
        run()
    assert run()['state'] == 'WAITING_FOR_RETRY'
    assert calls == ['prepare']
    assert run(revision='e'*40)['state'] == 'BLOCKED_DAILY_INPUT_CHANGED_REVIEW_REQUIRED'


def test_rollback_during_preparation_cannot_be_undone_by_stale_builder(runtime, monkeypatch):
    run, _, _, calls, _ = runtime
    epochs = iter(['e'*64, 'f'*64])
    monkeypatch.setattr(service, 'current_epoch', lambda *a: next(epochs))
    assert run()['state'] == 'BLOCKED_DAILY_HEAD_CHANGED_REVIEW_REQUIRED'
    assert calls == ['prepare']


def test_build_failure_backoff_ignores_retry_metadata_changes(runtime, monkeypatch):
    run, _, _, calls, _ = runtime
    def fail(*a, **kw):
        calls.append('failed-build')
        raise OSError('test-only-reload-failure')
    monkeypatch.setattr(service, 'build', fail)
    with pytest.raises(OSError):
        run()
    assert run(now=NOW+timedelta(minutes=6))['state'] == 'WAITING_FOR_BUILD_RETRY'
    assert calls.count('failed-build') == 1
    with pytest.raises(OSError):
        run(now=NOW+timedelta(minutes=31))
    assert calls.count('failed-build') == 2


def test_collection_marker_requires_fresh_nonfuture_idle_heartbeat(tmp_path):
    marker = tmp_path/'unified-data/service-state.json'
    marker.parent.mkdir()
    for state, clock, expected in [('WAITING', NOW, True), ('COLLECTING', NOW, False),
        ('WAITING', NOW-timedelta(minutes=6), False),
        ('WAITING', NOW+timedelta(seconds=1), False)]:
        marker.write_text(json.dumps({'state': state, 'heartbeat_at': clock.isoformat()}))
        assert service.collection_idle(tmp_path, NOW) is expected


@pytest.mark.parametrize('next_state', [
    'PREPARING_DAILY_INPUTS', 'WAITING_FOR_CURRENT_COLLECTION', 'WAITING_FOR_RETRY'])
def test_child_absorbs_actual_clock_boundary_jitter_once(tmp_path, monkeypatch, next_state):
    calls = []
    prepared = []
    waits = []
    real_sleep = time.sleep
    attempt = tmp_path/'attempt.json'
    attempt_bytes = None
    deadline = None

    def gated_cycle(*args, now, **kwargs):
        nonlocal deadline, attempt_bytes
        calls.append((now, kwargs))
        if len(calls) == 1:
            deadline = datetime.now(UTC)+timedelta(milliseconds=16)
            attempt.write_text(json.dumps(signed({'not_before': deadline.isoformat()})))
            attempt_bytes = attempt.read_bytes()
            return signed({'state': 'WAITING_FOR_RETRY', 'not_before': deadline.isoformat()})
        assert len(calls) == 2  # A second wait result must never spin or repeat work.
        assert now >= deadline
        assert attempt.read_bytes() == attempt_bytes
        if next_state == 'PREPARING_DAILY_INPUTS':
            prepared.append(now)
        return {'state': next_state}

    monkeypatch.setattr(service, 'cycle', gated_cycle)

    def wait(seconds):
        waits.append(seconds)
        real_sleep(seconds)

    monkeypatch.setattr(service.time, 'sleep', wait)
    ledger = tmp_path/'collection.sqlite'
    result = service.cycle_when_due(tmp_path, tmp_path/'db', tmp_path/'python', 'f'*40,
                                   collection_database=ledger)
    assert result['state'] == next_state
    assert len(calls) == 2
    assert calls[0][0] < deadline <= calls[1][0]
    assert all(kwargs == {'collection_database': ledger} for _, kwargs in calls)
    assert len(prepared) == (1 if next_state == 'PREPARING_DAILY_INPUTS' else 0)
    assert all(0 < seconds <= 0.016 for seconds in waits)


@pytest.mark.parametrize('defect', [
    'missing', 'malformed', 'naive', 'overlong', 'expired', 'unsigned'])
def test_child_rejects_invalid_retry_deadline_without_sleep_or_second_cycle(
    tmp_path, monkeypatch, defect
):
    calls = []

    def invalid_cycle(*args, now, **kwargs):
        calls.append(now)
        deadline = now+timedelta(seconds=301 if defect == 'overlong' else 1)
        if defect == 'expired':
            deadline = now
        if defect == 'naive':
            deadline = deadline.replace(tzinfo=None)
        value = {'state': 'WAITING_FOR_RETRY', 'not_before': deadline.isoformat()}
        if defect == 'missing':
            del value['not_before']
        if defect == 'malformed':
            value['not_before'] = 'invalid'
        value = signed(value)
        if defect == 'unsigned':
            value['not_before'] = (now+timedelta(seconds=2)).isoformat()
        return value

    monkeypatch.setattr(service, 'cycle', invalid_cycle)
    monkeypatch.setattr(service.time, 'sleep', lambda _: pytest.fail('invalid deadline slept'))
    with pytest.raises(ValueError, match='DATA_SERVICE_RETRY_DEADLINE_INVALID'):
        service.cycle_when_due(tmp_path, tmp_path/'db', tmp_path/'python', 'f'*40)
    assert len(calls) == 1


@pytest.mark.parametrize('state', ['PREPARING_DAILY_INPUTS', 'WAITING_FOR_CURRENT_COLLECTION',
                                  'WAITING_FOR_BUILD_RETRY'])
def test_child_does_not_repeat_or_wait_after_other_cycle_outcomes(tmp_path, monkeypatch, state):
    calls = []
    expected = {'state': state}

    def cycle(*args, **kwargs):
        calls.append(kwargs)
        return expected

    monkeypatch.setattr(service, 'cycle', cycle)
    monkeypatch.setattr(service.time, 'sleep', lambda _: pytest.fail('unexpected wait'))
    assert service.cycle_when_due(tmp_path, tmp_path/'db', tmp_path/'python', 'f'*40) is expected
    assert len(calls) == 1


def test_child_does_not_wait_again_after_deadline_passes_during_first_result(tmp_path, monkeypatch):
    calls = []
    deadline = None

    def gated_cycle(*args, now, **kwargs):
        nonlocal deadline
        calls.append(now)
        if len(calls) == 1:
            deadline = datetime.now(UTC)+timedelta(milliseconds=16)
            service.time.sleep(0.03)
            return signed({'state': 'WAITING_FOR_RETRY', 'not_before': deadline.isoformat()})
        assert now >= deadline
        return {'state': 'PREPARING_DAILY_INPUTS'}

    monkeypatch.setattr(service, 'cycle', gated_cycle)
    result = service.cycle_when_due(tmp_path, tmp_path/'db', tmp_path/'python', 'f'*40)
    assert result['state'] == 'PREPARING_DAILY_INPUTS' and len(calls) == 2


def test_cycle_cli_retains_single_900_second_alarm_around_wait_and_recheck(tmp_path, monkeypatch):
    alarms = []
    calls = []
    monkeypatch.setenv('LIVE_TRADING_ENABLED', 'false')
    monkeypatch.setenv('QTRADE_AKSHARE_NONCOMMERCIAL_ACK', 'true')
    monkeypatch.setattr(service, 'signal', SimpleNamespace(
        SIGALRM=14, SIG_DFL=0, signal=lambda *args: None, alarm=alarms.append))

    def run(*args, **kwargs):
        assert alarms == [900]
        calls.append((args, kwargs))
        return {'state': 'PREPARING_DAILY_INPUTS'}

    monkeypatch.setattr(service, 'cycle_when_due', run)
    monkeypatch.setattr(service.sys, 'argv', ['data-service', '--cycle', '--once',
        '--base', str(tmp_path), '--database', str(tmp_path/'db'),
        '--qlib-python', str(tmp_path/'python'), '--source-revision', 'f'*40,
        '--collection-database', str(tmp_path/'collection')])
    service.main()
    assert alarms == [900] and len(calls) == 1
    assert calls[0] == ((tmp_path, tmp_path/'db', tmp_path/'python', 'f'*40),
                        {'collection_database': tmp_path/'collection'})


def test_terminating_waiting_child_cannot_start_preparation(tmp_path):
    worker = """
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from scripts import run_qlib_data_service as service
base = Path(sys.argv[1])
def cycle(*args, now, **kwargs):
    if (base/'waiting').exists():
        (base/'prepared').write_text('unexpected preparation')
        return {'state': 'PREPARING_DAILY_INPUTS'}
    (base/'waiting').write_text('waiting')
    return service.signed({'state': 'WAITING_FOR_RETRY',
        'not_before': (now+timedelta(seconds=300)).isoformat()})
service.cycle = cycle
service.cycle_when_due(base, base/'db', base/'python', 'f'*40)
"""
    child = subprocess.Popen([sys.executable, '-c', worker, str(tmp_path)],
        cwd=service.REPO, env={**os.environ, 'PYTHONPATH': str(service.REPO/'src')},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic()+10
        while not (tmp_path/'waiting').exists() and child.poll() is None:
            if time.monotonic() >= deadline:
                pytest.fail('child did not reach retry wait')
            time.sleep(0.01)
        assert (tmp_path/'waiting').exists()
        child.terminate()
        child.communicate(timeout=5)
        if os.name != 'nt':
            assert child.returncode == -signal.SIGTERM
        assert child.returncode != 0 and not (tmp_path/'prepared').exists()
    finally:
        if child.poll() is None:
            child.kill()
        child.communicate(timeout=5)
