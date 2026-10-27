from __future__ import annotations

import json
import os
import shutil
import sqlite3
import subprocess
from contextlib import closing, nullcontext
from datetime import UTC, date, datetime, timedelta
from decimal import Decimal
from hashlib import sha1
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

import pytest

from qtrade.instruments.observed import SCHEMA, instrument_id
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research import etf_attempt_repair as repair
from qtrade.research import etf_trend
from qtrade.research.data_versions import file_hash
from qtrade.research.etf_trend import admit, sessions
from scripts import run_etf_shadow_service as shadow
from scripts import run_etf_trend_service as daily
from scripts.unified_store import UnifiedBatchStore

NOW = datetime(2026, 10, 27, 10, tzinfo=UTC)
OLD = 'a'*40
NEW = 'b'*40
DAY = '2026-10-27'
CHECK_REVISION = repair._check_revision
DURABLE_DIRECTORY = repair._durable_directory


def write(path, value, **kwargs):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def immutable(path, value):
    if path.exists():
        raise FileExistsError(path)
    write(path, value)


def resign(value):
    return signed({k: v for k, v in value.items() if k != 'evidence_sha256'})


@pytest.fixture
def runtime(monkeypatch):
    directory = Path(repair.REPO.anchor) if os.name == 'nt' else None
    with TemporaryDirectory(prefix='etfr-', dir=directory) as temporary:
        root = Path(temporary)
        base, local = root/'b', root/'l'
        archives = base/'online-research/etf-trend'
        cal = repair.calendar()
        symbols = ['159915', *(f'510{i:03d}' for i in range(29))]
        master = signed({**AUTHORITY, 'schema_version': SCHEMA,
            'observed_at': '2026-10-26T08:00:00+00:00', 'securities': [
                {'symbol': symbol, 'instrument_id': instrument_id(symbol, 'ETF'),
                 'security_type': 'ETF', 'in_target': True, 'status': 'ACTIVE_OBSERVED',
                 'issues': []} for symbol in symbols]})
        clock = [NOW]

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return clock[0].astimezone(tz) if tz else clock[0].replace(tzinfo=None)

        monkeypatch.setattr(etf_trend, 'datetime', Clock)
        monkeypatch.setenv('LIVE_TRADING_ENABLED', 'false')
        monkeypatch.setenv('QTRADE_AKSHARE_NONCOMMERCIAL_ACK', 'true')
        monkeypatch.setattr(repair, 'utc_now', lambda: clock[0])
        monkeypatch.setattr(repair, '_check_revision', lambda revision: None)
        if os.name == 'nt':
            monkeypatch.setattr(repair, '_require_linux', lambda: None)
            monkeypatch.setattr(repair, '_durable_directory', lambda path:
                                path.mkdir(parents=True, exist_ok=True))
            monkeypatch.setattr(repair, 'exclusive', lambda path: nullcontext())
            monkeypatch.setattr(repair, 'write_json_atomic', write)
            monkeypatch.setattr(repair, 'write_json_immutable', immutable)
            monkeypatch.setattr(repair, '_copy_origin', lambda src, dst: (
                dst.parent.mkdir(parents=True, exist_ok=True), shutil.copyfile(src, dst)))
            monkeypatch.setattr('scripts.unified_store._fsync_directory', lambda _: None)
            monkeypatch.setattr(repair.signal, 'SIGALRM', 14, raising=False)
            monkeypatch.setattr(repair.signal, 'signal', lambda *args: None)
            monkeypatch.setattr(repair.signal, 'alarm', lambda *args: None, raising=False)
        calls, gates = [], []

        def capture(symbol, day=DAY, observed=None):
            rows = []
            for index, session in enumerate(sessions(cal, date.fromisoformat(day))):
                close = Decimal('2')+Decimal(index)/100
                rows.append({'day': session, 'open': str(close), 'high': str(close+Decimal('.1')),
                    'low': str(close-Decimal('.1')), 'close': str(close), 'volume': '1000'})
            return {'source_id': 'sina_klines', 'request': repair.request_for(symbol),
                'received_at': (observed or clock[0]).isoformat(),
                'body': json.dumps(rows).encode(),
                'status_code': 200, 'transport_error': None, 'body_complete': True}

        def fetch(source, request):
            # Reservation MUST exist before the actual source function is invoked.
            current = archives/signed({**bindings, 'code_revision': NEW})['evidence_sha256']
            reservations = repair.receipts(current/'repair-requests')
            assert len(reservations) == len(calls)+1
            assert repair.read(reservations[-1])['request'] == request
            calls.append(request['symbol'][2:])
            return capture(request['symbol'][2:])

        def gate(*args, **kwargs):
            gates.append(args[4])
            return None

        monkeypatch.setattr(repair, 'fetch', fetch)
        monkeypatch.setattr(repair, 'source_gate', gate)
        bindings = {'master_sha256': master['evidence_sha256'], 'as_of': DAY,
            'code_revision': OLD, 'calendar_sha256': signed(cal.document)['evidence_sha256'],
            'contract': 'ETF_CLOSE_DIAGNOSTIC_V1'}
        old_id = signed(bindings)['evidence_sha256']
        old = archives/old_id
        job = local/'days'/DAY
        write(job/'master.json', master)
        write(job/'request.json', signed({**AUTHORITY, 'required_day': DAY,
            'source_revision': OLD, 'master_sha256': master['evidence_sha256'], 'symbols': symbols,
            'diagnostic_id': old_id, 'bindings': bindings}))
        write(job/'attempt.json', signed({**AUTHORITY, 'diagnostic_id': old_id,
            'started_at': '2026-10-27T08:33:59+00:00', 'maximum_queries': 30,
            'automatic_retry': False}))
        write(job/'failed.json', signed({**AUTHORITY, 'diagnostic_id': old_id,
            'error_code': repair.FAILURE, 'queries_completed': 1,
            'at': '2026-10-27T08:34:05+00:00'}))
        write(old/'collection-attempt.json', signed({**bindings,
            'started_at': '2026-10-27T08:33:59+00:00', 'maximum_queries': 30}))
        raw = UnifiedBatchStore(old/'capture-store').capture([
            capture(symbols[0], observed=datetime(2026, 10, 27, 8, 34, tzinfo=UTC))])
        write(old/'source-receipts/01.json', signed({**AUTHORITY,
            'diagnostic_id': old_id, 'raw': raw}))
        previous_bindings = {**bindings, 'as_of': '2026-10-26'}
        previous_id = signed(previous_bindings)['evidence_sha256']
        previous = archives/previous_id
        previous_captures = [capture(symbols[0], day='2026-10-26',
            observed=datetime(2026, 10, 26, 10, tzinfo=UTC))]
        raw = UnifiedBatchStore(previous/'capture-store').capture(previous_captures)
        write(previous/'receipt.json', signed({**AUTHORITY, 'bindings': previous_bindings,
                                             'raw': raw}))
        diagnostic = admit(previous_captures, [symbols[0]], cal, date(2026, 10, 26),
                           datetime(2026, 10, 26, 10, tzinfo=UTC))
        diagnostic.update(bindings=previous_bindings,
                          receipt_sha256=file_hash(previous/'receipt.json'))
        write(previous/'diagnostic.json', resign(diagnostic))
        write(archives/'latest.json', signed({**AUTHORITY, 'diagnostic_id': previous_id}))

        def plan():
            return repair.plan(base, local, DAY, NEW)

        def apply(value=None):
            value = value or plan()
            return repair.apply(base, local, value, value['evidence_sha256'])

        yield SimpleNamespace(root=root, base=base, local=local, archives=archives, cal=cal,
            old=old, job=job, capture=capture, calls=calls, gates=gates, clock=clock,
            plan=plan, apply=apply, previous_id=previous_id)


