"""Differential persistent/restart checks on the existing immutable raw-source fixture."""
from __future__ import annotations

import json
import os
import sqlite3
from datetime import timedelta

import pytest
from test_unified_market import NOW
from test_unified_market import source as source_fixture

from scripts import publish_unified_market as exporter
from scripts import unified_market_checkpoint as checkpoints

source = source_fixture

pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX cluster publisher lease')


def run(source, **kwargs):
    base, database, *_ = source
    return exporter.export_market(base, database, now=kwargs.pop('now', NOW),
        receipt_database=base/'local/receipts.sqlite', checkpoint_root=base/'checkpoints', **kwargs)


def refs(index):
    return {(entry['symbol'], entry['frequency']): entry['shards'] for entry in index['entries']}


def test_receipts_survive_restart_and_reuse_identical_shards(source, monkeypatch):
    base, database, _, _, _, add = source
    add()
    add('day', counter=2)
    plain = exporter.export_market(base, database, now=NOW)
    first = run(source)
    assert first['index'] == plain['index']
    exporter._CACHE.clear()
    def no_replay(*args, **kwargs):
        raise AssertionError('verified immutable batches must not replay after restart')
    monkeypatch.setattr(exporter, '_replay', no_replay)
    second = run(source, now=NOW+timedelta(seconds=1))
    assert refs(second['index']) == refs(first['index'])
    assert second['statistics']['receipt_hits'] == 2
    assert second['statistics']['replayed_batches'] == 0
    assert second['statistics']['reused_shards'] == 2
    assert second['statistics']['source_bytes_read'] > 0


def test_new_batch_rebuilds_only_its_pair_and_keeps_other_frequency(source):
    _, _, _, _, _, add = source
    add()
    add('day', counter=2)
    first = run(source)
    add(counter=3, minute=40)
    second = run(source, now=NOW+timedelta(seconds=1))
    assert second['statistics']['receipt_hits'] == 2
    assert second['statistics']['replayed_batches'] == 1
    assert refs(first['index'])[('301001', 'day')] == refs(second['index'])[('301001', 'day')]
    assert refs(first['index'])[('301001', '5min')] != refs(second['index'])[('301001', '5min')]
    assert second['statistics']['reused_pairs'] == 1


def test_equal_prices_with_new_source_rebuild_lineage(source):
    _, _, _, _, _, add = source
    add()
    first = run(source)
    add(counter=2)
    second = run(source, now=NOW+timedelta(seconds=1))
    assert refs(first['index'])[('301001', '5min')] != refs(second['index'])[('301001', '5min')]
    assert second['statistics']['replayed_batches'] == 1


def test_exclusion_invalidates_pair_and_does_not_reuse_removed_source(source):
    _, database, _, _, _, add = source
    add()
    run(source)
    with sqlite3.connect(database) as db:
        db.execute('INSERT INTO excluded_collection_chunks VALUES(?,?)', (f'{1:032x}', '301001'))
    second = run(source, now=NOW+timedelta(seconds=1))
    entry = next(e for e in second['index']['entries'] if e['symbol'] == '301001'
                 and e['frequency'] == '5min')
    assert entry['state'] == 'NO_DATA' and entry['shards'] == []


def test_corrupt_reused_shard_aborts_without_moving_head(source):
    base, _, _, _, _, add = source
    add()
    first = run(source)
    pointer = base/'unified-data/market-public/unified-market.json'
    before = pointer.read_bytes()
    ref = refs(first['index'])[('301001', '5min')][0]
    path = pointer.parent/'unified-market/objects'/(ref['sha256']+'.json')
    raw = path.read_bytes()
    path.write_bytes(raw.replace(b'"10"', b'"11"', 1))
    with pytest.raises(ValueError, match='IMMUTABLE_OBJECT_CHANGED'):
        run(source, now=NOW+timedelta(seconds=1))
    assert pointer.read_bytes() == before


