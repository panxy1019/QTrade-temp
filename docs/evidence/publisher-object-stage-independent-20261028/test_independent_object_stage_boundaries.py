"""All actual repositories are new local fixtures. No SSH, URL or public push."""
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime,timezone

import pytest
import test_publisher_object_stage_nonproduction_20261028 as original

h=original.h;run=original.run;fixture=original.fixture;HERE=Path(__file__).parent

def save(name,value):
    (HERE/name).write_text(json.dumps({'recorded_at':datetime.now(timezone.utc).isoformat(),
        'production_actions':False,**value},indent=2,sort_keys=True)+'\n')

def test_inherited_git_dir_redirects_requested_repo_to_another_owned_fixture(fixture,tmp_path,monkeypatch):
    source,*_=fixture
    other=tmp_path/'other-owned.git';other.mkdir();run(other,['init','--bare','--template='])
    with monkeypatch.context() as patch:
        patch.setenv('GIT_DIR',str(other))
        h.git(source,['config','--local','objectStageProbe.marker','redirected'])
    assert run(other,['config','--get','objectStageProbe.marker']).strip()==b'redirected'
    assert run(source,['config','--get','objectStageProbe.marker'],ok=False).returncode!=0
    save('git-dir-isolation-counterexample.json',{'requested_repo':str(source),
        'actual_config_modified_in_other_owned_fixture':str(other),'environment_git_dir_was_inherited':True})

def test_inherited_git_object_directory_writes_outside_requested_object_store(fixture,tmp_path,monkeypatch):
    source,*_=fixture
    other=tmp_path/'other-owned.git';other.mkdir();run(other,['init','--bare','--template='])
    with monkeypatch.context() as patch:
        patch.setenv('GIT_OBJECT_DIRECTORY',str(other/'objects'))
        oid=h.git(source,['hash-object','-w','--stdin'],b'SYNTHETIC isolated object redirection probe\n').decode().strip()
    assert (other/'objects'/oid[:2]/oid[2:]).is_file()
    assert not (source/'.git/objects'/oid[:2]/oid[2:]).exists()
    save('git-object-directory-counterexample.json',{'requested_repo':str(source),
        'object_written_only_in_other_owned_fixture':str(other),'oid':oid})

def stage_with_mature_local_git(fixture):
    source,remote,isolated,old,new,cert,target,transport=fixture
    plan=h.plan_batch(target,target.blobs,transport.refs(),1)
    # This deliberately uses mature Git, not the prototype's POSIX-only wire API.
    run(isolated,['push',str(remote),plan.commit+':'+plan.ref])
    assert transport.refs()['refs/heads/main']==old and transport.refs()[plan.ref]==plan.commit
    assert run(remote,['fsck','--strict','--no-reflogs'],ok=False).returncode==0
    return plan

def final_wire_objects(source,remote):
    before=set((remote/'objects/pack').glob('*.idx'))
    run(source,['-c','pack.useSparse=false','push',str(remote),'HEAD:refs/heads/main'])
    final=set((remote/'objects/pack').glob('*.idx'))-before
    assert final
    objects=set()
    for path in final:
        for line in run(remote,['verify-pack','-v',str(path)]).splitlines():
            fields=line.split()
            if len(fields)>=5 and h.identity(fields[0].decode()):objects.add(fields[0].decode())
    return objects

def test_original_unknown_aux_graph_retransmits_staged_public_blobs(fixture):
    source,remote,isolated,old,new,cert,target,transport=fixture
    plan=stage_with_mature_local_git(fixture)
    assert run(source,['cat-file','-t',plan.commit],ok=False).returncode!=0
    objects=final_wire_objects(source,remote)
    overlap=objects&target.blobs
    assert overlap and len(overlap)==5
    assert transport.refs()['refs/heads/main']==new
    save('independent-unknown-aux-graph-counterexample.json',{'fixture_root':str(source.parent),
        'source_has_aux_commit_before_main_push':False,'staged_blobs':len(target.blobs),
        'actual_final_pack_objects':len(objects),'staged_blob_retransmission':sorted(overlap),
        'aux_pack_bytes':len(plan.pack),'transport_scope':'ACTUAL_MATURE_GIT_LOCAL_BARE_ONLY'})

