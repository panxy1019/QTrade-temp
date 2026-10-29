"""Exact frozen core, local SQLite/mocks only. No cloud/credentials/network."""
import importlib.util
import json
import os
import sys
from dataclasses import replace
from datetime import timedelta
from hashlib import sha256
from pathlib import Path

import pytest

HERE=Path(__file__).parent
sys.path[:0]=['E:/QTrade-git/src','E:/QTrade-git','E:/QTrade-git/tests']
spec=importlib.util.spec_from_file_location('object_core_exact_d11',HERE/'object_publication_d11_fixed.py')
op=importlib.util.module_from_spec(spec);sys.modules[spec.name]=op;spec.loader.exec_module(op)
fixture_spec=importlib.util.spec_from_file_location('object_fixture_existing',
    'E:/QTrade-git/tests/test_object_publication.py')
fixture_module=importlib.util.module_from_spec(fixture_spec)
fixture_spec.loader.exec_module(fixture_module)
NOW=fixture_module.NOW


@pytest.fixture
def local(tmp_path,monkeypatch):
    assert sha256((HERE/'object_publication_d11_fixed.py').read_bytes()).hexdigest()==(
        'd11c801a90142d4b7142b8f67c422c341b30b84201999dd297c186c3582fa7d0')
    if os.name=='nt':
        # Same explicit Windows-only directory fsync instrumentation as baseline;
        # no claim that Windows tests establish POSIX fsync durability.
        monkeypatch.setattr(op,'_sync_directory',lambda path:None)
        original=op.write_json_immutable
        def write(path,value):
            try:original(path,value)
            except PermissionError:
                if not path.exists() or json.loads(path.read_bytes())!=value:raise
        monkeypatch.setattr(op,'write_json_immutable',write)
    data=fixture_module.fixture_data()
    store=op.SQLiteObjectStore(tmp_path/'objects.sqlite')
    first,objects=op.prepare_market(data.__getitem__,parent=None,source_git_commit='c'*40,now=NOW)
    yield data,store,first,objects,op.Publisher(store,tmp_path/'journal')
    store.close()


def forged_prepared(data,first):
    other,_=op.prepare_market(data.__getitem__,parent=None,source_git_commit='d'*40,now=NOW)
    forged=replace(other,publication_id=first.publication_id)
    assert forged.head==first.head
    assert sha256(forged.manifest).hexdigest()!=forged.publication_id
    return forged


def test_reconcile_must_reject_publication_id_not_hash_of_prepared_manifest(local):
    data,store,first,_,publisher=local
    publisher.publish(first,now=NOW)
    forged=forged_prepared(data,first)
    with pytest.raises(ValueError):
        publisher.reconcile(forged)


def test_publish_already_rejects_same_forged_prepared(local):
    data,store,first,_,publisher=local
    publisher.publish(first,now=NOW);before=store.get(op.LATEST)
    with pytest.raises(ValueError,match='CURRENT_SOURCE_CHANGED'):
        publisher.publish(forged_prepared(data,first),now=NOW)
    assert store.get(op.LATEST)==before


def test_opaque_etag_aba_rewrite_rejects_cas_even_when_parent_body_is_unchanged(local,tmp_path):
    data,base,first,_,publisher=local;publisher.publish(first,now=NOW)
    parent=op.read_parent(base)
    second,_=op.prepare_market(data.__getitem__,parent=parent,source_git_commit='c'*40,
        now=NOW+timedelta(seconds=1))
    class ABA:
        scope_id=base.scope_id
        changed=False
        def get(self,key):return base.get(key)
        def put(self,key,raw,**conditions):
            if key==op.LATEST and not self.changed:
                self.changed=True
                rewritten=base.put(key,parent.raw,if_match=parent.etag)
                assert rewritten.etag!=parent.etag and rewritten.raw==parent.raw
            return base.put(key,raw,**conditions)
    result=op.Publisher(ABA(),tmp_path/'aba-journal').publish(second,now=NOW+timedelta(seconds=1))
    assert result.state=='CAS_REJECTED'
    assert base.get(op.LATEST).raw==first.head
    assert op.read_parent(base).publication_id==first.publication_id


def test_immutable_commit_then_ack_loss_resumes_by_full_get_without_put_replay(local,tmp_path):
    _,base,first,_,_=local
    class Lost:
        scope_id=base.scope_id
        puts=[];lost=False
        def get(self,key):return base.get(key)
        def put(self,key,raw,**conditions):
            self.puts.append(key);result=base.put(key,raw,**conditions)
            if key!=op.LATEST and not self.lost:
                self.lost=True;raise op.OutcomeUnknown('SYNTHETIC_ACK_LOST')
            return result
    fault=Lost();root=tmp_path/'lost-immutable-journal'
    with pytest.raises(op.OutcomeUnknown):op.Publisher(fault,root).publish(first,now=NOW)
    first_key=fault.puts[0]
    result=op.Publisher(fault,root).publish(first,now=NOW)
    assert result.state=='COMMITTED_RECONCILED'
    assert fault.puts.count(first_key)==1


