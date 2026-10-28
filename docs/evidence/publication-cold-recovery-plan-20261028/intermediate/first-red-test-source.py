from __future__ import annotations

import ast
from datetime import datetime, timedelta, timezone
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shlex
import shutil
import subprocess
import sys
import time

import pytest

HERE=Path(__file__).resolve().parent
SOURCE=HERE/'publication_cold_recovery_operator_20261028.py'
BASE=HERE/'publication_native_relay_operator_v3_20261028.py'
spec=importlib.util.spec_from_file_location('cold_recovery_fixture_candidate',SOURCE)
op=importlib.util.module_from_spec(spec);spec.loader.exec_module(op)
NOW=datetime(2026,10,28,15,0,tzinfo=timezone.utc)


def sha(raw):return hashlib.sha256(raw).hexdigest()


def write(path,value):
    raw=(json.dumps(value,sort_keys=True,indent=2)+'\n').encode()
    path.write_bytes(raw);return sha(raw)


def portable_save(root):
    def save(name,value):
        with (root/name).open('xb') as handle:
            handle.write((json.dumps(value,sort_keys=True,indent=2)+'\n').encode())
    return save


@pytest.fixture
def claims(monkeypatch,tmp_path):
    legacy=tmp_path/'legacy';legacy.mkdir()
    for name in ('PUSH_INTENT.json','PUSH_UNCERTAIN.json'):
        shutil.copyfile(HERE/'fixtures'/name,legacy/name)
    monkeypatch.setattr(op,'LEGACY_ATTEMPT',legacy)
    return op.legacy_claims()


@pytest.fixture
def documents(monkeypatch,tmp_path,claims):
    monkeypatch.setattr(op,'ROOT',tmp_path)
    plan={'schema_version':'qtrade.publication-cold-recovery-target.v1','attempt_root':str(op.APPROVED_ATTEMPT),
        'source_revision':'fba51aade2ee2410dda087e95cf054e53375ed3a',
        'derived_from_operator_sha256':op.BASE_OPERATOR_SHA,'operator_sha256':sha(SOURCE.read_bytes()),
        'expected_remote_main':op.EXPECTED_MAIN,'legacy_uncertain_claims':claims,'target_commit':'b'*40,
        'approval_state':'PENDING_NEW_EXPLICIT_USER_AUTHORIZATION','proposed_one_shot_cold_push_seconds':180,
        'owned_cleanup_term_seconds':2,'owned_cleanup_kill_wait_seconds':2,
        'ordinary_export_budget_seconds':45,'ordinary_git_budget_seconds':90,
        'force_push':False,'automatic_retry':False,'preflight_advertisement_is_cross_transaction_lock':False,
        'all_frozen_git_shards_verified':True,'index_entries':3000,'shard_count':5987,
        'frozen_shard_audit_budget_seconds':90,'export_elapsed_seconds':30.0,'frozen_shard_audit_seconds':50.0,
        'captured_at':(NOW-timedelta(minutes=2)).isoformat(),'ssh_binding':{'fixed':'fixture'},
        'index_raw_sha256':sha(b'fixture-index')}
    plan_sha=write(tmp_path/'target.json',plan)
    approval={'schema_version':'qtrade.one-shot-cold-user-authorization.v1','plan_sha256':plan_sha,
        'authorization_reference':'SYNTHETIC_FIXTURE_APPROVAL_NOT_USER_AUTHORIZATION',
        'authorized_at':(NOW-timedelta(minutes=1)).isoformat(),
        'authorization_kind':'EXPLICIT_NEW_USER_APPROVAL_OF_EXACT_PREPARED_PLAN',
        'one_shot_cold_push_seconds':180,'ordinary_budgets_unchanged':True,'automatic_retry':False,'force_push':False}
    approval_sha=write(tmp_path/'USER_AUTHORIZATION.json',approval)
    return plan,approval,plan_sha,approval_sha


def test_frozen_base_and_unchanged_safety_helpers():
    assert sha(BASE.read_bytes())==op.BASE_OPERATOR_SHA
    old=ast.parse(BASE.read_text());new=ast.parse(SOURCE.read_text())
    functions=lambda tree:{x.name:ast.dump(x,include_attributes=False) for x in tree.body if isinstance(x,(ast.FunctionDef,ast.ClassDef))}
    a,b=functions(old),functions(new)
    for name in ('save','clean_env','runtime_origins','guard_scope','audit_remaining','guard_repository',
                 'validate_export_attempt','parse_frozen_tree','public_objects_directory','read_frozen_shard','initialize'):
        assert a[name]==b[name],name
    assert op.AUDIT_BUDGET_SECONDS==90 and op.COLD_PUSH_SECONDS==180
    assert op.APPROVED_ATTEMPT.name=='publication-cold-recovery-20261028T143000Z'


