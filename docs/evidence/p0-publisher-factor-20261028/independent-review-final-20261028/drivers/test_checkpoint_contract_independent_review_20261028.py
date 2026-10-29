"""Independent temporary fault probes; only run in an owned frozen source copy."""
from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from copy import deepcopy
from datetime import timedelta
from pathlib import Path

import pytest
from test_unified_market import NOW, source as source_fixture

from qtrade.monitoring.data_freshness import signed
from qtrade.research import market_validation_receipts as receipts
from scripts import publish_unified_market as exporter
from scripts import unified_market_checkpoint as checkpoints

source = source_fixture
pytestmark = pytest.mark.skipif(os.name != 'posix', reason='POSIX lease fault probes')


def run(source, *, now=NOW):
    base, database, *_ = source
    return exporter.export_market(base, database, now=now,
        receipt_database=base/'local/receipts.sqlite', checkpoint_root=base/'checkpoints')


def test_recovered_sidecar_reestablishes_directory_durability_before_sqlite_commit(source, monkeypatch):
    base, _, _, _, lake, add = source
    manifest = add()
    material = lake._batch_material(manifest['batch_id'])
    checked, _, rows = material.validate()
    key = receipts.receipt_key(manifest['batch_id'], material.identities(), 'a'*64)
    directory = base/'receipt-sidecars'
    with receipts.MarketValidationReceiptStore(base/'local/fault.sqlite',
                                               sidecar_root=directory) as store:
        def fail(_path):
            raise OSError('independent linked-sidecar directory fsync failure')
        monkeypatch.setattr(receipts, '_fsync_directory', fail)
        with pytest.raises(OSError, match='linked-sidecar'):
            store.put_success(key, checked, rows, validated_at=NOW)
        assert store._connection.execute('SELECT count(*) FROM market_validation_receipts').fetchone()[0] == 0
        assert len(list((directory/key.key_sha256).glob('*.json'))) == 1
        events = []
        monkeypatch.setattr(receipts, '_fsync_directory', lambda path: events.append(str(path)))
        store._connection.set_trace_callback(lambda statement: events.append(statement))
        assert store.get(key, now=NOW+timedelta(seconds=1)) is not None
        assert store._connection.execute('SELECT count(*) FROM market_validation_receipts').fetchone()[0] == 1
        commit = next(index for index, event in enumerate(events) if event == 'COMMIT')
        assert any(event == str(directory/key.key_sha256) for event in events[:commit]), (
            'Recovered a sidecar whose directory fsync failed and COMMITed its local index '
            'without re-establishing the immutable sidecar directory durability', events)


def test_changed_public_head_during_generation_fsync_is_not_overwritten(source, monkeypatch):
    base, _, _, _, _, add = source
    add(); first = run(source)
    pointer = base/'unified-data/market-public/unified-market.json'
    original = checkpoints.prepare
    external = signed({k: v for k, v in {**first['index'],
        'published_at':(NOW+timedelta(microseconds=1)).isoformat()}.items()
        if k != 'evidence_sha256'})
    def race(*args, **kwargs):
        result = original(*args, **kwargs)
        pointer.write_text(json.dumps(external))
        return result
    monkeypatch.setattr(checkpoints, 'prepare', race)
    with pytest.raises(ValueError, match='CHECKPOINT_HEAD_CHANGED'):
        run(source, now=NOW+timedelta(seconds=1))
    assert json.loads(pointer.read_bytes()) == external


def test_exclusion_during_generation_fsync_blocks_old_source_publication(source, monkeypatch):
    base, database, _, _, _, add = source
    manifest = add(); run(source)
    pointer = base/'unified-data/market-public/unified-market.json'
    before = pointer.read_bytes()
    original = checkpoints.prepare
    def race(*args, **kwargs):
        result = original(*args, **kwargs)
        with sqlite3.connect(database) as db:
            db.execute('INSERT INTO excluded_collection_chunks VALUES(?,?)',
                       (manifest['batch_id'], '301001'))
        return result
    monkeypatch.setattr(checkpoints, 'prepare', race)
    with pytest.raises(ValueError, match='LEDGER_OR_MASTER_CHANGED'):
        run(source, now=NOW+timedelta(seconds=1))
    assert pointer.read_bytes() == before


def test_future_generation_clock_is_corrupt_checkpoint_cache_miss(source):
    base, _, _, _, _, add = source
    add(); first = run(source)
    head = base/'unified-data/market-public/unified-market.json'
    path = base/'checkpoints/generations'/(first['index']['evidence_sha256']+'.json')
    doc = json.loads(path.read_bytes())
    doc['created_at'] = '2099-01-01T00:00:00+00:00'
    doc = signed({k: v for k, v in doc.items() if k != 'evidence_sha256'})
    path.write_text(json.dumps(doc))
    assert checkpoints.load(base/'checkpoints', head, doc['context_sha256']) is None


