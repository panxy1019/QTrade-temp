"""Offline verification of frozen v2 operator repairs, without SSH."""
import hashlib
import os
from pathlib import Path
import sys
from datetime import datetime, timezone

import pytest

from test_relay_independent_20261028 import load, setup_prepare, market_fixture
from qtrade.monitoring.data_freshness import AUTHORITY, signed


@pytest.fixture
def op():
    return load('relay_v2_operator_offline', 'publication_native_relay_operator_v2_20261028.py')


def prepare_fixture(op,monkeypatch,tmp_path,**kwargs):
    captured=setup_prepare(op,monkeypatch,tmp_path,**kwargs)
    # These platform/runtime guards are tested independently below. The batch
    # data is synthetic and uses the unchanged actual public validators.
    monkeypatch.setattr(op,'runtime_origins',lambda:None)
    monkeypatch.setattr(op,'guard_repository',lambda **kwargs:None)
    return captured


def test_v2_rejects_wrong_first_occurrence_of_shared_sha(op,monkeypatch,tmp_path):
    captured=prepare_fixture(op,monkeypatch,tmp_path,forged=True)
    with pytest.raises(ValueError,match='SHARD_INDEX_MISMATCH'):
        op.prepare()
    assert captured=={}


@pytest.mark.parametrize('damage',['unsigned','bad_checksum','negative_elapsed','boolean_elapsed',
    'wrong_schema','future_unordered_clocks','nonnull_error','elapsed_inf','elapsed_nan',
    'naive_clock','published_outside_attempt','wrong_authority','wrong_index'])
def test_v2_rejects_bad_attempt_before_plan_freeze(op,monkeypatch,tmp_path,damage):
    def change(value):
        if damage=='unsigned':value.pop('evidence_sha256');return
        if damage=='bad_checksum':value['evidence_sha256']='0'*64;return
        if damage=='negative_elapsed':value['elapsed_seconds']=-999
        elif damage=='boolean_elapsed':value['elapsed_seconds']=True
        elif damage=='wrong_schema':value['schema_version']='UNTRUSTED'
        elif damage=='future_unordered_clocks':
            value.update(started_at='2099-01-02T00:00:00+00:00',completed_at='2099-01-01T00:00:00+00:00')
        elif damage=='nonnull_error':value['error_code']='ACTUAL_FAILURE'
        elif damage=='elapsed_inf':value['elapsed_seconds']=float('inf')
        elif damage=='elapsed_nan':value['elapsed_seconds']=float('nan')
        elif damage=='naive_clock':value['started_at']='2026-10-28T11:59:50'
        elif damage=='published_outside_attempt':value['started_at']='2026-10-28T12:00:01+00:00'
        elif damage=='wrong_authority':value['live_trading_enabled']=True
        elif damage=='wrong_index':value['index_id']='9'*64
        # Reseal semantic counterexamples so checksum rejection cannot mask the
        # actual type/clock/success/authority checks under review.
        value['evidence_sha256']=signed({k:v for k,v in value.items() if k!='evidence_sha256'})['evidence_sha256']
    captured=prepare_fixture(op,monkeypatch,tmp_path,attempt_change=change)
    with pytest.raises((ValueError,TypeError)):
        op.prepare()
    assert captured=={}


def test_v2_valid_offline_batch_has_diagnostic_plan_only(op,monkeypatch,tmp_path):
    captured=prepare_fixture(op,monkeypatch,tmp_path)
    result=op.prepare()
    assert result['all_frozen_git_shards_verified'] is True
    assert result['ordinary_publisher_success'] is False
    assert captured=={'target.json':result}


@pytest.mark.parametrize('damage',['missing','directory','too_big','hardlink'])
def test_v2_attempt_file_is_bounded_regular_and_has_one_link(op,monkeypatch,tmp_path,damage):
    captured=prepare_fixture(op,monkeypatch,tmp_path)
    path=op.BASE/'unified-data/market-export-attempt.json'
    if damage=='missing':path.unlink()
    elif damage=='directory':path.unlink();path.mkdir()
    elif damage=='too_big':path.write_bytes(b' '*65_537)
    elif damage=='hardlink':os.link(path,tmp_path/'second-attempt-link')
    with pytest.raises(ValueError,match='EXPORT_ATTEMPT_PATH_OR_SIZE'):op.prepare()
    assert captured=={}


