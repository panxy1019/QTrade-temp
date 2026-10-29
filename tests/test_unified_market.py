from __future__ import annotations

import io
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import ROUND_DOWN, Context, Decimal, Inexact, getcontext, localcontext
from email.message import Message
from hashlib import sha256
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from qtrade.api import unified_market as api
from qtrade.api import unified_source as public_source
from qtrade.data import Bar
from qtrade.instruments.observed import build_observed_master
from qtrade.market_data.akshare import SHANGHAI
from qtrade.market_data.sina_incremental import IncrementalBar
from qtrade.market_data.sina_minute import SINA_FIVE_MINUTE_SESSION_TIMES
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research import unified_market as contract
from scripts import publish_unified_market as exporter
from scripts import unified_store as store_module
from scripts.unified_store import FIELDS, UnifiedBatchStore, canonical

NOW = datetime(2026, 10, 26, 10, tzinfo=UTC)
REVISION = 'c'*40


def resign(value):
    return signed({key: item for key, item in value.items() if key != 'evidence_sha256'})


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setenv('LIVE_TRADING_ENABLED', 'false')
    exporter._CACHE.clear()
    if os.name == 'nt':
        monkeypatch.setattr('scripts.unified_store._fsync_directory', lambda _: None)
        monkeypatch.setattr(exporter, '_fsync_directory', lambda _: None)
        from scripts.atomic_json import write_json_immutable
        original = write_json_immutable
        def write(path, value):
            try:
                original(path, value)
            except PermissionError:
                assert json.loads(path.read_bytes()) == value
        monkeypatch.setattr('scripts.unified_store.write_json_immutable', write)
    def atomic(path, value, **kwargs):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(contract.encoded(value))
    monkeypatch.setattr(exporter, 'write_json_atomic', atomic)
    master = build_observed_master(vendor_rows=[
        {'code': 'sh.600036', 'code_name': 'bank A', 'type': '1', 'status': '1'},
        {'code': 'sz.301001', 'code_name': 'bank B', 'type': '1', 'status': '1'}],
        vendor_sha256='1'*64, source_groups=[{'source_id': 'fixture', 'role': 'candidate',
            'sha256': '2'*64, 'rows': [{'symbol': '600036', 'security_type': 'STOCK'},
                                     {'symbol': '301001', 'security_type': 'STOCK'}]}],
        observed_at=NOW, target_count=2)
    for row in master['securities']:
        row['frequencies'] = {freq: {'state': 'PLANNED', 'planned': True}
                              for freq in ('day', '5min')}
    calendar = Calendar(Path('configs/observer_calendar_2026.json'))
    master.update(frequency_counts={freq: {'PLANNED': 2} for freq in ('day', '5min')},
                  collection={'calendar': calendar.document})
    master = resign(master)
    path = tmp_path/'unified-data/public/unified.json'
    atomic(path, signed({**AUTHORITY, 'schema_version': 'qtrade.unified-catalog.v1',
                        'built_at': NOW.isoformat(), 'master': master}))
    database = tmp_path/'collection.sqlite'
    with sqlite3.connect(database) as connection:
        connection.executescript('''
            CREATE TABLE collection_chunks(batch_id TEXT,universe_version TEXT,frequency TEXT,
                symbol TEXT,start_time TEXT,end_time TEXT,bar_count INTEGER);
            CREATE TABLE collection_batches(batch_id TEXT,state TEXT,manifest_sha256 TEXT);
            CREATE TABLE excluded_collection_chunks(batch_id TEXT,symbol TEXT);
        ''')
    store = UnifiedBatchStore(tmp_path/'unified-data/lake')
    def add(freq='5min', *, symbol='301001', close='10', counter=1, hour=9, minute=35,
            receipt_at=NOW, complete=True, status=200, scale=None):
        stamp = datetime(2026, 10, 26, 15 if freq == 'day' else hour,
                         0 if freq == 'day' else minute, tzinfo=SHANGHAI)
        bar = Bar(symbol, 'SSE' if symbol.startswith('6') else 'SZSE', stamp, stamp.date(),
                  Decimal('10'), Decimal('12'), Decimal('9'), Decimal(close), Decimal('100'))
        captures = [{'source_id': 'sina_klines', 'received_at': receipt_at.isoformat(),
            'status_code': status, 'transport_error': None, 'body_complete': complete,
            'request': {'symbol': ('sh' if symbol.startswith('6') else 'sz')+symbol,
                        'scale': scale or ('240' if freq == 'day' else '5'),
                        'ma': 'no', 'datalen': '2'},
            'body': json.dumps([{'day': str(stamp.date()) if freq == 'day' else
                stamp.replace(tzinfo=None).isoformat(sep=' '), 'open': '10', 'high': '12',
                'low': '9', 'close': close, 'volume': '100'}]).encode()}]
        if freq == 'day':
            fields = ['0']*34
            for index, value in {0: 'bank', 1: '10', 3: close, 4: '12', 5: '9',
                                 8: '100', 9: '1000', 30: '2026-10-26', 31: '15:00:00'}.items():
                fields[index] = value
            captures.append({'source_id': 'sina_quote', 'received_at': NOW.isoformat(),
                'status_code': 200, 'transport_error': None, 'body_complete': True,
                'request': {'list': ('sh' if symbol.startswith('6') else 'sz')+symbol},
                'body': ('var hq_str_'+('sh' if symbol.startswith('6') else 'sz')+symbol
                         +'="'+','.join(fields)+'";').encode()})
        raw = store.capture(captures)
        spec = {'frequency': freq, 'target_end': stamp.isoformat(),
            'universe_version': master['universe_version'], 'symbols': [symbol],
            'code_revision': '3'*40, 'after_by_symbol': {symbol: None if freq == 'day' else
                (stamp-timedelta(minutes=5)).isoformat()}}
        batch_id = f'{counter:032x}'
        record = IncrementalBar(bar, freq, Decimal('1000') if freq == 'day' else None,
            Decimal('10') if freq == 'day' else None, stamp if freq == 'day' else None,
            'SAME_VENDOR_ENDPOINT_MATCH_NOT_INDEPENDENT_RECONCILIATION'
            if freq == 'day' else 'VENDOR_CLAIM_UNVERIFIED')
        manifest = store.commit(batch_id, spec, raw, [record], {symbol: {'state': 'SUCCEEDED'}},
                                completed_at=NOW)
        with sqlite3.connect(database) as connection:
            connection.execute('INSERT INTO collection_batches VALUES(?,?,?)',
                               (batch_id, 'COMPLETE', manifest['evidence_sha256']))
            connection.execute('INSERT INTO collection_chunks VALUES(?,?,?,?,?,?,?)',
                (batch_id, master['universe_version'], freq, symbol,
                 stamp.astimezone(UTC).isoformat(), stamp.astimezone(UTC).isoformat(), 1))
        return manifest
    return tmp_path, database, calendar, master, store, add


def export(source):
    base, database, *_ = source
    return exporter.export_market(base, database, now=NOW)


def test_export_all_targets_without_queue_writer_and_cached_replay(source):
    base, database, _, _, store, add = source
    add()
    add('day', counter=2)
    before = database.read_bytes()
    files = {str(path): path.read_bytes() for path in store.root.rglob('*') if path.is_file()}
    first = export(source)
    assert len(first['index']['entries']) == 4
    assert len([row for row in first['index']['entries'] if row['state'] == 'NO_DATA']) == 2
    assert first['statistics']['replayed_batches'] == 2
    second = export(source)
    assert second['statistics']['cached_batches'] == 2
    assert second['statistics']['replayed_batches'] == 0
    assert first['index'] == second['index']
    assert database.read_bytes() == before
    assert files == {str(path): path.read_bytes() for path in store.root.rglob('*')
                     if path.is_file()}
    for entry in first['index']['entries']:
        for ref in entry['shards']:
            path = base/'unified-data/market-public/unified-market/objects'/(ref['sha256']+'.json')
            shard = contract.verify_shard_bytes(path.read_bytes(), ref, first['index'], entry, NOW)
            if entry['frequency'] == '5min':
                assert shard['bars'][0]['amount'] is None and shard['bars'][0]['vwap'] is None


def test_immutable_object_io_is_bounded_and_existing_bytes_are_rehashed(tmp_path, monkeypatch):
    objects = {sha256(raw).hexdigest(): raw for index in range(exporter.MAX_OBJECT_IO*3)
               for raw in [contract.encoded({'object': index})]}
    barrier, lock = threading.Barrier(exporter.MAX_OBJECT_IO), threading.Lock()
    active, peak, calls = 0, 0, []
    flushed = []
    monkeypatch.setattr(exporter, '_fsync_directory', flushed.append)
    def write(path, raw, **kwargs):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(active, peak)
            calls.append(path.name)
        try:
            barrier.wait(timeout=5)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(raw)
        finally:
            with lock:
                active -= 1
    monkeypatch.setattr(exporter, '_write_object', write)
    exporter._persist_objects(tmp_path, objects.items(), time.monotonic()+10)
    assert peak == exporter.MAX_OBJECT_IO == 16 and active == 0 and len(calls) == len(objects)
    assert sorted(calls) == sorted(digest+'.json' for digest in objects)
    directory = tmp_path/'unified-market/objects'
    expected_flushes = [directory, directory.parent, tmp_path, tmp_path.parent]
    assert flushed == expected_flushes
    def forbidden(*args):
        raise AssertionError('EXISTING_IMMUTABLE_OBJECT_MUST_NOT_BE_REWRITTEN')
    monkeypatch.setattr(exporter, '_write_object', forbidden)
    exporter._persist_objects(tmp_path, objects.items(), time.monotonic()+10)
    assert flushed == expected_flushes*2
    digest = next(iter(objects))
    path = tmp_path/'unified-market/objects'/(digest+'.json')
    path.write_bytes(b'changed')
    with pytest.raises(ValueError, match='UNIFIED_MARKET_IMMUTABLE_OBJECT_CHANGED'):
        exporter._persist_objects(tmp_path, objects.items(), time.monotonic()+10)
    assert path.read_bytes() == b'changed'
    assert not any(thread.name.startswith('market-object') for thread in threading.enumerate())


def test_object_file_fsync_is_required_even_when_directory_sync_is_deferred(tmp_path, monkeypatch):
    file_syncs, directory_syncs = [], []
    monkeypatch.setattr(exporter.os, 'fsync', file_syncs.append)
    monkeypatch.setattr(exporter, '_fsync_directory', directory_syncs.append)
    exporter._write_object(tmp_path/'deferred.json', b'deferred', _sync_directory=False)
    assert len(file_syncs) == 1 and directory_syncs == []
    exporter._write_object(tmp_path/'standalone.json', b'standalone')
    assert len(file_syncs) == 2 and directory_syncs == [tmp_path]
    assert (tmp_path/'deferred.json').read_bytes() == b'deferred'
    assert (tmp_path/'standalone.json').read_bytes() == b'standalone'


@pytest.mark.parametrize('failure', ['io', 'deadline'])
def test_object_io_stops_submission_and_joins_bounded_inflight_writes(
    tmp_path, monkeypatch, failure,
):
    objects = {sha256(raw).hexdigest(): raw for index in range(exporter.MAX_OBJECT_IO*2)
               for raw in [contract.encoded({'object': index})]}
    calls, lock, release = [], threading.Lock(), threading.Event()
    def write(path, raw, **kwargs):
        with lock:
            calls.append(path.name)
        if failure == 'io':
            raise OSError('immutable object storage unavailable')
        assert release.wait(timeout=5)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
    monkeypatch.setattr(exporter, '_write_object', write)
    timer = threading.Timer(0.1, release.set)
    timer.start()
    try:
        with pytest.raises(OSError if failure == 'io' else ValueError,
                           match='storage unavailable|UNIFIED_MARKET_TIME_BUDGET'):
            exporter._persist_objects(tmp_path, objects.items(),
                                      time.monotonic()+(10 if failure == 'io' else 0.02))
    finally:
        release.set()
        timer.join()
    assert 1 <= len(calls) <= exporter.MAX_OBJECT_IO
    assert not any(thread.name.startswith('market-object') for thread in threading.enumerate())


def test_object_io_failure_retains_previous_index_and_export_state(source, monkeypatch):
    base, _, _, _, _, add = source
    add()
    export(source)
    pointer = base/'unified-data/market-public/unified-market.json'
    state = base/'unified-data/market-export-state.json'
    before = pointer.read_bytes(), state.read_bytes(), pointer.stat().st_mtime_ns
    add(counter=2, minute=40)
    def fail(path, raw, **kwargs):
        raise OSError('immutable object storage unavailable')
    monkeypatch.setattr(exporter, '_write_object', fail)
    with pytest.raises(OSError, match='storage unavailable'):
        export(source)
    assert (pointer.read_bytes(), state.read_bytes(), pointer.stat().st_mtime_ns) == before
    assert not any(thread.name.startswith('market-object') for thread in threading.enumerate())


@pytest.mark.parametrize('gate', ['ledger', 'index'])
def test_post_write_gate_failure_leaves_only_unreferenced_objects_and_old_index(
    source, monkeypatch, gate,
):
    base, database, _, _, _, add = source
    add()
    export(source)
    pointer = base/'unified-data/market-public/unified-market.json'
    state = base/'unified-data/market-export-state.json'
    before = pointer.read_bytes(), state.read_bytes(), pointer.stat().st_mtime_ns
    manifest = add('day', counter=2)
    original, written = exporter._write_object, []
    def write(path, raw, **kwargs):
        original(path, raw, **kwargs)
        written.append(path)
        if gate == 'ledger':
            with sqlite3.connect(database) as connection:
                connection.execute('INSERT INTO excluded_collection_chunks VALUES(?,?)',
                                   (manifest['batch_id'], '301001'))
    monkeypatch.setattr(exporter, '_write_object', write)
    if gate == 'index':
        def reject(*args):
            raise ValueError('UNIFIED_MARKET_INDEX_SCOPE_INVALID')
        monkeypatch.setattr(exporter, 'validate_index', reject)
    with pytest.raises(ValueError, match='UNIFIED_MARKET_LEDGER_OR_MASTER_CHANGED|'
                                        'UNIFIED_MARKET_INDEX_SCOPE_INVALID'):
        export(source)
    assert written and all(path.is_file() for path in written)
    old = json.loads(before[0])
    referenced = {ref['sha256']+'.json' for entry in old['entries'] for ref in entry['shards']}
    assert all(path.name not in referenced for path in written)
    assert (pointer.read_bytes(), state.read_bytes(), pointer.stat().st_mtime_ns) == before
    assert not any(thread.name.startswith('market-object') for thread in threading.enumerate())


