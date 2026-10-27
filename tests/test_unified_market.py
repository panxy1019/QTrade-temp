from __future__ import annotations

import json
import os
import sqlite3
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from decimal import Decimal
from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from qtrade.api import unified_market as api
from qtrade.data import Bar
from qtrade.instruments.observed import build_observed_master
from qtrade.market_data.akshare import SHANGHAI
from qtrade.market_data.sina_incremental import IncrementalBar
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research import unified_market as contract
from scripts import publish_unified_market as exporter
from scripts.unified_store import FIELDS, UnifiedBatchStore, canonical

NOW = datetime(2026, 10, 26, 10, tzinfo=UTC)


def resign(value):
    return signed({key: item for key, item in value.items() if key != 'evidence_sha256'})


@pytest.fixture
def source(tmp_path, monkeypatch):
    monkeypatch.setenv('LIVE_TRADING_ENABLED', 'false')
    exporter._CACHE.clear()
    if os.name == 'nt':
        monkeypatch.setattr('scripts.unified_store._fsync_directory', lambda _: None)
        original = exporter.write_json_immutable
        def write(path, value):
            try:
                original(path, value)
            except PermissionError:
                assert json.loads(path.read_bytes()) == value
        monkeypatch.setattr(exporter, 'write_json_immutable', write)
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
    monkeypatch.setattr(api, '_object', lambda digest:
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
    assert len(names) == second['total_files']+1
    assert json.loads((publication/'unified-market.json').read_bytes()) == second


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