def scope_fixture(op,monkeypatch,tmp_path):
    base=tmp_path/'base';source=base/'publication';root=tmp_path/'attempt'
    source.joinpath('.git/objects/info').mkdir(parents=True);root.mkdir()
    repo=root/'delivery.git';repo.joinpath('objects/info').mkdir(parents=True)
    (repo/'config').write_text('[core]\n bare = true\n')
    (repo/'objects/info/alternates').write_bytes((str(source/'.git/objects')+'\n').encode())
    for key,value in {'BASE':base,'SOURCE':source,'ROOT':root,'REPO':repo,
        'APPROVED_ATTEMPT':root}.items():monkeypatch.setattr(op,key,value)
    calls=[];remote=b'ssh://fixed-public-endpoint/synthetic.git'
    monkeypatch.setattr(op,'REMOTE_SHA',hashlib.sha256(remote).hexdigest())
    def git(repo,args,**kwargs):
        calls.append(tuple(args))
        choices={('rev-parse','--is-bare-repository'):b'true\n',
            ('rev-parse','--absolute-git-dir'):(str(repo)+'\n').encode(),
            ('rev-parse','--path-format=absolute','--git-common-dir'):(str(repo)+'\n').encode(),
            ('remote','get-url','--all','origin'):remote+b'\n',
            ('remote','get-url','--push','--all','origin'):remote+b'\n'}
        assert tuple(args) in choices
        return choices[tuple(args)],0.0
    monkeypatch.setattr(op,'git',git)
    return calls,git


@pytest.mark.parametrize('damage',['wrong_attempt','wrong_repo','source_common',
    'repo_common','http_alternates','source_alternates','wrong_alternates','config_hardlink'])
def test_v2_rejects_alias_and_alternate_scope_before_git(op,monkeypatch,tmp_path,damage):
    calls,_=scope_fixture(op,monkeypatch,tmp_path)
    if damage=='wrong_attempt':monkeypatch.setattr(op,'APPROVED_ATTEMPT',tmp_path/'other')
    elif damage=='wrong_repo':monkeypatch.setattr(op,'REPO',op.SOURCE/'.git')
    elif damage=='source_common':(op.SOURCE/'.git/commondir').write_text('other')
    elif damage=='repo_common':(op.REPO/'commondir').write_text('other')
    elif damage=='http_alternates':(op.REPO/'objects/info/http-alternates').write_text('https://invalid')
    elif damage=='source_alternates':(op.SOURCE/'.git/objects/info/alternates').write_text('other')
    elif damage=='wrong_alternates':(op.REPO/'objects/info/alternates').write_text('other')
    elif damage=='config_hardlink':os.link(op.REPO/'config',tmp_path/'config-link')
    with pytest.raises(ValueError):op.guard_repository(network=True)
    assert calls==[]


@pytest.mark.parametrize('direction',['fetch','push'])
@pytest.mark.parametrize('raw',[b'wrong-endpoint\n',b'good\nsecond\n',b''])
def test_v2_rejects_effective_url_change_multiplicity_and_missing(
    op,monkeypatch,tmp_path,direction,raw):
    calls,original=scope_fixture(op,monkeypatch,tmp_path)
    target=('remote','get-url',*(('--push',) if direction=='push' else ()), '--all','origin')
    def changed(repo,args,**kwargs):
        if tuple(args)==target:calls.append(tuple(args));return raw,0.0
        return original(repo,args,**kwargs)
    monkeypatch.setattr(op,'git',changed)
    with pytest.raises(ValueError,match='EFFECTIVE_REMOTE_DESTINATION_CHANGED'):
        op.guard_repository(network=True)
    assert target in calls


def test_v2_guard_requires_both_effective_urls(op,monkeypatch,tmp_path):
    calls,_=scope_fixture(op,monkeypatch,tmp_path)
    op.guard_repository(network=True)
    assert ('remote','get-url','--all','origin') in calls
    assert ('remote','get-url','--push','--all','origin') in calls


@pytest.mark.parametrize('optional',['source_common','repo_common','http_alternates',
    'source_alternates','source_http_alternates'])
