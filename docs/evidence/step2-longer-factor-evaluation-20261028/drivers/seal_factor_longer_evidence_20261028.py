import gzip
import hashlib
import importlib.util
import json
import shutil
from datetime import UTC, datetime
from pathlib import Path

repo = Path('E:/QTrade-git')
local = Path('E:/QTrade-temp/factor-longer-s2b-20261028T112943Z-86cd84da')
remote = '/root/qtrade-local/operator-evidence/' + local.name
source_package = Path('E:/QTrade-temp/factor-strategy-s2c-20261028T111832Z-22bd6f85')
preflight = json.loads((local / 'preflight.json').read_bytes())
receipt = json.loads((local / 'receipt.json').read_bytes())
oracle = json.loads((local / 'independent-actual-price-oracle.json').read_bytes())

def h(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()

spec = importlib.util.spec_from_file_location('authorized_s2b_seal', 'E:/QTrade-temp/authorized_cluster_read.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
client = module.connect()
try:
    sftp = client.open_sftp()
    try:
        sftp.get(remote + '/inputs/factors.sqlite', str(local / 'factor-registry.sqlite'))
    finally:
        sftp.close()
finally:
    client.close()
assert h(local / 'factor-registry.sqlite') == preflight['original_factor_database_sha256']
(local / 'factor-registry.sqlite.gz').write_bytes(gzip.compress((local / 'factor-registry.sqlite').read_bytes(), mtime=0))
assert receipt['state'] == 'NONPRODUCTION_16_SESSION_DIAGNOSTICS_ARCHIVED'
assert oracle['state'] == 'PASS_INDEPENDENT_ACTUAL_PRICE_ORACLE'
assert oracle['report_manifest_sha256'] == receipt['reference']['report_manifest_sha256']
assert h(local / 'report.json.gz') == receipt['report_gzip_sha256'] == oracle['report_gzip_sha256']
assert h(local / 'request.json') == receipt['request_sha256'] == preflight['request_sha256']
assert (local / 'original-experiment-before.json').read_bytes() == (local / 'original-experiment-after.json').read_bytes()
package = json.loads((source_package / 'package-manifest.json').read_bytes())
for name in ('source-before.json', 'source-after.json'):
    check = json.loads((local / name).read_bytes())
    assert check['matches'] and check['files'] == package['expected_files']
for name in ('evaluation-operator-status.json', 'independent-oracle-operator.json'):
    assert json.loads((local / name).read_bytes())['returncode'] == 0
for name in ('preflight-stderr.log', 'evaluation-stderr.log', 'independent-oracle-stderr.log'):
    assert (local / name).stat().st_size == 0
destination = repo / 'docs/evidence/step2-longer-factor-evaluation-20261028'
destination.mkdir(parents=True, exist_ok=False)
for path in sorted(local.iterdir()):
    if path.is_file() and path.name != 'factor-registry.sqlite':
        shutil.copyfile(path, destination / path.name)
(destination / 'drivers').mkdir()
for name in ['prepare_factor_longer_window_20261028.py', 'run_factor_longer_window_20261028.py',
             'audit_factor_longer_prices_20261028.py', 'run_factor_longer_oracle_20261028.py',
             'review_factor_longer_local_20261028.py', 'seal_factor_longer_evidence_20261028.py']:
    shutil.copyfile(Path('E:/QTrade-temp') / name, destination / 'drivers' / name)
for name in ['STEP2_LONGER_FACTOR_EVALUATION_20261028.md', 'STEP2_FACTOR_QUEUE_BUILDER_PLAN_20261028.md']:
    shutil.copyfile(repo / 'docs' / name, destination / name)
shutil.copyfile(source_package / 'package-manifest.json', destination / 'source-package-manifest.json')
shutil.copyfile(source_package / 'overlay.tar', destination / 'source-overlay.tar')
files = [{'file': p.relative_to(destination).as_posix(), 'bytes': p.stat().st_size, 'sha256': h(p)}
         for p in sorted(destination.rglob('*')) if p.is_file()]
manifest = {'schema_version': 'qtrade.longer-factor-evaluation-acceptance.v1',
    'sealed_at': datetime.now(UTC).isoformat(), 'state': 'PASS_NONPRODUCTION_DIAGNOSTIC_ACCEPTANCE',
    'remote_root': remote, 'source_package': {'baseline_commit': package['baseline_commit'],
        'overlays': package['overlays'], 'file_count': len(package['expected_files']),
        'source_root': remote.rsplit('/', 1)[0] + '/factor-strategy-s2c-20261028T111832Z-22bd6f85/source'},
    'new_reference': receipt['reference'], 'new_report_sha256': receipt['report_gzip_sha256'],
    'parent_reference': preflight['original_evaluation_ref'],
    'original_experiment_files_unchanged': len(json.loads((local / 'original-experiment-before.json').read_bytes())['files']),
    'scope': {'instruments': 891, 'factors': 4, 'decisions': 16, 'dependency_cells': 983664,
              'knowledge_mode': 'RETROSPECTIVE_RECONSTRUCTION'},
    'independent_actual_price_checks': oracle['checks_total'],
    'max_metric_absolute_error': oracle['max_metric_absolute_error'],
    'limits': ['NO_HISTORICAL_PIT_CERTIFICATION', 'CURRENT_SELECTED_UNIVERSE',
        'NO_OOS_OR_SIGNIFICANCE_CLAIM', 'GROSS_LABEL_NOT_EXECUTED_NET_PORTFOLIO',
        'NO_PRODUCTION_STATE_OR_PAPER_ADMISSION_CLAIM'],
    'files': files}
(destination / 'manifest.json').write_text(json.dumps(manifest, indent=2) + '\n')
print(json.dumps({'path': str(destination), 'files': len(files),
                  'manifest_sha256': h(destination / 'manifest.json')}))
