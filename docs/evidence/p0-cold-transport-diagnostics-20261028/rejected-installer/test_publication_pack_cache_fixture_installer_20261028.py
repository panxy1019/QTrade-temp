"""Real local native Git fixtures; no SSH/URL/production source is accessible."""
import dataclasses
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import random
import stat
import subprocess
import sys
import threading
import time

import pytest

HERE=Path(__file__).parent
spec=importlib.util.spec_from_file_location('pack_cache_fixture_installer',
    HERE/'publication_pack_cache_fixture_installer_20261028.py')
cache=importlib.util.module_from_spec(spec);sys.modules[spec.name]=cache;spec.loader.exec_module(cache)


def native(repo,args,raw=None,ok=True):
    env=cache.environment()
    env.update(GIT_AUTHOR_NAME='SYNTHETIC',GIT_AUTHOR_EMAIL='synthetic@example.invalid',
        GIT_COMMITTER_NAME='SYNTHETIC',GIT_COMMITTER_EMAIL='synthetic@example.invalid',
        GIT_AUTHOR_DATE='2026-10-28T00:00:00+00:00',GIT_COMMITTER_DATE='2026-10-28T00:00:00+00:00')
    result=subprocess.run([str(cache.GIT),'-C',str(repo),'-c','core.hooksPath='+os.devnull,
        '-c','gc.auto=0','-c','protocol.allow=never','-c','protocol.file.allow=always',*args],
        input=raw,capture_output=True,env=env,timeout=15)
    if ok:assert result.returncode==0,result.stderr
    return result.stdout if ok else result


def files(root):
    return {p.relative_to(root).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
        for p in root.rglob('*') if p.is_file()}


def source_invariants(source):
    all_files=files(source)
    return {name:digest for name,digest in all_files.items() if not name.startswith('.git/objects/pack/.qtrade-')}