def tree(root):
    return {str(p.relative_to(root)): p.read_bytes() for p in root.rglob('*') if p.is_file()}


def test_plan_is_read_only_and_preserves_thirty_query_budget(runtime):
    before = tree(runtime.root)
    value = runtime.plan()
    assert value['maximum_total_queries'] == 30
    assert value['maximum_additional_queries'] == 29
    assert value['reused_symbols'] == ['159915']
    assert tree(runtime.root) == before and runtime.calls == [] and runtime.gates == []


def test_apply_reuses_original_and_strictly_resolves_partial(runtime):
    before = tree(runtime.old)
    value = runtime.plan()
    completion = runtime.apply(value)
    assert len(runtime.calls) == 29 and '159915' not in runtime.calls
    assert len(runtime.gates) == 31  # Before burst, each request, before sealing.
    assert completion['original_revision'] == OLD and completion['repair_revision'] == NEW
    diagnostic = repair.verify_resolution(runtime.old, runtime.cal)
    assert diagnostic['as_of'] == DAY and diagnostic['qualified_close_symbols'] == 30
    assert diagnostic['bindings']['code_revision'] == NEW
    for name, payload in before.items():
        assert (runtime.old/name).read_bytes() == payload
    assert not (runtime.old/'diagnostic.json').exists()
    errors = set()
    latest, candidates = shadow.discover(runtime.archives, errors)
    assert not errors and candidates[DAY] == [latest]
    assert runtime.old.name not in sum(candidates.values(), [])