def test_physical_hash_rejects_same_size_raw_change_with_original_mtime(source):
    base, _, _, _, lake, add = source
    manifest = add(); run(source)
    path = lake._blob_path(manifest['raw']['sha256'], manifest['raw']['suffix'])
    original = path.read_bytes(); stamp = path.stat()
    path.write_bytes(original[:-1]+bytes([original[-1]^1]))
    os.utime(path, ns=(stamp.st_atime_ns, stamp.st_mtime_ns))
    assert path.stat().st_size == len(original) and path.stat().st_mtime_ns == stamp.st_mtime_ns
    result = run(source, now=NOW+timedelta(seconds=1))
    pair = next(row for row in result['index']['entries']
                if row['symbol']=='301001' and row['frequency']=='5min')
    assert pair['state'] == 'QUARANTINED' and pair['shards'] == []


def test_future_receipt_is_not_usable_at_earlier_actual_clock(source):
    base, _, _, _, lake, add = source
    manifest=add();material=lake._batch_material(manifest['batch_id'])
    checked,_,rows=material.validate()
    key=receipts.receipt_key(manifest['batch_id'], material.identities(),'b'*64)
    with receipts.MarketValidationReceiptStore(base/'local/clock.sqlite') as store:
        store.put_success(key,checked,rows,validated_at=NOW+timedelta(days=1))
        assert store.get(key,now=NOW) is None


def test_replay_fingerprint_invalidates_changed_bar_import_policy(source):
    base,database,*_,add=source
    add();run(source)
    # Only an owned isolated package may be changed for this semantic-upgrade probe.
    assert 'checkpoint-review-' in str(exporter.REPO) and exporter.REPO.name=='source'
    barrel=exporter.REPO/'src/qtrade/data/__init__.py'
    original=barrel.read_bytes()
    policy=b'\n_OriginalReviewBar = Bar\ndef Bar(*args, **kwargs):\n    raise ValueError("INDEPENDENT_NEW_BAR_ADMISSION_POLICY")\n'
    code='''import json,sys
from pathlib import Path
from datetime import timedelta
from scripts import publish_unified_market as e
from test_unified_market import NOW
base=Path(sys.argv[1]);database=Path(sys.argv[2]);warm=sys.argv[3]=='warm'
kw={'receipt_database':base/'local/receipts.sqlite','checkpoint_root':base/'checkpoints'} if warm else {}
result=e.export_market(base,database,now=NOW+timedelta(seconds=1 if warm else 2),**kw)
pair=next(row for row in result['index']['entries'] if row['symbol']=='301001' and row['frequency']=='5min')
print(json.dumps({'state':pair['state'],'stats':result['statistics']}))
'''
    env={**os.environ,'PYTHONPATH':str(exporter.REPO/'src')+':'+str(exporter.REPO)+':'+str(exporter.REPO/'tests'),
         'PYTHONDONTWRITEBYTECODE':'1','LIVE_TRADING_ENABLED':'false'}
    try:
        barrel.write_bytes(original+policy)
        warm=subprocess.run([sys.executable,'-B','-c',code,str(base),str(database),'warm'],
                            cwd=exporter.REPO,env=env,capture_output=True,text=True,check=True)
        plain=subprocess.run([sys.executable,'-B','-c',code,str(base),str(database),'plain'],
                             cwd=exporter.REPO,env=env,capture_output=True,text=True,check=True)
        warm,plain=json.loads(warm.stdout),json.loads(plain.stdout)
    finally:
        barrel.write_bytes(original)
    assert warm['state']==plain['state'], (
        'Changed qtrade.data.Bar import policy affects full replay but is absent from '
        'persistent receipt fingerprint; stale semantic receipts survived a new process',warm,plain)


def test_prepare_crossing_deadline_keeps_old_head(source, monkeypatch):
    base,*_,add=source
    add();run(source)
    pointer=base/'unified-data/market-public/unified-market.json'
    before=pointer.read_bytes();original=checkpoints.prepare
    clock=[100.0]
    monkeypatch.setattr(exporter.time,'monotonic',lambda:clock[0])
    def delayed(*args,**kwargs):
        result=original(*args,**kwargs);clock[0]+=2;return result
    monkeypatch.setattr(checkpoints,'prepare',delayed)
    with pytest.raises(ValueError,match='TIME_BUDGET'):
        exporter.export_market(base,source[1],now=NOW+timedelta(seconds=1),seconds=1,
            receipt_database=base/'local/receipts.sqlite',checkpoint_root=base/'checkpoints')
    assert pointer.read_bytes()==before


def test_new_overlapping_conflict_during_prepare_keeps_old_head(source, monkeypatch):
    base,*_,add=source
    add();run(source)
    pointer=base/'unified-data/market-public/unified-market.json'
    before=pointer.read_bytes();original=checkpoints.prepare
    def conflict(*args,**kwargs):
        result=original(*args,**kwargs);add(counter=2,close='11');return result
    monkeypatch.setattr(checkpoints,'prepare',conflict)
    with pytest.raises(ValueError,match='LEDGER_OR_MASTER_CHANGED'):
        run(source,now=NOW+timedelta(seconds=1))
    assert pointer.read_bytes()==before


