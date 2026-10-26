from __future__ import annotations

import json
import os
import sqlite3
from contextlib import closing, nullcontext
from datetime import UTC, datetime, timedelta
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from types import SimpleNamespace

import pytest

from qtrade.instruments.observed import SCHEMA, instrument_id
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.etf_trend import sessions
from scripts import run_etf_trend as runner
from scripts import run_etf_trend_service as service

NOW = datetime(2026, 10, 23, 12, tzinfo=UTC)
REVISION = 'a'*40


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


@pytest.fixture
def runtime(monkeypatch):
    # Short real paths avoid Windows MAX_PATH with nested content-addressed objects.
    directory = Path(service.REPO.anchor) if os.name == 'nt' else None
    with TemporaryDirectory(prefix='etfs-', dir=directory) as temporary:
        root = Path(temporary)
        base, local, master = root/'base', root/'local', root/'master.json'
        base.mkdir()
        clock, calls = [NOW], []
        monkeypatch.setenv('LIVE_TRADING_ENABLED', 'false')
        monkeypatch.setenv('QTRADE_AKSHARE_NONCOMMERCIAL_ACK', 'true')
        monkeypatch.setattr(service, 'utc_now', lambda: clock[0])
        monkeypatch.setattr(service.shutil, 'disk_usage', lambda path: SimpleNamespace(
            free=200*1024**3))
        if os.name == 'nt':
            monkeypatch.setattr(service, 'exclusive', lambda path: nullcontext())
            monkeypatch.setattr(service, 'write_json_atomic', write)
            monkeypatch.setattr(runner, 'write_json_immutable', immutable)
            monkeypatch.setattr(service, 'write_json_immutable', immutable)
            monkeypatch.setattr('scripts.unified_store._fsync_directory', lambda path: None)
        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)
        monkeypatch.setattr(runner, 'datetime', Clock)
        calendar = Calendar(service.REPO/'configs/observer_calendar_2026.json')

        def refresh(now=NOW):
            clock[0] = now
            day = calendar.expected(now, 'daily').date().isoformat()
            write(base/'unified-data/service-state.json', {'state': 'WAITING',
                'heartbeat_at': now.isoformat(), 'live_trading_enabled': False})
            write(base/'unified-data/collection-progress.json', signed({**AUTHORITY,
                'updated_at': now.isoformat(), 'state': 'PASS_FINISHED',
                'universe_version': 'c'*64,
                'required_through': {'day': day+'T15:00:00+08:00'}, 'tasks': [
                    {'universe_version': 'c'*64, 'frequency': 'day', 'trading_day': day,
                     'state': 'SUCCEEDED', 'last_error': None, 'next_attempt_at': now.isoformat(),
                     'desired_end': day+'T15:00:00+08:00',
                     'completed_end': day+'T15:00:00+08:00'}]}))
        refresh()
        write(master, signed({**AUTHORITY, 'schema_version': SCHEMA,
            'observed_at': '2026-10-22T12:00:00+00:00', 'securities': [
                {'symbol': symbol, 'instrument_id': instrument_id(symbol, 'ETF'),
                 'security_type': 'ETF', 'in_target': True, 'status': 'ACTIVE_OBSERVED',
                 'issues': []} for symbol in ('510300', '159915')]}))

        def fetch(source, params):
            calls.append((source, params))
            day = calendar.expected(clock[0], 'daily').date()
            window = sessions(calendar, day)
            rows = [{'day': item, 'open': str(2+i*.01), 'high': str(2.1+i*.01),
                'low': str(1.9+i*.01), 'close': str(2+i*.01), 'volume': '1000'}
                for i, item in enumerate(window)]
            return {'source_id': source, 'request': params, 'received_at': clock[0].isoformat(),
                    'body': json.dumps(rows).encode(), 'status_code': 200,
                    'transport_error': None, 'body_complete': True}
        monkeypatch.setattr(service, 'fetch', fetch)

        def run(revision=REVISION, stop=None, collection_database=None):
            return service.cycle(base, local, master, revision, now=clock[0], stop=stop,
                                 collection_database=collection_database)
        yield SimpleNamespace(base=base, local=local, master=master, clock=clock, calls=calls,
                              run=run, refresh=refresh)


def immutable(path, value):
    if path.exists():
        raise FileExistsError(path)
    write(path, value)