@pytest.mark.parametrize('timeout',[0,-1,91,180,float('inf'),float('nan'),True])
def test_ordinary_git_cannot_receive_cold_budget(monkeypatch,timeout):
    monkeypatch.setattr(op.subprocess,'Popen',lambda *a,**k:pytest.fail('spawned'))
    with pytest.raises(ValueError,match='ORDINARY_GIT_90_SECOND_BOUND'):
        op.git(op.REPO,['show','HEAD'],timeout=timeout)


def test_generic_git_push_forbidden_before_network(monkeypatch):
    monkeypatch.setattr(op,'guard_repository',lambda **k:pytest.fail('network guard reached'))
    with pytest.raises(ValueError,match='PUSH_REQUIRES_EXPLICIT_COLD_SEND'):
        op.git(op.REPO,['push','origin','HEAD:refs/heads/main'])


def ssh_fixture(monkeypatch,tmp_path,mutate=None):
    tokens=['ssh','-i',str(tmp_path/'private-key'),'-o','IdentitiesOnly=yes',
        '-o','UserKnownHostsFile='+str(tmp_path/'known-hosts'),'-o','StrictHostKeyChecking=yes',
        '-o','BatchMode=yes','-o','ConnectTimeout=10']
    if mutate:tokens=mutate(tokens)
    raw=shlex.join(tokens).encode();monkeypatch.setattr(op,'SSH_SHA',sha(raw));return raw


def test_direct_parser_keeps_one_existing_identity_and_known_hosts(monkeypatch,tmp_path):
    raw=ssh_fixture(monkeypatch,tmp_path)
    original,identity,known=op.parse_direct_ssh(raw)
    assert len(original)==13 and identity==tmp_path/'private-key' and known==tmp_path/'known-hosts'


@pytest.mark.parametrize('option',['ProxyCommand=secret-proxy','ControlMaster=yes','UserKnownHostsFile=none',
    'UserKnownHostsFile=/a /b','UserKnownHostsFile=/a,/b','IdentitiesOnly=no','StrictHostKeyChecking=no'])
def test_direct_parser_rejects_ambiguous_or_override_options(monkeypatch,tmp_path,option):
    def mutate(tokens):
        key=option.split('=')[0]
        position=next((i for i,x in enumerate(tokens) if x.startswith(key+'=')),12)
        tokens[position]=option;return tokens
    raw=ssh_fixture(monkeypatch,tmp_path,mutate)
    with pytest.raises(ValueError):op.parse_direct_ssh(raw)


def test_direct_parser_rejects_changed_existing_command(monkeypatch,tmp_path):
    raw=ssh_fixture(monkeypatch,tmp_path)
    with pytest.raises(ValueError,match='EXISTING_SSH_COMMAND_CHANGED'):op.parse_direct_ssh(raw+b' ')


def test_bounded_regular_input_and_hardlink_rejection(tmp_path):
    f=tmp_path/'public';f.write_bytes(b'public-hostkey')
    assert op.bounded_regular_bytes(f,100)==b'public-hostkey'
    with pytest.raises(ValueError,match='REGULAR_INPUT_BOUND'):op.bounded_regular_bytes(f,2)
    link=tmp_path/'linked';os.link(f,link)
    with pytest.raises(ValueError,match='REGULAR_INPUT_BOUND'):op.bounded_regular_bytes(f,100)


def test_actual_old_claim_bytes_are_bound_and_immutable(monkeypatch,claims):
    assert claims['intent_sha256']==op.LEGACY_INTENT_SHA
    assert claims['uncertain_sha256']==op.LEGACY_UNCERTAIN_SHA
    f=op.LEGACY_ATTEMPT/'PUSH_UNCERTAIN.json';f.write_bytes(f.read_bytes()+b' ')
    with pytest.raises(ValueError,match='LEGACY_UNCERTAIN_CLAIMS_CHANGED'):op.legacy_claims()


def test_exact_current_stage_authorization_required(documents):
    plan,approval,plan_sha,approval_sha=documents
    assert op.approved_plan(plan_sha,approval_sha,NOW)==plan
    with pytest.raises(ValueError,match='EXACT_DOCUMENT_SHA_REQUIRED'):op.approved_plan(plan_sha,None,NOW)
    with pytest.raises(ValueError,match='EXACT_DOCUMENT_BYTES_CHANGED'):op.approved_plan('0'*64,approval_sha,NOW)