def test_directory_sync_failure_blocks_index_and_retry_syncs_all_existing_objects(
    source, monkeypatch,
):
    base, _, _, _, _, add = source
    add()
    export(source)
    pointer = base/'unified-data/market-public/unified-market.json'
    state = base/'unified-data/market-export-state.json'
    before = pointer.read_bytes(), state.read_bytes(), pointer.stat().st_mtime_ns
    add('day', counter=2)
    def fail(path):
        raise OSError('immutable object directory sync unavailable')
    monkeypatch.setattr(exporter, '_fsync_directory', fail)
    with pytest.raises(OSError, match='directory sync unavailable'):
        export(source)
    assert (pointer.read_bytes(), state.read_bytes(), pointer.stat().st_mtime_ns) == before
    flushed = []
    monkeypatch.setattr(exporter, '_fsync_directory', flushed.append)
    def forbidden(*args, **kwargs):
        raise AssertionError('RETRY_MUST_REUSE_ALREADY_WRITTEN_IMMUTABLE_OBJECTS')
    monkeypatch.setattr(exporter, '_write_object', forbidden)
    result = export(source)
    directory = base/'unified-data/market-public/unified-market/objects'
    assert flushed == [directory, directory.parent, directory.parent.parent,
                       directory.parent.parent.parent]
    assert result['statistics']['total_files'] == 2
    assert pointer.read_bytes() != before[0]


@pytest.mark.parametrize('damage', ['late_raw_failure', 'late_conflict'])
def test_pair_is_not_written_before_its_last_frozen_batch_is_qualified(source, monkeypatch, damage):
    _, _, _, _, store, add = source
    add(counter=1)
    add(symbol='600036', counter=2)
    late = add(counter=3, minute=35 if damage == 'late_conflict' else 40,
               close='11' if damage == 'late_conflict' else '10')
    if damage == 'late_raw_failure':
        store._blob_path(late['raw']['sha256'], late['raw']['suffix']).write_bytes(b'corrupt')
    original, written = exporter._write_object, []
    def write(path, raw, **kwargs):
        written.append(json.loads(raw)['symbol'])
        original(path, raw, **kwargs)
    monkeypatch.setattr(exporter, '_write_object', write)
    result = export(source)['index']
    affected = next(row for row in result['entries']
                    if row['symbol'] == '301001' and row['frequency'] == '5min')
    assert affected['state'] == 'QUARANTINED' and affected['shards'] == []
    assert affected['issues'] == (['SOURCE_REPLAY_FAILED'] if damage == 'late_raw_failure'
                                  else ['CONFLICTING_ACCEPTED_BARS'])
    assert written == ['600036']


def test_entire_batch_failure_is_marked_before_any_selected_pair_can_seal(source, monkeypatch):
    _, database, _, _, _, _ = source
    material = grouped_replay_material(source, '5min')
    manifest = material.manifest
    with sqlite3.connect(database) as connection:
        connection.execute('DELETE FROM collection_chunks')
        connection.execute('DELETE FROM collection_batches')
        connection.execute('INSERT INTO collection_batches VALUES(?,?,?)',
                           (manifest['batch_id'], 'COMPLETE', manifest['evidence_sha256']))
        connection.executemany('INSERT INTO collection_chunks VALUES(?,?,?,?,?,?,?)', [
            (manifest['batch_id'], manifest['spec']['universe_version'], '5min', symbol,
             start, '2026-10-26T09:40:00+08:00', 2) for symbol, start in
            [('301001', '2026-10-26T09:35:00+08:00'),
             ('600036', '2026-10-26T09:40:00+08:00')]])
    def forbidden(*args, **kwargs):
        raise AssertionError('NO_PAIR_FROM_PARTIALLY_FAILED_BATCH_MAY_SEAL')
    monkeypatch.setattr(exporter, '_write_object', forbidden)
    value = export(source)['index']
    assert len(value['entries']) == 4
    minute = [row for row in value['entries'] if row['frequency'] == '5min']
    assert all(row['state'] == 'QUARANTINED' and row['shards'] == []
               and row['issues'] == ['SOURCE_REPLAY_FAILED'] for row in minute)


def test_pair_completion_counts_every_repeated_selected_chunk_without_deduplication(source):
    base, database, _, _, _, add = source
    add()
    with sqlite3.connect(database) as connection:
        connection.execute('INSERT INTO collection_chunks SELECT * FROM collection_chunks')
    result = export(source)
    assert result['statistics']['selected_chunks'] == 2
    assert result['statistics']['selected_batches'] == 1
    entry = next(row for row in result['index']['entries']
                 if row['symbol'] == '301001' and row['frequency'] == '5min')
    assert entry['state'] == 'AVAILABLE' and len(entry['shards']) == 1
    ref, = entry['shards']
    raw = (base/'unified-data/market-public/unified-market/objects'
           /(ref['sha256']+'.json')).read_bytes()
    assert len(json.loads(raw)['bars']) == len(json.loads(raw)['source_batches']) == 1


def test_source_prefetch_overlaps_one_read_and_keeps_replay_on_main(source, monkeypatch):
    _, _, _, _, _, add = source
    add()
    add(counter=2, minute=40)
    add(symbol='600036', counter=3)
    main_thread = threading.get_ident()
    started, release = threading.Event(), threading.Event()
    active, peak, reads, processed = 0, 0, [], []
    lock = threading.Lock()
    original_material, original_load, original_replay = (
        exporter._SourceReads.material, exporter._load, exporter._replay)
    def material(self, store, batch_id):
        nonlocal active, peak
        assert threading.get_ident() != main_thread
        with lock:
            active += 1
            peak = max(peak, active)
            reads.append(batch_id)
        try:
            if batch_id == f'{2:032x}':
                started.set()
                assert release.wait(timeout=5)
            return original_material(self, store, batch_id)
        finally:
            with lock:
                active -= 1
    def load(*args, **kwargs):
        assert threading.get_ident() == main_thread
        if not processed:
            assert started.wait(timeout=5), 'next physical read must overlap current validation'
            release.set()
        processed.append(args[1])
        return original_load(*args, **kwargs)
    def replay(*args, **kwargs):
        assert threading.get_ident() == main_thread
        return original_replay(*args, **kwargs)
    monkeypatch.setattr(exporter._SourceReads, 'material', material)
    monkeypatch.setattr(exporter, '_load', load)
    monkeypatch.setattr(exporter, '_replay', replay)
    try:
        result = export(source)
    finally:
        release.set()
    assert peak == 1 and active == 0 and len(reads) == len(processed) == 3
    assert result['statistics']['replayed_batches'] == 3
    assert not any(thread.name.startswith('market-source') for thread in threading.enumerate())


@pytest.mark.parametrize('failure', [ValueError, OSError])
def test_prefetched_physical_errors_keep_batch_classification_and_no_lingering_read(
    source, monkeypatch, failure,
):
    base, _, _, _, _, add = source
    add()
    export(source)
    pointer = base/'unified-data/market-public/unified-market.json'
    state = base/'unified-data/market-export-state.json'
    before = pointer.read_bytes(), state.read_bytes()
    add(counter=2, minute=40)
    original = store_module.UnifiedBatchStore._batch_material
    def material(self, batch_id, **kwargs):
        if batch_id == f'{2:032x}':
            raise failure('PREFETCHED_PHYSICAL_SOURCE_FAULT')
        return original(self, batch_id, **kwargs)
    monkeypatch.setattr(store_module.UnifiedBatchStore, '_batch_material', material)
    if failure is OSError:
        with pytest.raises(OSError, match='^PREFETCHED_PHYSICAL_SOURCE_FAULT$'):
            export(source)
        assert (pointer.read_bytes(), state.read_bytes()) == before
    else:
        entry = next(row for row in export(source)['index']['entries']
                     if row['symbol'] == '301001' and row['frequency'] == '5min')
        assert entry['state'] == 'QUARANTINED' and entry['shards'] == []
        assert entry['issues'] == ['SOURCE_REPLAY_FAILED']
    assert not any(thread.name.startswith('market-source') for thread in threading.enumerate())


def test_prefetch_source_byte_gate_counts_all_reads_at_exact_boundary(source, monkeypatch):
    base, _, _, _, _, add = source
    add()
    add(counter=2, minute=40)
    add('day', counter=3)
    expected = export(source)['statistics']['source_bytes_read']
    pointer = base/'unified-data/market-public/unified-market.json'
    state = base/'unified-data/market-export-state.json'
    before = pointer.read_bytes(), state.read_bytes()
    exporter._CACHE.clear()
    monkeypatch.setattr(exporter, 'SOURCE_BYTE_BUDGET', expected)
    assert export(source)['statistics']['source_bytes_read'] == expected
    before = pointer.read_bytes(), state.read_bytes()
    exporter._CACHE.clear()
    monkeypatch.setattr(exporter, 'SOURCE_BYTE_BUDGET', expected-1)
    with pytest.raises(ValueError, match='^UNIFIED_MARKET_SOURCE_BYTE_BUDGET$'):
        export(source)
    assert (pointer.read_bytes(), state.read_bytes()) == before
    assert not any(thread.name.startswith('market-source') for thread in threading.enumerate())


def test_object_failure_explicitly_closes_and_joins_source_generator(tmp_path, monkeypatch):
    release, started = threading.Event(), threading.Event()
    closed = []
    def read():
        started.set()
        assert release.wait(timeout=5)
    def objects():
        try:
            with ThreadPoolExecutor(max_workers=1, thread_name_prefix='market-source') as pool:
                pending = pool.submit(read)
                try:
                    assert started.wait(timeout=5)
                    for index in range(exporter.MAX_OBJECT_IO*2):
                        raw = str(index).encode()
                        yield sha256(raw).hexdigest(), raw
                finally:
                    pending.cancel()
        finally:
            closed.append(True)
    def fail(*args, **kwargs):
        raise OSError('OBJECT_FAILURE_WITH_SOURCE_IN_FLIGHT')
    monkeypatch.setattr(exporter, '_write_object', fail)
    timer = threading.Timer(.1, release.set)
    timer.start()
    try:
        with pytest.raises(OSError, match='^OBJECT_FAILURE_WITH_SOURCE_IN_FLIGHT$'):
            exporter._persist_objects(tmp_path, objects(), time.monotonic()+5)
    finally:
        release.set()
        timer.join()
    assert closed == [True]
    assert not any(thread.name.startswith(('market-source', 'market-object'))
                   for thread in threading.enumerate())


@pytest.mark.parametrize('damage', ['manifest', 'raw_checksum', 'normalized_checksum', 'raw_parse'])
@pytest.mark.parametrize('unused_future', [False, True])
def test_failed_source_reads_are_charged_even_for_unused_prefetch(
    source, monkeypatch, damage, unused_future,
):
    _, _, _, _, store, add = source
    manifest = add()
    if damage == 'manifest':
        path = store.batch_path(manifest['batch_id'])
        path.write_bytes(b' '*100+b'{')
    elif damage in {'raw_checksum', 'normalized_checksum'}:
        ref = manifest['raw' if damage == 'raw_checksum' else 'normalized']
        path = store._blob_path(ref['sha256'], ref['suffix'])
        raw = path.read_bytes()
        path.write_bytes(b'x'+raw[1:])
    else:
        ref = store._put_blob(b'not gzip', '.jsonl.gz')
        manifest['raw'] = {**manifest['raw'], **ref}
        store.batch_path(manifest['batch_id']).write_bytes(contract.encoded(resign(manifest)))
    physical = []
    original_read = Path.read_bytes
    def read(path):
        raw = original_read(path)
        if store.root in path.parents:
            physical.append(len(raw))
        return raw
    monkeypatch.setattr(Path, 'read_bytes', read)
    stats = {'source_bytes_read': 0}
    accounting = exporter._SourceReads(stats)
    if unused_future:
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(accounting.material, store, manifest['batch_id'])
        if damage != 'raw_parse':
            assert isinstance(future.exception(), ValueError)
    else:
        with pytest.raises((ValueError, OSError)):
            accounting.material(store, manifest['batch_id']).validate()
    assert physical and stats['source_bytes_read'] == sum(physical)


@pytest.mark.parametrize('change', ['grow', 'short', 'oserror', 'missing', 'before_budget'])
def test_observed_read_reservation_releases_on_every_exit(tmp_path, monkeypatch, change):
    path = tmp_path/'actual-bytes'
    path.write_bytes(b'abcde')
    stats = {'source_bytes_read': 0}
    accounting = exporter._SourceReads(stats)
    monkeypatch.setattr(exporter, 'SOURCE_BYTE_BUDGET', 4 if change == 'before_budget' else 5)
    original, calls = Path.read_bytes, []
    def read(target):
        calls.append(target)
        if change == 'oserror':
            raise OSError('PHYSICAL_READ_FAILED')
        if change in {'grow', 'short'}:
            target.write_bytes(b'abcdef' if change == 'grow' else b'abc')
        return original(target)
    monkeypatch.setattr(Path, 'read_bytes', read)
    if change == 'missing':
        path.unlink()
    if change == 'short':
        assert store_module._read_bytes(path, accounting.observe) == b'abc'
        assert stats['source_bytes_read'] == 3
    else:
        with pytest.raises((ValueError, OSError)):
            store_module._read_bytes(path, accounting.observe)
        assert stats['source_bytes_read'] == (6 if change == 'grow' else 0)
    assert accounting._reserved == {}
    assert len(calls) == (0 if change in {'missing', 'before_budget'} else 1)


def test_prefetch_reservation_contention_aborts_before_pointer_and_accounts_joined_read(
    source, monkeypatch,
):
    base, _, _, _, store, add = source
    first = add()
    export(source)
    pointer = base/'unified-data/market-public/unified-market.json'
    state = base/'unified-data/market-export-state.json'
    before = pointer.read_bytes(), state.read_bytes()
    second = add(symbol='600036', counter=2)
    first_size = (store.batch_path(first['batch_id']).stat().st_size
                  +first['raw']['bytes']+first['normalized']['bytes'])
    limit = first_size+store.batch_path(first['batch_id']).stat().st_size+store.batch_path(
        second['batch_id']).stat().st_size-1
    monkeypatch.setattr(exporter, 'SOURCE_BYTE_BUDGET', limit)
    original_read, original_load, original_init = (
        Path.read_bytes, exporter._load, exporter._SourceReads.__init__)
    started, release = threading.Event(), threading.Event()
    physical, accounting = [], []
    def initialize(self, *args, **kwargs):
        original_init(self, *args, **kwargs)
        accounting.append(self)
    def read(path):
        if path == store.batch_path(second['batch_id']):
            started.set()
            assert release.wait(timeout=5)
        raw = original_read(path)
        if store.root in path.parents:
            physical.append(len(raw))
        return raw
    def load(*args, **kwargs):
        assert started.wait(timeout=5)
        return original_load(*args, **kwargs)
    monkeypatch.setattr(exporter._SourceReads, '__init__', initialize)
    monkeypatch.setattr(Path, 'read_bytes', read)
    monkeypatch.setattr(exporter, '_load', load)
    timer = threading.Timer(.2, release.set)
    timer.start()
    try:
        with pytest.raises(ValueError, match='^UNIFIED_MARKET_SOURCE_BYTE_BUDGET$'):
            export(source)
    finally:
        release.set()
        timer.join()
    assert accounting[-1]._stats['source_bytes_read'] == sum(physical)
    assert accounting[-1]._reserved == {} and sum(physical) <= limit
    assert (pointer.read_bytes(), state.read_bytes()) == before
    assert not any(thread.name.startswith('market-source') for thread in threading.enumerate())


