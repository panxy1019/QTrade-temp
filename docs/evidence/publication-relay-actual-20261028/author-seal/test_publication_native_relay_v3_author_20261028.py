"""Author tests for v3 preparation only; all bytes and Git replies are synthetic.

Native Linux dir_fd/NOFOLLOW behavior remains an independent Linux acceptance
item. Windows tests retain real content reads, hashes and unmodified validators
while translating the directory-related syscall boundary explicitly.
"""
import contextlib
import hashlib
import importlib.util
import os
from pathlib import Path
import stat
import sys
import time
import types

import pytest

HERE = Path(__file__).parent
sys.path.insert(0, str(HERE/'relay-independent-review-20261028'))
from test_relay_independent_20261028 import market_fixture, setup_prepare


@pytest.fixture
def op():
    spec=importlib.util.spec_from_file_location('relay_v3_author',
        HERE/'publication_native_relay_operator_v3_20261028.py')
    module=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def blob_oid(raw):
    return hashlib.sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest()


def record(key='a'*64,oid='b'*40):
    return f'100644 blob {oid}\tunified-market/objects/{key}.json'.encode()+b'\0'


def test_tree_covers_current_but_keeps_all_old_objects(op):
    assert op.parse_frozen_tree(record()+record('c'*64,'d'*40),['a'*64])=={
        'a'*64:'b'*40,'c'*64:'d'*40}


@pytest.mark.parametrize('raw',[
    record()[:-1],record()+b'bad',record()+b'\0',record()+record(),
    record().replace(b'100644',b'100755'),record().replace(b'100644',b'120000'),
    record().replace(b' blob ',b' tree '),record().replace(b'b'*40,b'b'*39),
    record().replace(b'b'*40,b'B'*40),record().replace(b'a'*64,b'A'*64),
    record().replace(b'objects/',b'objects/../'),record().replace(b'.json',b'.json/extra'),
    record().replace(b'\t',b' '),record().replace(b'unified-market',b'private'),
])
def test_tree_rejects_malformed_or_ambiguous_listing(op,raw):
    with pytest.raises(ValueError):op.parse_frozen_tree(raw,['a'*64])


def test_tree_rejects_missing_current_ref(op):
    with pytest.raises(ValueError,match='ALL_CURRENT'):
        op.parse_frozen_tree(record(),['c'*64])


def test_tree_checks_complete_listing_size_and_record_bounds(op,monkeypatch):
    monkeypatch.setattr(op,'TREE_LISTING_LIMIT',len(record())-1)
    with pytest.raises(ValueError,match='COMPLETE_FROZEN'):op.parse_frozen_tree(record(),[])
    monkeypatch.setattr(op,'TREE_LISTING_LIMIT',10000)
    monkeypatch.setattr(op,'TREE_ENTRY_LIMIT',1)
    with pytest.raises(ValueError,match='ENTRY_BOUND'):
        op.parse_frozen_tree(record()+record('c'*64),[])


def shard_io(op,monkeypatch,directory):
    real_open=os.open
    calls=[]
    monkeypatch.setattr(op.os,'O_NOFOLLOW',getattr(os,'O_NOFOLLOW',0x20000),raising=False)
    monkeypatch.setattr(op.os,'O_NONBLOCK',getattr(os,'O_NONBLOCK',0x800),raising=False)
    def opened(name,flags,*,dir_fd=None):
        assert dir_fd==123
        assert flags & op.os.O_NOFOLLOW and flags & op.os.O_NONBLOCK
        assert Path(name).name==name
        calls.append(name)
        return real_open(directory/name,os.O_RDONLY|getattr(os,'O_BINARY',0))
    monkeypatch.setattr(op.os,'open',opened)
    return calls


def test_data_hardlink_is_read_and_double_hash_verified(op,monkeypatch,tmp_path):
    raw=b'{"synthetic_public":1}\n';key=hashlib.sha256(raw).hexdigest()
    path=tmp_path/(key+'.json');path.write_bytes(raw)
    os.link(path,tmp_path/'second-link')
    assert path.stat().st_nlink==2
    calls=shard_io(op,monkeypatch,tmp_path)
    assert op.read_frozen_shard(123,key,len(raw),blob_oid(raw),time.monotonic()+90)==raw
    assert calls==[key+'.json']