@pytest.mark.parametrize('field,value', [('error_code', 'SOURCE_HTTP_ERROR'),
                                         ('queries_completed', 0), ('queries_completed', 2)])
def test_unproven_original_failure_cannot_authorize_repair(runtime, field, value):
    failed = repair.read(runtime.job/'failed.json')
    failed[field] = value
    write(runtime.job/'failed.json', resign(failed))
    with pytest.raises(ValueError):
        runtime.plan()
    assert runtime.calls == []


def test_plan_hash_and_changed_origin_are_checked_before_requests(runtime):
    value = runtime.plan()
    with pytest.raises(ValueError, match='REVIEW_OR_CONFIGURATION'):
        repair.apply(runtime.base, runtime.local, value, 'f'*64)
    failed = repair.read(runtime.job/'failed.json')
    failed['at'] = '2026-10-27T08:34:06+00:00'
    write(runtime.job/'failed.json', resign(failed))
    with pytest.raises(ValueError, match='PLAN_CHANGED'):
        runtime.apply(value)
    assert runtime.calls == []


def test_collection_priority_stops_burst_and_never_resumes(runtime, monkeypatch):
    calls = [0]

    def gate(*args, **kwargs):
        calls[0] += 1
        return repair.FAILURE if calls[0] == 3 else None

    monkeypatch.setattr(repair, 'source_gate', gate)
    value = runtime.plan()
    with pytest.raises(ValueError, match=repair.FAILURE):
        runtime.apply(value)
    assert len(runtime.calls) == 1
    with pytest.raises(ValueError, match='ALREADY_ATTEMPTED'):
        runtime.apply(value)
    assert len(runtime.calls) == 1
    assert not (runtime.old/'repair-resolution.json').exists()


def test_crash_after_reservation_is_uncertain_and_never_refetched(runtime, monkeypatch):
    def interrupted(*args):
        raise OSError('test-only-network-interruption')

    monkeypatch.setattr(repair, 'fetch', interrupted)
    value = runtime.plan()
    with pytest.raises(OSError):
        runtime.apply(value)
    new = runtime.archives/value['repair_diagnostic_id']
    assert len(repair.receipts(new/'repair-requests')) == 1
    assert not repair.receipts(new/'repair-receipts')
    with pytest.raises(ValueError, match='ALREADY_ATTEMPTED'):
        runtime.apply(value)
    assert not (runtime.old/'repair-resolution.json').exists()


def test_failed_http_response_is_durable_and_sets_real_cooldown(runtime, monkeypatch):
    original = repair.fetch

    def unavailable(*args):
        result = original(*args)
        result['status_code'] = 429
        return result

    monkeypatch.setattr(repair, 'fetch', unavailable)
    value = runtime.plan()
    with pytest.raises(ValueError, match='SOURCE_UNHEALTHY'):
        runtime.apply(value)
    assert len(runtime.calls) == 1
    backoff = repair.read(runtime.local/'source-backoff.json')
    assert backoff['reason'] == 'ETF_SOURCE_RESPONSE_FAILED'
    new = runtime.archives/value['repair_diagnostic_id']
    assert len(repair.receipts(new/'repair-receipts')) == 1
    assert not (new/'repair-completed.json').exists()


