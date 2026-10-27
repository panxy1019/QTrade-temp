import json
import os
from contextlib import contextmanager, nullcontext
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from test_unified_queue import master, receipt
from test_unified_store import store as platform_store

from qtrade.market_data.sina_minute import SINA_FIVE_MINUTE_SESSION_TIMES
from qtrade.monitoring.data_freshness import Calendar, signed
from scripts import repair_incremental_gaps as module
from scripts.collect_unified_data import collect_batch
from scripts.unified_queue import UnifiedCollectionQueue

store = platform_store
NOW = datetime(2026, 10, 27, 3, 45, tzinfo=UTC)
OLD = datetime(2026, 10, 26, 2, tzinfo=UTC)
REVISION = 'b'*40
CALENDAR = Calendar(module.REPO/'configs/observer_calendar_2026.json')


def source(*, missing=False, calls=None):
    def fetch(source_id, params):
        assert source_id == 'sina_klines'
        assert params['scale'] == '5' and params['ma'] == 'no'
        assert params['symbol'].startswith(('sh', 'sz'))
        if calls is not None:
            calls.append(params)
        rows = [{'day': day+' '+str(t), 'open': '10', 'high': '11', 'low': '9',
                 'close': '10', 'volume': '100'}
                for day in ('2026-10-26', '2026-10-27')
                for t in SINA_FIVE_MINUTE_SESSION_TIMES
                if not (missing and day == '2026-10-26' and str(t) == '10:05:00')]
        return {'source_id': source_id, 'request': params, 'received_at': NOW.isoformat(),
                'status_code': 200, 'transport_error': None, 'body_complete': True,
                'body': json.dumps(rows[-int(params['datalen']):]).encode()}
    return fetch


@pytest.fixture
def setup(store, tmp_path, monkeypatch):
    base, database = tmp_path/'base', tmp_path/'queue.sqlite'
    path = base/'unified-data/public/unified.json'
    path.parent.mkdir(parents=True)
    path.write_text(json.dumps({'master': master(2)}))
    monkeypatch.setattr(module, 'validate_publication', lambda value: value)
    monkeypatch.setattr(module, 'UnifiedBatchStore', lambda _: store)
    monkeypatch.setattr(module.shutil, 'disk_usage', lambda _: SimpleNamespace(free=200*1024**3))
    monkeypatch.setenv('QTRADE_AKSHARE_NONCOMMERCIAL_ACK', 'true')
    monkeypatch.setenv('LIVE_TRADING_ENABLED', 'false')
    queue = UnifiedCollectionQueue(database)
    queue.plan(master(2), CALENDAR, OLD)
    batch = queue.claim(master(2)['universe_version'], '5min', OLD.date(),
                        now=OLD, code_revision=REVISION, limit=2)
    manifest = collect_batch(store, batch, CALENDAR, source=source())
    queue.finish(manifest, now=OLD)
    queue.plan(master(2), CALENDAR, OLD+timedelta(minutes=5))
    batch = queue.claim(master(2)['universe_version'], '5min', OLD.date(),
                        now=OLD+timedelta(minutes=5), code_revision=REVISION, limit=2)
    queue.finish(receipt(batch, batch['spec']['symbols']), now=OLD+timedelta(minutes=5))
    return base, database, queue, store, manifest


def run(setup, **kwargs):
    base, database, _, _, _ = setup
    return module.repair(base, database, revision=REVISION, now=NOW,
                         _lease=lambda **_: nullcontext(), _priority=lambda *_: None, **kwargs)


def tasks(setup):
    _, _, queue, _, _ = setup
    return queue.snapshot(master(2)['universe_version'])['tasks']


def test_plan_performs_no_writes_lease_or_source_requests(setup, monkeypatch):
    before = tasks(setup)
    forbidden = Mock(side_effect=AssertionError('plan must not mutate or fetch'))
    monkeypatch.setattr(module, 'UnifiedCollectionQueue', forbidden)
    result = run(setup, source=forbidden)
    assert result['state'] == 'PLAN' and result['candidate_count'] == 2
    assert result['previous_sessions'] == ['2026-10-26', '2026-10-23', '2026-10-22']
    assert tasks(setup) == before
    forbidden.assert_not_called()


