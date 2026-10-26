import json
import sqlite3
from datetime import UTC, date, datetime, timedelta

import pytest

from qtrade.research.rolling_control import RollingControl
from qtrade.research.rolling_policy import default_policy

NOW = datetime(2026, 10, 26, 15, tzinfo=UTC)


@pytest.fixture
def control(tmp_path):
    db = sqlite3.connect(tmp_path/'jobs.sqlite')
    db.executescript('''CREATE TABLE jobs(id TEXT PRIMARY KEY,request TEXT,state TEXT,
        updated TEXT,result TEXT,error TEXT); CREATE TABLE events(id TEXT,at TEXT,state TEXT);''')
    yield RollingControl(db)
    db.close()


def cmd(control, mode='DAILY', request='a'):
    return {'request_id': request*32, 'expected_policy_id': control.current()['policy_id'],
            'policy': {**default_policy(), 'mode': mode}, 'actor': 'test-user'}


def schedule(control, offset=0):
    days = [(date(2017, 4, 5)+timedelta(days=i*2)).isoformat() for i in range(1600)]
    return control.schedule(days, days[-4+offset], 'd'*64, 'c'*40, now=NOW)


def test_off_is_default_and_stale_policy_or_changed_retry_is_rejected(control):
    assert schedule(control) is None
    first = cmd(control)
    receipt = control.apply(first, now=NOW)
    assert receipt['state'] == 'APPLIED'
    assert control.apply(first, now=NOW) == receipt
    with pytest.raises(ValueError, match='IMMUTABLE'):
        control.apply({**first, 'policy': default_policy()}, now=NOW)
    assert control.apply({**first, 'request_id': 'b'*32}, now=NOW)['state'] == 'REJECTED'
    assert control.current()['policy']['mode'] == 'DAILY'


def test_claim_and_cadence_are_durable_and_failed_work_is_not_repeated(control):
    control.apply(cmd(control), now=NOW)
    identity = schedule(control)
    assert identity
    assert schedule(control) is None
    row = control.db.execute('SELECT request FROM jobs WHERE id=?', (identity,)).fetchone()
    request = json.loads(row[0])
    assert request['live_trading_enabled'] is False
    assert request['plan']['segments']['test'][1] < request['plan']['data_end']
    control.db.execute("UPDATE jobs SET state='FAILED'")
    control.db.commit()
    reopened = RollingControl(control.db)
    assert schedule(reopened) is None
    assert schedule(reopened, 1) != identity
    assert reopened.publication()['automatic_activation'] is False


def test_policy_replacement_cancels_old_queued_work_but_keeps_attempt_audit(control):
    control.apply(cmd(control), now=NOW)
    identity = schedule(control)
    control.apply(cmd(control, 'OFF', 'b'), now=NOW)
    assert schedule(control, 1) is None
    row = control.db.execute('SELECT state,error FROM jobs WHERE id=?', (identity,)).fetchone()
    assert row == ('CANCELLED', 'ROLLING_POLICY_REPLACED')
    assert control.publication()['tasks'][0]['state'] == 'CANCELLED'


def test_pending_manual_research_has_priority_without_consuming_rolling_day(control):
    control.apply(cmd(control), now=NOW)
    control.db.execute('INSERT INTO jobs VALUES(?,?,?,?,NULL,NULL)',
                        ('manual', '{}', 'QUEUED', NOW.isoformat()))
    control.db.commit()
    assert schedule(control) is None
    assert control.current()['last_attempt_day'] is None
    control.db.execute("UPDATE jobs SET state='COMPLETE' WHERE id='manual'")
    control.db.commit()
    assert schedule(control)


def test_job_insert_failure_does_not_advance_last_attempt(control):
    control.apply(cmd(control), now=NOW)
    control.db.execute("CREATE TRIGGER reject_job BEFORE INSERT ON jobs BEGIN "
                       "SELECT RAISE(ABORT,'simulated full database'); END")
    control.db.commit()
    with pytest.raises(sqlite3.IntegrityError):
        schedule(control)
    assert control.current()['last_attempt_day'] is None
    assert control.db.execute('SELECT count(*) FROM events').fetchone()[0] == 0
