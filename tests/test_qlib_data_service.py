import json
import os
from contextlib import nullcontext
from datetime import UTC, datetime, timedelta

import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts import run_qlib_data_service as service

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    current = {'version_id': 'a'*64, 'actual_end': '2026-10-23'}
    monkeypatch.setattr(service, 'head', lambda *a: (tmp_path/'old', current))
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