@pytest.mark.parametrize('field,value',[
    ('authorization_kind','ABC_CONTINUING_AUTHORIZATION'),('plan_sha256','0'*64),
    ('one_shot_cold_push_seconds',90),('ordinary_budgets_unchanged',False),
    ('automatic_retry',True),('force_push',True),
    ('authorized_at','2026-10-28T15:01:00+00:00'),('authorized_at','2026-10-28T12:00:00+00:00'),
    ('authorized_at','2026-10-28T14:59:00'),('authorized_at','2026-10-28T14:57:00+00:00')])
def test_approval_rejects_wrong_scope_budget_or_clock(documents,field,value):
    plan,approval,plan_sha,_=documents;approval[field]=value
    approval_sha=write(op.ROOT/'USER_AUTHORIZATION.json',approval)
    with pytest.raises(ValueError):op.approved_plan(plan_sha,approval_sha,NOW)


@pytest.mark.parametrize('field,value',[
    ('operator_sha256','0'*64),('expected_remote_main','0'*40),('index_entries',2999),('shard_count',5986),
    ('ordinary_git_budget_seconds',180),('ordinary_export_budget_seconds',46),
    ('export_elapsed_seconds',45.01),('frozen_shard_audit_seconds',90.01),
    ('force_push',True),('automatic_retry',True),('approval_state','ABC_ALREADY_APPROVED')])
def test_prepared_scope_is_strict(documents,field,value):
    plan,approval,_,_=documents;plan[field]=value
    plan_sha=write(op.ROOT/'target.json',plan);approval['plan_sha256']=plan_sha
    approval_sha=write(op.ROOT/'USER_AUTHORIZATION.json',approval)
    with pytest.raises(ValueError):op.approved_plan(plan_sha,approval_sha,NOW)


def test_document_duplicate_keys_and_nonfinite_rejected(tmp_path):
    f=tmp_path/'input.json'
    for raw in (b'{"x":1,"x":2}',b'{"x":NaN}'):
        f.write_bytes(raw)
        with pytest.raises(ValueError):op.exact_document(f,sha(raw),1000)


def test_progress_never_exports_sensitive_unknown_text():
    p=op.Progress()
    secret=b'UNKNOWN ssh private configuration token=FIXTURE_SECRET_NEVER_SAVE'
    p.feed(b'Enumerating objects: 20, done.\nCounting objects: 50% (10/20)\r',.1)
    p.feed(b'Writing objects: 100% (20/20), fixture secret suffix\r',.2)
    p.feed(b'remote: Resolving deltas: 50% (2/4)\n'+secret+b'\n',.3)
    out=p.finish(.4);raw=json.dumps(out)
    assert len(out['events'])==4 and 'Resolving deltas' in raw
    assert 'FIXTURE_SECRET_NEVER_SAVE' not in raw and 'fixture secret suffix' not in raw
    assert out['unknown_text_sha256']==sha(secret) and out['unknown_text_bytes']==len(secret)
    assert out['writing_100_percent_is_server_ack'] is False


def test_progress_storage_and_chunk_bounds():
    p=op.Progress()
    for i in range(4200):p.feed(b'Writing objects: 1% (1/100)\r',float(i))
    p.feed(b'x'*8192,4201.0)
    out=p.finish(4202.0)
    assert len(out['events'])==4096 and out['dropped_event_count']==104
    assert len(p.pending)<=4096 and out['unknown_text_bytes']==8192
    with pytest.raises(ValueError,match='TELEMETRY_CHUNK_BOUND'):p.feed(b'x'*8193,1)


@pytest.fixture
def sender(monkeypatch,tmp_path):
    plan={'target_commit':'b'*40,'ssh_binding':{'fixture':True},'legacy_uncertain_claims':{'old':True},
          'index_raw_sha256':sha(b'fixture-index')}
    state={'ads':op.EXPECTED_MAIN,'calls':[],'sends':0,'failure':None}
    monkeypatch.setattr(op,'ROOT',tmp_path)
    monkeypatch.setattr(op,'save',portable_save(tmp_path))
    monkeypatch.setattr(op,'approved_plan',lambda *a:plan)
    monkeypatch.setattr(op,'legacy_claims',lambda:plan['legacy_uncertain_claims'])
    monkeypatch.setattr(op,'guard_repository',lambda **k:None)
    monkeypatch.setattr(op,'guard_owner',lambda:None)
    monkeypatch.setattr(op,'ssh_environment',lambda:({'fixture':'not-real-credentials'},plan['ssh_binding']))
    def git(*args,**kwargs):
        state['calls'].append(args[1]);return (state['ads']+'\trefs/heads/main\n').encode(),.1
    monkeypatch.setattr(op,'git',git)
    monkeypatch.setattr(op,'source',lambda args,**kwargs:b'fixture-index' if args[0]=='show' else b'')
    def send(*args):
        state['sends']+=1
        assert (tmp_path/'PUSH_INTENT.json').exists()
        if state['failure']:raise state['failure']
        return 1.0
    monkeypatch.setattr(op,'cold_push',send)
    return plan,state