def test_third_party_pointer_update_is_not_overwritten(runtime, monkeypatch):
    original = repair.fetch

    def changing_pointer(*args):
        value = original(*args)
        write(runtime.archives/'latest.json', signed({**AUTHORITY, 'diagnostic_id': 'd'*64}))
        return value

    monkeypatch.setattr(repair, 'fetch', changing_pointer)
    with pytest.raises(ValueError, match='PUBLICATION_CAS_CHANGED'):
        runtime.apply()
    assert repair.read(runtime.archives/'latest.json')['diagnostic_id'] == 'd'*64
    assert not (runtime.old/'repair-resolution.json').exists()


@pytest.mark.parametrize('damage', [
    'old_receipt', 'completion', 'raw', 'reservation', 'resolution'])
def test_resolution_replays_all_lineage_and_corruption_stays_degraded(runtime, damage):
    value = runtime.plan()
    runtime.apply(value)
    new = runtime.archives/value['repair_diagnostic_id']
    path = {'old_receipt': runtime.old/'source-receipts/01.json',
        'completion': new/'repair-completed.json', 'raw': new/'receipt.json',
        'reservation': new/'repair-requests/01.json',
        'resolution': runtime.old/'repair-resolution.json'}[damage]
    document = repair.read(path)
    document['unexpected_change'] = True
    write(path, resign(document))
    with pytest.raises(ValueError):
        repair.verify_resolution(runtime.old, runtime.cal)
    errors = set()
    shadow.discover(runtime.archives, errors)
    assert 'ETF_SHADOW_ARCHIVED_SOURCE_INVALID' in errors


def test_unresolved_partial_is_not_silently_ignored(runtime):
    errors = set()
    shadow.discover(runtime.archives, errors)
    assert errors == {'ETF_SHADOW_ARCHIVED_SOURCE_INVALID'}


def test_another_revision_cannot_reissue_uncertain_budget(runtime, monkeypatch):
    def interrupted(*args):
        raise OSError('test-only-uncertain')

    monkeypatch.setattr(repair, 'fetch', interrupted)
    with pytest.raises(OSError):
        runtime.apply()
    with pytest.raises(ValueError, match='ALREADY_ATTEMPTED'):
        repair.plan(runtime.base, runtime.local, DAY, 'c'*40)


def test_deadline_consumes_no_further_reservations(runtime, monkeypatch):
    original = repair.fetch

    def slow(*args):
        response = original(*args)
        runtime.clock[0] += timedelta(seconds=901)
        return response

    monkeypatch.setattr(repair, 'fetch', slow)
    value = runtime.plan()
    with pytest.raises(TimeoutError, match='DEADLINE'):
        runtime.apply(value)
    new = runtime.archives/value['repair_diagnostic_id']
    assert len(runtime.calls) == 1 and len(repair.receipts(new/'repair-requests')) == 1
    assert not (runtime.old/'repair-resolution.json').exists()


def test_real_source_gate_reads_existing_collector_ledger_without_writes(runtime, monkeypatch):
    database = runtime.local.parent/'unified-data.sqlite'
    with closing(sqlite3.connect(database)) as connection:
        connection.execute('CREATE TABLE collection_tasks '
            '(universe_version TEXT,frequency TEXT,trading_day TEXT,state TEXT,last_error TEXT,'
            'next_attempt_at TEXT,desired_end TEXT,completed_end TEXT)')
        connection.execute('INSERT INTO collection_tasks VALUES (?,?,?,?,?,?,?,?)',
            ('c'*64, 'day', DAY, 'SUCCEEDED', None, NOW.isoformat(),
             DAY+'T15:00:00+08:00', DAY+'T15:00:00+08:00'))
        connection.commit()
    write(runtime.base/'unified-data/service-state.json', {'state': 'WAITING',
        'heartbeat_at': NOW.isoformat(), 'live_trading_enabled': False})
    write(runtime.base/'unified-data/collection-progress.json', signed({**AUTHORITY,
        'updated_at': NOW.isoformat(), 'state': 'PASS_FINISHED', 'universe_version': 'c'*64,
        'required_through': {'5min': DAY+'T15:00:00+08:00'}, 'tasks': []}))
    before = database.read_bytes()
    monkeypatch.setattr(repair, 'source_gate', daily.source_gate)
    monkeypatch.setattr(daily.shutil, 'disk_usage', lambda path: SimpleNamespace(free=200*1024**3))
    runtime.apply()
    assert len(runtime.calls) == 29 and database.read_bytes() == before