def _fingerprint_inputs():
    target={'symbol':'301001','instrument_id':'SZSE.301001','security_type':'STOCK','name':'A',
            'incremental':{'day':{'state':'PLANNED'},'5min':{'state':'PLANNED'}}}
    chunk={'symbol':'301001','frequency':'5min','batch_id':'a'*32,
           'start_time':'2026-10-26T01:35:00+00:00','end_time':'2026-10-26T01:35:00+00:00',
           'bar_count':1,'manifest_sha256':'b'*64,'universe_version':'c'*64}
    return [chunk],[target],{'day':['2026-10-23'],'5min':['2026-10-26']}


def test_unconsumed_cross_frequency_target_metadata_does_not_invalidate_views():
    chunks,targets,windows=_fingerprint_inputs()
    first=checkpoints.fingerprints(chunks,targets,'d'*64,windows)
    targets[0]['incremental']['day'].update(state='NEW_STATUS',observed_end='2026-10-26')
    targets[0].update(observed_at='2026-10-28T10:00:00+00:00',source=['new-observation'])
    assert checkpoints.fingerprints(chunks,targets,'d'*64,windows)==first


@pytest.mark.parametrize('field,value',[('symbol','301002'),('instrument_id','SZSE.301002'),
                                      ('security_type','ETF'),('name','B')])
def test_every_consumed_target_field_remains_fingerprint_bound(field,value):
    chunks,targets,windows=_fingerprint_inputs()
    first=checkpoints.fingerprints(chunks,targets,'d'*64,windows)
    targets[0][field]=value
    assert checkpoints.fingerprints(chunks,targets,'d'*64,windows)!=first


def test_window_and_metadata_changes_invalidate_only_affected_frequency():
    chunks,targets,windows=_fingerprint_inputs()
    first=checkpoints.fingerprints(chunks,targets,'d'*64,windows)
    windows['day']=['2026-10-22','2026-10-23']
    second=checkpoints.fingerprints(chunks,targets,'d'*64,windows)
    assert first['301001:day']!=second['301001:day']
    assert first['301001:5min']==second['301001:5min']
    chunks[0]['manifest_sha256']='e'*64
    third=checkpoints.fingerprints(chunks,targets,'d'*64,windows)
    assert second['301001:day']==third['301001:day']
    assert second['301001:5min']!=third['301001:5min']


@pytest.mark.parametrize('bad',[float('nan'),float('inf'),(1,2),{1:'integer-key'},
                               {'non-json-type':b'bytes'}])
def test_public_json_boundary_still_rejects_noncanonical_trees(bad):
    with pytest.raises(ValueError):receipts.canonical_json(bad)


@pytest.mark.parametrize('mutation',['duplicate','nan','depth','hash'])
def test_decode_still_rejects_bad_json_before_receipt_reuse(source,mutation):
    base,_,_,_,lake,add=source
    manifest=add();material=lake._batch_material(manifest['batch_id'])
    checked,_,rows=material.validate()
    key=receipts.receipt_key(manifest['batch_id'],material.identities(),'c'*64)
    with receipts.MarketValidationReceiptStore(base/'local/decode.sqlite') as store:
        store.put_success(key,checked,rows,validated_at=NOW)
        raw=store._connection.execute('SELECT payload FROM market_validation_receipts').fetchone()[0]
    if mutation=='duplicate':raw=raw[:-1]+b',"outcome":"PASSED"}'
    else:
        value=json.loads(raw)
        if mutation=='nan':value['result']['rows'][0]['amount']=float('nan')
        elif mutation=='hash':value['result_sha256']='0'*64
        else:
            nested='leaf'
            for _ in range(30):nested=[nested]
            value['failure']=nested
        raw=json.dumps(value,sort_keys=True,separators=(',',':')).encode()
    with pytest.raises(ValueError):receipts._decode(raw,key,NOW+timedelta(seconds=1))


def test_put_rejects_bad_tree_without_persisting_a_lossy_receipt(source):
    base,_,_,_,lake,add=source
    manifest=add();material=lake._batch_material(manifest['batch_id'])
    checked,_,rows=material.validate();checked=deepcopy(checked)
    checked['stage_counts']['non_json_tuple']=(1,2)
    checked=signed({k:v for k,v in checked.items() if k!='evidence_sha256'})
    key=receipts.receipt_key(manifest['batch_id'],material.identities(),'d'*64)
    with receipts.MarketValidationReceiptStore(base/'local/put.sqlite') as store:
        with pytest.raises(ValueError):store.put_success(key,checked,rows,validated_at=NOW)
        assert store._connection.execute('SELECT count(*) FROM market_validation_receipts').fetchone()[0]==0