def test_changed_raw_bytes_cannot_be_hidden_by_receipt_or_checkpoint(source):
    _, _, _, _, store, add = source
    manifest = add()
    run(source)
    path = store._blob_path(manifest['raw']['sha256'], manifest['raw']['suffix'])
    raw = path.read_bytes()
    path.write_bytes(raw[:-1]+bytes([raw[-1] ^ 1]))
    second = run(source, now=NOW+timedelta(seconds=1))
    entry = next(e for e in second['index']['entries'] if e['symbol'] == '301001'
                 and e['frequency'] == '5min')
    assert entry['state'] == 'QUARANTINED' and entry['shards'] == []


def test_checkpoint_write_failure_keeps_old_public_head(source, monkeypatch):
    base, _, _, _, _, add = source
    add()
    run(source)
    pointer = base/'unified-data/market-public/unified-market.json'
    before = pointer.read_bytes()
    add(counter=2, minute=40)
    def fail(*args, **kwargs):
        raise OSError('checkpoint durability failure')
    monkeypatch.setattr(checkpoints, 'prepare', fail)
    with pytest.raises(OSError, match='checkpoint durability failure'):
        run(source, now=NOW+timedelta(seconds=1))
    assert pointer.read_bytes() == before


def test_corrupt_checkpoint_is_cache_miss_with_receipts_still_valid(source):
    base, _, _, _, _, add = source
    add()
    first = run(source)
    path = base/'checkpoints/generations'/(first['index']['evidence_sha256']+'.json')
    path.write_bytes(b'{"broken":true}')
    second = run(source, now=NOW+timedelta(seconds=1))
    assert second['statistics']['receipt_hits'] == 1
    assert second['statistics']['reused_shards'] == 0
    assert refs(first['index']) == refs(second['index'])


def test_public_head_is_complete_when_following_state_write_fails(source, monkeypatch):
    base, _, _, _, _, add = source
    add()
    first = run(source)
    original = exporter.write_json_atomic
    def write(path, value, **kwargs):
        if path.name == 'market-export-state.json':
            raise OSError('after public commit')
        return original(path, value, **kwargs)
    monkeypatch.setattr(exporter, 'write_json_atomic', write)
    with pytest.raises(OSError, match='after public commit'):
        run(source, now=NOW+timedelta(seconds=1))
    index = json.loads((base/'unified-data/market-public/unified-market.json').read_bytes())
    assert refs(index) == refs(first['index'])
    assert (base/'checkpoints/generations'/(index['evidence_sha256']+'.json')).exists()


def test_publisher_lease_rejects_second_writer_before_public_mutation(source):
    base, _, _, _, _, add = source
    add()
    first = run(source)
    with (checkpoints.publication_lease(base/'local/receipts.sqlite'),
          pytest.raises(ValueError, match='OWNER_BUSY')):
        run(source)
    assert json.loads((base/'unified-data/market-public/unified-market.json').read_bytes()) == (
        first['index'])


def test_retry_reestablishes_generation_directory_durability(source, monkeypatch):
    base, _, _, _, _, add = source
    add()
    run(source)
    pointer = base/'unified-data/market-public/unified-market.json'
    before = pointer.read_bytes()
    original = checkpoints._fsync_directory
    def fail(path):
        raise OSError('generation directory fsync')
    monkeypatch.setattr(checkpoints, '_fsync_directory', fail)
    with pytest.raises(OSError, match='generation directory fsync'):
        run(source, now=NOW+timedelta(seconds=1))
    assert pointer.read_bytes() == before
    calls = []
    def record(path):
        calls.append(path)
        return original(path)
    monkeypatch.setattr(checkpoints, '_fsync_directory', record)
    run(source, now=NOW+timedelta(seconds=1))
    assert base/'checkpoints/generations' in calls and base/'checkpoints' in calls


def test_pair_identity_ignores_collection_status_from_other_frequency():
    target={'symbol':'600036','instrument_id':'SSE.600036.STOCK','security_type':'STOCK',
            'name':'bank','frequencies':{'day':{'state':'PLANNED'},'5min':{'state':'PLANNED'}}}
    windows={'day':['2026-10-27'],'5min':['2026-10-27']}
    before=checkpoints.fingerprints([], [target], 'context', windows)
    target['frequencies']['day']['state']='SUCCEEDED'
    after=checkpoints.fingerprints([], [target], 'context', windows)
    assert before==after
    target['name']='new legal name'
    assert checkpoints.fingerprints([], [target], 'context', windows)!=before