def test_previous_session_recovers_full_grid_preserving_old_artifacts_and_clocks(setup):
    _, _, _, store, old = setup
    prior_manifest = store.batch_path(old['batch_id']).read_bytes()
    prior_normalized = store.read_blob(old['normalized'])
    calls = []
    result = run(setup, apply=True, source=source(calls=calls))
    assert result['state'] == 'REPAIRED' and len(result['batches']) == 2
    assert {params['datalen'] for params in calls} == {'240'}
    assert module.TRAILING_ROWS <= module.PROVIDER_CAP
    assert store.batch_path(old['batch_id']).read_bytes() == prior_manifest
    assert store.read_blob(old['normalized']) == prior_normalized
    for batch in result['batches']:
        saved = store.load(batch['batch_id'])
        assert saved['spec']['target_end'] == '2026-10-26T07:00:00+00:00'
        assert saved['spec']['after_by_symbol'] == {saved['spec']['symbols'][0]:
                                                   '2026-10-26T01:55:00+00:00'}
        assert saved['bar_count'] == 43
        assert saved['completed_at'] == NOW.isoformat()
        assert store.read_captures(saved['raw'])[0]['request']['datalen'] == '240'
    assert all(row['completed_end'] == '2026-10-26T07:00:00+00:00' for row in tasks(setup))
    assert result['historical_pit_certified'] is False
    assert result['live_trading_enabled'] is False


def test_remaining_grid_gap_retains_prior_success_and_normal_backoff(setup):
    before = {row['symbol']: row for row in tasks(setup)}
    result = run(setup, apply=True, source=source(missing=True), max_batches=1)
    assert result['state'] == 'UNRESOLVED' and result['unresolved_count'] == 2
    saved = setup[3].load(result['batches'][0]['batch_id'])
    assert saved['normalized'] is None and saved['bar_count'] == 0
    symbol = saved['spec']['symbols'][0]
    after = next(row for row in tasks(setup) if row['symbol'] == symbol)
    assert after['last_error'] == 'MINUTE_WINDOW_GAP_REASON_UNKNOWN'
    assert after['completed_end'] == before[symbol]['completed_end']
    assert after['last_success_batch'] == before[symbol]['last_success_batch']
    assert datetime.fromisoformat(after['next_attempt_at']) == NOW+timedelta(seconds=120)
    assert setup[3].read_captures(saved['raw'])


def test_not_before_is_respected_without_fetch(setup):
    with setup[2].connect() as connection:
        connection.execute('UPDATE collection_tasks SET next_attempt_at=?',
                           ((NOW+timedelta(hours=1)).isoformat(),))
    before = tasks(setup)
    forbidden = Mock(side_effect=AssertionError('backoff must prevent request'))
    result = run(setup, apply=True, source=forbidden)
    assert result['state'] == 'UNRESOLVED' and not result['batches']
    assert tasks(setup) == before
    forbidden.assert_not_called()


@pytest.mark.parametrize('block', ['priority', 'lease', 'owner', 'running'])
def test_priority_lease_and_existing_owner_cannot_be_bypassed(setup, monkeypatch, block):
    base, database, queue, _, _ = setup
    if block == 'owner':
        with queue.connect() as connection:
            connection.execute('UPDATE process_owner SET process_id=?', (os.getpid()+10000,))
        monkeypatch.setattr(module, '_process_is_alive', lambda _: True)
    if block == 'running':
        with queue.connect() as connection:
            connection.execute("UPDATE collection_tasks SET state='RUNNING'")
    before = tasks(setup)

    @contextmanager
    def lease(**kwargs):
        assert kwargs == {'blocking': False}
        if block == 'lease':
            raise BlockingIOError('lease already owned')
        yield

    forbidden = Mock(side_effect=AssertionError('blocked apply must not fetch'))
    result = module.repair(base, database, revision=REVISION, apply=True, now=NOW,
                           source=forbidden, _lease=lease,
                           _priority=lambda *_: 'BUSY' if block == 'priority' else None)
    assert result['state'] == 'BLOCKED'
    assert tasks(setup) == before
    forbidden.assert_not_called()


@pytest.mark.parametrize('cap', [0, 4])
def test_batch_cap_rejects_before_mutation(setup, cap):
    before = tasks(setup)
    with pytest.raises(ValueError, match='RESOURCE_BOUND'):
        run(setup, apply=True, max_batches=cap)
    assert tasks(setup) == before