def minute_only_progress(runtime):
    path = runtime.base/'unified-data/collection-progress.json'
    progress = json.loads(path.read_text())
    progress['required_through'] = {'5min': '2026-10-23T07:00:00+00:00'}
    progress['tasks'] = []
    write(path, signed({k: v for k, v in progress.items() if k != 'evidence_sha256'}))


def collection_ledger(runtime, rows=None):
    database = runtime.local/'collection.sqlite'
    database.parent.mkdir(parents=True, exist_ok=True)
    with closing(sqlite3.connect(database)) as connection:
        connection.execute('CREATE TABLE collection_tasks '
                           '(universe_version TEXT,frequency TEXT,trading_day TEXT,state TEXT,'
                           'last_error TEXT,next_attempt_at TEXT,'
                           'desired_end TEXT,completed_end TEXT)')
        selected = rows if rows is not None else [
            ('day', '2026-10-23', 'SUCCEEDED', None, NOW.isoformat()),
            ('5min', '2026-10-23', 'SUCCEEDED', None, NOW.isoformat())]
        normalized = []
        for row in selected:
            scoped = ('c'*64, *row) if len(row) == 5 else row
            if len(scoped) == 6:
                desired = scoped[2]+'T15:00:00+08:00'
                scoped = (*scoped, desired, desired if scoped[3] == 'SUCCEEDED' else None)
            normalized.append(scoped)
        connection.executemany('INSERT INTO collection_tasks VALUES(?,?,?,?,?,?,?,?)', normalized)
        connection.commit()
    return database


def test_minute_only_snapshot_without_daily_ledger_waits_without_key_error(runtime):
    minute_only_progress(runtime)
    assert runtime.run()['state'] == 'ETF_SERVICE_WAITING_CURRENT_DAILY_COLLECTION'
    assert runtime.calls == []
    assert not (runtime.local/'days/2026-10-23/attempt.json').exists()


def test_read_only_daily_ledger_supplies_proof_when_snapshot_rotates_to_minutes(runtime):
    minute_only_progress(runtime)
    database = collection_ledger(runtime)
    before = database.read_bytes()
    result = runtime.run(collection_database=database)
    assert result['state'] == 'ETF_DAILY_DIAGNOSTIC_PUBLISHED'
    assert len(runtime.calls) == 2 and database.read_bytes() == before
    with closing(sqlite3.connect(database)) as connection:
        assert connection.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall(
            ) == [('collection_tasks',)]


@pytest.mark.parametrize('retired_state', ['RUNNING', 'FAILED'])
def test_retired_universe_tasks_cannot_block_current_universe(runtime, retired_state):
    minute_only_progress(runtime)
    database = collection_ledger(runtime, [
        ('c'*64, 'day', '2026-10-23', 'SUCCEEDED', None, NOW.isoformat()),
        ('a'*64, '5min', '2026-10-23', retired_state, 'SOURCE_HTTP_ERROR', NOW.isoformat()),
    ])
    assert runtime.run(collection_database=database)['state'] == 'ETF_DAILY_DIAGNOSTIC_PUBLISHED'
    assert len(runtime.calls) == 2


def test_retired_universe_daily_success_cannot_supply_current_universe_proof(runtime):
    minute_only_progress(runtime)
    database = collection_ledger(runtime, [
        ('a'*64, 'day', '2026-10-23', 'SUCCEEDED', None, NOW.isoformat()),
        ('c'*64, '5min', '2026-10-23', 'SUCCEEDED', None, NOW.isoformat()),
    ])
    assert runtime.run(collection_database=database)['state'] == (
        'ETF_SERVICE_WAITING_CURRENT_DAILY_COLLECTION')
    assert runtime.calls == []
    assert not (runtime.local/'days/2026-10-23/attempt.json').exists()


@pytest.mark.parametrize('universe', [None, '../retired', 'g'*64])
def test_progress_universe_requires_checked_identity(runtime, universe):
    minute_only_progress(runtime)
    database = collection_ledger(runtime)
    path = runtime.base/'unified-data/collection-progress.json'
    progress = json.loads(path.read_text())
    progress['universe_version'] = universe
    write(path, signed({k: v for k, v in progress.items() if k != 'evidence_sha256'}))
    assert runtime.run(collection_database=database)['state'] == (
        'ETF_SERVICE_CURRENT_COLLECTION_UNIVERSE_INVALID')
    assert runtime.calls == []


