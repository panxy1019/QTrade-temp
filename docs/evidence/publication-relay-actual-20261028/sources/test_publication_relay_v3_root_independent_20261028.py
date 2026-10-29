"""Independent Linux tree/file checks. Synthetic data; no network or credentials."""
from pathlib import Path
import copy
import hashlib
import importlib.util
import json
import os
import stat
import time

import pytest


@pytest.fixture
def op():
    path=Path(__file__).with_name('publication_native_relay_operator_v3_20261028.py')
    spec=importlib.util.spec_from_file_location('root_review_relay_v3',path)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
    return module


def ids(raw):
    return hashlib.sha256(raw).hexdigest(),hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()


def listing(key,oid,mode=b'100644',kind=b'blob'):
    return mode+b' '+kind+b' '+oid.encode()+b'\tunified-market/objects/'+key.encode()+b'.json\0'


@pytest.mark.parametrize('damage',['truncated','mode','type','bad_oid','bad_name','duplicate','missing','too_large','too_many'])
def test_tree_rejects_invalid_binding(op,monkeypatch,damage):
    key,oid=ids(b'public synthetic data');row=listing(key,oid)
    raw=row;refs=[key]
    if damage=='truncated':raw=raw[:-1]
    elif damage=='mode':raw=listing(key,oid,b'120000')
    elif damage=='type':raw=listing(key,oid,kind=b'tree')
    elif damage=='bad_oid':raw=listing(key,'g'*40)
    elif damage=='bad_name':raw=row.replace(b'unified-market/objects/',b'../outside/')
    elif damage=='duplicate':raw=row+row
    elif damage=='missing':refs=['a'*64]
    elif damage=='too_large':monkeypatch.setattr(op,'TREE_LISTING_LIMIT',len(row)-1)
    elif damage=='too_many':monkeypatch.setattr(op,'TREE_ENTRY_LIMIT',0)
    with pytest.raises(ValueError):op.parse_frozen_tree(raw,refs)


def test_complete_listing_keeps_old_objects_without_claiming_their_bytes(op):
    key,oid=ids(b'current');old,oldoid=ids(b'old')
    result=op.parse_frozen_tree(listing(old,oldoid)+listing(key,oid),[key])
    assert result=={key:oid,old:oldoid}


@pytest.mark.parametrize('damage',['same_size_changed','wrong_oid','wrong_sha256','symlink','directory','fifo','too_short','too_long','deadline'])
def test_real_posix_shard_rejects_bad_files(op,tmp_path,damage):
    raw=b'PUBLIC_SYNTHETIC_ONLY';key,oid=ids(raw);path=tmp_path/(key+'.json');path.write_bytes(raw)
    expected=len(raw);deadline=time.monotonic()+5
    if damage=='same_size_changed':path.write_bytes(b'X'*len(raw))
    elif damage=='wrong_oid':oid='1'*40
    elif damage=='wrong_sha256':
        wrong='2'*64;path.rename(tmp_path/(wrong+'.json'));key=wrong
    elif damage=='symlink':
        path.unlink();other=tmp_path/'other';other.write_bytes(raw);path.symlink_to(other)
    elif damage=='directory':path.unlink();path.mkdir()
    elif damage=='fifo':path.unlink();os.mkfifo(path)
    elif damage=='too_short':expected+=1
    elif damage=='too_long':expected-=1
    elif damage=='deadline':deadline=time.monotonic()-1
    descriptor=os.open(tmp_path,os.O_DIRECTORY|os.O_RDONLY)
    try:
        with pytest.raises((ValueError,OSError)):op.read_frozen_shard(descriptor,key,expected,oid,deadline)
    finally:os.close(descriptor)


def test_real_hardlinked_data_is_allowed_only_when_both_hashes_match(op,tmp_path):
    raw=b'PUBLIC_SYNTHETIC_ONLY';key,oid=ids(raw);path=tmp_path/(key+'.json');path.write_bytes(raw)
    os.link(path,tmp_path/'second-reference');assert path.stat().st_nlink==2
    descriptor=os.open(tmp_path,os.O_DIRECTORY|os.O_RDONLY)
    try:assert op.read_frozen_shard(descriptor,key,len(raw),oid,time.monotonic()+5)==raw
    finally:os.close(descriptor)


@pytest.mark.parametrize('damage',['ancestor_symlink','leaf_symlink','rename_while_open'])
def test_anchored_object_directory_rejects_alias_or_replacement(op,monkeypatch,tmp_path,damage):
    source=tmp_path/'publication';objects=source/'unified-market/objects';objects.mkdir(parents=True)
    monkeypatch.setattr(op,'SOURCE',source)
    if damage=='ancestor_symlink':
        real=tmp_path/'real';source.rename(real);source.symlink_to(real,target_is_directory=True)
    elif damage=='leaf_symlink':
        other=source/'unified-market/other';objects.rename(other);objects.symlink_to(other,target_is_directory=True)
    with pytest.raises((ValueError,OSError)):
        with op.public_objects_directory():
            if damage=='rename_while_open':
                objects.rename(objects.with_name('old'));objects.mkdir()