def test_current_future_and_fourth_previous_session_never_selected(setup):
    queue = setup[2]
    queue.plan(master(2), CALENDAR, NOW)
    queue.plan(master(2), CALENDAR, datetime(2026, 10, 21, 2, tzinfo=UTC))
    queue.plan(master(2), CALENDAR, NOW+timedelta(days=1))
    result = run(setup)
    assert result['candidate_count'] == 2
    assert {row['trading_day'] for row in result['candidates']} == {'2026-10-26'}
    with queue.connect() as connection:
        before = [dict(row) for row in connection.execute(
            "SELECT * FROM collection_tasks WHERE trading_day!='2026-10-26' "
            "ORDER BY trading_day,symbol")]
    repaired = run(setup, apply=True, source=source())
    assert repaired['state'] == 'REPAIRED'
    with queue.connect() as connection:
        after = [dict(row) for row in connection.execute(
            "SELECT * FROM collection_tasks WHERE trading_day!='2026-10-26' "
            "ORDER BY trading_day,symbol")]
    assert after == before


def test_no_noncommercial_ack_or_live_override(setup, monkeypatch):
    monkeypatch.delenv('QTRADE_AKSHARE_NONCOMMERCIAL_ACK')
    with pytest.raises(ValueError, match='ACK_REQUIRED'):
        run(setup, apply=True)
    monkeypatch.setenv('LIVE_TRADING_ENABLED', 'true')
    with pytest.raises(ValueError, match='SAFETY'):
        run(setup, apply=True)


def test_no_data_task_can_recover_whole_prior_session(setup):
    with setup[2].connect() as connection:
        connection.execute('UPDATE collection_tasks SET completed_end=NULL,last_success_batch=NULL')
    result = run(setup, apply=True, source=source(), max_batches=1)
    saved = setup[3].load(result['batches'][0]['batch_id'])
    assert saved['bar_count'] == 48
    assert list(saved['spec']['after_by_symbol'].values()) == [None]
    assert result['state'] == 'UNRESOLVED' and result['unresolved_count'] == 1


def test_successful_partial_session_task_extends_only_its_requested_boundary(setup):
    with setup[2].connect() as connection:
        connection.execute("UPDATE collection_tasks "
                           "SET state='SUCCEEDED',desired_end=completed_end")
    result = run(setup, apply=True, source=source(), max_batches=1)
    assert result['state'] == 'UNRESOLVED'
    assert setup[3].load(result['batches'][0]['batch_id'])['bar_count'] == 43


def test_current_session_only_response_cannot_replace_prior_day(setup):
    def current_only(source_id, params):
        response = source()(source_id, params)
        response['body'] = json.dumps([row for row in json.loads(response['body'])
                                       if row['day'].startswith('2026-10-27')]).encode()
        return response

    before = {row['symbol']: row['completed_end'] for row in tasks(setup)}
    result = run(setup, apply=True, source=current_only, max_batches=1)
    assert result['state'] == 'UNRESOLVED'
    saved = setup[3].load(result['batches'][0]['batch_id'])
    assert saved['normalized'] is None
    assert next(iter(saved['outcomes'].values()))['error_code'] == (
        'NO_COMPLETED_REQUESTED_BARS_REASON_UNKNOWN')
    assert {row['symbol']: row['completed_end'] for row in tasks(setup)} == before


def test_plan_never_acquires_vendor_lease(setup):
    forbidden = Mock(side_effect=AssertionError('no plan lease'))
    result = module.repair(setup[0], setup[1], revision=REVISION, now=NOW,
                           source=forbidden, _lease=forbidden, _priority=forbidden)
    assert result['state'] == 'PLAN'
    forbidden.assert_not_called()


def test_real_priority_guard_blocks_collecting_service(setup):
    path = setup[0]/'unified-data/service-state.json'
    path.write_text(json.dumps({'state': 'COLLECTING', 'heartbeat_at': NOW.isoformat()}))
    forbidden = Mock(side_effect=AssertionError('current work has priority'))
    result = module.repair(setup[0], setup[1], revision=REVISION, now=NOW, apply=True,
                           source=forbidden, _lease=forbidden)
    assert result['state'] == 'BLOCKED'
    assert result['error_code'] == 'REPAIR_CURRENT_PRIORITY_NOT_IDLE'
    forbidden.assert_not_called()


