"""Isolated registration of the existing longer report; writes confined to one operator root."""
import gc
import gzip
import hashlib
import inspect
import json
import os
import resource
import shutil
import sqlite3
import subprocess
import sys
import time
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import parse_qs, urlsplit
from uuid import uuid4

owned = Path(sys.argv[1]).resolve()
assert owned.parent == Path('/root/qtrade-local/operator-evidence')
source = owned / 'source'
base = Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade')
old = Path('/root/qtrade-local/operator-evidence/step2-real-daily-20261028T045123Z')
longer = Path('/root/qtrade-local/operator-evidence/factor-longer-s2b-20261028T112943Z-86cd84da')
sys.path[:0] = [str(source / 'src'), str(source)]
assert os.environ['LIVE_TRADING_ENABLED'] == 'false'
os.nice(10)
events = []


def local_path(value):
    if isinstance(value, int):
        return True
    return Path(value).resolve().is_relative_to(owned)


def guard(event, args):
    if event == 'open' and args[2] & (os.O_WRONLY | os.O_RDWR | os.O_CREAT | os.O_TRUNC | os.O_APPEND):
        assert local_path(args[0]), ('OUTSIDE_WRITE_BLOCKED', str(args[0]))
    if event in {'os.mkdir', 'os.remove', 'os.rmdir'}:
        assert local_path(args[0]), ('OUTSIDE_MUTATION_BLOCKED', event)
    if event in {'os.rename', 'os.link', 'os.symlink'}:
        assert local_path(args[0]) and local_path(args[1]), ('OUTSIDE_MUTATION_BLOCKED', event)
    if event == 'sqlite3.connect':
        name = str(args[0])
        read_only = name.startswith('file:') and parse_qs(urlsplit(name).query).get('mode') == ['ro']
        assert read_only or local_path(name), ('OUTSIDE_SQLITE_WRITE_BLOCKED', name)
        events.append({'event': event, 'read_only_uri': read_only, 'database': name})


sys.addaudithook(guard)
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.factor_strategy_catalog import KIND, REQUEST_SCHEMA, preview_from_catalog
from qtrade.research.strategy_contract import _admission
from qtrade.research.workflow import DEFAULT_SPLITS, digest
from qtrade.strategies.registry import StrategyRegistry
from scripts.atomic_json import write_json_immutable
from scripts.factor_research_contracts import FactorResearchContracts, INDEX_SCHEMA