@pytest.mark.parametrize('delay,complete', [(60, False), (-1, False), (60, True), (-1, True)])
def test_failed_nonhttp_retry_priority_matches_collector_claim(runtime, delay, complete):
    minute_only_progress(runtime)
    desired = '2026-10-23T15:00:00+08:00'
    database = collection_ledger(runtime, [
        ('c'*64, 'day', '2026-10-23', 'FAILED', 'STALE_SOURCE_END',
         (NOW+timedelta(seconds=delay)).isoformat(), desired, desired if complete else None),
        ('c'*64, '5min', '2026-10-23', 'SUCCEEDED', None, NOW.isoformat()),
    ])
    waiting = delay <= 0 and not complete
    expected = ('ETF_SERVICE_WAITING_CURRENT_COLLECTION_RETRY' if waiting
                else 'ETF_DAILY_DIAGNOSTIC_PUBLISHED')
    assert runtime.run(collection_database=database)['state'] == expected
    assert len(runtime.calls) == (0 if waiting else 2)


def test_previous_day_snapshot_tasks_cannot_supply_current_retry_priority(runtime):
    path = runtime.base/'unified-data/collection-progress.json'
    progress = json.loads(path.read_text())
    progress['tasks'].append({**progress['tasks'][0], 'trading_day': '2026-10-22',
        'state': 'FAILED', 'last_error': 'SOURCE_HTTP_ERROR', 'next_attempt_at': NOW.isoformat()})
    write(path, signed({k: v for k, v in progress.items() if k != 'evidence_sha256'}))
    assert runtime.run()['state'] == 'ETF_DAILY_DIAGNOSTIC_PUBLISHED'
    assert len(runtime.calls) == 2


@pytest.mark.parametrize('rows', [[], [('day', '2026-10-22', 'SUCCEEDED', None, NOW.isoformat())]])
def test_daily_ledger_requires_exact_completed_day_tasks(runtime, rows):
    minute_only_progress(runtime)
    database = collection_ledger(runtime, rows)
    assert runtime.run(collection_database=database)['state'] == (
        'ETF_SERVICE_WAITING_CURRENT_DAILY_COLLECTION')
    assert runtime.calls == []
    assert not (runtime.local/'days/2026-10-23/attempt.json').exists()


@pytest.mark.parametrize('state', ['RUNNING', 'QUEUED', 'PLANNED', 'INTERRUPTED'])
def test_any_current_frequency_collection_preempts_etf_even_when_daily_is_terminal(runtime, state):
    minute_only_progress(runtime)
    database = collection_ledger(runtime, [
        ('day', '2026-10-23', 'SUCCEEDED', None, NOW.isoformat()),
        ('5min', '2026-10-23', state, None, NOW.isoformat()),
    ])
    assert runtime.run(collection_database=database)['state'] == (
        'ETF_SERVICE_WAITING_CURRENT_COLLECTION')
    assert runtime.calls == []


@pytest.mark.parametrize('error', ['SOURCE_HTTP_ERROR', 'SOURCE_TRANSPORT_ERROR'])
@pytest.mark.parametrize('delay', [60, -1])
def test_minute_source_backoff_and_due_retry_take_priority_from_daily_ledger(runtime, error, delay):
    minute_only_progress(runtime)
    database = collection_ledger(runtime, [
        ('day', '2026-10-23', 'SUCCEEDED', None, NOW.isoformat()),
        ('5min', '2026-10-23', 'FAILED', error, (NOW+timedelta(seconds=delay)).isoformat()),
    ])
    expected = 'ETF_SERVICE_CURRENT_SOURCE_BACKOFF_ACTIVE' if delay > 0 else (
        'ETF_SERVICE_WAITING_CURRENT_SOURCE_RETRY')
    assert runtime.run(collection_database=database)['state'] == expected
    assert runtime.calls == []
    assert not (runtime.local/'days/2026-10-23/attempt.json').exists()


def test_intraday_collection_today_preempts_previous_complete_day_etf(runtime):
    runtime.refresh(datetime(2026, 10, 26, 2, tzinfo=UTC))
    minute_only_progress(runtime)
    database = collection_ledger(runtime, [
        ('day', '2026-10-23', 'SUCCEEDED', None, NOW.isoformat()),
        ('5min', '2026-10-26', 'RUNNING', None, NOW.isoformat()),
    ])
    assert runtime.run(collection_database=database)['state'] == (
        'ETF_SERVICE_WAITING_CURRENT_COLLECTION')
    assert runtime.calls == []