def test_active_collection_gate_does_not_claim_or_issue_repair(runtime, monkeypatch):
    monkeypatch.setattr(repair, 'source_gate', lambda *args, **kwargs:
                        'ETF_SERVICE_WAITING_CURRENT_COLLECTION')
    with pytest.raises(ValueError, match='WAITING_CURRENT_COLLECTION'):
        runtime.apply()
    assert runtime.calls == [] and not (runtime.old/'repair-claim.json').exists()


@pytest.mark.parametrize('seconds', [0, -1, 901, True])
def test_deadline_must_be_explicitly_bounded(runtime, seconds):
    value = runtime.plan()
    with pytest.raises(ValueError, match='REVIEW_OR_CONFIGURATION'):
        repair.apply(runtime.base, runtime.local, value, value['evidence_sha256'],
                     deadline_seconds=seconds)
    assert runtime.calls == []


def source_manifest(runtime, monkeypatch):
    release = runtime.root/'releases'/NEW
    for name, body in {'src/qtrade/example.py': b'x = 1\n',
                       'scripts/example.py': b'x = 2\n',
                       'configs/observer_calendar_2026.json': (
                           repair.REPO/'configs/observer_calendar_2026.json').read_bytes()}.items():
        path = release/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
    files = {}
    for path in release.rglob('*'):
        if path.is_file():
            payload = path.read_bytes()
            files[str(path.relative_to(release)).replace('\\', '/')] = sha1(
                b'blob '+str(len(payload)).encode()+b'\0'+payload).hexdigest()
    manifest = signed({**AUTHORITY, 'schema_version': 'qtrade.etf-repair-source-manifest.v1',
                       'revision': NEW, 'files': files})
    path = runtime.root/'source-manifest.json'
    write(path, manifest)
    monkeypatch.setattr(repair, 'REPO', release)
    return path, release


def test_gitless_manifest_is_bound_into_plan_and_replayed_archive(runtime, monkeypatch):
    manifest, _ = source_manifest(runtime, monkeypatch)
    value = repair.plan(runtime.base, runtime.local, DAY, NEW, source_manifest=manifest)
    assert value['source_verification']['manifest_file_sha256'] == file_hash(manifest)
    repair.apply(runtime.base, runtime.local, value, value['evidence_sha256'],
                 source_manifest=manifest)
    diagnostic = repair.verify_resolution(runtime.old, runtime.cal)
    assert diagnostic['qualified_close_symbols'] == 30 and len(runtime.calls) == 29


@pytest.mark.parametrize('damage', ['blob', 'extra', 'missing', 'escape', 'manifest_revision'])
def test_gitless_manifest_rejects_source_changes(runtime, monkeypatch, damage):
    manifest, release = source_manifest(runtime, monkeypatch)
    if damage == 'blob':
        (release/'src/qtrade/example.py').write_bytes(b'x = 0\n')
    elif damage == 'extra':
        (release/'src/unreviewed.py').write_bytes(b'x = 0\n')
    elif damage == 'missing':
        (release/'src/qtrade/example.py').unlink()
    else:
        value = repair.read(manifest)
        if damage == 'escape':
            value['files']['src/../../outside.py'] = 'a'*40
        else:
            value['revision'] = OLD
        write(manifest, resign(value))
    with pytest.raises(ValueError):
        repair.plan(runtime.base, runtime.local, DAY, NEW, source_manifest=manifest)
    assert runtime.calls == []


def test_gitless_manifest_byte_changes_invalidate_reviewed_plan(runtime, monkeypatch):
    manifest, _ = source_manifest(runtime, monkeypatch)
    value = repair.plan(runtime.base, runtime.local, DAY, NEW, source_manifest=manifest)
    manifest.write_text(json.dumps(repair.read(manifest), indent=2))
    with pytest.raises(ValueError, match='SOURCE_PROVENANCE_CHANGED'):
        repair.apply(runtime.base, runtime.local, value, value['evidence_sha256'],
                     source_manifest=manifest)
    assert runtime.calls == []


