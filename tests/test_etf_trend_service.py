from __future__ import annotations

import json
import os
from contextlib import nullcontext
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
                'required_through': {'day': day+'T15:00:00+08:00'}, 'tasks': []}))
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

        def run(revision=REVISION, stop=None):
            return service.cycle(base, local, master, revision, now=clock[0], stop=stop)
        yield SimpleNamespace(base=base, local=local, master=master, clock=clock, calls=calls,
                              run=run, refresh=refresh)


def immutable(path, value):
    if path.exists():
        raise FileExistsError(path)
    write(path, value)


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
            progress['tasks'] = [{'state': 'FAILED', 'last_error': 'SOURCE_HTTP_ERROR',
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
    monkeypatch.setattr(service, 'os', SimpleNamespace(name='posix'))
    monkeypatch.setattr(service, 'exclusive', lambda path: nullcontext())
    monkeypatch.setattr(service.signal, 'SIGALRM', 14, raising=False)
    monkeypatch.setattr(service.signal, 'signal', lambda *args: None)
    monkeypatch.setattr(service.signal, 'alarm', alarms.append, raising=False)
    monkeypatch.setattr(service, 'cycle', lambda *args, **kwargs: {'state': 'TEST_ONLY'})
    monkeypatch.setattr('sys.argv', ['run_etf_trend_service', '--base', str(runtime.base),
        '--local-root', str(runtime.local), '--source-revision', REVISION, '--once'])
    service.main()
    assert alarms == [service.HARD_DEADLINE_SECONDS, 0]