def test_missing_daily_ledger_is_not_created_by_source_gate(runtime):
    minute_only_progress(runtime)
    database = runtime.local/'does-not-exist.sqlite'
    assert runtime.run(collection_database=database)['state'] == (
        'ETF_SERVICE_COLLECTION_LEDGER_INVALID')
    assert not database.exists() and runtime.calls == []


def test_current_daily_ledger_cannot_bypass_stale_signed_progress(runtime):
    minute_only_progress(runtime)
    database = collection_ledger(runtime)
    path = runtime.base/'unified-data/collection-progress.json'
    progress = json.loads(path.read_text())
    progress['updated_at'] = (NOW-timedelta(minutes=6)).isoformat()
    write(path, signed({k: v for k, v in progress.items() if k != 'evidence_sha256'}))
    assert runtime.run(collection_database=database)['state'] == (
        'ETF_SERVICE_CURRENT_COLLECTION_EVIDENCE_INVALID')
    assert runtime.calls == []


def test_minute_source_error_without_valid_retry_clock_fails_closed(runtime):
    minute_only_progress(runtime)
    database = collection_ledger(runtime, [
        ('day', '2026-10-23', 'SUCCEEDED', None, NOW.isoformat()),
        ('5min', '2026-10-23', 'FAILED', 'SOURCE_HTTP_ERROR', None),
    ])
    assert runtime.run(collection_database=database)['state'] == (
        'ETF_SERVICE_CURRENT_SOURCE_BACKOFF_INVALID')
    assert runtime.calls == []


def test_collection_ledger_query_is_bounded(runtime, monkeypatch):
    minute_only_progress(runtime)
    database = collection_ledger(runtime)
    monkeypatch.setattr(service, 'MAX_COLLECTION_TASKS', 1)
    assert runtime.run(collection_database=database)['state'] == (
        'ETF_SERVICE_COLLECTION_LEDGER_BOUND_EXCEEDED')
    assert runtime.calls == []


def test_ledger_priority_is_rechecked_between_source_requests(runtime, monkeypatch):
    minute_only_progress(runtime)
    database = collection_ledger(runtime)
    original = service.fetch

    def start_current_collection(source, params):
        response = original(source, params)
        with closing(sqlite3.connect(database)) as connection:
            connection.execute("UPDATE collection_tasks SET state='RUNNING' WHERE frequency='5min'")
            connection.commit()
        return response

    monkeypatch.setattr(service, 'fetch', start_current_collection)
    assert runtime.run(collection_database=database)['state'] == (
        'ETF_DAILY_ATTEMPT_FAILED_REVIEW_REQUIRED')
    assert len(runtime.calls) == 1
    assert runtime.run(collection_database=database)['state'] == (
        'ETF_SERVICE_WAITING_CURRENT_COLLECTION')
    assert len(runtime.calls) == 1


def test_complete_day_publishes_once_and_restart_never_recaptures(runtime):
    first = runtime.run()
    assert first['state'] == 'ETF_DAILY_DIAGNOSTIC_PUBLISHED'
    assert first['qualified_close_symbols'] == 2 and len(runtime.calls) == 2
    latest = runtime.base/'online-research/etf-trend/latest.json'
    stamp = latest.stat().st_mtime_ns
    assert runtime.run()['state'] == 'ETF_DAILY_DIAGNOSTIC_ALREADY_READY'
    assert len(runtime.calls) == 2 and latest.stat().st_mtime_ns == stamp
    assert (runtime.local/'days/2026-10-23/completed.json').exists()


def test_existing_verified_same_day_archive_survives_revision_and_master_changes(runtime):
    runtime.run()
    (runtime.local/'days/2026-10-23/completed.json').unlink()
    runtime.master.unlink()
    assert runtime.run(revision='b'*40)['state'] == 'ETF_DAILY_DIAGNOSTIC_ALREADY_READY'
    assert len(runtime.calls) == 2


@pytest.mark.parametrize('gate', ['active', 'stale', 'future', 'storage', 'progress',
                                 'own_backoff', 'collector_backoff', 'due_source_retry'])
