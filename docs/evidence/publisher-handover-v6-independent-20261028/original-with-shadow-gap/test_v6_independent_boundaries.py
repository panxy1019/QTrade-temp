"""Independent local fixtures. No SSH/RPC, private-wrapper or production access."""
import copy
import hashlib
import importlib.machinery
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).parent
PACKAGE = Path('E:/QTrade-temp/publisher-v6-actual-bundle-20261028T114221Z-0eec8c94')

def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, ROOT / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

h = load('independent_handover_v6', 'publisher_handover_v6_preparation_20261028.py')
b = load('independent_bridge_v6', 'publisher_handover_bridge_v6_20261028.py')
c = load('independent_composite_v6', 'publisher_actual_composite_v6_20261028.py')
a = c.a

@pytest.fixture
def actual():
    manifest = json.loads((PACKAGE/'manifest.json').read_bytes())
    gates = json.loads((PACKAGE/'gates.json').read_bytes())
    artifacts = {role: (PACKAGE/'gate-evidence'/item['file']).read_bytes()
                 for role, item in gates['artifact_files'].items()}
    return manifest, gates, artifacts

def replace(manifest, gates, artifacts, role, value, signed=False):
    if signed:
        value.pop('evidence_sha256', None)
        value['evidence_sha256'] = a.digest(value)
    artifacts[role] = json.dumps(value, sort_keys=True).encode()
    gates['artifact_files'][role]['sha256'] = a.sha(artifacts[role])
    manifest['gate_bundle_sha256'] = a.digest(gates)

def test_old_check_source_accepts_untracked_shadow_package(tmp_path, monkeypatch):
    """Counterexample, not desired behavior: tracked bytes alone miss new origins."""
    base = tmp_path/'base'
    revision = 'a'*40
    release = base/'releases'/revision
    files = {}
    for relative in ('scripts/unified_market_checkpoint.py',
                     'src/qtrade/research/market_validation_receipts.py'):
        path = release/relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b'# SYNTHETIC tracked module\n')
        files[relative] = hashlib.sha256(path.read_bytes()).hexdigest()
    shadow = release/'scripts/unified_market_checkpoint/__init__.py'
    shadow.parent.mkdir()
    shadow.write_bytes(b'# SYNTHETIC untracked package, never executed\n')
    monkeypatch.setattr(h, 'BASE', base)
    adapter = h.PublisherOnlyAdapter({'runtime_files':files, 'release_root':str(release),
                                     'new_revision':revision}, None, None)
    adapter.check_source()
    resolved = importlib.machinery.PathFinder.find_spec('unified_market_checkpoint',
                                                      [str(release/'scripts')])
    assert Path(resolved.origin) == shadow
    assert all(hashlib.sha256((release/path).read_bytes()).hexdigest() == digest
               for path, digest in files.items())

def test_raw_output_contradiction_is_rejected_even_after_outer_reseal(actual):
    manifest, gates, artifacts = actual
    report = json.loads(artifacts['report_current'])
    report['results'][0]['index_raw_sha256'] = '0'*64
    replace(manifest,gates,artifacts,'report_current',report,signed=True)
    with pytest.raises(ValueError,match='ORACLE_MEASURED_OUTPUT_BINDING'):
        c.validate_actual_composite(manifest,gates,artifacts)

def test_report_and_stdout_identical_false_output_still_fails_original_oracle(actual):
    manifest, gates, artifacts = actual
    report = json.loads(artifacts['report_current'])
    stdout = json.loads(artifacts['round_current_incremental'])
    stdout['statistics']['index_id'] = '0'*64
    report['results'][0]['statistics']['index_id'] = '0'*64
    replace(manifest,gates,artifacts,'round_current_incremental',stdout)
    replace(manifest,gates,artifacts,'report_current',report,signed=True)
    with pytest.raises(ValueError,match='ORACLE_MEASURED_OUTPUT_BINDING'):
        c.validate_actual_composite(manifest,gates,artifacts)

def test_duplicate_json_cannot_override_original_state():
    with pytest.raises(ValueError,match='DUPLICATE_JSON_KEY'):
        a.document(b'{"state":"FAILED","state":"SUCCEEDED"}')

