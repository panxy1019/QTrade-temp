import json
import os
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from test_model_registry import DATA, NOW, command
from test_online_model_control import runtime  # noqa: F401
from test_prediction_registry import result

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts import online_prediction_worker as worker


@pytest.fixture
def predictor(runtime, monkeypatch):  # noqa: F811
    online, _, data = runtime
    data['bindings'] = {'observation_universe_version': 'f'*64}
    event = signed({**AUTHORITY, 'kind': 'QLIB_DATA_READY', 'version_id': DATA})
    with sqlite3.connect(online.data_database) as c:
        c.execute('CREATE TABLE data_events(sequence INTEGER,version_id TEXT,'
                  'event_id TEXT,payload TEXT)')
        c.execute('INSERT INTO data_events VALUES(1,?,?,?)',
                  (DATA, event['evidence_sha256'], json.dumps(event)))
    if os.name == 'nt':
        def write(path, value):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(json.dumps(value))
        monkeypatch.setattr(worker, 'write_json_atomic', write)
        monkeypatch.setattr(worker, 'write_json_immutable', write)

    class Clock(datetime):
        current = NOW

        @classmethod
        def now(cls, tz=UTC):
            return cls.current
    monkeypatch.setattr(worker, 'datetime', Clock)
    monkeypatch.setattr(worker.shutil, 'disk_usage',
                        lambda p: type('Disk', (), {'free': 200*1024**3}))
    launches = []

    class Child:
        pid = 999999999
        returncode = None

        def poll(self):
            return self.returncode
    def launch(*args, **kw):
        launches.append((args, kw))
        return Child()
    monkeypatch.setattr(worker.subprocess, 'Popen', launch)
    value = worker.OnlinePredictor(online, 'test-python', 'c'*40)
    return value, launches, Clock


def test_unapproved_worker_waits_without_launch_or_claim(predictor):
    value, launches, _ = predictor
    value.tick()
    assert value.state == 'WAITING_FOR_APPROVAL' and launches == []
    assert value.ledger.tasks() == []
    assert value.publication(now=NOW)['current_prediction_id'] is None


def test_approved_prediction_is_nonblocking_once_per_event_and_strips_queue_secrets(
    predictor, monkeypatch,
):
    value, launches, clock = predictor
    monkeypatch.setenv('QTRADE_RESEARCH_QUEUE_SECRET', 'test-only-not-forwarded')
    value.online.registry.apply(command(), current_data_version=DATA, now=NOW)
    value.tick()
    assert value.child is not None and len(launches) == 1
    assert value.state == 'RUNNING'
    assert 'QTRADE_RESEARCH_QUEUE_SECRET' not in launches[0][1]['env']
    assert launches[0][1]['env']['LIVE_TRADING_ENABLED'] == 'false'
    value.tick()
    assert len(launches) == 1
    request = value.ledger.tasks()[0]['request']
    monkeypatch.setattr(worker, 'verify_prediction', lambda p: result(request))
    value.child.returncode = 0
    clock.current += timedelta(minutes=2)
    value.tick()
    assert value.child is None and value.state == 'COMPLETE'
    view = value.publication(now=clock.current)
    assert view['current_prediction_id'] == request['prediction_id']
    assert view['predictions'][0]['score_count'] == 888
    value.tick()
    assert len(launches) == 1
    assert value.publication(now=NOW+timedelta(days=1))['current_prediction_id'] is None


def test_failed_subprocess_preserves_old_model_and_never_invents_prediction(predictor):
    value, launches, _ = predictor
    value.online.registry.apply(command(), current_data_version=DATA, now=NOW)
    value.tick()
    value.child.returncode = 1
    value.tick()
    assert value.state == 'FAILED'
    assert value.online.registry.snapshot()['active_model'] == 'a'*64
    assert value.publication(now=NOW)['current_prediction_id'] is None
    value.tick()
    assert len(launches) == 1


def test_launch_error_is_durable_instead_of_leaving_a_running_claim(predictor, monkeypatch):
    value, _, _ = predictor
    value.online.registry.apply(command(), current_data_version=DATA, now=NOW)
    def fail(*a, **kw):
        raise OSError('test-only-start-error')
    monkeypatch.setattr(worker.subprocess, 'Popen', fail)
    value.tick()
    assert value.child is None
    assert value.ledger.tasks()[0]['state'] == 'FAILED'
    assert value.ledger.tasks()[0]['error_code'] == 'PREDICTION_LAUNCH_FAILED'


def test_next_session_open_blocks_late_prediction_even_before_new_daily_close(predictor):
    value, launches, clock = predictor
    value.online.registry.apply(command(), current_data_version=DATA, now=NOW)
    clock.current = datetime(2026, 10, 27, 2, tzinfo=UTC)
    value.tick()
    assert value.state == 'BLOCKED_EXECUTION_WINDOW_PASSED'
    assert not launches and not value.ledger.tasks()


def test_restart_reconciles_sealed_result_without_launching_it_again(predictor, monkeypatch):
    value, launches, clock = predictor
    value.online.registry.apply(command(), current_data_version=DATA, now=NOW)
    value.tick()
    request = value.ledger.tasks()[0]['request']
    output = value.root/request['prediction_id']/'output'/request['prediction_id']
    output.mkdir(parents=True)
    (output/'manifest.json').write_text('{}')
    monkeypatch.setattr(worker, 'verify_prediction', lambda p: result(request))
    clock.current += timedelta(minutes=2)
    restored = worker.OnlinePredictor(value.online, 'test-python', 'c'*40)
    assert restored.ledger.state(request['prediction_id']) == 'COMPLETE'
    restored.tick()
    assert len(launches) == 1
    view = restored.publication(now=clock.current)
    assert view['current_prediction_id'] == request['prediction_id']