def test_priority_backoff_and_reserve_gates_do_not_spend_daily_attempt(runtime, monkeypatch, gate):
    if gate in {'active', 'stale', 'future'}:
        stamp = NOW-timedelta(minutes=6) if gate == 'stale' else (
            NOW+timedelta(seconds=1) if gate == 'future' else NOW)
        write(runtime.base/'unified-data/service-state.json', {
            'state': 'COLLECTING' if gate == 'active' else 'WAITING',
            'heartbeat_at': stamp.isoformat(), 'live_trading_enabled': False})
    elif gate == 'storage':
        monkeypatch.setattr(service.shutil, 'disk_usage', lambda path: SimpleNamespace(free=1))
    elif gate == 'own_backoff':
        write(runtime.local/'source-backoff.json', signed({**AUTHORITY,
            'not_before': (NOW+timedelta(hours=1)).isoformat()}))
    else:
        path = runtime.base/'unified-data/collection-progress.json'
        progress = json.loads(path.read_text())
        if gate == 'progress':
            progress['state'] = 'COLLECTING'
        else:
            progress['tasks'] = [{**progress['tasks'][0],
                'state': 'FAILED', 'last_error': 'SOURCE_HTTP_ERROR', 'completed_end': None,
                'next_attempt_at': (NOW+timedelta(minutes=1) if gate == 'collector_backoff'
                                    else NOW-timedelta(seconds=1)).isoformat()}]
        progress.pop('evidence_sha256')
        write(path, signed(progress))
    result = runtime.run()
    assert not result['published'] and runtime.calls == []
    assert not (runtime.local/'days/2026-10-23/attempt.json').exists()


def test_first_source_failure_stops_burst_preserves_raw_and_cools_down(runtime, monkeypatch):
    original = service.fetch
    def failed(source, params):
        response = original(source, params)
        response['status_code'] = 429
        return response
    monkeypatch.setattr(service, 'fetch', failed)
    first = runtime.run()
    assert first['state'] == 'ETF_DAILY_ATTEMPT_FAILED_REVIEW_REQUIRED'
    assert len(runtime.calls) == 1
    archive = runtime.base/'online-research/etf-trend'/first['diagnostic_id']
    assert (archive/'source-receipts/01.json').exists()
    cooldown = json.loads((runtime.local/'source-backoff.json').read_text())
    assert datetime.fromisoformat(cooldown['not_before']) == NOW+timedelta(hours=6)
    assert runtime.run()['state'] == 'ETF_SERVICE_SOURCE_BACKOFF_ACTIVE'
    runtime.refresh(NOW+timedelta(hours=7))
    assert runtime.run()['state'] == 'ETF_DAILY_ATTEMPT_REVIEW_REQUIRED'
    assert len(runtime.calls) == 1


def test_current_collection_preempts_burst_and_prevents_retry(runtime, monkeypatch):
    original = service.fetch
    def concurrent_collection(source, params):
        response = original(source, params)
        write(runtime.base/'unified-data/service-state.json', {
            'state': 'COLLECTING', 'heartbeat_at': NOW.isoformat(), 'live_trading_enabled': False})
        return response
    monkeypatch.setattr(service, 'fetch', concurrent_collection)
    assert runtime.run()['state'] == 'ETF_DAILY_ATTEMPT_FAILED_REVIEW_REQUIRED'
    runtime.refresh()
    assert runtime.run()['state'] == 'ETF_DAILY_ATTEMPT_REVIEW_REQUIRED'
    assert len(runtime.calls) == 1


def test_interruption_before_source_cannot_retry_or_change_frozen_revision(runtime, monkeypatch):
    def interrupted(*args, **kwargs):
        raise OSError('test-only-interruption')
    monkeypatch.setattr(service, 'run', interrupted)
    assert runtime.run()['state'] == 'ETF_DAILY_ATTEMPT_FAILED_REVIEW_REQUIRED'
    assert runtime.run()['state'] == 'ETF_DAILY_ATTEMPT_REVIEW_REQUIRED'
    assert runtime.run(revision='b'*40)['state'] == 'ETF_DAILY_FROZEN_INPUT_CHANGED_REVIEW_REQUIRED'
    assert runtime.calls == []