def test_metadata_only_pack_makes_advertised_graph_visible_without_old_file_changes(fixture):
    source,remote,isolated,old,new,cert,target,transport=fixture
    plan=stage_with_mature_local_git(fixture)
    before={p.relative_to(source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
            for p in source.rglob('*') if p.is_file()}
    metadata=[plan.commit,plan.tree]
    raw_objects={oid:run(isolated,['cat-file',run(isolated,['cat-file','-t',oid]).decode().strip(),oid])
                 for oid in metadata}
    for oid,raw in raw_objects.items():
        kind=run(isolated,['cat-file','-t',oid]).decode().strip()
        assert kind in ['commit','tree']
        assert hashlib.sha1((kind+' '+str(len(raw))+'\0').encode()+raw).hexdigest()==oid
    pack=run(isolated,['pack-objects','--stdout','--window=0','--depth=0'],
             ('\n'.join(metadata)+'\n').encode())
    assert len(pack)<=2*1024*1024
    run(source,['index-pack','--strict','--stdin'],pack)
    after={p.relative_to(source).as_posix():hashlib.sha256(p.read_bytes()).hexdigest()
           for p in source.rglob('*') if p.is_file()}
    assert all(after[path]==value for path,value in before.items())
    added=set(after)-set(before)
    assert added and all(path.startswith('.git/objects/pack/') and
        path.endswith(('.pack','.idx')) for path in added)
    assert run(source,['cat-file','-t',plan.commit]).strip()==b'commit'
    assert transport.refs()['refs/heads/main']==old
    transmitted=final_wire_objects(source,remote)
    assert transmitted and not(transmitted&target.blobs)
    assert transport.refs()['refs/heads/main']==new
    assert run(remote,['fsck','--strict','--no-reflogs'],ok=False).returncode==0
    save('independent-metadata-graph-visibility-proof.json',{'fixture_root':str(source.parent),
        'metadata_pack_sha256':h.sha(pack),'metadata_pack_bytes':len(pack),
        'metadata_oids':metadata,'metadata_raw_sha256':{oid:h.sha(raw) for oid,raw in raw_objects.items()},
        'all_preexisting_source_files_unchanged_after_metadata_import':True,
        'only_added_source_files':sorted(added),'main_unchanged_during_staging':True,
        'actual_final_pack_objects':len(transmitted),'staged_blob_retransmission':[],
        'scope':'PROPOSED_STRATEGY_ONLY_NOT_THE_FUTURE_V2_TOOL_OR_PRODUCTION_SSH'})

def test_mock_uncertain_failure_has_no_consumed_intent_and_allows_same_plan_retry(fixture,monkeypatch):
    source,remote,isolated,old,new,cert,target,transport=fixture
    plan=h.plan_batch(target,target.blobs,transport.refs(),1)
    remote_refs=dict(plan.remote_refs);calls=[];buffer=bytearray()
    class Fake:
        def __init__(self,*args,**kwargs):
            self.returncode=None;self.stdout=SimpleNamespace(fileno=lambda:97)
            row=(old+' refs/heads/main\0report-status\n').encode()
            buffer[:]=h.pkt(row)+b'0000';calls.append(self)
        def communicate(self,data,timeout):
            wire=data[data.index(b'PACK'):]
            if wire!=plan.pack:self.returncode=1;return b'',b'SYNTHETIC incomplete packet'
            self.returncode=0;remote_refs[plan.ref]=plan.commit
            return h.pkt(b'unpack ok\n')+h.pkt(('ok '+plan.ref+'\n').encode())+b'0000',b''
    def read(fd,count):
        assert fd==97
        value=bytes(buffer[:count]);del buffer[:count];return value
    monkeypatch.setattr(h,'plan_batch',lambda *args:plan)
    monkeypatch.setattr(h,'os',SimpleNamespace(name='posix',read=read))
    monkeypatch.setattr(h,'subprocess',SimpleNamespace(Popen=Fake,PIPE=-1))
    monkeypatch.setattr(h,'environment',lambda:{})
    monkeypatch.setattr(h,'select',SimpleNamespace(select=lambda *args:([args[0][0]],[],[])))
    monkeypatch.setattr(h,'cleanup',lambda _:None)
    transport.refs=lambda:dict(remote_refs)
    with pytest.raises(ValueError,match='ACK_NOT_CONFIRMED_NO_RETRY'):
        transport.send(target,plan,truncate_pack_at=len(plan.pack)//2)
    assert plan.ref not in remote_refs
    result=transport.send(target,plan)
    assert result['state']=='STAGED_LOCAL_FIXTURE_ONLY' and len(calls)==2
    save('mock-uncertain-plan-replay-counterexample.json',{'scope':'SYNTHETIC_PROTOCOL_FLOW_NOT_ACTUAL_SSH',
        'same_plan_send_attempts':len(calls),'first_attempt_ref_absent':True,
        'second_attempt_allowed':True,'no_consumed_intent_enforced':True})