@pytest.mark.parametrize('boundary', [
    'diagnostic', 'repair_completion', 'latest', 'local_completion', 'resolution'])
def test_incomplete_repair_is_never_discovered_or_admitted(runtime, monkeypatch, boundary):
    value = runtime.plan()
    new = runtime.archives/value['repair_diagnostic_id']
    original_immutable, original_atomic = repair.write_json_immutable, repair.write_json_atomic

    def fail_immutable(path, document):
        selected = {'diagnostic': new/'diagnostic.json',
            'repair_completion': new/'repair-completed.json',
            'local_completion': runtime.local/'repairs'/value['evidence_sha256']/'completed.json',
            'resolution': runtime.old/'repair-resolution.json'}.get(boundary)
        if path == selected:
            raise OSError('test-only-publication-boundary')
        return original_immutable(path, document)

    def fail_atomic(path, document):
        if boundary == 'latest' and path == runtime.archives/'latest.json':
            raise OSError('test-only-latest-boundary')
        return original_atomic(path, document)

    monkeypatch.setattr(repair, 'write_json_immutable', fail_immutable)
    monkeypatch.setattr(repair, 'write_json_atomic', fail_atomic)
    with pytest.raises(OSError):
        runtime.apply(value)
    assert len(runtime.calls) == 29
    errors = set()
    _, candidates = shadow.discover(runtime.archives, errors)
    assert new.name not in sum(candidates.values(), [])
    assert 'ETF_SHADOW_ARCHIVED_SOURCE_INVALID' in errors
    with pytest.raises((ValueError, FileNotFoundError)):
        shadow.source(new, runtime.cal, NOW)
    assert not (runtime.old/'repair-resolution.json').exists()


def test_deleted_repair_plan_cannot_turn_repair_into_ordinary_source(runtime):
    value = runtime.plan()
    runtime.apply(value)
    new = runtime.archives/value['repair_diagnostic_id']
    (new/'repair-plan.json').unlink()
    with pytest.raises((ValueError, FileNotFoundError)):
        shadow.source(new, runtime.cal, NOW)


def test_git_mode_rejects_untracked_importable_python(runtime, monkeypatch):
    _, release = source_manifest(runtime, monkeypatch)
    for name in ('src/qtrade/research/etf_attempt_repair.py',
                 'scripts/repair_etf_daily_attempt.py', 'scripts/run_etf_shadow_service.py'):
        path = release/name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text('"""Synthetic Git inventory fixture."""\n')
    for command in (['git', 'init', '-q'], ['git', 'config', 'core.autocrlf', 'false'],
            ['git', 'add', '.'],
            ['git', '-c', 'user.name=Repair Test', '-c', 'user.email=repair@example.invalid',
             'commit', '-qm', 'Synthetic inventory fixture']):
        subprocess.run(command, cwd=release, check=True, capture_output=True)
    revision = subprocess.run(['git', 'rev-parse', 'HEAD'], cwd=release, check=True,
                              text=True, capture_output=True).stdout.strip()
    CHECK_REVISION(revision)
    (release/'src/qtrade/unreviewed.py').write_text('x = 1\n')
    with pytest.raises(ValueError, match='SOURCE_INVENTORY_CHANGED'):
        CHECK_REVISION(revision)


def test_every_created_directory_entry_is_flushed_before_source_work(runtime, monkeypatch):
    flushed, descriptors = [], {}

    def opened(path, flags):
        descriptor = len(descriptors)+10
        descriptors[descriptor] = Path(path)
        return descriptor

    monkeypatch.setattr(repair.os, 'open', opened)
    monkeypatch.setattr(repair.os, 'fsync', lambda descriptor:
                        flushed.append(descriptors[descriptor]))
    monkeypatch.setattr(repair.os, 'close', lambda descriptor: None)
    monkeypatch.setattr(repair, '_durable_directory', DURABLE_DIRECTORY)
    target = runtime.root/'new/a/b'
    DURABLE_DIRECTORY(target)
    assert target.is_dir()
    assert set(flushed) == {runtime.root, runtime.root/'new', runtime.root/'new/a', target}