@pytest.fixture
def fixture(tmp_path,monkeypatch):
    (tmp_path/'fixture.json').write_bytes(cache.MARKER)
    source=tmp_path/'source';source.mkdir();native(source,['init','--template=','-b','main'])
    (source/'latest.json').write_bytes(b'{"fixture":"public-baseline"}\n')
    native(source,['add','--all']);native(source,['commit','-m','SYNTHETIC baseline'])
    base=native(source,['rev-parse','HEAD']).decode().strip()
    remote=tmp_path/'remote.git';remote.mkdir();native(remote,['init','--bare','--template='])
    native(source,['push',str(remote),'HEAD:refs/heads/main'])
    # A real existing pack+MIDX predates the new public loose objects.
    native(source,['repack','-ad']);native(source,['multi-pack-index','write'])
    assert (source/'.git/objects/pack/multi-pack-index').is_file()
    rng=random.Random(29)
    for number in range(4):
        raw=(json.dumps({'synthetic_public':rng.randbytes(8192).hex(),
            'live_trading_enabled':False},sort_keys=True)+'\n').encode()
        path=source/'unified-market/objects'/(hashlib.sha256(raw).hexdigest()+'.json')
        path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    (source/'unified-market.json').write_bytes(b'{"fixture":"complete-public-generation"}\n')
    native(source,['add','--all']);native(source,['commit','-m','SYNTHETIC complete generation'])
    target=native(source,['rev-parse','HEAD']).decode().strip()
    selected=native(source,['rev-list','--objects','--no-object-names',target,'^'+base]).decode().splitlines()
    certificate={}
    for oid in selected:
        kind=native(source,['cat-file','-t',oid]).decode().strip()
        raw=native(source,['cat-file',kind,oid])
        certificate[oid]={'kind':kind,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    pack_raw=native(source,['pack-objects','--stdout','--no-reuse-delta','--no-reuse-object',
        '--window=0','--depth=0'],('\n'.join(selected)+'\n').encode())
    pack=tmp_path/'candidate.pack';pack.write_bytes(pack_raw)
    verify=tmp_path/'stage.git';verify.mkdir();native(verify,['init','--bare','--template='])
    (verify/'objects/info/alternates').write_bytes((str(source/'.git/objects')+'\n').encode())
    native(verify,['index-pack','--strict',str(pack)])
    index=pack.with_suffix('.idx')
    candidate=cache.Candidate(pack,index,target,base,hashlib.sha256(pack_raw).hexdigest(),
        hashlib.sha256(index.read_bytes()).hexdigest(),certificate)
    events=[];real_sync=cache.sync_directory
    def directory_sync(path):
        events.append(('directory_fsync',Path(path)))
        # Windows has no supported os.open(directory)+fsync. Its test run
        # instruments this boundary; a POSIX run delegates to the real syscall.
        if os.name=='posix':real_sync(path)
    monkeypatch.setattr(cache,'sync_directory',directory_sync)
    return tmp_path,source,remote,candidate,events


def install(fixture,**kwargs):
    root,_,_,candidate,_=fixture
    return cache.install_fixture_pack(root,candidate,'a'*64,time.monotonic()+30,**kwargs)


def test_success_installs_additive_pair_preserves_loose_refs_config_midx_and_pushes_complete(fixture):
    root,source,remote,candidate,events=fixture
    before=source_invariants(source)
    result=install(fixture)
    assert result['state']=='CACHE_INSTALLED_LOCAL_FIXTURE_ONLY'
    after=source_invariants(source)
    assert all(after.get(name)==digest for name,digest in before.items())
    added=set(after)-set(before)
    assert added=={'.git/objects/pack/pack-'+result['pack_id']+suffix for suffix in ('.pack','.idx')}
    assert not any(path.name.endswith('.tmp') for path in (source/'.git/objects/pack').iterdir())
    native(source,['fsck','--strict','--no-reflogs'])
    native(source,['push',str(remote),'HEAD:refs/heads/main'])
    assert native(remote,['rev-parse','refs/heads/main']).decode().strip()==candidate.target
    assert native(remote,['ls-tree','-r','refs/heads/main'])==native(source,['ls-tree','-r',candidate.target])
    assert native(remote,['show','refs/heads/main:unified-market.json'])==b'{"fixture":"complete-public-generation"}\n'
    native(remote,['fsck','--strict','--no-reflogs'])
    assert result['manual_intent_consumed'] is False and result['push_performed'] is False
    # The push above belongs only to this explicit native fixture test.


def test_create_only_pack_then_dirsync_then_index_then_dirsync(fixture):
    root,source,_,candidate,events=fixture
    def phase(name):events.append(('phase',name))
    install(fixture,phase_hook=phase)
    packdir=source/'.git/objects/pack'
    ordered=[item for item in events if item[0]=='phase' or item==('directory_fsync',packdir)]
    assert ordered[:7]==[('phase','COPIES_VERIFIED'),('phase','PACK_LINKED'),
        ('directory_fsync',packdir),('phase','PACK_DIRECTORY_DURABLE'),
        ('phase','INDEX_LINKED'),('directory_fsync',packdir),('phase','INDEX_DIRECTORY_DURABLE')]


def test_identical_hit_does_not_rewrite_any_source_file_or_claim(fixture):
    root,source,_,_,_=fixture
    first=install(fixture);before=files(source);claim=Path(first['claim']);claim_before=files(claim)
    result=install(fixture)
    assert result['state']=='VERIFIED_CACHE_HIT'
    assert files(source)==before and files(claim)==claim_before


def test_old_manual_intent_cannot_be_rebound_to_reset_existing_claim(fixture):
    root,source,_,candidate,_=fixture
    install(fixture);before=files(source)
    with pytest.raises(ValueError,match='CLAIM_BINDING_CHANGED'):
        cache.install_fixture_pack(root,candidate,'b'*64,time.monotonic()+30)
    assert files(source)==before


@pytest.mark.parametrize('damage',['pack','index','certificate','objectset'])
def test_bad_candidate_rejected_before_source_write(fixture,damage):
    root,source,_,candidate,_=fixture
    before=files(source)
    if damage=='pack':candidate.pack.write_bytes(candidate.pack.read_bytes()+b'bad')
    elif damage=='index':
        candidate.index.chmod(stat.S_IREAD|stat.S_IWRITE)
        candidate.index.write_bytes(candidate.index.read_bytes()+b'bad')
    else:
        changed={k:dict(v) for k,v in candidate.objects.items()}
        key=next(iter(changed))
        if damage=='certificate':changed[key]['sha256']='0'*64
        else:changed.pop(key)
        candidate=dataclasses.replace(candidate,objects=changed)
    with pytest.raises(ValueError):
        cache.install_fixture_pack(root,candidate,'a'*64,time.monotonic()+30)
    assert files(source)==before


def test_different_existing_final_content_is_never_overwritten(fixture):
    _,source,_,candidate,_=fixture
    final=source/'.git/objects/pack'/('pack-'+candidate.pack.read_bytes()[-20:].hex()+'.idx')
    final.write_bytes(b'CORRUPT_SYNTHETIC_INDEX')
    before=files(source)
    with pytest.raises((ValueError,cache.InstallationUncertain)):install(fixture)
    assert files(source)==before and final.read_bytes()==b'CORRUPT_SYNTHETIC_INDEX'


@pytest.mark.parametrize('phase',['PACK_LINKED','INDEX_LINKED'])
def test_fault_between_visibility_points_keeps_original_objects_and_blocks_replay(fixture,phase):
    root,source,remote,candidate,_=fixture
    before=source_invariants(source)
    def fail(name):
        if name==phase:raise OSError('SYNTHETIC_CRASH_POINT')
    with pytest.raises(cache.InstallationUncertain):install(fixture,phase_hook=fail)
    after=source_invariants(source)
    assert all(after.get(name)==digest for name,digest in before.items())
    assert not list((root/'claims').rglob('RESULT.json'))
    with pytest.raises(ValueError,match='RECONCILIATION_REQUIRED'):install(fixture)
    native(source,['fsck','--strict','--no-reflogs'])
    native(source,['push',str(remote),'HEAD:refs/heads/main'])
    assert native(remote,['rev-parse','refs/heads/main']).decode().strip()==candidate.target


def test_partial_index_copy_never_becomes_active_index(fixture):
    root,source,_,candidate,_=fixture
    before=source_invariants(source)
    def damage(name):
        if name=='COPIES_VERIFIED':
            path=next((source/'.git/objects/pack').glob('.qtrade-*.idx.tmp'))
            path.write_bytes(path.read_bytes()[:16])
    with pytest.raises(cache.InstallationUncertain):install(fixture,phase_hook=damage)
    assert source_invariants(source)==before
    assert not list((source/'.git/objects/pack').glob('pack-'+candidate.pack.read_bytes()[-20:].hex()+'.idx'))
    assert not list((root/'claims').rglob('RESULT.json'))


@pytest.mark.parametrize('failing_packdir_sync',[1,2])
def test_directory_fsync_failure_is_uncertain_and_never_claims_success(fixture,monkeypatch,failing_packdir_sync):
    root,source,_,_,_=fixture
    before=source_invariants(source);real=cache.sync_directory;calls=[0]
    def fail(path):
        if path==source/'.git/objects/pack':
            calls[0]+=1
            if calls[0]==failing_packdir_sync:raise OSError('SYNTHETIC_DIRECTORY_FSYNC_FAILURE')
        real(path)
    monkeypatch.setattr(cache,'sync_directory',fail)
    with pytest.raises(cache.InstallationUncertain):install(fixture)
    assert not list((root/'claims').rglob('RESULT.json'))
    assert all(source_invariants(source).get(name)==digest for name,digest in before.items())


def test_real_native_reader_and_push_during_pack_only_visibility(fixture):
    root,source,remote,candidate,_=fixture
    before=source_invariants(source);errors=[];reads=[]
    visible=threading.Event();finished=threading.Event()
    def reader():
        try:
            assert visible.wait(10)
            for _ in range(3):
                for oid,item in candidate.objects.items():
                    raw=native(source,['cat-file',item['kind'],oid])
                    assert hashlib.sha256(raw).hexdigest()==item['sha256']
                reads.append(True)
            native(source,['push',str(remote),'HEAD:refs/heads/main'])
        except BaseException as error:errors.append(error)
        finally:finished.set()
    worker=threading.Thread(target=reader);worker.start()
    def pause(name):
        if name=='PACK_DIRECTORY_DURABLE':
            visible.set();assert finished.wait(15)
    result=install(fixture,phase_hook=pause)
    worker.join(15)
    assert not worker.is_alive() and not errors and len(reads)==3
    assert all(source_invariants(source).get(name)==digest for name,digest in before.items())
    assert native(remote,['rev-parse','refs/heads/main']).decode().strip()==candidate.target
    native(remote,['fsck','--strict','--no-reflogs'])


def test_nonfixture_and_source_redirection_reject_before_install(fixture):
    root,source,_,candidate,_=fixture
    before=files(source)
    (root/'fixture.json').write_bytes(b'{}')
    with pytest.raises(ValueError,match='SYNTHETIC_FIXTURE_REQUIRED'):install(fixture)
    assert files(source)==before


def test_deleted_historical_private_path_is_rejected_even_with_clean_tip(fixture):
    root,source,_,old_candidate,_=fixture
    private=source/'not-public/deleted.json';private.parent.mkdir()
    private.write_bytes(b'{"synthetic":"deleted-path-counterexample"}\n')
    native(source,['add','--all']);native(source,['commit','-m','SYNTHETIC disallowed intermediate tree'])
    private.unlink();private.parent.rmdir()
    native(source,['add','--all']);native(source,['commit','-m','SYNTHETIC clean final tree'])
    target=native(source,['rev-parse','HEAD']).decode().strip()
    assert b'not-public' not in native(source,['ls-tree','-r',target])
    selected=native(source,['rev-list','--objects','--no-object-names',target,'^'+old_candidate.base]).decode().splitlines()
    certificate={}
    for oid in selected:
        kind=native(source,['cat-file','-t',oid]).decode().strip()
        raw=native(source,['cat-file',kind,oid])
        certificate[oid]={'kind':kind,'bytes':len(raw),'sha256':hashlib.sha256(raw).hexdigest()}
    raw=native(source,['pack-objects','--stdout','--no-reuse-delta','--no-reuse-object',
        '--window=0','--depth=0'],('\n'.join(selected)+'\n').encode())
    pack=root/'history.pack';pack.write_bytes(raw)
    native(root/'stage.git',['index-pack','--strict',str(pack)])
    candidate=cache.Candidate(pack,pack.with_suffix('.idx'),target,old_candidate.base,
        hashlib.sha256(raw).hexdigest(),hashlib.sha256(pack.with_suffix('.idx').read_bytes()).hexdigest(),certificate)
    before=files(source)
    with pytest.raises(ValueError,match='PUBLIC_TREE_PATH'):
        cache.install_fixture_pack(root,candidate,'a'*64,time.monotonic()+30)
    assert files(source)==before