def constructed_bar(**changes):
    return {'symbol': '301001', 'exchange': 'SZSE', 'frequency': '5min',
        'timestamp': '2026-10-26T09:35:00+08:00', 'trading_day': '2026-10-26',
        'open': '10', 'high': '12', 'low': '9', 'close': '10', 'volume': '100',
        'amount': None, 'vwap': None, 'amount_basis': 'VENDOR_CLAIM_UNVERIFIED',
        'price_basis': contract.PRICE_BASIS, 'historical_available_at': None, **changes}


def constructed_shard():
    return signed({**AUTHORITY, 'schema_version': 'qtrade.unified-market-shard.v1',
        'universe_version': '1'*64, 'symbol': '301001', 'security_type': 'STOCK',
        'instrument_id': 'SZSE.301001', 'frequency': '5min', 'price_basis': contract.PRICE_BASIS,
        'bars': [constructed_bar()], 'source_batches': [{'batch_id': '2'*32,
            'manifest_sha256': '3'*64, 'raw_sha256': '4'*64, 'normalized_sha256': '5'*64}]})


@pytest.mark.parametrize('foreign_decimal_context', [False, True])
def test_validated_shard_encoding_is_exact_at_size_boundary_and_preserves_context(
    monkeypatch, foreign_decimal_context,
):
    shard = constructed_shard()
    calendar = Calendar(Path('configs/observer_calendar_2026.json'))
    expected = contract.encoded(shard)
    context = Context(prec=6, rounding=ROUND_DOWN)
    context.traps[Inexact] = True
    context.flags[Inexact] = True
    with localcontext(context if foreign_decimal_context else getcontext()):
        before = getcontext().copy()
        monkeypatch.setattr(contract, 'SHARD_LIMIT', len(expected))
        value, raw = contract._validated_shard_bytes(shard, calendar, NOW)
        assert value is shard and raw == expected and json.loads(raw) == shard
        assert contract.validate_shard(shard, calendar, NOW) is shard
        monkeypatch.setattr(contract, 'SHARD_LIMIT', len(expected)-1)
        for validate in (contract.validate_shard, contract._validated_shard_bytes):
            with pytest.raises(ValueError, match='^UNIFIED_MARKET_DOCUMENT_INVALID$'):
                validate(shard, calendar, NOW)
        assert getcontext().prec == before.prec and getcontext().rounding == before.rounding
        assert getcontext().traps == before.traps and getcontext().flags == before.flags


@pytest.mark.parametrize('stage', ['encoding', 'quality'])
@pytest.mark.parametrize('resign_mutation', [False, True])
def test_validated_encoding_rejects_content_mutation_before_return(
    monkeypatch, stage, resign_mutation,
):
    shard = constructed_shard()
    calendar = Calendar(Path('configs/observer_calendar_2026.json'))
    def mutate():
        shard['bars'][0]['close'] = '11'
        if resign_mutation:
            shard.update(resign(shard))
    if stage == 'encoding':
        original = contract.encoded
        def encode(value):
            raw = original(value)
            mutate()
            return raw
        monkeypatch.setattr(contract, 'encoded', encode)
    else:
        original_bar = contract._validate_bar
        def validate_bar(*args, **kwargs):
            original_bar(*args, **kwargs)
            mutate()
        monkeypatch.setattr(contract, '_validate_bar', validate_bar)
    with pytest.raises(ValueError, match='^UNIFIED_MARKET_DOCUMENT_INVALID$'):
        contract._validated_shard_bytes(shard, calendar, NOW)


def test_export_validation_fault_uses_encoded_validator_and_retains_pointer(source, monkeypatch):
    base, _, _, _, _, add = source
    add()
    export(source)
    pointer = base/'unified-data/market-public/unified-market.json'
    state = base/'unified-data/market-export-state.json'
    before = pointer.read_bytes(), state.read_bytes()
    add(counter=2, minute=40)
    calls = []
    def reject(*args):
        calls.append(args[0]['symbol'])
        raise ValueError('ENCODED_SHARD_VALIDATION_FAULT')
    monkeypatch.setattr(exporter, '_validated_shard_bytes', reject)
    with pytest.raises(ValueError, match='^ENCODED_SHARD_VALIDATION_FAULT$'):
        export(source)
    assert calls and (pointer.read_bytes(), state.read_bytes()) == before


def validation_outcome(call):
    try:
        return ('ACCEPTED', call())
    except (ValueError, TypeError, OverflowError) as error:
        return (type(error).__name__, str(error))


@pytest.mark.parametrize('changes', [
    {}, {'amount': '1000', 'vwap': '10'},
    {'amount': '1000', 'vwap': '10.0'}, {'amount': '1', 'vwap': '0.01'},
    {'amount': '0', 'volume': '0'}, {'amount': '1', 'volume': '0'},
    {'amount': '0'}, {'amount': '-1'}, {'volume': '-1'},
    {'amount': '1000', 'volume': '3', 'high': '400', 'vwap': str(Decimal(1000)/3)},
    {'close': '13'}, {'low': '11'}, {'open': '0'}, {'close': '-1'},
    {'close': 'NaN'}, {'close': 'sNaN'}, {'close': 'Infinity'}, {'close': 'x'},
    {'close': 10}, {'volume': None}, {'close': '1'*81}, {'close': ''},
    {'open': '1e999999', 'high': '1e999999', 'low': '1e999999', 'close': '1e999999',
     'amount': '1e999999', 'volume': '0.00001', 'vwap': '1e1000004'},
    {'timestamp': '2026-10-26T01:35:00Z'},
    {'timestamp': '2026-10-26T09:35:00'},
    {'timestamp': '2026-10-26T09:36:00+08:00'},
    {'timestamp': '2026-10-26T09:30:00+08:00'},
    {'timestamp': '2026-10-26T09:35:00.000001+08:00'},
    {'timestamp': '2026-10-26T09:35:00+08:00:01'},
    {'timestamp': '2026-10-26T09:35:00+09:00'},
    {'trading_day': '20261026'}, {'trading_day': '2026-10-23'},
    {'timestamp': 'bad'}, {'trading_day': 'bad'},
    {'historical_available_at': NOW.isoformat()}, {'price_basis': 'ADJUSTED'},
    {'amount_basis': 'UNVERIFIED'}, {'exchange': 'SSE'}, {'extra': True},
    {'frequency': 'day', 'timestamp': '2026-10-26T15:00:00+08:00'},
    {'frequency': 'day', 'timestamp': '2026-10-26T14:59:00+08:00'},
    {'frequency': 'unknown'},
])
@pytest.mark.parametrize('foreign_decimal_context', [False, True])
@pytest.mark.parametrize('scope', ['bar', 'shard'])
def test_constructed_row_path_matches_original_json_path_exactly(
    monkeypatch, changes, foreign_decimal_context, scope,
):
    from qtrade.market_data.sina_incremental import completed_klines
    calendar = Calendar(Path('configs/observer_calendar_2026.json'))
    row = constructed_bar(**changes)
    shard = signed({**AUTHORITY, 'schema_version': 'qtrade.unified-market-shard.v1',
        'universe_version': '1'*64, 'symbol': '301001', 'security_type': 'STOCK',
        'instrument_id': 'SZSE.301001', 'frequency': row['frequency'],
        'price_basis': contract.PRICE_BASIS, 'bars': [row],
        'source_batches': [{'batch_id': '2'*32, 'manifest_sha256': '3'*64,
                            'raw_sha256': '4'*64, 'normalized_sha256': '5'*64}]})
    def validate():
        if scope == 'shard':
            return contract.validate_shard(shard, calendar, NOW)
        return contract.validate_bar(row, '301001', 'STOCK', row['frequency'], calendar, NOW)
    class LegacyRows:
        def __init__(self, symbol, kind, frequency, observed_at, cal):
            self.kwargs = {'symbol': symbol, 'security_type': kind, 'frequency': frequency,
                           'observed_at': observed_at, 'calendar': cal}
        def parse(self, rows, day):
            with localcontext(Context(prec=28)):
                return completed_klines(json.dumps(rows).encode(), requested_day=day, **self.kwargs)
    context = Context(prec=6, rounding=ROUND_DOWN)
    context.traps[Inexact] = True
    with localcontext(context if foreign_decimal_context else getcontext()):
        before = getcontext().copy()
        actual = validation_outcome(validate)
        with monkeypatch.context() as patch:
            patch.setattr(contract, 'completed_kline_rows',
                lambda rows, **kwargs: completed_klines(json.dumps(rows).encode(), **kwargs))
            patch.setattr(contract, '_ConstructedKlineRows', LegacyRows)
            expected = validation_outcome(validate)
        assert actual == expected
        assert getcontext().prec == before.prec and getcontext().rounding == before.rounding
        assert getcontext().traps == before.traps and getcontext().flags == before.flags


@pytest.mark.parametrize(('payload', 'updates'), [
    (None, {}), ({}, {}), ([], {}), ([None], {}), ([{}], {}), ([{'day': 1}], {}),
    ([{'day': 'bad'}], {}), ([{'day': '2026-10-23'}], {}),
    ([{'day': '2026-10-26'}], {}),
    ([{'day': '2026-10-26T15:00:00'}], {'frequency': 'day'}),
    ([{'day': '2026-10-26 09:35:00+08:00'}], {}),
    ([{'day': '2026-10-26 09:36:00'}], {}),
    ([{'day': '2026-10-26 09:35:00'}]*1971, {}),
])
def test_decoded_kline_helper_keeps_original_shape_and_clock_errors(payload, updates):
    from qtrade.market_data.sina_incremental import completed_kline_rows, completed_klines
    kwargs = {'symbol': '301001', 'security_type': 'STOCK', 'frequency': '5min',
        'requested_day': datetime(2026, 10, 26).date(), 'observed_at': NOW,
        'calendar': Calendar(Path('configs/observer_calendar_2026.json')), **updates}
    actual = validation_outcome(lambda: completed_kline_rows(payload, **kwargs))
    expected = validation_outcome(lambda: completed_klines(json.dumps(payload).encode(), **kwargs))
    assert actual == expected and actual[0] != 'ACCEPTED'


@pytest.mark.parametrize('updates', [
    {'observed_at': datetime(2026, 10, 26)}, {'requested_day': datetime(2026, 10, 25).date()},
    {'requested_day': datetime(2026, 10, 27).date()}, {'frequency': 'invalid'},
    {'symbol': '123'}, {'security_type': 'unknown'},
    {'observed_at': datetime(2026, 10, 26, 9, 34, tzinfo=SHANGHAI)},
    {'require_complete_session': True},
])
def test_decoded_kline_helper_keeps_original_admission_errors(updates):
    from qtrade.market_data.sina_incremental import completed_kline_rows, completed_klines
    row = constructed_bar()
    payload = [{'day': '2026-10-26 09:35:00',
                **{key: row[key] for key in ('open', 'high', 'low', 'close', 'volume')}}]
    kwargs = {'symbol': '301001', 'security_type': 'STOCK', 'frequency': '5min',
        'requested_day': datetime(2026, 10, 26).date(), 'observed_at': NOW,
        'calendar': Calendar(Path('configs/observer_calendar_2026.json')), **updates}
    actual = validation_outcome(lambda: completed_kline_rows(payload, **kwargs))
    expected = validation_outcome(lambda: completed_klines(json.dumps(payload).encode(), **kwargs))
    assert actual == expected and actual[0] != 'ACCEPTED'


def test_raw_kline_path_keeps_byte_and_json_guards_before_decoded_rows(monkeypatch):
    from qtrade.market_data import sina_incremental as incremental
    kwargs = {'symbol': '301001', 'security_type': 'STOCK', 'frequency': '5min',
        'requested_day': datetime(2026, 10, 26).date(), 'observed_at': NOW,
        'calendar': Calendar(Path('configs/observer_calendar_2026.json'))}
    def forbidden(*args, **kwargs):
        raise AssertionError('INVALID_RAW_INPUT_REACHED_DECODED_ROW_VALIDATION')
    monkeypatch.setattr(incremental, '_completed_kline_rows', forbidden)
    with pytest.raises(ValueError, match='BOUNDED_SOURCE_RESPONSE_TOO_LARGE'):
        incremental.completed_klines(b' '*2_000_001, **kwargs)
    for raw in (b'', b'[', b'\xff', b'[] trailing'):
        with pytest.raises((ValueError, UnicodeDecodeError)):
            incremental.completed_klines(raw, **kwargs)


@pytest.mark.parametrize('frequency', ['day', '5min'])
def test_captured_reader_minute_memo_matches_uncached_clocks_and_errors(frequency):
    from qtrade.market_data.sina_incremental import _CapturedKlineReader, completed_klines
    calendar = Calendar(Path('configs/observer_calendar_2026.json'))
    reader = _CapturedKlineReader(calendar)
    raw = json.dumps([{'day': '2026-10-26' if frequency == 'day' else '2026-10-26 09:35:00',
        'open': '10', 'high': '12', 'low': '9', 'close': '10', 'volume': '100'}]).encode()
    kwargs = {'symbol': '301001', 'security_type': 'STOCK', 'frequency': frequency,
              'requested_day': datetime(2026, 10, 26).date(), 'calendar': calendar}
    for day in (23, 24, 25, 26, 27):
        for hour, minute in ((9, 34), (9, 35), (9, 36), (11, 30), (11, 31), (12, 0),
                             (13, 4), (13, 5), (13, 6), (15, 0), (15, 1),
                             (16, 29), (16, 30), (16, 31), (23, 59)):
            for second, microsecond in ((0, 0), (1, 1), (30, 500000), (59, 999999)):
                for zone in (SHANGHAI, UTC):
                    observed = datetime(2026, 10, day, hour, minute, second, microsecond,
                                        tzinfo=SHANGHAI).astimezone(zone)
                    actual = validation_outcome(lambda observed=observed: reader.parse(
                        raw, observed_at=observed, **kwargs))
                    expected = validation_outcome(lambda observed=observed: completed_klines(
                        raw, observed_at=observed, **kwargs))
                    assert actual == expected
    for observed in (datetime(2026, 10, 26), datetime(2027, 1, 1, tzinfo=SHANGHAI)):
        assert validation_outcome(lambda observed=observed: reader.parse(
            raw, observed_at=observed, **kwargs)) == validation_outcome(
                lambda observed=observed: completed_klines(raw, observed_at=observed, **kwargs))
    for invalid in (b'', b'[', b'\xff', b'[] trailing', b' '*2_000_001):
        assert validation_outcome(lambda invalid=invalid: reader.parse(
            invalid, observed_at=NOW, **kwargs)) == validation_outcome(
                lambda invalid=invalid: completed_klines(invalid, observed_at=NOW, **kwargs))


