"""Independent review counterexamples. Synthetic-only; zero production I/O.

Passing counterexamples document a boundary or missing integration. They are NOT
measurements, production gates, deployment authorization, or fixes.
"""
import copy
import importlib.util
import json
from pathlib import Path
import pytest

ROOT=Path(__file__).parent

def load(name,path):
 spec=importlib.util.spec_from_file_location(name,ROOT/path)
 m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m);return m

h=load('independent_h5','publisher_handover_v5_20261028.py')
g=load('independent_g4','publisher_incremental_gates_v4_20261028.py')
f=load('independent_g4_fixtures','test_publisher_incremental_gates_v4_20261028.py')
legacy=load('independent_old_fixtures','test_publisher_handover_draft_20261028.py')


def test_failed_original_measurement_is_not_derived_or_rejected_by_schema():
 manifest,bundle,evidence,retain=f.fixture()
 mode='normal_incremental';ref=bundle['modes'][mode][0]
 run=json.loads(evidence[ref])
 original={'fixture_only':True,'state':'FAILED','elapsed_seconds':999.0,
           'wall_seconds':999.0,'source_revision':'0'*40,'mode':'cold_bootstrap',
           'filesystem':'tmpfs','failure':'SYNTHETIC_CONTRADICTION'}
 failed=retain(original)
 run['run_id']=failed;run['evidence_refs'][0]=failed
 bundle['modes'][mode][0]=retain(run)
 assert g.check_incremental_gates_v4(manifest,bundle,evidence)['state']=='GATES_PASS_PREPARATION_ONLY'


def test_same_original_execution_with_whitespace_variants_counts_as_twenty():
 manifest,bundle,evidence,retain=f.fixture()
 execution={'fixture_only':True,'execution_id':'ONE_SYNTHETIC_EXECUTION_ONLY',
            'scenario':'unchanged','elapsed_seconds':38}
 for index,ref in enumerate(list(bundle['modes']['normal_incremental'])):
  run=json.loads(evidence[ref])
  clone=retain(json.dumps(execution,sort_keys=True).encode()+b' '*index)
  run['run_id']=clone;run['evidence_refs'][0]=clone
  bundle['modes']['normal_incremental'][index]=retain(run)
 assert g.check_incremental_gates_v4(manifest,bundle,evidence)['mode_samples']['normal_incremental']==20


def test_all_recovery_samples_can_use_older_freeze_and_no_filesystem_is_bound():
 manifest,bundle,evidence,_=f.fixture()
 for mode in ['cold_bootstrap','checkpoint_new_process','local_db_loss_sidecar_recovery']:
  assert len(bundle['modes'][mode])==5
  assert all(json.loads(evidence[x])['freeze_name']=='baseline_5640' for x in bundle['modes'][mode])
 assert not any('filesystem' in json.loads(evidence[x]) for refs in bundle['modes'].values() for x in refs)
 assert g.check_incremental_gates_v4(manifest,bundle,evidence)['state']=='GATES_PASS_PREPARATION_ONLY'


def test_old_handover_gate_accepts_4200_without_incremental_bundle():
 manifest,gates=legacy.fixture()
 assert manifest['required_freeze']['batches']==4200
 assert not any(name in gates for name in ['modes','fault_suite'])
 assert h.check_gates(manifest,gates)['state']=='GATES_PASS_PREPARATION_ONLY'


def test_durable_term_intent_prevents_replay_with_same_attempt(monkeypatch):
 monkeypatch.setattr(legacy,'h',h)
 manifest,gates=legacy.fixture();adapter=legacy.MockAdapter();journal=legacy.MemoryJournal(manifest)
 first=legacy.step(manifest,gates,adapter,journal)
 second=legacy.step(manifest,gates,adapter,journal)
 assert first['state']==second['state']=='DRAIN_PENDING'
 assert adapter.signals==1
 assert sum(x['phase']=='TERM_INTENT' for x in journal.rows)==1


def test_unexpected_natural_exit_before_term_is_refused_without_signalling(monkeypatch):
 monkeypatch.setattr(legacy,'h',h)
 manifest,gates=legacy.fixture();adapter=legacy.MockAdapter();journal=legacy.MemoryJournal(manifest)
 original=adapter.install_wrapper
 def installed_then_old_exits(before,backup):
  result=original(before,backup);adapter.changed_owner=True;return result
 adapter.install_wrapper=installed_then_old_exits
 with pytest.raises(ValueError,match='ORIGINAL_OWNER_CHANGED'):
  legacy.step(manifest,gates,adapter,journal)
 assert adapter.signals==0
 assert not any(x['phase']=='TERM_INTENT' for x in journal.rows)


def test_source_preflight_does_not_require_new_dependency_modules(monkeypatch):
 manifest,gates=legacy.fixture()
 assert 'scripts/unified_market_checkpoint.py' not in manifest['runtime_files']
 assert 'src/qtrade/research/market_validation_receipts.py' not in manifest['runtime_files']
 assert h.check_gates(manifest,gates)['state']=='GATES_PASS_PREPARATION_ONLY'
 # No disk read: demonstrate the preflight loop only checks supplied entries.
 adapter=h.PublisherOnlyAdapter(manifest,None,None)
 monkeypatch.setattr(h.Path,'is_symlink',lambda self:False)
 monkeypatch.setattr(h,'file_sha',lambda path:manifest['runtime_files'][str(path.relative_to(manifest['release_root']))])
 adapter.check_source()