@pytest.mark.parametrize('damage',['wrong_oid','wrong_key','wrong_length','too_large','zero','bool'])
def test_shard_identity_and_bounds_reject(op,monkeypatch,tmp_path,damage):
    raw=b'PUBLIC_FIXTURE';key=hashlib.sha256(raw).hexdigest();oid=blob_oid(raw);size=len(raw)
    if damage=='wrong_key':key='0'*64
    (tmp_path/(key+'.json')).write_bytes(raw)
    if damage=='wrong_oid':oid='0'*40
    elif damage=='wrong_length':size+=1
    elif damage=='too_large':size=1_000_001
    elif damage=='zero':size=0
    elif damage=='bool':size=True
    shard_io(op,monkeypatch,tmp_path)
    with pytest.raises(ValueError):op.read_frozen_shard(123,key,size,oid,time.monotonic()+90)


def test_nonregular_descriptor_rejected_before_read(op,monkeypatch,tmp_path):
    raw=b'PUBLIC_FIXTURE';key=hashlib.sha256(raw).hexdigest()
    (tmp_path/(key+'.json')).write_bytes(raw)
    shard_io(op,monkeypatch,tmp_path)
    real_fstat=os.fstat
    def changed(fd):
        result=real_fstat(fd)
        return types.SimpleNamespace(st_mode=stat.S_IFIFO,st_nlink=1,st_size=result.st_size)
    monkeypatch.setattr(op.os,'fstat',changed)
    with pytest.raises(ValueError,match='PUBLIC_BLOB_FILE_OR_SIZE'):
        op.read_frozen_shard(123,key,len(raw),blob_oid(raw),time.monotonic()+90)


def test_expired_deadline_never_opens_data(op,monkeypatch):
    monkeypatch.setattr(op.os,'open',lambda *a,**kw:pytest.fail('expired open'))
    with pytest.raises(ValueError,match='AUDIT_BUDGET'):
        op.read_frozen_shard(123,'a'*64,1,'b'*40,time.monotonic()-1)


def directory_syscalls(op,monkeypatch,source):
    """Explicit Windows translation: production keeps actual POSIX syscalls."""
    monkeypatch.setattr(op,'SOURCE',source)
    monkeypatch.setattr(op.os,'O_DIRECTORY',getattr(os,'O_DIRECTORY',0x10000),raising=False)
    monkeypatch.setattr(op.os,'O_NOFOLLOW',getattr(os,'O_NOFOLLOW',0x20000),raising=False)
    descriptors={};opened=[];closed=[]
    def open_directory(name,flags,*,dir_fd=None):
        assert flags & op.os.O_DIRECTORY and flags & op.os.O_NOFOLLOW
        path=Path(name) if dir_fd is None else descriptors[dir_fd]/name
        assert path.is_dir()
        fd=90000+len(descriptors)
        descriptors[fd]=path;opened.append((path,dir_fd))
        return fd
    monkeypatch.setattr(op.os,'open',open_directory)
    monkeypatch.setattr(op.os,'fstat',lambda fd:descriptors[fd].stat())
    monkeypatch.setattr(op.os,'close',lambda fd:closed.append(fd))
    return descriptors,opened,closed


def test_directory_walk_is_anchored_from_root_and_closes_reverse(op,monkeypatch,tmp_path):
    source=tmp_path/'publication';(source/'unified-market/objects').mkdir(parents=True)
    descriptors,opened,closed=directory_syscalls(op,monkeypatch,source)
    with op.public_objects_directory() as fd:
        assert descriptors[fd]==source/'unified-market/objects'
        assert opened[0]==(Path(source.anchor),None)
        assert all(parent==90000+i-1 for i,(_,parent) in enumerate(opened) if i)
    assert closed==list(reversed(list(descriptors)))


def test_directory_context_failure_still_closes_all(op,monkeypatch,tmp_path):
    source=tmp_path/'publication';(source/'unified-market/objects').mkdir(parents=True)
    descriptors,_,closed=directory_syscalls(op,monkeypatch,source)
    with pytest.raises(RuntimeError,match='synthetic'):
        with op.public_objects_directory():raise RuntimeError('synthetic')
    assert closed==list(reversed(list(descriptors)))