def test_captured_reader_memo_is_bounded_and_does_not_cross_calendar_objects():
    from qtrade.market_data.sina_incremental import _CapturedKlineReader, completed_klines
    calendar = Calendar({'valid_from': '2000-01-01', 'valid_through': '2040-12-31',
                         'weekday_closures': []})
    reader = _CapturedKlineReader(calendar)
    start = datetime(2026, 1, 1, tzinfo=SHANGHAI)
    for index in range(4100):
        assert reader.identity(f'{index:06d}', 'STOCK') == 'SZSE.'+f'{index:06d}'
        reader.is_open((start+timedelta(days=index)).date())
        stamp = start+timedelta(minutes=index)
        assert reader.expected(stamp, 'minute') == calendar.expected(stamp, 'minute')
    assert len(reader._identities) == len(reader._days) == len(reader._cutoffs) == 4096
    raw = json.dumps([{'day': '2026-10-26 09:35:00', 'open': '10', 'high': '12',
                       'low': '9', 'close': '10', 'volume': '100'}]).encode()
    closed = Calendar({**calendar.document, 'weekday_closures': ['2026-10-26']})
    kwargs = {'symbol': '301001', 'security_type': 'STOCK', 'frequency': '5min',
              'requested_day': datetime(2026, 10, 26).date(), 'observed_at': NOW,
              'calendar': closed}
    assert validation_outcome(lambda: reader.parse(raw, **kwargs)) == (
        validation_outcome(lambda: completed_klines(raw, **kwargs)))


@pytest.mark.parametrize(('kind', 'symbol', 'amount', 'vwap', 'accepted'), [
    ('STOCK', '600036', '1000.5', '10.005', True),
    ('ETF', '510300', '1000.05', '10.0005', True),
    ('ETF', '510300', '1000.5', '10.005', False),
])
def test_constructed_rows_keep_security_specific_amount_tolerance(
    monkeypatch, kind, symbol, amount, vwap, accepted,
):
    from qtrade.market_data.sina_incremental import completed_klines
    row = constructed_bar(symbol=symbol, exchange='SSE', open='10', high='10', low='10',
                          close='10', amount=amount, vwap=vwap)
    calendar = Calendar(Path('configs/observer_calendar_2026.json'))
    def validate():
        return contract.validate_bar(row, symbol, kind, '5min', calendar, NOW)
    actual = validation_outcome(validate)
    monkeypatch.setattr(contract, 'completed_kline_rows',
        lambda rows, **kwargs: completed_klines(json.dumps(rows).encode(), **kwargs))
    assert actual == validation_outcome(validate)
    assert (actual[0] == 'ACCEPTED') is accepted


@pytest.mark.parametrize('damage', [None, 'duplicate', 'unordered'])
def test_shard_keeps_independent_bar_quality_and_duplicate_order_scope(damage):
    bars = [constructed_bar(), constructed_bar(timestamp='2026-10-26T09:40:00+08:00',
             open='100', high='101', low='99', close='100')]
    if damage == 'duplicate':
        bars[1]['timestamp'] = bars[0]['timestamp']
    elif damage == 'unordered':
        bars.reverse()
    shard = signed({**AUTHORITY, 'schema_version': 'qtrade.unified-market-shard.v1',
        'universe_version': '1'*64, 'symbol': '301001', 'security_type': 'STOCK',
        'instrument_id': 'SZSE.301001', 'frequency': '5min', 'price_basis': contract.PRICE_BASIS,
        'bars': bars, 'source_batches': [{'batch_id': '2'*32, 'manifest_sha256': '3'*64,
                                         'raw_sha256': '4'*64, 'normalized_sha256': '5'*64}]})
    calendar = Calendar(Path('configs/observer_calendar_2026.json'))
    if damage:
        with pytest.raises(ValueError, match='UNIFIED_MARKET_DUPLICATE_OR_UNORDERED_BAR'):
            contract.validate_shard(shard, calendar, NOW)
    else:
        # The existing contract validates each retained bar independently; a new
        # bulk parser must not invent a cross-bar jump admission rule here.
        assert contract.validate_shard(shard, calendar, NOW) == shard


def test_export_bytes_are_identical_across_process_hash_seeds_and_track_real_changes(source):
    base, database, _, _, store, add = source
    add()
    add(counter=2, minute=40)
    add('day', counter=3)
    add(symbol='600036', counter=4)
    database_before = database.read_bytes()
    inputs_before = {path: path.read_bytes() for path in store.root.rglob('*') if path.is_file()}
    code = '''
import json, os, sys
from datetime import datetime
from pathlib import Path
from scripts import publish_unified_market as exporter
if os.name == 'nt':
    # The Windows fixture filesystem cannot fsync directory handles. Keep the
    # actual atomic/immutable writes, accepting only that post-write limitation.
    def compatible(writer):
        def write(path, value, **kwargs):
            try:
                writer(path, value, **kwargs)
            except PermissionError:
                assert path.read_bytes() == exporter.encoded(value)
        return write
    exporter.write_json_atomic = compatible(exporter.write_json_atomic)
    exporter._fsync_directory = lambda path: None
base, database = map(Path, sys.argv[1:3])
index = exporter.export_market(base, database, now=datetime.fromisoformat(sys.argv[3]))['index']
objects = {ref['sha256']: (base/'unified-data/market-public/unified-market/objects'/
           (ref['sha256']+'.json')).read_bytes().hex()
           for entry in index['entries'] for ref in entry['shards']}
print(json.dumps({'index': index, 'objects': objects}))
'''
    def run(seed):
        result = subprocess.run([sys.executable, '-c', code, str(base), str(database),
                                 NOW.isoformat()], capture_output=True, text=True, timeout=30,
            cwd=exporter.REPO, env={**os.environ, 'PYTHONHASHSEED': str(seed),
                'LIVE_TRADING_ENABLED': 'false', 'PYTHONPATH': os.pathsep.join(
                    (str(exporter.REPO/'src'), str(exporter.REPO)))})
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)
    first = run(1)
    assert len(first['objects']) == 3
    assert run(2) == run(8675309) == first
    assert database.read_bytes() == database_before
    assert all(path.read_bytes() == value for path, value in inputs_before.items())
    add(counter=5, minute=45)
    changed = run(3)
    assert changed['index']['entries'] != first['index']['entries']
    assert len(set(changed['objects'])-set(first['objects'])) == 1
    assert len(set(changed['objects']) & set(first['objects'])) == 2
    assert run(4) == changed
    for digest, raw in first['objects'].items():
        path = base/'unified-data/market-public/unified-market/objects'/(digest+'.json')
        assert path.read_bytes() == bytes.fromhex(raw)


@pytest.mark.parametrize('damage', ['conflict', 'excluded', 'not_complete', 'raw', 'ledger'])
def test_unqualified_chunks_never_create_bars_and_conflicts_are_quarantined(source, damage):
    _, database, _, _, store, add = source
    manifest = add()
    if damage == 'conflict':
        add(close='11', counter=2)
    elif damage == 'raw':
        blob = store._blob_path(manifest['raw']['sha256'], '.jsonl.gz')
        blob.write_bytes(b'X'*blob.stat().st_size)
    else:
        with sqlite3.connect(database) as connection:
            if damage == 'excluded':
                connection.execute('INSERT INTO excluded_collection_chunks VALUES(?,?)',
                                   (manifest['batch_id'], '301001'))
            elif damage == 'not_complete':
                connection.execute("UPDATE collection_batches SET state='RUNNING'")
            else:
                connection.execute('UPDATE collection_chunks SET bar_count=2')
    value = export(source)['index']
    entry = next(row for row in value['entries']
                 if row['symbol'] == '301001' and row['frequency'] == '5min')
    assert entry['shards'] == []
    expected = 'NO_DATA' if damage in {'excluded', 'not_complete'} else 'QUARANTINED'
    assert entry['state'] == expected


@pytest.fixture
def http(source, monkeypatch):
    base, _, _, _, _, add = source
    add()
    add(counter=2, minute=40)
    add('day', counter=3)
    value = export(source)['index']
    root = base/'unified-data/market-public'
    monkeypatch.setattr(api, '_index', lambda _: value)
    monkeypatch.setattr(api, 'current_revision', lambda: REVISION)
    monkeypatch.setattr(api, '_object', lambda digest, revision:
        (root/'unified-market/objects'/(digest+'.json')).read_bytes())
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz or UTC)
    monkeypatch.setattr(api, 'datetime', Clock)
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as client:
        yield client, value, root


def test_api_query_new_target_both_frequencies_and_empty_target_without_legacy_fallback(http):
    client, index, _ = http
    response = client.get('/unified/bars?symbol=301001&frequency=5min&limit=1')
    assert response.status_code == 200 and response.headers['cache-control'] == 'no-store'
    body = response.json()
    assert body['index_id'] == body['version'] == index['evidence_sha256']
    assert body['publication_revision'] == REVISION
    assert body['universe_version'] == index['universe_version']
    assert body['state'] == 'PARTIAL' and body['freshness'] == 'PARTIAL'
    assert body['count'] == 1 and body['total_count'] == 2 and body['truncated']
    assert body['bars'][0]['timestamp'].endswith('09:40:00+08:00')
    assert body['gaps']['missing_timestamps']
    assert client.get('/unified/bars?symbol=301001&frequency=day').json()['count'] == 1
    missing = client.get('/unified/bars?symbol=600036&frequency=day').json()
    assert missing['state'] == 'NO_DATA' and missing['bars'] == []
    assert missing['actual_end'] is None


@pytest.mark.parametrize(('query', 'status'), [
    ('symbol=000001', 404), ('symbol=301001&frequency=1min', 422),
    ('symbol=301001&limit=2001', 422), ('symbol=301001&start=2026-10-27&end=2026-10-26', 422),
    ('symbol=301001&version='+'0'*64, 409), ('symbol=../../secret', 422)])
def test_api_range_identity_and_resource_bounds(http, query, status):
    assert http[0].get('/unified/bars?'+query).status_code == status


def test_explicit_revision_pins_index_while_default_head_advances_and_version_still_binds(
    http, monkeypatch,
):
    client, original, _ = http
    newer = deepcopy(original)
    newer['published_at'] = (NOW-timedelta(seconds=1)).isoformat()
    newer = resign(newer)
    head, requests = [], []
    def current():
        head.append('d'*40)
        return 'd'*40
    def index(revision):
        requests.append(revision)
        return original if revision == REVISION else newer
    monkeypatch.setattr(api, 'current_revision', current)
    monkeypatch.setattr(api, '_index', index)
    query = '/unified/bars?symbol=301001&publication_revision='+REVISION
    pinned = client.get(query+'&version='+original['evidence_sha256'])
    assert pinned.status_code == 200 and pinned.json()['publication_revision'] == REVISION
    assert pinned.json()['index_id'] == original['evidence_sha256'] and head == []
    default = client.get('/unified/bars?symbol=301001')
    assert default.status_code == 200 and default.json()['publication_revision'] == 'd'*40
    assert default.json()['index_id'] == newer['evidence_sha256']
    assert requests == [REVISION, 'd'*40] and head == ['d'*40]
    mismatch = client.get(query+'&version='+newer['evidence_sha256'])
    assert mismatch.status_code == 409
    assert mismatch.json()['detail'] == 'UNIFIED_MARKET_VERSION_CHANGED_REFRESH_REQUIRED'


@pytest.mark.parametrize('revision', ['main', 'c'*39, 'C'*40, '../unified.json',
                                    'https://other.invalid/unified.json'])
def test_invalid_revision_is_rejected_before_source_access(http, monkeypatch, revision):
    def forbidden():
        pytest.fail('invalid revision must not discover or read a source')
    monkeypatch.setattr(api, 'current_revision', forbidden)
    assert http[0].get('/unified/bars', params={'symbol': '301001',
        'publication_revision': revision}).status_code == 422


def test_default_head_failure_never_reuses_previous_successful_index(http, monkeypatch):
    client, _, _ = http
    assert client.get('/unified/bars?symbol=301001').status_code == 200
    def failed():
        raise ValueError('UNIFIED_SOURCE_HTTP_FETCH_FAILED')
    monkeypatch.setattr(api, 'current_revision', failed)
    result = client.get('/unified/bars?symbol=301001')
    assert result.status_code == 503
    assert result.json()['detail'] == 'UNIFIED_MARKET_PUBLICATION_UNAVAILABLE'


def test_index_cache_key_and_validated_bytes_use_exact_same_revision(source, monkeypatch):
    source[-1]()
    original = export(source)['index']
    newer = resign({**original, 'published_at': (NOW-timedelta(seconds=1)).isoformat()})
    requests = []
    actual = [REVISION]
    def download(name, limit, *, publication_revision):
        assert name == 'unified-market.json' and limit == contract.INDEX_LIMIT
        requests.append(publication_revision)
        value = original if publication_revision == REVISION else newer
        return contract.encoded(value), actual[0]
    monkeypatch.setattr(api, 'download', download)
    api._index.cache_clear()
    try:
        assert api._index(REVISION) == original
        assert api._index(REVISION) == original and requests == [REVISION]
        with pytest.raises(ValueError, match='SOURCE_REVISION_MISMATCH'):
            api._index('d'*40)
        actual[0] = 'd'*40
        assert api._index('d'*40) == newer
        assert requests == [REVISION, 'd'*40, 'd'*40]
    finally:
        api._index.cache_clear()


def test_new_shard_uses_pinned_commit_when_main_is_unavailable_without_cross_revision_fallback(
    source, monkeypatch,
):
    base, _, _, _, _, add = source
    add()
    index = export(source)['index']
    root = base/'unified-data/market-public'
    requests = []
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz or UTC)
    class Response(io.BytesIO):
        status = 200
        def __init__(self, raw, url):
            super().__init__(raw)
            self.url = url
            self.headers = Message()
            self.headers['Content-Type'] = 'text/plain; charset=utf-8'
            self.headers['Content-Length'] = str(len(raw))
        def geturl(self):
            return self.url
    class Opener:
        def open(self, request, timeout):
            from urllib.error import HTTPError
            url = request.full_url
            requests.append(url)
            assert request.get_method() == 'GET' and timeout == 8
            assert '/main/' not in url
            revision, name = url.removeprefix(public_source.RAW_ROOT).split('/', 1)
            assert revision in {REVISION, 'd'*40}
            if name == 'unified-market.json':
                return Response(contract.encoded(index), url)
            # This commit's newly published object is available even though main
            # is unavailable. A different commit's failed read cannot use it.
            if revision == 'd'*40:
                raise HTTPError(url, 404, 'fixture missing snapshot', {}, None)
            return Response((root/name).read_bytes(), url)
    monkeypatch.setattr(api, 'datetime', Clock)
    monkeypatch.setattr(public_source, 'getproxies', lambda: {})
    monkeypatch.setattr(public_source, 'build_opener', lambda *handlers: Opener())
    monkeypatch.setattr(api, 'current_revision', lambda: pytest.fail('commit is pinned'))
    api._index.cache_clear()
    api._object.cache_clear()
    app = FastAPI()
    app.include_router(api.router)
    try:
        with TestClient(app) as client:
            query = '/unified/bars?symbol=301001&frequency=5min&version='+index['evidence_sha256']
            pinned = client.get(query+'&publication_revision='+REVISION)
            assert pinned.status_code == 200 and pinned.json()['count'] == 1
            assert pinned.json()['publication_revision'] == REVISION
            absent = client.get(query+'&publication_revision='+'d'*40)
            assert absent.status_code == 503
            assert absent.json()['detail'] == 'UNIFIED_MARKET_SHARD_UNAVAILABLE'
        assert len(requests) == 4 and all('/main/' not in url for url in requests)
        assert api._object.cache_info().maxsize == 128
    finally:
        api._index.cache_clear()
        api._object.cache_clear()