def test_sender_drift_stops_before_intent_and_never_retries(sender):
    plan,state=sender;state['ads']='c'*40
    with pytest.raises(ValueError,match='REMOTE_MAIN_DRIFT'):op.send_cold('1'*64,'2'*64)
    assert state['sends']==0 and not (op.ROOT/'PUSH_INTENT.json').exists()
    with pytest.raises(ValueError,match='ONE_SHOT_ALREADY_CLAIMED'):op.send_cold('1'*64,'2'*64)


def test_sender_already_target_does_not_send(sender):
    plan,state=sender;state['ads']=plan['target_commit']
    assert op.send_cold('1'*64,'2'*64)['state']=='REMOTE_ALREADY_EXACT_TARGET_NO_SEND'
    assert state['sends']==0 and not (op.ROOT/'PUSH_INTENT.json').exists()


def test_sender_one_durable_claim_only(sender):
    plan,state=sender
    result=op.send_cold('1'*64,'2'*64)
    assert state['sends']==1 and result['ordinary_publisher_success'] is False
    intent=json.loads((op.ROOT/'PUSH_INTENT.json').read_bytes())
    assert intent['one_shot_cold_push_seconds']==180 and intent['ordinary_git_budget_seconds']==90
    assert intent['legacy_uncertain_claims']==plan['legacy_uncertain_claims'] and intent['force_push'] is False
    with pytest.raises(ValueError,match='ONE_SHOT_ALREADY_CLAIMED'):op.send_cold('1'*64,'2'*64)
    assert state['sends']==1


@pytest.mark.parametrize('failure',[TimeoutError('fixture'),OSError('fixture'),KeyboardInterrupt()])
def test_uncertain_push_persists_claim_and_is_not_retried(sender,failure):
    plan,state=sender;state['failure']=failure
    with pytest.raises(type(failure)):op.send_cold('1'*64,'2'*64)
    assert json.loads((op.ROOT/'PUSH_UNCERTAIN.json').read_bytes())['state']=='OUTCOME_UNCERTAIN_NO_RETRY'
    with pytest.raises(ValueError,match='ONE_SHOT_ALREADY_CLAIMED'):op.send_cold('1'*64,'2'*64)
    assert state['sends']==1


def test_sender_requires_authority_before_advertisement(sender,monkeypatch):
    _,state=sender
    def reject(*args):raise ValueError('NATIVE_RELAY_NEW_EXACT_PLAN_USER_AUTHORIZATION_REQUIRED')
    monkeypatch.setattr(op,'approved_plan',reject)
    with pytest.raises(ValueError):op.send_cold(None,None)
    assert state['calls']==[] and state['sends']==0


def test_cli_cannot_run_from_unapproved_path_or_windows():
    env={k:v for k,v in os.environ.items() if not k.upper().startswith(('GIT_','QTRADE_'))}
    env['LIVE_TRADING_ENABLED']='false'
    cp=subprocess.run([sys.executable,'-B','-P',str(SOURCE),'send-cold'],env=env,capture_output=True,timeout=10)
    assert cp.returncode==2
    result=json.loads(cp.stdout)
    assert result['code'] in {'NATIVE_RELAY_FIXED_PATHS_REQUIRED','NATIVE_RELAY_SAFE_RUNTIME_REQUIRED'}
    assert b'Traceback' not in cp.stderr


def test_cli_help_is_safe_and_successful():
    cp=subprocess.run([sys.executable,'-B','-P',str(SOURCE),'--help'],capture_output=True,timeout=10)
    assert cp.returncode==0 and b'send-cold' in cp.stdout