def prepare_fixture(op,monkeypatch,tmp_path,forged=False):
    from datetime import datetime,timezone
    from qtrade.monitoring.data_freshness import AUTHORITY,Calendar,signed
    from qtrade.research import unified_market as contract
    now=datetime(2026,10,28,12,tzinfo=timezone.utc)
    calendar=Calendar(op.RELEASE/'configs/observer_calendar_2026.json')
    entries=[{'symbol':str(n),'instrument_id':'SSE.'+str(n),'security_type':'STOCK','name':'SYNTHETIC',
        'frequency':f,'state':'NO_DATA','issues':[],'shards':[]} for n in range(600000,601500) for f in ('day','5min')]
    bar={'symbol':'601499','exchange':'SSE','frequency':'5min','timestamp':'2026-10-28T09:35:00+08:00',
        'trading_day':'2026-10-28','open':'10','high':'12','low':'9','close':'10','volume':'100',
        'amount':None,'vwap':None,'amount_basis':'VENDOR_CLAIM_UNVERIFIED',
        'price_basis':contract.PRICE_BASIS,'historical_available_at':None}
    shard=signed({**AUTHORITY,'schema_version':'qtrade.unified-market-shard.v1','universe_version':'1'*64,
        'symbol':'601499','security_type':'STOCK','instrument_id':'SSE.601499','frequency':'5min',
        'price_basis':contract.PRICE_BASIS,'bars':[bar],'source_batches':[{'batch_id':'2'*32,
            'manifest_sha256':'3'*64,'raw_sha256':'4'*64,'normalized_sha256':'5'*64}]})
    raw=contract.encoded(shard);key,oid=ids(raw)
    ref={'sha256':key,'evidence_sha256':shard['evidence_sha256'],'bytes':len(raw),
        'start':bar['timestamp'],'end':bar['timestamp'],'count':1}
    entries[-1].update(state='AVAILABLE',shards=[ref])
    if forged:entries[1].update(state='AVAILABLE',shards=[copy.deepcopy(ref)])
    index=signed({**AUTHORITY,'schema_version':'qtrade.unified-market-index.v1','universe_version':'1'*64,
        'master_sha256':'2'*64,'published_at':now.isoformat(),'calendar':calendar.document,
        'windows':{f:contract.sessions(calendar,calendar.expected(now,'daily' if f=='day' else 'minute').date(),
            20 if f=='day' else 3) for f in ('day','5min')},'entries':entries,'total_bytes':len(raw),
        'total_files':1,'limitations':contract.LIMITATIONS,'implementation_sources':{
            'scripts/publish_unified_market.py':'64caab0023626ef0f531f563c3a392d4a2429a9a6c004c5789af17321bbd8d8a',
            'src/qtrade/research/unified_market.py':'3'*64}})
    base=tmp_path/'base';source=base/'publication';objects=source/'unified-market/objects';objects.mkdir(parents=True)
    (objects/(key+'.json')).write_bytes(raw);os.link(objects/(key+'.json'),tmp_path/'valid-data-second-link')
    data=base/'unified-data';data.mkdir()
    attempt=signed({**AUTHORITY,'schema_version':'qtrade.unified-market-export-attempt.v1',
        'started_at':'2026-10-28T11:59:50+00:00','completed_at':'2026-10-28T12:00:10+00:00',
        'state':'SUCCEEDED','index_id':index['evidence_sha256'],'error_class':None,'error_code':None,'elapsed_seconds':20.0})
    (data/'market-export-attempt.json').write_text(json.dumps(attempt))
    root=tmp_path/'attempt';root.mkdir();target='6'*40
    for name,value in {'BASE':base,'SOURCE':source,'ROOT':root,'APPROVED_ATTEMPT':root}.items():monkeypatch.setattr(op,name,value)
    monkeypatch.setattr(op,'runtime_origins',lambda:None)
    monkeypatch.setattr(op,'guard_repository',lambda **kwargs:None)
    calls=[]
    def read(args,**kwargs):
        calls.append((args,kwargs))
        assert 0<kwargs['timeout']<=90
        if args==['rev-parse','HEAD']:return target.encode()+b'\n'
        if args==['show',target+':unified-market.json']:return contract.encoded(index)
        if args==['ls-tree','-r','-z',target,'--','unified-market/objects']:return listing(key,oid)
        pytest.fail('Unexpected Git call: '+repr(args))
    monkeypatch.setattr(op,'source',read)
    return root,calls


def test_actual_prepare_full_relationship_rejection_before_target(op,monkeypatch,tmp_path):
    root,calls=prepare_fixture(op,monkeypatch,tmp_path,forged=True)
    with pytest.raises(ValueError,match='SHARD_INDEX_MISMATCH'):op.prepare()
    assert not (root/'target.json').exists()


def test_actual_prepare_fsync_target_with_hardlinks_and_no_batch_random_reads(op,monkeypatch,tmp_path):
    root,calls=prepare_fixture(op,monkeypatch,tmp_path)
    result=op.prepare();assert json.loads((root/'target.json').read_bytes())==result
    assert result['index_entries']==3000 and result['shard_count']==1
    assert result['verified_current_occurrences']==1
    assert result['all_frozen_git_shards_verified'] and not result['ordinary_publisher_success']
    assert not any(args[0]=='cat-file' for args,_ in calls)
    with pytest.raises(ValueError,match='TARGET_ALREADY_FROZEN'):op.prepare()