def prepare_fixture(op,monkeypatch,tmp_path,**kwargs):
    captured=setup_prepare(op,monkeypatch,tmp_path,**kwargs)
    old_source=op.source
    index_raw=old_source(['show','6'*40+':unified-market.json'])
    _,raw,ref=market_fixture()
    source=op.BASE/'publication';directory=source/'unified-market/objects';directory.mkdir(parents=True)
    (directory/(ref['sha256']+'.json')).write_bytes(raw)
    monkeypatch.setattr(op,'SOURCE',source)
    monkeypatch.setattr(op,'runtime_origins',lambda:None)
    monkeypatch.setattr(op,'guard_repository',lambda **kw:None)
    @contextlib.contextmanager
    def anchored():yield 123
    monkeypatch.setattr(op,'public_objects_directory',anchored)
    shard_io(op,monkeypatch,directory)
    calls=[]
    def replies(args,**kw):
        calls.append((args,kw))
        if args[0]=='ls-tree':return record(ref['sha256'],blob_oid(raw))
        if args[0]=='show':return index_raw
        assert args==['rev-parse','HEAD']
        return b'6'*40+b'\n'
    monkeypatch.setattr(op,'source',replies)
    return captured,calls


def test_prepare_reads_one_tree_and_real_public_validator_without_cat_file(op,monkeypatch,tmp_path):
    captured,calls=prepare_fixture(op,monkeypatch,tmp_path)
    result=op.prepare()
    assert [c[0][0] for c in calls]==['rev-parse','show','ls-tree']
    assert captured=={'target.json':result}
    assert result['verified_current_occurrences']==1 and result['frozen_tree_entry_count']==1
    assert result['worktree_bytes_read']>0 and result['frozen_shard_audit_budget_seconds']==90
    assert result['ordinary_publisher_success'] is False


def test_prepare_checks_every_occurrence_with_actual_quality_validator(op,monkeypatch,tmp_path):
    captured,_=prepare_fixture(op,monkeypatch,tmp_path,forged=True)
    with pytest.raises(ValueError,match='SHARD_INDEX_MISMATCH'):op.prepare()
    assert captured=={}


@pytest.mark.parametrize('damage',['unsigned','badchecksum','negative','bool','schema','future','error'])
def test_signed_attempt_regressions_keep_no_target(op,monkeypatch,tmp_path,damage):
    from qtrade.monitoring.data_freshness import signed
    def changed(attempt):
        if damage=='unsigned':attempt.pop('evidence_sha256');return
        if damage=='badchecksum':attempt['evidence_sha256']='0'*64;return
        if damage=='negative':attempt['elapsed_seconds']=-1
        elif damage=='bool':attempt['elapsed_seconds']=True
        elif damage=='schema':attempt['schema_version']='WRONG'
        elif damage=='future':attempt['started_at']='2099-01-01T00:00:00+00:00'
        elif damage=='error':attempt['error_code']='FAILED'
        attempt['evidence_sha256']=signed({k:v for k,v in attempt.items() if k!='evidence_sha256'})['evidence_sha256']
    captured,_=prepare_fixture(op,monkeypatch,tmp_path,attempt_change=changed)
    with pytest.raises((ValueError,TypeError)):op.prepare()
    assert captured=={}


def test_prepare_deadline_is_shared_not_reset_per_git_call(op,monkeypatch,tmp_path):
    captured,calls=prepare_fixture(op,monkeypatch,tmp_path)
    clock=[1000.0]
    monkeypatch.setattr(op.time,'monotonic',lambda:clock[0])
    monkeypatch.setattr(op,'guard_repository',lambda **kw:clock.__setitem__(0,clock[0]+10))
    original=op.source
    def advancing(args,**kw):
        result=original(args,**kw);clock[0]+=4;return result
    monkeypatch.setattr(op,'source',advancing)
    op.prepare()
    assert [kwargs['timeout'] for _,kwargs in calls]==[80,76,72]
    assert captured['target.json']['frozen_shard_audit_seconds']==22


def test_prepare_deadline_after_listing_prevents_target(op,monkeypatch,tmp_path):
    captured,_=prepare_fixture(op,monkeypatch,tmp_path)
    clock=[1000.0]
    monkeypatch.setattr(op.time,'monotonic',lambda:clock[0])
    original=op.source
    def advancing(args,**kw):
        result=original(args,**kw)
        if args[0]=='ls-tree':clock[0]+=91
        return result
    monkeypatch.setattr(op,'source',advancing)
    with pytest.raises(ValueError,match='AUDIT_BUDGET'):op.prepare()
    assert captured=={}