@pytest.fixture
def native(tmp_path):
    executable=shutil.which('git')
    if not executable:pytest.skip('native Git unavailable; do not claim native CAS acceptance')
    env={k:v for k,v in os.environ.items() if not k.upper().startswith('GIT_')}
    env.update(GIT_CONFIG_NOSYSTEM='1',GIT_CONFIG_GLOBAL=str(tmp_path/'empty-config'),
        GIT_ALLOW_PROTOCOL='file',GIT_TERMINAL_PROMPT='0')
    (tmp_path/'empty-config').write_bytes(b'')
    records=[]
    def args(repo,*words):
        return [executable,'-c','protocol.allow=never','-c','protocol.file.allow=always',
            '-c','core.hooksPath=/dev/null','-c','core.autocrlf=false','-C',str(repo),*words]
    def run(repo,*words,ok=True):
        argv=args(repo,*words);cp=subprocess.run(argv,env=env,capture_output=True,timeout=15)
        records.append({'argv':argv,'rc':cp.returncode,'stdout':cp.stdout.decode('utf-8','replace'),'stderr':cp.stderr.decode('utf-8','replace')})
        (tmp_path/'native-commands.json').write_text(json.dumps(records,indent=2)+'\n')
        if ok:assert cp.returncode==0,records[-1]
        return cp
    source=tmp_path/'source';remote=tmp_path/'remote.git'
    run(tmp_path,'init','--initial-branch=fixture',str(source))
    run(source,'config','user.name','Synthetic Fixture');run(source,'config','user.email','fixture@example.invalid')
    def commit(value):
        (source/'content').write_text(value)
        run(source,'add','content');run(source,'-c','commit.gpgsign=false','commit','-m',value)
        return run(source,'rev-parse','HEAD').stdout.decode().strip()
    old=commit('base');run(source,'branch','old',old)
    middle=commit('middle');target=commit('target');run(source,'branch','target',target)
    run(source,'checkout','--detach',old);rival=commit('rival')
    run(tmp_path,'init','--bare',str(remote))
    run(source,'push',str(remote),old+':refs/heads/main',rival+':refs/heads/fixture-rival')
    return {'root':tmp_path,'source':source,'remote':remote,'env':env,'args':args,'run':run,
            'old':old,'middle':middle,'target':target,'rival':rival}


def test_native_nonforce_rejects_divergent_change_after_ls(native):
    n=native;run=n['run']
    assert run(n['source'],'ls-remote',str(n['remote']),'refs/heads/main').stdout.startswith(n['old'].encode())
    run(n['remote'],'update-ref','refs/heads/main',n['rival'],n['old'])
    cp=run(n['source'],'push',str(n['remote']),n['target']+':refs/heads/main',ok=False)
    assert cp.returncode!=0 and run(n['remote'],'rev-parse','refs/heads/main').stdout.decode().strip()==n['rival']


def test_native_ls_send_gap_can_allow_safe_forward_change(native):
    n=native;run=n['run']
    assert run(n['source'],'ls-remote',str(n['remote']),'refs/heads/main').stdout.startswith(n['old'].encode())
    run(n['source'],'push',str(n['remote']),n['middle']+':refs/heads/main')
    run(n['source'],'push',str(n['remote']),n['target']+':refs/heads/main')
    assert run(n['remote'],'rev-parse','refs/heads/main').stdout.decode().strip()==n['target']


def test_native_server_old_oid_cas_after_its_advertisement(native):
    n=native;run=n['run'];barrier=n['root']/'barrier';barrier.mkdir()
    receiver=shlex.join([Path(sys.executable).as_posix(),'-B',
        (HERE/'native_receive_pack_barrier.py').as_posix(),barrier.as_posix()])
    argv=n['args'](n['source'],'push','--receive-pack='+receiver,str(n['remote']),n['target']+':refs/heads/main')
    with subprocess.Popen(argv,env=n['env'],stdout=subprocess.PIPE,stderr=subprocess.PIPE) as child:
        deadline=time.monotonic()+10
        while not (barrier/'ADVERTISED').exists():
            assert child.poll() is None,'native fixture exited before advertisement barrier'
            assert time.monotonic()<deadline,'native fixture advertisement deadline'
            time.sleep(.01)
        run(n['remote'],'update-ref','refs/heads/main',n['rival'],n['old'])
        (barrier/'RELEASE').write_bytes(b'allow unchanged native protocol bytes')
        out,err=child.communicate(timeout=15)
    (n['root']/'native-cas-raw-result.json').write_text(json.dumps({'argv':argv,'returncode':child.returncode,
        'stdout':out.decode('utf-8','replace'),'stderr':err.decode('utf-8','replace'),
        'method':'native receive-pack advertisement then real concurrent update-ref, byte relay only; no hooks or fabricated protocol'},indent=2)+'\n')
    assert child.returncode!=0 and b'cannot lock ref' in err and b'but expected' in err
    assert run(n['remote'],'rev-parse','refs/heads/main').stdout.decode().strip()==n['rival']
    run(n['remote'],'fsck','--full')