def test_cas_unknown_and_readback_disconnect_never_replays_latest(local,tmp_path):
    _,base,first,_,_=local
    class Lost:
        scope_id=base.scope_id
        latest_puts=0;disconnect=False
        def get(self,key):
            if key==op.LATEST and self.disconnect:
                self.disconnect=False;raise TimeoutError('SYNTHETIC_GET_DISCONNECTED')
            return base.get(key)
        def put(self,key,raw,**conditions):
            result=base.put(key,raw,**conditions)
            if key==op.LATEST:
                self.latest_puts+=1;self.disconnect=True
                raise op.OutcomeUnknown('SYNTHETIC_CAS_ACK_LOST')
            return result
    fault=Lost();root=tmp_path/'cas-read-disconnect'
    with pytest.raises(TimeoutError):op.Publisher(fault,root).publish(first,now=NOW)
    assert op.Publisher(fault,root).publish(first,now=NOW).state=='COMMITTED_RECONCILED'
    assert fault.latest_puts==1


def test_post_link_journal_sync_error_prevents_any_put_and_persists_no_replay(local,monkeypatch):
    _,base,first,_,publisher=local
    original=op.write_json_immutable;failed=False
    def write(path,value):
        nonlocal failed
        original(path,value)
        if not failed:
            failed=True;raise OSError('SYNTHETIC_POST_LINK_DIRECTORY_FSYNC_ERROR')
    monkeypatch.setattr(op,'write_json_immutable',write)
    with pytest.raises(OSError):publisher.publish(first,now=NOW)
    assert base.connection.execute('SELECT COUNT(*) FROM objects').fetchone()[0]==0
    with pytest.raises(op.OutcomeUnknown):publisher.publish(first,now=NOW)
    assert base.connection.execute('SELECT COUNT(*) FROM objects').fetchone()[0]==0


def test_manifest_body_corruption_even_with_self_consistent_store_checksum_fails(local):
    _,base,first,_,publisher=local;publisher.publish(first,now=NOW)
    row=base.get(first.manifest_key);changed=row.raw+b' '
    base.connection.execute('UPDATE objects SET body=?,sha256=? WHERE key=?',
        (changed,sha256(changed).hexdigest(),first.manifest_key))
    with pytest.raises(ValueError,match='MANIFEST_SHA256'):publisher.reconcile(first)


def test_external_delete_between_verification_and_cas_is_not_false_success(local,tmp_path):
    _,base,first,objects,_=local
    shard=next(k for k in objects if k.startswith('market/shards/'))
    class Deleted:
        scope_id=base.scope_id
        def get(self,key):return base.get(key)
        def put(self,key,raw,**conditions):
            if key==op.LATEST:base.connection.execute('DELETE FROM objects WHERE key=?',(shard,))
            return base.put(key,raw,**conditions)
    with pytest.raises(ValueError,match='COMMITTED_OBJECT_MISSING'):
        op.Publisher(Deleted(),tmp_path/'external-delete').publish(first,now=NOW)
    # This demonstrates the documented mandatory no-delete ACL assumption.
    assert base.get(op.LATEST).raw==first.head and base.get(shard) is None


def test_declared_source_git_commit_is_not_attested_by_the_protocol(local):
    data,_,_,_,_=local
    prepared,_=op.prepare_market(data.__getitem__,parent=None,source_git_commit='d'*40,now=NOW)
    quality=json.loads(data['quality.json']);manifest=json.loads(prepared.manifest)
    assert quality['source_revision']=='c'*40 and manifest['source_git_commit']=='d'*40
    # No production-origin PASS is claimed: caller must bind its real source.


@pytest.mark.parametrize('field',['source_git_commit','created_at','parent','summary_contract_sha256','summary_validator'])
def test_reconcile_rejects_inconsistent_dataclass_context_before_store_read(local,field):
    _,base,first,_,publisher=local;publisher.publish(first,now=NOW)
    values={'source_git_commit':'d'*40,'created_at':NOW+timedelta(seconds=1),
        'parent':op.read_parent(base),'summary_contract_sha256':'f'*64,
        'summary_validator':lambda value,now:None}
    with pytest.raises(ValueError):publisher.reconcile(replace(first,**{field:values[field]}))


def test_reconcile_rechecks_actual_manifest_bytes_after_parent_read(local,tmp_path):
    _,base,first,_,publisher=local;publisher.publish(first,now=NOW)
    class ChangedManifest:
        scope_id=base.scope_id
        reads=0
        def get(self,key):
            result=base.get(key)
            if key==first.manifest_key:
                self.reads+=1
                if self.reads>1:return op.StoredObject(result.raw+b' ',result.etag)
            return result
        def put(self,*args,**kwargs):pytest.fail('read-only reconciliation wrote an object')
    with pytest.raises(ValueError,match='COMMITTED_MANIFEST_CONTEXT'):
        op.Publisher(ChangedManifest(),tmp_path/'manifest-second-read').reconcile(first)

