"""Independent offline review: imports frozen source, never runs any mode/process.

Popen is always a fake in process-boundary tests. It delegates __exit__ to the
real Python implementation solely to demonstrate its wait(timeout=None) call.
"""
import hashlib
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import types

import pytest

OLD=Path('E:/QTrade-temp/publication-cold-recovery-preparation-20261028T143911Z-9d25431d/publication_cold_recovery_operator_20261028.py')
FINAL=Path('E:/QTrade-temp/publication-cold-recovery-final-20261028T145240Z-ad5676c1/publication_cold_recovery_operator_20261028.py')


def load(path,expected):
    assert hashlib.sha256(path.read_bytes()).hexdigest()==expected
    spec=importlib.util.spec_from_file_location('independent_'+expected[:8],path)
    result=importlib.util.module_from_spec(spec);spec.loader.exec_module(result)
    return result


@pytest.mark.parametrize('path,sha,has_unbounded_wait',[
    (OLD,'7ec53cc558c62c56dade0cf390ba274ff34de2c97c80ddb664a6103e6ce79f09',True),
    (FINAL,'738c295d55d9b1a4081ca2547d2bfdad3ea59553d0102cc152bb34b3916917fa',False),
])
def test_cleanup_failure_actual_popen_exit_semantics(path,sha,has_unbounded_wait,monkeypatch):
    op=load(path,sha);waits=[];closes=[];spawns=[]
    class Pipe:
        def close(self):closes.append(True)
    class Child:
        __exit__=subprocess.Popen.__exit__
        def __enter__(self):return self
        stdin=None;stderr=None;returncode=None
        stdout=Pipe()
        def communicate(self,**kwargs):raise subprocess.TimeoutExpired('SYNTHETIC',1)
        def wait(self,timeout=None):waits.append(timeout);self.returncode=-9;return -9
    def popen(*args,**kwargs):spawns.append(True);return Child()
    def cleanup(_):raise RuntimeError('SYNTHETIC_CLEANUP_FAILED')
    module=types.ModuleType('scripts.publish_research_snapshot');module._cleanup_git_group=cleanup
    monkeypatch.setitem(sys.modules,'scripts.publish_research_snapshot',module)
    monkeypatch.setattr(op,'runtime_origins',lambda:None)
    monkeypatch.setattr(op.subprocess,'Popen',popen)
    with pytest.raises(RuntimeError,match='SYNTHETIC_CLEANUP_FAILED'):
        op.git(op.REPO,['show','SYNTHETIC'],timeout=1)
    assert spawns==[True] and closes
    assert (None in waits) is has_unbounded_wait


def test_final_timeout_has_no_context_wait_and_closes_both_pipes(monkeypatch):
    op=load(FINAL,'738c295d55d9b1a4081ca2547d2bfdad3ea59553d0102cc152bb34b3916917fa')
    closed=[];cleanup_calls=[]
    class Pipe:
        def __init__(self,name):self.name=name
        def close(self):closed.append(self.name)
    class Child:
        stdin=Pipe('stdin');stdout=Pipe('stdout');returncode=-9
        def communicate(self,**kwargs):raise subprocess.TimeoutExpired('SYNTHETIC',1)
        def wait(self,*args,**kwargs):pytest.fail('extra wait outside cleanup')
        def __enter__(self):pytest.fail('context must not be used')
    module=types.ModuleType('scripts.publish_research_snapshot')
    module._cleanup_git_group=lambda child:cleanup_calls.append(True)
    monkeypatch.setitem(sys.modules,'scripts.publish_research_snapshot',module)
    monkeypatch.setattr(op,'runtime_origins',lambda:None)
    monkeypatch.setattr(op.subprocess,'Popen',lambda *a,**k:Child())
    with pytest.raises(subprocess.TimeoutExpired):
        op.git(op.REPO,['cat-file','--batch'],input_data=b'SYNTHETIC\n',timeout=1)
    assert cleanup_calls==[True] and closed==['stdout','stdin']


@pytest.mark.parametrize('arguments,timeout',[
    (['push','origin','SYNTHETIC:refs/heads/main'],90),
    (['show','SYNTHETIC'],180),
])
def test_final_ordinary_leaf_cannot_become_cold_send_or_raise_budget(arguments,timeout,monkeypatch):
    op=load(FINAL,'738c295d55d9b1a4081ca2547d2bfdad3ea59553d0102cc152bb34b3916917fa')
    monkeypatch.setattr(op.subprocess,'Popen',lambda *a,**k:pytest.fail('no real process allowed'))
    monkeypatch.setattr(op,'runtime_origins',lambda:pytest.fail('guard ordering'))
    with pytest.raises(ValueError):op.git(op.REPO,arguments,timeout=timeout)


def test_final_progress_projection_drops_sensitive_suffixes_and_unknown_lines():
    op=load(FINAL,'738c295d55d9b1a4081ca2547d2bfdad3ea59553d0102cc152bb34b3916917fa')
    progress=op.Progress()
    progress.feed(b'Writing objects: 100% (3/3), SYNTHETIC_PRIVATE_TEXT\n'
        b'UNKNOWN_SYNTHETIC_PRIVATE_TEXT\nremote: Resolving deltas: 99% (2/3), x\r',1.0)
    output=progress.finish(2.0);raw=json.dumps(output)
    assert 'PRIVATE_TEXT' not in raw
    assert [event['phase'] for event in output['events']]==['Writing objects','Resolving deltas']
    assert output['unknown_text_bytes']>0 and output['raw_stderr_saved'] is False
    assert output['writing_100_percent_is_server_ack'] is False


def test_final_exact_document_rejects_duplicate_keys_even_with_correct_hash(tmp_path):
    op=load(FINAL,'738c295d55d9b1a4081ca2547d2bfdad3ea59553d0102cc152bb34b3916917fa')
    path=tmp_path/'synthetic-document.json';raw=b'{"authorization_kind":"one","authorization_kind":"two"}'
    path.write_bytes(raw)
    with pytest.raises(ValueError,match='DUPLICATE_DOCUMENT_KEY'):
        op.exact_document(path,hashlib.sha256(raw).hexdigest(),4096)