def test_failed_verification_never_publishes_and_restart_only_replays(runtime, monkeypatch):
    original = service.verify_etf_archive
    def invalid(*args):
        raise ValueError('ETF_TEST_ARCHIVE_INVALID')
    monkeypatch.setattr(service, 'verify_etf_archive', invalid)
    assert runtime.run()['state'] == 'ETF_DAILY_ATTEMPT_FAILED_REVIEW_REQUIRED'
    assert not (runtime.base/'online-research/etf-trend/latest.json').exists()
    monkeypatch.setattr(service, 'verify_etf_archive', original)
    assert runtime.run()['state'] == 'ETF_DAILY_DIAGNOSTIC_PUBLISHED'
    assert len(runtime.calls) == 2


def test_receipt_after_crash_can_complete_without_another_source_request(runtime, monkeypatch):
    original = service.run
    def interrupted(*args, **kwargs):
        value = original(*args, **kwargs)
        identity = signed(value['bindings'])['evidence_sha256']
        (runtime.base/'online-research/etf-trend'/identity/'diagnostic.json').unlink()
        raise OSError('test-only-crash-after-receipt')
    monkeypatch.setattr(service, 'run', interrupted)
    assert runtime.run()['state'] == 'ETF_DAILY_ATTEMPT_FAILED_REVIEW_REQUIRED'
    monkeypatch.setattr(service, 'run', original)
    assert runtime.run()['state'] == 'ETF_DAILY_DIAGNOSTIC_PUBLISHED'
    assert len(runtime.calls) == 2


def test_later_complete_exchange_day_creates_exactly_one_new_archive(runtime):
    first = runtime.run()
    runtime.refresh(datetime(2026, 10, 26, 12, tzinfo=UTC))
    later = runtime.run()
    assert later['state'] == 'ETF_DAILY_DIAGNOSTIC_PUBLISHED'
    assert later['required_day'] == '2026-10-26' and len(runtime.calls) == 4
    assert later['diagnostic_id'] != first['diagnostic_id']
    assert runtime.run()['state'] == 'ETF_DAILY_DIAGNOSTIC_ALREADY_READY'
    assert len(runtime.calls) == 4


def test_completed_old_day_cannot_roll_back_a_newer_pointer(runtime):
    runtime.run()
    runtime.refresh(datetime(2026, 10, 26, 12, tzinfo=UTC))
    newer = runtime.run()
    runtime.refresh(NOW)
    with pytest.raises(ValueError, match='LATEST_REPLACEMENT_REQUIRES_REVIEW'):
        runtime.run()
    pointer = json.loads((runtime.base/'online-research/etf-trend/latest.json').read_text())
    assert pointer['diagnostic_id'] == newer['diagnostic_id'] and len(runtime.calls) == 4


def test_invalid_existing_pointer_never_authorizes_another_source_burst(runtime):
    latest = runtime.base/'online-research/etf-trend/latest.json'
    write(latest, {'diagnostic_id': 'bad'})
    with pytest.raises(ValueError):
        runtime.run()
    assert runtime.calls == []
    assert not (runtime.local/'days/2026-10-23/attempt.json').exists()


def test_stopped_or_unacknowledged_service_never_collects(runtime, monkeypatch):
    stop = Event()
    stop.set()
    assert runtime.run(stop=stop)['state'] == 'STOPPED'
    monkeypatch.setenv('QTRADE_AKSHARE_NONCOMMERCIAL_ACK', 'false')
    with pytest.raises(ValueError, match='CONFIGURATION_REQUIRED'):
        runtime.run()
    assert runtime.calls == []


def test_once_cli_sets_and_cancels_hard_deadline(runtime, monkeypatch):
    alarms = []
    calls = []
    monkeypatch.setattr(service, 'os', SimpleNamespace(name='posix'))
    monkeypatch.setattr(service, 'exclusive', lambda path: nullcontext())
    monkeypatch.setattr(service.signal, 'SIGALRM', 14, raising=False)
    monkeypatch.setattr(service.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(service.signal, 'alarm', alarms.append, raising=False)
    def cycle(*args, **kwargs):
        calls.append(kwargs)
        return {'state': 'TEST_ONLY'}
    monkeypatch.setattr(service, 'cycle', cycle)
    database = runtime.local/'collection.sqlite'
    monkeypatch.setattr('sys.argv', ['run_etf_trend_service', '--base', str(runtime.base),
        '--local-root', str(runtime.local), '--source-revision', REVISION,
        '--collection-database', str(database), '--once'])
    service.main()
    assert alarms == [service.HARD_DEADLINE_SECONDS, 0]
    assert calls[0]['collection_database'] == database