def h(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def inventory(folder):
    return {p.relative_to(folder).as_posix(): {'sha256': h(p), 'bytes': p.stat().st_size}
            for p in folder.rglob('*') if p.is_file()}


def save(name, value):
    write_json_immutable(owned / name, value)


def resources(name):
    free = {'nfs': shutil.disk_usage(base).free, 'local': shutil.disk_usage(owned).free}
    assert free['nfs'] >= 100 * 1024**3 and free['local'] >= 2 * 1024**3
    save(name, free)


def controls():
    result = {'live_trading_enabled': False, 'write_scope': str(owned)}
    database = Path('/root/qtrade-local/online-models.sqlite')
    if database.is_file():
        with sqlite3.connect(database.as_uri() + '?mode=ro', uri=True, timeout=3) as c:
            result['active_model'] = c.execute('SELECT model_id FROM active_model WHERE singleton=1'
                                              ).fetchall()
    public = base / 'workflow/public/online.json'
    if public.is_file():
        value = json.loads(public.read_bytes())
        result['published_active_model'] = value.get('active_model')
        result['published_auto_train_mode'] = value.get('auto_train', {}).get('policy', {}).get('mode')
    return result


resources('actual-resources-before.json')
manifest = json.loads((owned / 'package-manifest.json').read_bytes())
assert inventory(source) == manifest['expected_files']
save('actual-source-before.json', inventory(source))
before_old, before_longer = inventory(old), inventory(longer)
save('actual-originals-before.json', {'old': before_old, 'longer': before_longer})
parent_root = Path('/root/qtrade-local/operator-evidence/factor-queue-s2d-20261028T123014Z-b12b8041')
parent_files = ['isolated-strategies.sqlite','actual-acceptance.json','actual-request.json','actual-child-output/registration.json','actual-child-output/strategy-definition.json']
parent_before = {name: h(parent_root / name) for name in parent_files}
save('actual-parent-before.json', parent_before)
before_control = controls()
save('actual-controls-before.json', before_control)
assert h(old / 'report.json.gz') == '47bed3add84aff16f901531115c17ac4534cfd667b6716595a1ce098b9af37dd'
assert h(longer / 'report.json.gz') == '74c246d7822b4e860706dcc2c212763ede3aef62f38f5b94297237acf5457ada'
report = json.loads(gzip.decompress((longer / 'report.json.gz').read_bytes()))
reference = {k: report[k] for k in ('evaluation_id', 'specification_sha256', 'report_manifest_sha256')}
specification = report['documents']['specification']
binding = specification['data_binding']
identity = binding['data_version_id']
database = Path('/root/qtrade-local/qlib-versions.sqlite')
before_admission = _admission(database, identity)
assert len(specification['request']['instruments']) == 891
assert len(specification['request']['decisions']) == 16
calendar = specification['calendar']
segments = json.loads(json.dumps(DEFAULT_SPLITS))
segments['test'][1] = binding['actual_end']
assert all(day in calendar for pair in segments.values() for day in pair)
archive = owned / 'approved-factor-archive'
(archive / 'reports').mkdir(parents=True)
factor_input = longer / 'inputs/factors.sqlite'
assert factor_input.is_file()
shutil.copyfile(factor_input, archive / 'factors.sqlite')
shutil.copyfile(longer / 'report.json.gz', archive / 'reports' / (reference['evaluation_id'] + '.json.gz'))
templates = []
for n, ref in enumerate(specification['request']['ordered_factor_refs']):
    templates.append({'name': f'step2-actual-factor-register-{n}', 'factor_ref': ref,
        'evaluation_ref': reference, 'signal_kind': 'FACTOR_COMPOSITE' if n == 3 else 'FACTOR_SINGLE',
        'segments': segments, 'portfolio': {'method': 'TopkDropout', 'topk': 50,
            'n_drop': 5, 'hold_thresh': 2, 'risk_degree': '0.8'},
        'execution': {'buy_cost_bps': '8', 'sell_cost_bps': '13', 'min_cost': '5',
            'initial_cash': '1000000', 'slippage_bps': '0'}, 'parent': None})
index = signed({**AUTHORITY, 'schema_version': INDEX_SCHEMA,
    'registry_sha256': h(archive / 'factors.sqlite'),
    'evaluations': [{**reference, 'data_version_id': identity,
                     'gzip_sha256': h(longer / 'report.json.gz')}], 'templates': templates})
write_json_immutable(archive / 'index.json', index)
del report, specification
gc.collect()
strategies = owned / 'isolated-strategies.sqlite'
parent_database = parent_root / 'isolated-strategies.sqlite'
assert not any(parent_database.with_name(parent_database.name + suffix).exists() and parent_database.with_name(parent_database.name + suffix).stat().st_size for suffix in ('-wal','-journal'))
shutil.copyfile(parent_database, strategies)
assert h(strategies) == parent_before['isolated-strategies.sqlite']
from qtrade.research.factor_strategy_contract import validate_strategy, strategy_spec
legacy_path = source / 'tests/fixtures/factor_strategy_s2c13_archived.json'
legacy = json.loads(legacy_path.read_bytes())
assert validate_strategy(legacy) == legacy
try:
 StrategyRegistry(strategies).register_factor_research_experiment(strategy_spec(legacy, now=datetime.now(UTC)),contract=legacy,job_id='f'*32,actor='old-C-negative-probe')
except ValueError as error:
 assert str(error) == 'FACTOR_STRATEGY_RUNTIME_SOURCE_MISMATCH'
else:
 raise AssertionError('OLD_C_RUNTIME_MUST_REJECT')
assert h(strategies) == parent_before['isolated-strategies.sqlite']
save('actual-legacy-replay.json', {'old_c_archive_sha256':h(legacy_path),'archived_reader_pass':True,'current_runtime_registry_rejected':True,'registry_bytes_unchanged':True})
resolver = FactorResearchContracts(base, database, manifest['baseline_commit'],
    StrategyRegistry(strategies), archive)
started = time.perf_counter()
catalog = resolver.publication()
save('actual-catalog.json', catalog)
parent_definition = json.loads((parent_root / 'actual-child-output/strategy-definition.json').read_bytes())
selected_spec = json.loads(json.dumps(templates[0]))
selected_spec['name'] = 'step2-factor-source-compatibility-revalidation'
selected_spec['parent'] = {'strategy_id':parent_definition['strategy_id'],'strategy_version':parent_definition['strategy_version'],'contract_sha256':parent_definition['evidence_sha256'],'reason':'SOURCE_CONTRACT_COMPATIBILITY_REVALIDATION','hypothesis':'Archived S2-C13 stays readable while current runtime requires exact20 source bytes.','change_summary':'Explicit source upgrade; same frozen factor, evaluation, data and cost assumptions; no backtest or promotion.'}
definition = preview_from_catalog(catalog, selected_spec)
save('actual-preview.json', definition)
request = {'schema_version': REQUEST_SCHEMA, 'kind': KIND, 'job_id': uuid4().hex,
    'actor': 'nonproduction-independent-acceptance', 'factor_spec': selected_spec,
    'factor_spec_sha256': digest(selected_spec),
    'expected_strategy_version': definition['strategy_version'],
    'expected_catalog_sha256': catalog['catalog_sha256'], 'source_revision': manifest['baseline_commit'],
    'data_version_id': identity, 'live_trading_enabled': False}
save('actual-request.json', request)
env = {k: v for k, v in os.environ.items() if not k.startswith('QTRADE_RESEARCH_')}
env.update(PYTHONPATH=str(source / 'src') + ':' + str(source), PYTHONDONTWRITEBYTECODE='1',
           LIVE_TRADING_ENABLED='false', OMP_NUM_THREADS='2')
wrapper = owned / 'actual_guarded_runner.py'
wrapper.write_text("import json,os,runpy,sys\nfrom pathlib import Path\n"
    "from urllib.parse import parse_qs,urlsplit\n"
    "owned=Path(sys.argv[1]).resolve();events=[]\n"
    + inspect.getsource(local_path) + '\n' + inspect.getsource(guard)
    + "\nsys.addaudithook(guard)\nsys.argv=['scripts.run_factor_strategy_registration',*sys.argv[2:]]\n"
    + "try:\n runpy.run_module('scripts.run_factor_strategy_registration',run_name='__main__')\n"
    + "finally:\n (owned/'actual-child-write-guard.json').write_text(json.dumps({'sqlite':events,"
      "'writes_confined_to':str(owned)},indent=2)+'\\n')\n")
command = [sys.executable, '-P', '-B', str(wrapper), str(owned),
    '--base', str(base), '--data-database', str(database), '--strategy-database', str(strategies),
    '--archive-root', str(archive), '--request', str(owned / 'actual-request.json'),
    '--output', str(owned / 'actual-child-output'), '--source-revision', manifest['baseline_commit']]
child_started = time.perf_counter()
with (owned / 'actual-child-stdout.log').open('w') as out, (owned / 'actual-child-stderr.log').open('w') as err:
    child = subprocess.run(command, cwd=source, env=env, stdout=out, stderr=err, timeout=300)
assert child.returncode == 0, ('CHILD_FAILURE', child.returncode)
receipt = resolver.completion(request, owned / 'actual-child-output')
assert receipt['registration_only'] is True and receipt['status'] == 'EXPERIMENT'
assert receipt['role'] == 'NONE' and receipt['paper_admission'] is receipt['model_activation'] is False
assert json.loads((owned / 'actual-child-output/strategy-definition.json').read_bytes()) == definition
with sqlite3.connect(strategies.as_uri() + '?mode=ro', uri=True) as c:
    counts = {table: c.execute('SELECT COUNT(*) FROM ' + table).fetchone()[0]
        for table in ['strategies', 'strategy_research_contracts', 'strategy_research_jobs', 'strategy_transitions']}
assert all(value == 2 for value in counts.values()), counts
lineage = resolver.registry.lineage(definition['strategy_id'])
assert lineage is not None and lineage.parent_strategy_id == parent_definition['strategy_id']
assert resolver.registry.research_contract(parent_definition['strategy_id']) == parent_definition
assert {name:h(parent_root/name) for name in parent_files} == parent_before
save('actual-parent-after.json',{name:h(parent_root/name) for name in parent_files})
assert before_admission == _admission(database, identity)
after_old, after_longer = inventory(old), inventory(longer)
assert (before_old, before_longer) == (after_old, after_longer)
save('actual-originals-after.json', {'old': after_old, 'longer': after_longer})
after_control = controls()
save('actual-controls-after.json', after_control)
assert before_control == after_control
assert inventory(source) == manifest['expected_files']
save('actual-source-after.json', inventory(source))
resources('actual-resources-after.json')
save('actual-acceptance.json', signed({**AUTHORITY, 'state': 'PASS_ISOLATED_REGISTRATION_ONLY',
    'completed_at': datetime.now(UTC).isoformat(), 'elapsed_seconds': time.perf_counter() - started,
    'child_seconds': time.perf_counter() - child_started, 'source_files': len(manifest['expected_files']),
    'baseline_commit': manifest['baseline_commit'], 'source_overlays': manifest['overlays'],
    'registration': receipt, 'registry_counts': counts, 'copied_parent_registry_sha256': parent_before['isolated-strategies.sqlite'], 'parent_strategy_id': parent_definition['strategy_id'], 'parent_originals_unchanged': True, 'lineage_parent': lineage.parent_strategy_id, 'old_files_preserved': len(before_old),
    'longer_files_preserved': len(before_longer), 'source_before_after_exact': True,
    'originals_before_after_exact': True, 'controls_before_after_exact': True,
    'selected_ready_admission_unchanged': True, 'sqlite_audit': events,
    'child_write_guard': json.loads((owned / 'actual-child-write-guard.json').read_bytes()),
    'guarded_runner_sha256': h(wrapper),
    'maxrss_kib': resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss}))
print((owned / 'actual-acceptance.json').read_text(), flush=True)