def test_v2_lexists_rejects_present_but_dangling_redirect_mock(op,monkeypatch,tmp_path,optional):
    calls,_=scope_fixture(op,monkeypatch,tmp_path)
    target={'source_common':op.SOURCE/'.git/commondir',
        'repo_common':op.REPO/'commondir',
        'http_alternates':op.REPO/'objects/info/http-alternates',
        'source_alternates':op.SOURCE/'.git/objects/info/alternates',
        'source_http_alternates':op.SOURCE/'.git/objects/info/http-alternates'}[optional]
    assert not target.exists()
    original=os.path.lexists
    monkeypatch.setattr(op.os.path,'lexists',lambda path:Path(path)==target or original(path))
    with pytest.raises(ValueError):op.guard_repository(network=True)
    assert calls==[]


@pytest.mark.parametrize('command',['push','ls-remote'])
def test_v2_network_leaf_rechecks_before_process_creation(op,monkeypatch,command):
    calls=[]
    def reject(**kwargs):calls.append(kwargs);raise ValueError('SYNTHETIC_GUARD_REJECT')
    monkeypatch.setattr(op,'guard_repository',reject)
    monkeypatch.setattr(op.subprocess,'Popen',lambda *a,**k:pytest.fail('process forbidden'))
    with pytest.raises(ValueError,match='SYNTHETIC_GUARD_REJECT'):
        op.git(op.REPO,[command,'origin'])
    assert calls==[{'network':True}]


@pytest.mark.parametrize('shadow',[False,True])
def test_v2_pathfinder_does_not_execute_modules_and_rejects_root_shadow(
    op,monkeypatch,tmp_path,shadow):
    assert sys.flags.safe_path,'run pytest with -P as actual operator'
    release=tmp_path/'release';root=tmp_path/'operator'
    for parent in [release/'scripts',release/'src/qtrade/research',root/'scripts']:
        parent.mkdir(parents=True)
    for relative in ['scripts/__init__.py','scripts/publish_research_snapshot.py',
        'src/qtrade/__init__.py','src/qtrade/research/__init__.py',
        'src/qtrade/research/unified_market.py']:
        (release/relative).write_text('raise RuntimeError("MODULE_EXECUTION_FORBIDDEN")\n')
    if shadow:
        (root/'scripts/__init__.py').write_text('raise RuntimeError("SHADOW_EXECUTION_FORBIDDEN")\n')
        (root/'scripts/publish_research_snapshot.py').write_text('raise RuntimeError("SHADOW_EXECUTION_FORBIDDEN")\n')
    monkeypatch.setattr(op,'RELEASE',release)
    monkeypatch.setattr(sys,'path',[str(root),str(release/'src'),str(release)])
    if shadow:
        with pytest.raises(ValueError,match='RUNTIME_IMPORT_SHADOW'):op.runtime_origins()
    else:op.runtime_origins()


def test_v2_intent_file_is_create_only_and_fsyncs_file_plus_directory(op,monkeypatch,tmp_path):
    # Windows cannot fsync a directory handle: instrument only the directory
    # syscalls, keep exclusive file creation/write/flush on the real filesystem.
    monkeypatch.setattr(op,'ROOT',tmp_path)
    real_open=os.open;real_close=os.close;real_fsync=os.fsync;events=[];directory_fd=987654
    monkeypatch.setattr(op.os,'O_DIRECTORY',getattr(os,'O_DIRECTORY',0),raising=False)
    def opened(path,flags,*args,**kwargs):
        if Path(path)==tmp_path:events.append('directory_open');return directory_fd
        return real_open(path,flags,*args,**kwargs)
    def synced(fd):
        if fd==directory_fd:events.append('directory_fsync')
        else:events.append('file_fsync');real_fsync(fd)
    def closed(fd):
        if fd==directory_fd:events.append('directory_close')
        else:real_close(fd)
    monkeypatch.setattr(op.os,'open',opened);monkeypatch.setattr(op.os,'fsync',synced)
    monkeypatch.setattr(op.os,'close',closed)
    op.save('PUSH_INTENT.json',{'no_retry_on_uncertain':True})
    before=(tmp_path/'PUSH_INTENT.json').read_bytes()
    assert events==['file_fsync','directory_open','directory_fsync','directory_close']
    with pytest.raises(FileExistsError):op.save('PUSH_INTENT.json',{'replay':True})
    assert (tmp_path/'PUSH_INTENT.json').read_bytes()==before