def test_changed_raw_bundle_ref_cannot_enter_real_leaf_guard(actual):
    manifest,gates,_ = actual
    gates['artifact_files']['report_current']['sha256'] = '0'*64
    adapter = h.PublisherOnlyAdapter(manifest,None,None,mutation_stage_approved=True,
                                     actual_gate_bundle=gates)
    with pytest.raises(ValueError,match='GATE_BUNDLE_CHANGED'):
        adapter._mutation_guard()

def test_gate_guard_is_repeated_and_passes_same_bundle_object(monkeypatch):
    seen=[]
    manifest={'synthetic':True}; gates={'synthetic':True}
    monkeypatch.setattr(h,'check_gates',lambda m,g: seen.append((m,g)))
    adapter=h.PublisherOnlyAdapter(manifest,None,None,mutation_stage_approved=True,
                                   actual_gate_bundle=gates)
    adapter._mutation_guard();adapter._mutation_guard()
    assert len(seen)==2 and all(m is manifest and g is gates for m,g in seen)

def test_bridge_uncertain_socket_failure_consumes_single_term(monkeypatch):
    import stat
    monkeypatch.setattr(b,'SOCKET',SimpleNamespace(stat=lambda:SimpleNamespace(
        st_mode=stat.S_IFSOCK,st_dev=1,st_ino=2,st_uid=0)))
    rpc=b.UnixPublisherRPC({'device':9,'inode':9,'uid':0},allow_one_term=True)
    with pytest.raises(ValueError,match='UNIX_SOCKET_IDENTITY_CHANGED'):
        rpc('supervisor.signalProcess',(b.PROGRAM,'TERM'))
    assert rpc.term_consumed
    with pytest.raises(ValueError,match='ONE_FIXED_TERM_NOT_AVAILABLE'):
        rpc('supervisor.signalProcess',(b.PROGRAM,'TERM'))

def journal_fixture(tmp_path,handover,gates,term_intent=False):
    root=tmp_path/'publisher-handover-SYNTHETIC'
    handover.update(attempt_root=str(root),old_owner={'pid':123})
    before={'owners':{},'context':{},'frozen_controls':{}}
    rows=[{'phase':'PREPARED','payload':{'binding_sha256':h.digest(
        {'manifest':handover,'gates':gates}),'before':before}},
        {'phase':'WRAPPER_INSTALLED','payload':{}}]
    if term_intent:rows.append({'phase':'TERM_INTENT','payload':{}})
    return SimpleNamespace(root=root,read=lambda:copy.deepcopy(rows),
        append=lambda phase,payload:rows.append({'phase':phase,'payload':payload})),rows

def test_immutable_uncertain_term_intent_never_calls_leaf_again(tmp_path,monkeypatch):
    monkeypatch.setattr(h,'check_gates',lambda *args:None) # Lifecycle-only fixture.
    manifest={};gates={};journal,rows=journal_fixture(tmp_path,manifest,gates,True)
    def forbidden(*args):raise AssertionError('no leaf may be called after uncertain intent')
    adapter=SimpleNamespace(guard_old=forbidden,named_term_once=forbidden,observe=forbidden)
    for _ in range(2):
        result=h.advance(manifest,gates,adapter,journal,backup=journal.root/'private/wrapper.old.py')
        assert result['state']=='TERM_DELIVERY_UNCERTAIN_NO_REPLAY'
    assert sum(row['phase']=='TERM_INTENT' for row in rows)==1

def test_owner_natural_exit_before_term_cannot_be_signalled(tmp_path,monkeypatch):
    monkeypatch.setattr(h,'check_gates',lambda *args:None) # Lifecycle-only fixture.
    manifest={};gates={};journal,rows=journal_fixture(tmp_path,manifest,gates)
    def changed():raise ValueError('ORIGINAL_OWNER_CHANGED')
    adapter=SimpleNamespace(guard_old=changed,named_term_once=lambda *x:pytest.fail('no signal'))
    with pytest.raises(ValueError,match='ORIGINAL_OWNER_CHANGED'):
        h.advance(manifest,gates,adapter,journal,backup=journal.root/'private/wrapper.old.py')
    assert not any(row['phase']=='TERM_INTENT' for row in rows)