@pytest.mark.parametrize('zero_candidates', [False, True])
def test_legacy_marker_absence_cannot_migrate_unrelated_rows(setup, monkeypatch, zero_candidates):
    base, database, queue, _, _ = setup
    queue.plan(master(2), CALENDAR, NOW)
    queue.plan(master(2), CALENDAR, OLD.replace(hour=9))  # Includes unrelated daily tasks.
    queue.plan(master(2), CALENDAR, datetime(2026, 10, 21, 9, tzinfo=UTC))
    other = signed({**{key: value for key, value in master(2).items()
                       if key != 'evidence_sha256'}, 'universe_version': 'c'*64})
    queue.plan(other, CALENDAR, OLD.replace(hour=9))
    with queue.connect() as connection:
        connection.execute("DELETE FROM collection_metadata WHERE key='failure_streak_v1'")
        connection.execute("UPDATE collection_tasks SET consecutive_failures=4,"
                           "attempts=9,next_attempt_at='2030-01-01T00:00:00+00:00'")
        if zero_candidates:
            connection.execute("UPDATE collection_tasks SET state='SUCCEEDED',"
                               "completed_end=desired_end WHERE trading_day='2026-10-26'")
        before_rows = [dict(row) for row in connection.execute(
            'SELECT * FROM collection_tasks '
            'ORDER BY universe_version,frequency,symbol,trading_day')]
        before_metadata = list(connection.execute('SELECT * FROM collection_metadata ORDER BY key'))
    forbidden = Mock(side_effect=AssertionError('legacy apply cannot construct queue or fetch'))
    monkeypatch.setattr(module, 'UnifiedCollectionQueue', forbidden)
    result = module.repair(base, database, revision=REVISION, now=NOW, apply=True,
                           source=forbidden, _lease=forbidden, _priority=forbidden)
    assert result['state'] == 'BLOCKED'
    assert result['candidate_count'] == (0 if zero_candidates else 2)
    assert result['error_code'] == 'REPAIR_EXISTING_FAILURE_MIGRATION_REQUIRED'
    forbidden.assert_not_called()
    with queue.connect() as connection:
        assert [dict(row) for row in connection.execute(
            'SELECT * FROM collection_tasks ORDER BY universe_version,frequency,symbol,trading_day')
                ] == before_rows
        assert list(connection.execute('SELECT * FROM collection_metadata ORDER BY key')) == (
            before_metadata)


def test_missing_failure_streak_column_rejected_before_queue_construction(setup, monkeypatch):
    with setup[2].connect() as connection:
        connection.execute('ALTER TABLE collection_tasks DROP COLUMN consecutive_failures')
    forbidden = Mock(side_effect=AssertionError('repair cannot add legacy columns'))
    monkeypatch.setattr(module, 'UnifiedCollectionQueue', forbidden)
    result = run(setup, apply=True, source=forbidden)
    assert result['state'] == 'BLOCKED'
    assert result['error_code'] == 'REPAIR_EXISTING_FAILURE_MIGRATION_REQUIRED'
    forbidden.assert_not_called()
    with setup[2].connect() as connection:
        assert 'consecutive_failures' not in {row['name'] for row in connection.execute(
            'PRAGMA table_info(collection_tasks)')}


def test_task_query_filters_dates_in_sql_and_empty_scope_never_opens_database(setup, monkeypatch):
    original = module._read
    queries = []

    def traced(database):
        connection = original(database)
        connection.set_trace_callback(queries.append)
        return connection

    monkeypatch.setattr(module, '_read', traced)
    result = module._tasks(setup[1], master(2)['universe_version'],
                           ['2026-10-26'], {'600000'})
    assert [row['symbol'] for row in result] == ['600000']
    assert any("AND trading_day IN ('2026-10-26')" in query for query in queries)
    forbidden = Mock(side_effect=AssertionError('empty date scope must not open database'))
    monkeypatch.setattr(module, '_read', forbidden)
    assert module._tasks(setup[1], master(2)['universe_version'], [], {'600000'}) == []
    with pytest.raises(ValueError, match='SESSION_SCOPE_BOUND'):
        module._tasks(setup[1], master(2)['universe_version'], ['2026-10-26']*4, {'600000'})
    forbidden.assert_not_called()