def test_requested_old_window_is_explicit_without_inventing_history(http):
    result = http[0].get('/unified/bars?symbol=301001&frequency=5min'
                         '&start=2026-01-01&end=2026-01-02')
    assert result.status_code == 200
    value = result.json()
    assert value['state'] == 'OUTSIDE_WINDOW' and value['bars'] == []
    assert value['gaps']['requested_outside_window'] is True


def test_api_corrupted_object_and_stale_index_never_become_fresh(http, monkeypatch):
    client, index, root = http
    stale = deepcopy(index)
    stale['published_at'] = (NOW-timedelta(seconds=181)).isoformat()
    stale = resign(stale)
    monkeypatch.setattr(api, '_index', lambda _: stale)
    assert client.get('/unified/bars?symbol=301001').json()['freshness'] == 'STALE_PUBLICATION'
    entry = next(row for row in index['entries']
                 if row['symbol'] == '301001' and row['frequency'] == 'day')
    path = root/'unified-market/objects'/(entry['shards'][0]['sha256']+'.json')
    path.write_bytes(b'corrupt')
    assert client.get('/unified/bars?symbol=301001').status_code == 503


def test_transport_never_imports_cluster_scripts_in_serverless():
    import subprocess
    import sys
    code = '''
import importlib.abc,sys
class Block(importlib.abc.MetaPathFinder):
 def find_spec(self,name,path=None,target=None):
  if name=='scripts' or name.startswith('scripts.'): raise AssertionError(name)
sys.meta_path.insert(0,Block())
from qtrade.api.unified_market import router
assert router.routes
'''
    result = subprocess.run([sys.executable, '-c', code], capture_output=True,
                            text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize('field', ['close', 'amount', 'source_hashes', 'raw_pack_sha256'])
def test_hash_valid_normalized_forgery_is_rejected_by_raw_replay(source, field):
    import io
    from hashlib import sha256
    _, database, _, _, store, add = source
    manifest = add('day')
    rows = pq.read_table(io.BytesIO(store.read_blob(manifest['normalized']))).to_pylist()
    rows[0][field] = (['0'*64] if field == 'source_hashes' else '0'*64
                      if field == 'raw_pack_sha256' else '11' if field == 'close' else '1100')
    if field == 'amount':
        rows[0]['vwap'] = '11'
    buffer = io.BytesIO()
    pq.write_table(pa.Table.from_pylist(rows, schema=FIELDS), buffer, compression='zstd')
    manifest['normalized'] = store._put_blob(buffer.getvalue(), '.parquet')
    plain = [{key: value for key, value in row.items()
              if key not in {'raw_pack_sha256', 'source_hashes'}} for row in rows]
    manifest['admitted_rows_sha256'] = sha256(canonical(plain)).hexdigest()
    manifest = resign(manifest)
    store.batch_path(manifest['batch_id']).write_bytes(contract.encoded(manifest))
    with sqlite3.connect(database) as connection:
        connection.execute('UPDATE collection_batches SET manifest_sha256=?',
                           (manifest['evidence_sha256'],))
    assert store.load(manifest['batch_id']) == manifest
    index = export(source)['index']
    entry = next(row for row in index['entries'] if row['symbol'] == '301001'
                 and row['frequency'] == 'day')
    assert entry['state'] == 'QUARANTINED' and entry['shards'] == []


def test_cached_source_still_checks_actual_blobs_and_transient_io_retains_pointer(source,
                                                                                monkeypatch):
    base, database, _, _, store, add = source
    manifest = add()
    first = export(source)
    path = base/'unified-data/market-public/unified-market.json'
    prior = path.read_bytes()
    original = exporter._load
    def fail(*args, **kwargs):
        raise OSError('temporary fixture storage failure')
    monkeypatch.setattr(exporter, '_load', fail)
    with pytest.raises(OSError):
        export(source)
    assert path.read_bytes() == prior
    monkeypatch.setattr(exporter, '_load', original)
    blob = store._blob_path(manifest['raw']['sha256'], '.jsonl.gz')
    blob.write_bytes(b'X'*blob.stat().st_size)
    changed = exporter.export_market(base, database, now=NOW)['index']
    assert any(row['state'] == 'QUARANTINED' for row in changed['entries'])
    assert first['index']['evidence_sha256'] != changed['evidence_sha256']


@pytest.mark.parametrize('field', ['authority', 'future', 'implementation', 'window', 'counts'])
def test_pure_index_validation_rejects_resealed_semantic_forgery(source, field):
    source[-1]()
    value = deepcopy(export(source)['index'])
    if field == 'authority':
        value['orders_enabled'] = True
    elif field == 'future':
        value['published_at'] = (NOW+timedelta(seconds=1)).isoformat()
    elif field == 'implementation':
        value['implementation_sources'] = {}
    elif field == 'window':
        value['windows']['day'] = value['windows']['day'][-1:]
    else:
        value['total_bytes'] += 1
    with pytest.raises(ValueError):
        contract.validate_index(resign(value), NOW)


def test_shard_objects_reused_across_index_heartbeats_and_bounded_publication_copy(source):
    base, database, _, _, _, add = source
    add()
    first = export(source)['index']
    second = exporter.export_market(base, database, now=NOW+timedelta(seconds=1))['index']
    assert first['evidence_sha256'] != second['evidence_sha256']
    assert first['entries'] == second['entries']
    publication = base/'publication'
    names = exporter.publish_market_files(base, publication)
    assert len(names) == 2*second['total_files']+1
    assert json.loads((publication/'unified-market.json').read_bytes()) == second


def test_publication_aliases_are_stable_hardlinks_and_git_reuses_blob_identity(source, monkeypatch):
    base, _, _, _, _, add = source
    add()
    add('day', counter=2)
    index = export(source)['index']
    publication = base/'publication'
    names = exporter.publish_market_files(base, publication)
    aliases = [name for name in names if '/.delta/' in name]
    assert len(aliases) == index['total_files'] == 2
    assert {name.split('/')[-2] for name in aliases} == {'day', '20261026'}
    for alias_name in aliases:
        alias = publication/alias_name
        raw = alias.read_bytes()
        digest = exporter.sha256(raw).hexdigest()
        target = publication/'unified-market/objects'/(digest+'.json')
        assert alias.samefile(target) and alias.stat().st_ino == target.stat().st_ino
        value = json.loads(raw)
        group = 'day' if value['frequency'] == 'day' else '20261026'
        identity = f"{value['symbol']}|{value['frequency']}|{group}"
        prefix = 'D' if value['frequency'] == 'day' else 'M'
        suffix = exporter.sha256(identity.encode()).hexdigest()[:16]
        assert alias.name == f"{prefix}{value['symbol']}-{suffix}.json"

    def unexpected_link(*args, **kwargs):
        raise AssertionError('unchanged alias must skip relinking')
    monkeypatch.setattr(exporter.os, 'link', unexpected_link)
    assert exporter.publish_market_files(base, publication) == names
    subprocess.run(['git', 'init', '-q', str(publication)], check=True, timeout=30)
    subprocess.run(['git', 'add', '--', *names], cwd=publication, check=True, timeout=30)
    staged = subprocess.run(['git', 'ls-files', '--stage'], cwd=publication, check=True,
                            capture_output=True, text=True, timeout=30).stdout.splitlines()
    blobs = {row.split()[1] for row in staged}
    assert len(staged) == 2*index['total_files']+1
    assert len(blobs) == index['total_files']+1  # Extra paths do not duplicate payload blobs.


def test_alias_atomic_update_preserves_old_immutable_object_and_old_link(source, monkeypatch):
    base, _, _, _, _, add = source
    add()
    export(source)
    publication = base/'publication'
    first = exporter.publish_market_files(base, publication)
    alias_name, = (name for name in first if '/.delta/' in name)
    old_name, = (name for name in first if '/objects/' in name)
    old = publication/old_name
    prior_bytes, prior_inode = old.read_bytes(), old.stat().st_ino
    retained = base/'old-alias-reader'
    os.link(publication/alias_name, retained)
    add(counter=2, minute=40)
    export(source)
    original = exporter.os.replace
    replaced = []
    def replace(temporary, target):
        assert target == publication/alias_name
        assert old.read_bytes() == prior_bytes and retained.read_bytes() == prior_bytes
        assert Path(temporary).samefile(next(path for path in
            (publication/'unified-market/objects').glob('*.json') if path != old))
        replaced.append(target)
        original(temporary, target)
    monkeypatch.setattr(exporter.os, 'replace', replace)
    second = exporter.publish_market_files(base, publication)
    assert replaced == [publication/alias_name] and alias_name in second
    assert old.read_bytes() == retained.read_bytes() == prior_bytes
    assert old.stat().st_ino == retained.stat().st_ino == prior_inode
    assert (publication/alias_name).read_bytes() != prior_bytes
    assert not list((publication/alias_name).parent.glob('*.tmp'))


def test_seed_existing_preserves_exact_pointer_clock_and_never_reads_export(source, monkeypatch):
    base, _, _, _, _, add = source
    add()
    index = export(source)['index']
    publication = base/'publication'
    names = exporter.publish_market_files(base, publication)
    for name in names:
        if '/.delta/' in name:
            (publication/name).unlink()
    pointer = publication/'unified-market.json'
    pointer.write_bytes(json.dumps(index, separators=(',', ':')).encode()+b'\n')
    os.utime(pointer, ns=(1_700_000_000_000_000_000, 1_700_000_000_000_000_000))
    before = pointer.read_bytes(), pointer.stat().st_mtime_ns
    (base/'unified-data/market-public').rename(base/'unused-source-export')
    def unexpected_export(*args, **kwargs):
        raise AssertionError('seed must not export or replace the publication pointer')
    monkeypatch.setattr(exporter, 'export_market', unexpected_export)
    monkeypatch.setattr(exporter, 'write_json_atomic', unexpected_export)
    assert exporter.publish_market_files(base, publication, seed_existing=True) == names
    assert (pointer.read_bytes(), pointer.stat().st_mtime_ns) == before
    assert exporter.publish_market_files(base, publication, seed_existing=True) == names


def test_seed_rejects_database_and_missing_pointer_without_writing(source, monkeypatch):
    base, database, *_ = source
    publication = base/'publication'
    def unexpected_export(*args, **kwargs):
        raise AssertionError('invalid seed must not export')
    monkeypatch.setattr(exporter, 'export_market', unexpected_export)
    with pytest.raises(ValueError, match='SEED_DATABASE_FORBIDDEN'):
        exporter.publish_market_files(base, publication, database, seed_existing=True)
    with pytest.raises(ValueError, match='PUBLIC_INDEX_INVALID'):
        exporter.publish_market_files(base, publication, seed_existing=True)
    assert not publication.exists()


@pytest.mark.parametrize('operation', ['link', 'replace'])
def test_alias_filesystem_failure_retains_pointer_old_inode_and_cleans_temporary(source,
                                                                               monkeypatch,
                                                                               operation):
    base, _, _, _, _, add = source
    add()
    export(source)
    publication = base/'publication'
    names = exporter.publish_market_files(base, publication)
    alias_name, = (name for name in names if '/.delta/' in name)
    alias = publication/alias_name
    pointer = publication/'unified-market.json'
    before = alias.read_bytes(), alias.stat().st_ino, pointer.read_bytes()
    add(counter=2, minute=40)
    export(source)
    def fail(*args, **kwargs):
        raise OSError('fixture hardlink/replace failure')
    monkeypatch.setattr(exporter.os, operation, fail)
    with pytest.raises(OSError):
        exporter.publish_market_files(base, publication)
    assert (alias.read_bytes(), alias.stat().st_ino, pointer.read_bytes()) == before
    assert list(alias.parent.iterdir()) == [alias]


@pytest.mark.parametrize('seed', [False, True])
@pytest.mark.parametrize('damage', ['index', 'source', 'destination'])
def test_publication_corruption_fails_closed_before_alias_or_pointer_write(source, seed, damage):
    base, _, _, _, _, add = source
    add()
    export(source)
    publication = base/'publication'
    names = exporter.publish_market_files(base, publication)
    object_name, = (name for name in names if '/objects/' in name)
    alias_name, = (name for name in names if '/.delta/' in name)
    (publication/alias_name).unlink()
    root = publication if seed else base/'unified-data/market-public'
    if damage == 'index':
        path = root/'unified-market.json'
        value = json.loads(path.read_bytes())
        value['total_bytes'] += 1
        path.write_bytes(contract.encoded(resign(value)))
    else:
        path = (root if damage == 'source' else publication)/object_name
        path.write_bytes(b'corrupt')
    pointer = publication/'unified-market.json'
    prior = pointer.read_bytes(), pointer.stat().st_mtime_ns
    with pytest.raises(ValueError):
        exporter.publish_market_files(base, publication, seed_existing=seed)
    assert not (publication/alias_name).exists()
    assert (pointer.read_bytes(), pointer.stat().st_mtime_ns) == prior


@pytest.mark.parametrize('location', ['source_object', 'source_parent', 'pointer',
                                     'target_object', 'alias', 'alias_parent'])
@pytest.mark.parametrize('inside', [False, True])
def test_publication_rejects_symlink_files_and_parents_even_within_root(source, location, inside):
    base, _, _, _, _, add = source
    add()
    export(source)
    publication = base/'publication'
    names = exporter.publish_market_files(base, publication)
    object_name, = (name for name in names if '/objects/' in name)
    alias_name, = (name for name in names if '/.delta/' in name)
    root = base/'unified-data/market-public'
    path = {'source_object': root/object_name,
            'source_parent': (root/object_name).parent,
            'pointer': publication/'unified-market.json',
            'target_object': publication/object_name,
            'alias': publication/alias_name,
            'alias_parent': (publication/alias_name).parent}[location]
    directory = path.is_dir()
    destination = path.parent/'retained-original' if inside else base/'outside-original'
    path.rename(destination)
    try:
        path.symlink_to(destination, target_is_directory=directory)
    except OSError as error:
        if os.name == 'nt' and getattr(error, 'winerror', None) == 1314:
            pytest.skip('Windows account cannot create symlinks')
        raise
    prior = {item.relative_to(destination): item.read_bytes()
             for item in destination.rglob('*') if item.is_file()} if directory else {
                 Path('.'): destination.read_bytes()}
    with pytest.raises(ValueError, match='PATH_INVALID'):
        exporter.publish_market_files(base, publication)
    after = {item.relative_to(destination): item.read_bytes()
             for item in destination.rglob('*') if item.is_file()} if directory else {
                 Path('.'): destination.read_bytes()}
    assert after == prior


def test_missing_database_is_never_created(source):
    base = source[0]
    missing = base/'does-not-exist.sqlite'
    with pytest.raises(sqlite3.OperationalError):
        exporter.export_market(base, missing, now=NOW)
    assert not missing.exists()


def test_range_coverage_does_not_override_global_dataset_freshness(http):
    client, _, _ = http
    value = client.get('/unified/bars?symbol=301001&frequency=day'
                       '&start=2026-01-01&end=2026-01-02').json()
    assert value['state'] == 'OUTSIDE_WINDOW' and value['bars'] == []
    assert value['freshness'] == 'FRESH' and value['actual_end'] is not None


@pytest.mark.parametrize('kwargs', [{'receipt_at': NOW+timedelta(seconds=1)},
                                   {'complete': False}, {'status': 503}, {'scale': '240'}])
def test_incomplete_future_or_wrong_frequency_raw_receipts_are_never_exported(source, kwargs):
    source[-1](**kwargs)
    value = export(source)['index']
    entry = next(row for row in value['entries'] if row['symbol'] == '301001'
                 and row['frequency'] == '5min')
    assert entry['state'] == 'QUARANTINED' and entry['shards'] == []


def test_expired_current_calendar_returns_structured_503_even_for_empty_target(http, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return datetime(2027, 1, 8, 10, tzinfo=UTC).astimezone(tz or UTC)
    monkeypatch.setattr(api, 'datetime', Clock)
    response = http[0].get('/unified/bars?symbol=600036')
    assert response.status_code == 503
    assert response.json()['detail'] == 'UNIFIED_MARKET_CURRENT_CALENDAR_UNAVAILABLE'


def test_budget_failure_does_not_replace_last_success(source, monkeypatch):
    base, _, _, _, _, add = source
    add()
    export(source)
    pointer = base/'unified-data/market-public/unified-market.json'
    before = pointer.read_bytes()
    add(counter=2, minute=40)
    monkeypatch.setattr(exporter, 'MAX_CHUNKS', 1)
    with pytest.raises(ValueError, match='BUDGET'):
        export(source)
    assert pointer.read_bytes() == before


def test_decoded_cache_budget_preserves_existing_verified_working_set(source, monkeypatch):
    _, _, _, _, _, add = source
    add()
    add(counter=2, minute=40)
    monkeypatch.setattr(exporter, 'MAX_CACHE_ROWS', 1)
    first = export(source)
    second = export(source)
    assert first['statistics']['cached_rows'] == second['statistics']['cached_rows'] == 1
    assert second['statistics']['cached_batches'] == 1
    assert second['statistics']['replayed_batches'] == 1
    assert second['statistics']['cache_estimated_bytes'] <= exporter.MAX_CACHE_ESTIMATED_BYTES
    assert first['index'] == second['index']


@pytest.mark.parametrize('budget', ['rows', 'bytes'])
def test_uncacheable_batches_keep_full_output_and_replay_without_size_encoding(
    source, monkeypatch, budget,
):
    _, _, _, _, _, add = source
    add()
    add('day', counter=2)
    expected = export(source)['index']
    exporter._CACHE.clear()
    if budget == 'rows':
        monkeypatch.setattr(exporter, 'MAX_CACHE_ROWS', 0)
    else:
        # One row's fixed overhead already consumes this entire allowance.
        monkeypatch.setattr(exporter, 'MAX_CACHE_ESTIMATED_BYTES', 1024)
    original = exporter.encoded
    sized = []
    def encode(value):
        if set(value) == {'rows'}:
            sized.append(value)
        return original(value)
    monkeypatch.setattr(exporter, 'encoded', encode)
    result = export(source)
    assert result['index'] == expected
    assert result['statistics']['replayed_batches'] == 2
    assert result['statistics']['cached_batches'] == result['statistics']['cached_rows'] == 0
    assert result['statistics']['cache_estimated_bytes'] == 0
    assert sized == []


def grouped_replay_material(source, frequency, damage=None):
    _, _, _, _, store, add = source
    manifests = [add(frequency, counter=1),
                 add(frequency, symbol='600036', counter=2,
                     minute=40 if frequency == '5min' else 35)]
    if frequency == '5min':
        manifests.append(add(counter=3, minute=40))
    captures = []
    records = []
    for manifest in manifests:
        for capture in store.read_captures(manifest['raw']):
            captures.append({key: capture[key] for key in ('source_id', 'request',
                'received_at', 'status_code', 'body', 'transport_error', 'body_complete')})
        rows = pq.read_table(io.BytesIO(store.read_blob(manifest['normalized']))).to_pylist()
        for row in rows:
            stamp = datetime.fromisoformat(row['timestamp'])
            bar = Bar(row['symbol'], row['exchange'], stamp, stamp.date(),
                      *(Decimal(row[field]) for field in
                        ('open', 'high', 'low', 'close', 'volume')))
            records.append(IncrementalBar(bar, frequency,
                Decimal(row['amount']) if row['amount'] is not None else None,
                Decimal(row['vwap']) if row['vwap'] is not None else None,
                datetime.fromisoformat(row['source_clock']) if row['source_clock'] else None,
                row['amount_basis']))
    if frequency == '5min':
        first, other, last = captures
        first['body'] = json.dumps(json.loads(first['body'])+json.loads(last['body'])).encode()
        captures = [other, first]
        if damage == 'missing_minute':
            first['body'] = json.dumps(json.loads(first['body'])[:1]).encode()
    else:
        kline_a, quote_a, kline_b, quote_b = captures
        captures = [quote_b, kline_a, quote_a, kline_b]
    if damage == 'duplicate_kline':
        captures.append(next(capture for capture in captures
                             if capture['source_id'] == 'sina_klines'))
    spec = deepcopy(manifests[0]['spec'])
    spec['symbols'] = ['301001', '600036']
    if frequency == '5min':
        spec['target_end'] = '2026-10-26T09:40:00+08:00'
        spec['after_by_symbol'] = {'301001': '2026-10-26T09:30:00+08:00',
                                   '600036': '2026-10-26T09:35:00+08:00'}
    else:
        spec['after_by_symbol'] = dict.fromkeys(spec['symbols'])
    manifest = store.commit('e'*32, spec, store.capture(captures), records,
        {symbol: {'state': 'SUCCEEDED'} for symbol in spec['symbols']}, completed_at=NOW)
    return store._batch_material(manifest['batch_id'])


@pytest.mark.parametrize('frequency', ['day', '5min'])
def test_grouped_replay_preserves_capture_order_and_each_symbols_own_window(source, frequency):
    _, _, calendar, _, _, _ = source
    material = grouped_replay_material(source, frequency)
    _, captures, rows = material.validate()
    for row in rows:
        code = ('sh' if row['symbol'].startswith('6') else 'sz')+row['symbol']
        expected = [capture['body_sha256'] for capture in captures
                    if capture['request'].get('symbol') == code
                    or code in capture['request'].get('list', '').split(',')]
        assert row['source_hashes'] == expected
    exporter._replay(material, calendar, {'301001': 'STOCK', '600036': 'STOCK'})
    assert len(rows) == (3 if frequency == '5min' else 2)
    if frequency == 'day':
        rows[0]['source_hashes'].reverse()
        with pytest.raises(ValueError, match='RAW_NORMALIZED_REPLAY_MISMATCH'):
            exporter._replay(material, calendar, {'301001': 'STOCK', '600036': 'STOCK'})


@pytest.mark.parametrize(('damage', 'error'), [
    ('missing_minute', 'RAW_MINUTE_WINDOW_INCOMPLETE'),
    ('duplicate_kline', 'RAW_SOURCE_OR_CLOCK_INVALID'),
])
def test_grouped_replay_still_rejects_missing_window_or_duplicate_response(source, damage, error):
    _, _, calendar, _, _, _ = source
    material = grouped_replay_material(source, '5min', damage)
    with pytest.raises(ValueError, match=error):
        exporter._replay(material, calendar, {'301001': 'STOCK', '600036': 'STOCK'})


def test_export_context_is_signed_once_and_distinct_contexts_cannot_reuse_cached_replay(
    source, monkeypatch,
):
    _, _, calendar, _, store, add = source
    manifest = add()
    add('day', counter=2)
    add(counter=3, minute=40)
    kinds = {'301001': 'STOCK', '600036': 'STOCK'}
    original = exporter.signed
    contexts = []
    def sign(value):
        if value == calendar.document:
            contexts.append('calendar')
        elif value == kinds:
            contexts.append('kinds')
        return original(value)
    monkeypatch.setattr(exporter, 'signed', sign)
    result = export(source)
    assert contexts == ['calendar', 'kinds']
    assert result['statistics']['replayed_batches'] == 3
    def load(cal, security_kinds):
        stats = {'source_bytes_read': 0, 'cached_batches': 0, 'replayed_batches': 0}
        context = (original(cal.document)['evidence_sha256'],
                   original(security_kinds)['evidence_sha256'])
        exporter._load(store, manifest['batch_id'], stats, cal, security_kinds, context)
        return stats
    assert load(calendar, kinds)['cached_batches'] == 1
    changed_calendar = Calendar({**calendar.document, 'limitation': 'changed fixture context'})
    assert load(changed_calendar, kinds)['replayed_batches'] == 1
    changed_kinds = {**kinds, '600036': 'ETF'}
    assert load(calendar, changed_kinds)['replayed_batches'] == 1
    assert load(calendar, kinds)['cached_batches'] == 1


def test_grouped_rows_preserve_all_symbols_bars_and_duplicate_receipt_lineage(source, monkeypatch):
    base, database, _, _, store, add = source
    manifests = [add(), add(counter=2), add(counter=3, minute=40),
                 add(symbol='600036', counter=4)]
    paired = [manifests[0], manifests[3]]
    records = []
    captures = []
    for manifest in paired:
        captures.extend({key: row[key] for key in ('source_id', 'request', 'received_at',
            'status_code', 'body', 'transport_error', 'body_complete')}
            for row in store.read_captures(manifest['raw']))
        for row in pq.read_table(io.BytesIO(store.read_blob(manifest['normalized']))).to_pylist():
            stamp = datetime.fromisoformat(row['timestamp'])
            bar = Bar(row['symbol'], row['exchange'], stamp, stamp.date(),
                      *(Decimal(row[field]) for field in
                        ('open', 'high', 'low', 'close', 'volume')))
            records.append(IncrementalBar(bar, '5min', None, None, None,
                                          row['amount_basis']))
    spec = deepcopy(paired[0]['spec'])
    spec['symbols'] = sorted(manifest['spec']['symbols'][0] for manifest in paired)
    spec['after_by_symbol'] = {symbol: spec['after_by_symbol']['301001']
                               for symbol in spec['symbols']}
    combined = store.commit('f'*32, spec, store.capture(captures), records,
                             {symbol: {'state': 'SUCCEEDED'} for symbol in spec['symbols']},
                             completed_at=NOW)
    with sqlite3.connect(database) as connection:
        for manifest in paired:
            connection.execute('UPDATE collection_chunks SET batch_id=? WHERE batch_id=?',
                               (combined['batch_id'], manifest['batch_id']))
        connection.execute('INSERT INTO collection_batches VALUES(?,?,?)',
                           (combined['batch_id'], 'COMPLETE', combined['evidence_sha256']))
    manifests = [manifests[1], manifests[2], combined]
    original = exporter._load
    class OnceRows(list):
        def __iter__(self):
            assert not getattr(self, 'iterated', False), 'export scans each decoded batch once'
            self.iterated = True
            return super().__iter__()
    def load(*args, **kwargs):
        manifest, rows = original(*args, **kwargs)
        return manifest, OnceRows(rows)
    monkeypatch.setattr(exporter, '_load', load)
    value = export(source)['index']
    for symbol in ('301001', '600036'):
        entry = next(row for row in value['entries'] if row['symbol'] == symbol
                     and row['frequency'] == '5min')
        ref, = entry['shards']
        expected_rows = {}
        expected_sources = []
        for manifest in manifests:
            if symbol not in manifest['spec']['symbols']:
                continue
            rows = pq.read_table(io.BytesIO(store.read_blob(manifest['normalized']))).to_pylist()
            for original in rows:
                if original['symbol'] != symbol:
                    continue
                bar = {key: original[key] for key in sorted(contract.BAR_FIELDS)
                       if key != 'amount_basis'}
                bar['amount_basis'] = original['amount_basis']
                expected_rows[bar['timestamp']] = bar
            expected_sources.append({'batch_id': manifest['batch_id'],
                'manifest_sha256': manifest['evidence_sha256'],
                'raw_sha256': manifest['raw']['sha256'],
                'normalized_sha256': manifest['normalized']['sha256']})
        raw = (base/'unified-data/market-public/unified-market/objects'/
               (ref['sha256']+'.json')).read_bytes()
        shard = contract.verify_shard_bytes(raw, ref, value, entry, NOW)
        expected = resign({**shard, 'bars': sorted(expected_rows.values(),
                                                key=lambda row: row['timestamp']),
                           'source_batches': sorted(expected_sources,
                                                    key=lambda row: row['batch_id'])})
        assert raw == contract.encoded(expected)
        assert len(shard['bars']) == (2 if symbol == '301001' else 1)
        assert len(shard['source_batches']) == (3 if symbol == '301001' else 1)


def test_shards_are_encoded_once_and_identical_raw_bytes_drive_reference_and_writer(
    source, monkeypatch,
):
    base, _, _, _, _, add = source
    add()
    add('day', counter=2)
    calls = []
    original = contract.encoded
    def encode(value):
        raw = original(value)
        if value.get('schema_version') == 'qtrade.unified-market-shard.v1':
            calls.append(raw)
        return raw
    monkeypatch.setattr(contract, 'encoded', encode)
    value = export(source)['index']
    assert len(calls) == value['total_files'] == 2
    assert sum(map(len, calls)) == value['total_bytes']
    for raw in calls:
        digest = exporter.sha256(raw).hexdigest()
        assert (base/'unified-data/market-public/unified-market/objects'/
                (digest+'.json')).read_bytes() == raw


@pytest.mark.parametrize('failure', ['link', 'directory_fsync'])
def test_preencoded_object_failure_keeps_old_pointer_and_cleans_temporary(source, monkeypatch,
                                                                         failure):
    base, _, _, _, _, add = source
    add()
    export(source)
    pointer = base/'unified-data/market-public/unified-market.json'
    before = pointer.read_bytes()
    add(counter=2, minute=40)
    def fail(*args, **kwargs):
        raise OSError('fixture write failure')
    if failure == 'link':
        monkeypatch.setattr(exporter.os, 'link', fail)
    else:
        monkeypatch.setattr(exporter, '_fsync_directory', fail)
    with pytest.raises(OSError):
        export(source)
    assert pointer.read_bytes() == before
    assert not list((pointer.parent/'unified-market/objects').glob('*.tmp'))


def test_preencoded_object_writer_never_overwrites_existing_bytes(tmp_path, monkeypatch):
    path = tmp_path/'object.json'
    path.write_bytes(b'original')
    monkeypatch.setattr(exporter, '_fsync_directory', lambda _: None)
    with pytest.raises(FileExistsError):
        exporter._write_object(path, b'changed')
    assert path.read_bytes() == b'original'
    assert list(tmp_path.iterdir()) == [path]


@pytest.mark.parametrize(('code', 'reported'), [
    ('UNIFIED_MARKET_CHUNK_BUDGET', 'UNIFIED_MARKET_CHUNK_BUDGET'),
    ('private-source-path-or-credential', None)])
def test_export_attempt_failure_is_separate_safe_and_retains_last_success(source, monkeypatch,
                                                                       code, reported):
    base, database, _, _, _, add = source
    add()
    first = export(source)
    pointer = base/'unified-data/market-public/unified-market.json'
    success = base/'unified-data/market-export-state.json'
    before = pointer.read_bytes(), success.read_bytes(), pointer.stat().st_mtime_ns
    def failed(*args, **kwargs):
        raise ValueError(code)
    monkeypatch.setattr(exporter, 'export_market', failed)
    exporter.publish_market_files(base, base/'publication', database)
    assert (pointer.read_bytes(), success.read_bytes(), pointer.stat().st_mtime_ns) == before
    attempt = json.loads((base/'unified-data/market-export-attempt.json').read_bytes())
    assert attempt['state'] == 'FAILED' and attempt['index_id'] is None
    assert attempt['error_class'] == 'ValueError' and attempt['error_code'] == reported
    assert datetime.fromisoformat(attempt['completed_at']) >= datetime.fromisoformat(
        attempt['started_at'])
    assert attempt['elapsed_seconds'] >= 0
    assert json.loads((base/'publication/unified-market.json').read_bytes()) == first['index']
    if reported is None:
        assert code not in json.dumps(attempt)


def test_export_attempt_success_binds_real_index_without_rewriting_its_clock(source, monkeypatch):
    base, database, _, _, _, add = source
    add()
    original = exporter.export_market
    monkeypatch.setattr(exporter, 'export_market', lambda base, database:
                        original(base, database, now=NOW))
    exporter.publish_market_files(base, base/'publication', database)
    attempt = json.loads((base/'unified-data/market-export-attempt.json').read_bytes())
    index = json.loads((base/'unified-data/market-public/unified-market.json').read_bytes())
    assert attempt['state'] == 'SUCCEEDED' and attempt['index_id'] == index['evidence_sha256']
    assert index['published_at'] == NOW.isoformat()
    assert attempt['error_class'] is None and attempt['error_code'] is None


def test_chunk_metadata_budget_covers_more_than_sixty_thousand_without_dropping_rows(source):
    _, database, calendar, master, _, _ = source
    # Exercise metadata selection only: synthetic complete-batch rows do not
    # establish source admission without the subsequent immutable-store replay.
    count = 60_001
    batches = (count+49)//50
    rows = []
    for number in range(count):
        clock = SINA_FIVE_MINUTE_SESSION_TIMES[number//1500]
        stamp = datetime(2026, 10, 26, clock.hour, clock.minute, tzinfo=SHANGHAI).isoformat()
        rows.append((f'{number//50:032x}', master['universe_version'], '5min',
                     f'{300000+number%1500:06}', stamp, stamp, 1))
    with sqlite3.connect(database) as connection:
        connection.execute('CREATE UNIQUE INDEX chunk_keys ON collection_chunks(batch_id,symbol)')
        connection.executemany('INSERT INTO collection_batches VALUES(?,?,?)',
            [(f'{batch:032x}', 'COMPLETE', 'a'*64) for batch in range(batches)])
        connection.executemany('INSERT INTO collection_chunks VALUES(?,?,?,?,?,?,?)', rows)
    windows = {freq: contract.sessions(calendar, calendar.expected(NOW,
        'daily' if freq == 'day' else 'minute').date(), 20 if freq == 'day' else 3)
        for freq in ('day', '5min')}
    selected = exporter._chunks(database, master['universe_version'], windows)
    assert exporter.MAX_CHUNKS == exporter.MAX_BATCHES*50 == 600_000
    assert len(selected) == count
    assert {(row['batch_id'], row['symbol']) for row in selected} == {
        (row[0], row[3]) for row in rows}
    assert sum(row['bar_count'] for row in selected) == count


def test_chunk_limit_is_cumulative_and_fetches_only_remaining_plus_one(source, monkeypatch):
    base, database, _, _, _, add = source
    add('day')
    add(counter=2)
    add(counter=3, minute=40)
    monkeypatch.setattr(exporter, 'MAX_CHUNKS', 3)
    first = export(source)
    assert first['statistics']['selected_chunks'] == 3
    pointer = base/'unified-data/market-public/unified-market.json'
    success = base/'unified-data/market-export-state.json'
    before = pointer.read_bytes(), success.read_bytes()
    queries = []
    original = sqlite3.connect
    def connect(*args, **kwargs):
        connection = original(*args, **kwargs)
        connection.set_trace_callback(queries.append)
        return connection
    monkeypatch.setattr(exporter.sqlite3, 'connect', connect)
    monkeypatch.setattr(exporter, 'MAX_CHUNKS', 2)
    with pytest.raises(ValueError, match='CHUNK_BUDGET'):
        export(source)
    selected = [query for query in queries if 'SELECT c.*' in query]
    assert len(selected) == 2
    assert selected[0].rstrip().endswith('LIMIT 3')
    assert selected[1].rstrip().endswith('LIMIT 2')  # One day row consumed the shared budget.
    assert (pointer.read_bytes(), success.read_bytes()) == before


@pytest.mark.parametrize('cap', [1, 2])
def test_single_frequency_chunk_cap_and_cap_plus_one_reject_without_new_pointer(source,
                                                                              monkeypatch, cap):
    base, _, _, _, _, add = source
    add()
    monkeypatch.setattr(exporter, 'MAX_CHUNKS', cap)
    export(source)
    pointer = base/'unified-data/market-public/unified-market.json'
    before = pointer.read_bytes()
    add(counter=2, minute=40)
    if cap == 2:
        assert export(source)['statistics']['selected_chunks'] == cap
        before = pointer.read_bytes()
        add(counter=3, minute=45)
    with pytest.raises(ValueError, match='CHUNK_BUDGET'):
        export(source)
    assert pointer.read_bytes() == before


def test_material_chain_reads_each_blob_once_and_decodes_only_misses(source, monkeypatch):
    _, _, _, _, store, add = source
    add()
    add('day', counter=2)
    reads, decodes, parquet_reads, manifest_reads = [], [], [], []
    original_blob = store_module.UnifiedBatchStore.read_blob
    original_decode = store_module._decode_captures
    original_file = store_module.pq.ParquetFile
    original_bytes = Path.read_bytes
    def read_blob(self, ref, **kwargs):
        raw = original_blob(self, ref, **kwargs)
        reads.append(len(raw))
        return raw
    def decode(ref, raw):
        decodes.append(ref['sha256'])
        return original_decode(ref, raw)
    class Parquet:
        def __init__(self, *args, **kwargs):
            self.file = original_file(*args, **kwargs)
        def __getattr__(self, name):
            return getattr(self.file, name)
        def read(self, *args, **kwargs):
            parquet_reads.append(1)
            return self.file.read(*args, **kwargs)
    def read_bytes(path):
        raw = original_bytes(path)
        if path.parent == store.root/'batches':
            manifest_reads.append(len(raw))
        return raw
    monkeypatch.setattr(store_module.UnifiedBatchStore, 'read_blob', read_blob)
    monkeypatch.setattr(store_module, '_decode_captures', decode)
    monkeypatch.setattr(store_module.pq, 'ParquetFile', Parquet)
    monkeypatch.setattr(Path, 'read_bytes', read_bytes)
    first = export(source)
    assert len(reads) == 4 and len(decodes) == len(parquet_reads) == 2
    assert len(manifest_reads) == 4  # Recheck exact manifest bytes after each admission replay.
    assert first['statistics']['source_bytes_read'] == sum(reads)+sum(manifest_reads)
    reads.clear()
    decodes.clear()
    parquet_reads.clear()
    manifest_reads.clear()
    second = export(source)
    assert first['index'] == second['index']
    assert len(reads) == 4 and decodes == parquet_reads == []
    assert len(manifest_reads) == 4
    assert second['statistics']['source_bytes_read'] == sum(reads)+sum(manifest_reads)


def test_every_published_bar_has_one_full_validation_and_cold_batches_still_replay(source):
    base, _, _, _, _, add = source
    add()
    add(counter=2)  # Identical re-observation retains both receipt bindings.
    add(counter=3, minute=40)
    add('day', counter=4)
    add(symbol='600036', counter=5)
    for expected_replays in (5, 0):
        checked, replayed = [], []
        previous = sys.getprofile()
        def observe(frame, event, arg, checked=checked, replayed=replayed):
            if event == 'call':
                if frame.f_code is contract._validate_bar.__code__:
                    caller = frame.f_back
                    if caller.f_code.co_name == 'validate_bar':
                        caller = caller.f_back
                    checked.append((dict(frame.f_locals['row']), caller.f_code.co_name))
                elif frame.f_code is exporter._replay.__code__:
                    replayed.append(frame.f_locals['material'].manifest['batch_id'])
        try:
            sys.setprofile(observe)
            value = export(source)['index']
        finally:
            sys.setprofile(previous)
        published = [bar for entry in value['entries'] for ref in entry['shards']
                     for bar in json.loads((base/'unified-data/market-public/unified-market/objects'
                                            /(ref['sha256']+'.json')).read_bytes())['bars']]
        assert len(checked) == len(published) == 4
        assert all(caller == '_validated_shard_bytes' for _, caller in checked)
        assert sorted((bar['symbol'], bar['timestamp']) for bar, _ in checked) == sorted(
            (bar['symbol'], bar['timestamp']) for bar in published)
        assert len(replayed) == expected_replays


@pytest.mark.parametrize(('field', 'invalid'), [
    ('exchange', 'SSE'), ('frequency', 'day'), ('close', '100'), ('volume', '-1'),
    ('amount_basis', 'UNVERIFIED'), ('price_basis', 'ADJUSTED'),
    ('historical_available_at', NOW.isoformat()), ('trading_day', '2026-10-23'),
    ('vwap', '11'),
])
def test_invalid_retained_cached_row_fails_entire_export_and_retains_last_success(
    source, field, invalid,
):
    base, _, _, _, _, add = source
    add()
    add('day', counter=2)
    export(source)
    pointer = base/'unified-data/market-public/unified-market.json'
    state = base/'unified-data/market-export-state.json'
    before = pointer.read_bytes(), state.read_bytes(), pointer.stat().st_mtime_ns
    manifest, rows = next(value for value in exporter._CACHE.values()
                          if value[0]['spec']['frequency'] == '5min')
    assert manifest['batch_id'] == f'{1:032x}'
    rows[0][field] = invalid
    with pytest.raises(ValueError, match='UNIFIED_MARKET_|INVALID_OHLC|INVALID_SOURCE_NUMBER'):
        export(source)
    assert (pointer.read_bytes(), state.read_bytes(), pointer.stat().st_mtime_ns) == before


@pytest.mark.parametrize(('field', 'different'), [
    ('close', '11'), ('amount_basis', 'SAME_VENDOR_ENDPOINT_MATCH_NOT_INDEPENDENT_RECONCILIATION'),
])
def test_different_cached_duplicate_never_hides_conflict_in_retained_shard(
    source, field, different,
):
    _, _, _, _, _, add = source
    add()
    add(counter=2)
    export(source)
    _, rows = next(value for value in exporter._CACHE.values()
                   if value[0]['batch_id'] == f'{2:032x}')
    rows[0][field] = different
    entry = next(row for row in export(source)['index']['entries']
                 if row['symbol'] == '301001' and row['frequency'] == '5min')
    assert entry['state'] == 'QUARANTINED' and entry['shards'] == []
    assert entry['issues'] == ['CONFLICTING_ACCEPTED_BARS']


@pytest.mark.parametrize('invalid', [False, True])
def test_outside_window_rows_still_receive_full_validation_before_discard(source, invalid):
    base, database, _, _, store, add = source
    manifest = add()
    capture, = store.read_captures(manifest['raw'])
    records = []
    body = json.loads(capture['body'])
    body.append({**body[0], 'day': '2026-10-26 09:40:00'})
    capture['body'] = json.dumps(body).encode()
    for minute in (35, 40):
        stamp = datetime(2026, 10, 26, 9, minute, tzinfo=SHANGHAI)
        bar = Bar('301001', 'SZSE', stamp, stamp.date(), Decimal('10'), Decimal('12'),
                  Decimal('9'), Decimal('10'), Decimal('100'))
        records.append(IncrementalBar(bar, '5min', None, None, None, 'VENDOR_CLAIM_UNVERIFIED'))
    spec = deepcopy(manifest['spec'])
    spec['target_end'] = records[-1].bar.timestamp.isoformat()
    capture = {key: capture[key] for key in ('source_id', 'request', 'received_at',
        'status_code', 'body', 'transport_error', 'body_complete')}
    combined = store.commit('f'*32, spec, store.capture([capture]), records,
                            {'301001': {'state': 'SUCCEEDED'}}, completed_at=NOW)
    with sqlite3.connect(database) as connection:
        connection.execute('DELETE FROM collection_chunks')
        connection.execute('INSERT INTO collection_batches VALUES(?,?,?)',
                           (combined['batch_id'], 'COMPLETE', combined['evidence_sha256']))
        connection.execute('INSERT INTO collection_chunks VALUES(?,?,?,?,?,?,?)',
            (combined['batch_id'], spec['universe_version'], '5min', '301001',
             records[0].bar.timestamp.isoformat(), records[-1].bar.timestamp.isoformat(), 2))
    # Establish real first-load receipt replay. Then inject a spanning cached batch:
    # its older row must be checked even though only the recent row could be published.
    first = export(source)
    assert first['statistics']['replayed_batches'] == 1
    cached_manifest, rows = next(iter(exporter._CACHE.values()))
    rows[0]['timestamp'] = '2026-10-21T09:35:00+08:00'
    rows[0]['trading_day'] = '2026-10-21'
    if invalid:
        rows[0]['close'] = '100'
    cached_manifest['outcomes']['301001']['start'] = rows[0]['timestamp']
    with sqlite3.connect(database) as connection:
        connection.execute('UPDATE collection_chunks SET start_time=?', (rows[0]['timestamp'],))
    checked = []
    previous = sys.getprofile()
    def observe(frame, event, arg):
        if event == 'call' and frame.f_code is contract._validate_bar.__code__:
            caller = frame.f_back
            if caller.f_code.co_name == 'validate_bar':
                caller = caller.f_back
            checked.append((frame.f_locals['row']['timestamp'], caller.f_code.co_name))
    try:
        sys.setprofile(observe)
        value = export(source)['index']
    finally:
        sys.setprofile(previous)
    entry = next(row for row in value['entries'] if row['symbol'] == '301001'
                 and row['frequency'] == '5min')
    assert checked[0] == (rows[0]['timestamp'], 'collect_batch')
    if invalid:
        assert entry['state'] == 'QUARANTINED' and entry['shards'] == []
        assert entry['issues'] == ['SOURCE_REPLAY_FAILED']
        assert len(checked) == 1
    else:
        assert entry['state'] == 'AVAILABLE'
        ref, = entry['shards']
        shard = json.loads((base/'unified-data/market-public/unified-market/objects'
                            /(ref['sha256']+'.json')).read_bytes())
        assert [row['timestamp'] for row in shard['bars']] == [rows[1]['timestamp']]
        assert checked == [(rows[0]['timestamp'], 'collect_batch'),
                           (rows[1]['timestamp'], '_validated_shard_bytes')]


def test_manifest_switch_during_same_material_replay_is_rejected_before_cache(source, monkeypatch):
    _, _, calendar, _, store, add = source
    manifest = add()
    kinds = {'301001': 'STOCK', '600036': 'STOCK'}
    context = (signed(calendar.document)['evidence_sha256'], signed(kinds)['evidence_sha256'])
    original = exporter._replay
    def replay(material, *args, **kwargs):
        original(material, *args, **kwargs)
        path = store.batch_path(manifest['batch_id'])
        # Same parsed document, different physical source.
        path.write_bytes(path.read_bytes()+b'\n')
    monkeypatch.setattr(exporter, '_replay', replay)
    stats = {'source_bytes_read': 0, 'cached_batches': 0, 'replayed_batches': 0}
    with pytest.raises(ValueError, match='BATCH_CHANGED_DURING_REPLAY'):
        exporter._load(store, manifest['batch_id'], stats, calendar, kinds, context)
    assert exporter._CACHE == {} and stats['replayed_batches'] == 0


def test_cached_material_rechecks_manifest_after_physical_blob_read(source, monkeypatch):
    _, _, calendar, _, store, add = source
    manifest = add()
    export(source)
    kinds = {'301001': 'STOCK', '600036': 'STOCK'}
    context = (signed(calendar.document)['evidence_sha256'], signed(kinds)['evidence_sha256'])
    original = store.read_blob
    def read_blob(ref, **kwargs):
        raw = original(ref, **kwargs)
        if ref['suffix'] == '.parquet':
            path = store.batch_path(manifest['batch_id'])
            path.write_bytes(path.read_bytes()+b'\n')
        return raw
    monkeypatch.setattr(store, 'read_blob', read_blob)
    stats = {'source_bytes_read': 0, 'cached_batches': 0, 'replayed_batches': 0}
    with pytest.raises(ValueError, match='BATCH_CHANGED_DURING_REPLAY'):
        exporter._load(store, manifest['batch_id'], stats, calendar, kinds, context)
    assert stats['cached_batches'] == stats['replayed_batches'] == 0
    manifest_size = store.batch_path(manifest['batch_id']).stat().st_size
    assert stats['source_bytes_read'] == (manifest['raw']['bytes']+manifest['normalized']['bytes']
                                         +2*manifest_size-1)


def test_private_material_has_no_caller_supplied_source_or_validation_bypass(source):
    import inspect
    _, _, _, _, store, add = source
    manifest = add()
    parameters = inspect.signature(store._batch_material).parameters
    assert list(parameters) == ['batch_id', '_read_observer']
    assert parameters['_read_observer'].kind is inspect.Parameter.KEYWORD_ONLY
    assert parameters['_read_observer'].default is None
    assert list(inspect.signature(store.load).parameters) == ['batch_id']
    # An observer can return any value; it cannot supply or replace source bytes.
    observed = store._batch_material(manifest['batch_id'],
                                     _read_observer=lambda count, stage: b'forged')
    assert observed.validate()[0] == manifest
    with pytest.raises(TypeError):
        store._batch_material(manifest['batch_id'], raw=b'forged')
    with pytest.raises(TypeError):
        store.load(manifest['batch_id'], captures=[])


def test_missing_normalized_source_still_counts_material_reads_toward_budget(source, monkeypatch):
    _, _, calendar, _, store, add = source
    manifest = add()
    manifest['normalized'] = None
    manifest = resign(manifest)
    path = store.batch_path(manifest['batch_id'])
    raw_manifest = contract.encoded(manifest)
    path.write_bytes(raw_manifest)
    kinds = {'301001': 'STOCK', '600036': 'STOCK'}
    context = (signed(calendar.document)['evidence_sha256'], signed(kinds)['evidence_sha256'])
    stats = {'source_bytes_read': 0, 'cached_batches': 0, 'replayed_batches': 0}
    with pytest.raises(ValueError, match='NORMALIZED_SOURCE_MISSING'):
        exporter._load(store, manifest['batch_id'], stats, calendar, kinds, context)
    actual = len(raw_manifest)+manifest['raw']['bytes']
    assert stats['source_bytes_read'] == actual
    monkeypatch.setattr(exporter, 'SOURCE_BYTE_BUDGET', actual)
    def forbidden(path):
        raise AssertionError('EXHAUSTED_BUDGET_MUST_PRECEDE_ANOTHER_PHYSICAL_READ')
    monkeypatch.setattr(Path, 'read_bytes', forbidden)
    with pytest.raises(ValueError, match='SOURCE_BYTE_BUDGET'):
        exporter._load(store, manifest['batch_id'], stats, calendar, kinds, context)
    assert stats['source_bytes_read'] == actual
    assert exporter._CACHE == {}




def test_serial_shard_refs_come_from_validated_bytes(source, monkeypatch):
    base, _, _, _, _, add = source
    add()
    original = exporter._validated_shard_bytes
    def mutate_after_validation(shard, *args):
        value, raw = original(shard, *args)
        shard['evidence_sha256'] = '0'*64
        shard['bars'][0]['timestamp'] = '1900-01-01T00:00:00+08:00'
        return value, raw
    monkeypatch.setattr(exporter, '_validated_shard_bytes', mutate_after_validation)
    value = export(source)['index']
    ref = next(row['shards'][0] for row in value['entries'] if row['shards'])
    raw = (base/'unified-data/market-public/unified-market/objects'
           /(ref['sha256']+'.json')).read_bytes()
    shard = json.loads(raw)
    assert ref == {'sha256': sha256(raw).hexdigest(), 'bytes': len(raw),
        'evidence_sha256': shard['evidence_sha256'], 'count': len(shard['bars']),
        'start': shard['bars'][0]['timestamp'], 'end': shard['bars'][-1]['timestamp']}


def test_worker_frame_bounds_before_body_read():
    import struct
    class HeaderOnly(io.BytesIO):
        def read(self, count=-1):
            assert self.tell() < 5, 'BODY_MUST_NOT_BE_READ'
            return super().read(count)
    for tag, limit in exporter._WORKER_FRAME_LIMITS.items():
        source = HeaderOnly(struct.pack('!I', limit+1)+tag)
        with pytest.raises(ValueError, match='WORKER_FRAME_BOUND'):
            exporter._read_worker_frame(source, exporter._WORKER_INPUT_LIMIT)
    with pytest.raises(ValueError, match='WORKER_FRAME_BOUND'):
        exporter._read_worker_frame(HeaderOnly(struct.pack('!I', 20)+b'X'),
                                    exporter._WORKER_INPUT_LIMIT)


def test_unreaped_owned_worker_blocks_new_spawn(monkeypatch):
    class Unreaped:
        def poll(self):
            return None
    monkeypatch.setattr(exporter, '_UNREAPED_SHARD_WORKERS', [Unreaped()])
    def forbidden(*args, **kwargs):
        raise AssertionError('MUST_NOT_SPAWN')
    monkeypatch.setattr(exporter.subprocess, 'Popen', forbidden)
    with pytest.raises(ValueError, match='PREVIOUS_CHILD_UNREAPED'):
        exporter._ShardEncoders(None, NOW, time.monotonic()+5)


def test_worker_job_enters_pipe_before_building_the_next_shard(monkeypatch):
    pool = object.__new__(exporter._ShardEncoders)
    pool.workers = [{'output': None}, {'output': None}]
    monkeypatch.setattr(pool, 'close', lambda success: None)
    def send(state, body):
        state['output'] = body
    def pump():
        for state in pool.workers:
            if state['output'] is not None:
                state['result'] = (json.loads(state['output'][5:]), b'validated')
                state['output'] = None
    monkeypatch.setattr(pool, 'send', send)
    monkeypatch.setattr(pool, 'pump', pump)
    def shards():
        yield {'evidence_sha256': '1'*64}
        # Source replay can be expensive. A merely queued selector write leaves
        # the child idle during this work and turns validation into serial waves.
        assert pool.workers[0]['output'] is None
        yield {'evidence_sha256': '2'*64}
    assert len(list(pool.encode(shards()))) == 2


@pytest.fixture
def force_shard_workers(source, monkeypatch):
    if os.name != 'posix':
        pytest.skip('POSIX selector pipes; Windows chooses serial before spawn')
    monkeypatch.setattr(exporter, '_SHARD_WORKER_MIN_TARGETS', 0)
    owned = []
    original = exporter.subprocess.Popen
    def create(*args, **kwargs):
        proc = original(*args, **kwargs)
        owned.append(proc)
        return proc
    monkeypatch.setattr(exporter.subprocess, 'Popen', create)
    yield owned
    assert all(proc.poll() is not None for proc in owned)
    assert not exporter._UNREAPED_SHARD_WORKERS
    assert not any(thread.name.startswith('market-source') for thread in threading.enumerate())


def test_real_worker_export_exact_serial_bytes_and_join_before_index(
    source, force_shard_workers, monkeypatch,
):
    base, _, _, _, _, add = source
    add()
    add('day', counter=2)
    add(symbol='600036', counter=3)
    monkeypatch.setattr(exporter, '_SHARD_WORKER_MIN_TARGETS', 99)
    expected = export(source)['index']
    expected_bytes = {path.name: path.read_bytes() for path in
        (base/'unified-data/market-public/unified-market/objects').glob('*.json')}
    monkeypatch.setattr(exporter, '_SHARD_WORKER_MIN_TARGETS', 0)
    atomic = exporter.write_json_atomic
    def write(path, value, **kwargs):
        if path.name == 'unified-market.json':
            assert len(force_shard_workers) == 2
            assert all(proc.poll() == 0 for proc in force_shard_workers)
        atomic(path, value, **kwargs)
    monkeypatch.setattr(exporter, 'write_json_atomic', write)
    actual = export(source)['index']
    assert actual == expected
    assert {path.name: path.read_bytes() for path in
        (base/'unified-data/market-public/unified-market/objects').glob('*.json')} == expected_bytes


@pytest.mark.parametrize('failure', ['worker', 'object', 'ledger', 'parent_source', 'deadline'])
def test_real_workers_all_failures_reap_and_preserve_index(
    source, force_shard_workers, monkeypatch, failure,
):
    base, _, _, _, _, add = source
    add()
    monkeypatch.setattr(exporter, '_SHARD_WORKER_MIN_TARGETS', 99)
    export(source)
    pointer = base/'unified-data/market-public/unified-market.json'
    state = base/'unified-data/market-export-state.json'
    before = (pointer.read_bytes(), state.read_bytes(), pointer.stat().st_mtime_ns)
    add('day', counter=2)
    add(symbol='600036', counter=3)
    monkeypatch.setattr(exporter, '_SHARD_WORKER_MIN_TARGETS', 0)
    if failure == 'worker':
        original = exporter._ShardEncoders.send
        def send(pool, worker, body):
            if body[:1] == b'J':
                worker['process'].kill()
            original(pool, worker, body)
        monkeypatch.setattr(exporter._ShardEncoders, 'send', send)
    elif failure == 'object':
        monkeypatch.setattr(exporter, 'MAX_OBJECT_IO', 1)
        def fail(*args, **kwargs):
            raise OSError('OBJECT_FAULT')
        monkeypatch.setattr(exporter, '_write_object', fail)
    elif failure == 'ledger':
        original = exporter._chunks
        calls = 0
        def chunks(*args):
            nonlocal calls
            calls += 1
            if calls == 2:
                assert all(proc.poll() == 0 for proc in force_shard_workers)
                return []
            return original(*args)
        monkeypatch.setattr(exporter, '_chunks', chunks)
    elif failure == 'parent_source':
        inventory = exporter._worker_inventory
        changed = False
        def validate(*args):
            nonlocal changed
            changed = True
        def sources():
            value = inventory()
            if changed:
                value['scripts/publish_unified_market.py'] = '0'*64
            return value
        monkeypatch.setattr(exporter, 'validate_index', validate)
        monkeypatch.setattr(exporter, '_worker_inventory', sources)
    else:
        original = exporter._ShardEncoders.send
        def send(pool, worker, body):
            original(pool, worker, body)
            if body[:1] == b'J':
                pool.deadline = time.monotonic()-1
        monkeypatch.setattr(exporter._ShardEncoders, 'send', send)
    with pytest.raises((ValueError, OSError)):
        export(source)
    assert len(force_shard_workers) == 2
    assert (pointer.read_bytes(), state.read_bytes(), pointer.stat().st_mtime_ns) == before


@pytest.mark.parametrize('failure', [ValueError, OSError])
def test_prefetched_decode_failure_preserves_quarantine_or_old_pointer(source, monkeypatch,
                                                                       failure):
    base, _, _, _, _, add = source
    add()
    export(source)
    pointer = base/'unified-data/market-public/unified-market.json'
    state = base/'unified-data/market-export-state.json'
    before = pointer.read_bytes(), state.read_bytes()
    add(counter=2, minute=40)
    original = store_module._BatchMaterial.validate
    main_thread = threading.get_ident()
    failed_threads = []
    def validate(material):
        if material.manifest['batch_id'] == f'{2:032x}':
            failed_threads.append(threading.get_ident())
            raise failure('PREFETCHED_DECODE_SOURCE_FAULT')
        return original(material)
    monkeypatch.setattr(store_module._BatchMaterial, 'validate', validate)
    if failure is OSError:
        with pytest.raises(OSError, match='^PREFETCHED_DECODE_SOURCE_FAULT$'):
            export(source)
        assert (pointer.read_bytes(), state.read_bytes()) == before
    else:
        entry = next(row for row in export(source)['index']['entries']
                     if row['symbol'] == '301001' and row['frequency'] == '5min')
        assert entry['state'] == 'QUARANTINED' and entry['shards'] == []
        assert entry['issues'] == ['SOURCE_REPLAY_FAILED']
    assert failed_threads and all(thread != main_thread for thread in failed_threads)
    assert not any(thread.name.startswith('market-source') for thread in threading.enumerate())
