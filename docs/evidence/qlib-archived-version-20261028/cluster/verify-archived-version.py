"""Independent read-only replay of one completed non-head data-version job."""
import importlib.util
import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.qlib_publication import verify_archive, verify_signed
from qtrade.research.strategy_contract import freeze_strategy, ready_dataset
from scripts.atomic_json import write_json_immutable
from scripts.qlib_restore_bundle import verify_bundle

BASE = Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade')
LOCAL = Path('/root/qtrade-local')
OP = LOCAL/'operator-evidence/archived-version-578bb610-20261028'
JOB = '578bb6107577406185782a08e1c499d7'
OLD = 'd33a74663373b1d4a0d7cf1ecf1622b34657a6fda08aacc7bacadb70d47ec4fa'
CURRENT = '09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407'
REVISION = '096387b8088e4361c9e5cd9cd2c165e21819fead'
spec = importlib.util.spec_from_file_location('directed_handover_read_helpers',
    LOCAL/'operator-evidence/forward-handover-9846a7f-20261028/directed_handover.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)

def read(path):
    return json.loads(path.read_bytes())

def save(name, raw):
    with (OP/name).open('xb') as f:
        f.write(raw)

def signed_read(path):
    value = read(path)
    verify_signed(value)
    return value

def checksum(path):
    return sha256(path.read_bytes()).hexdigest()

started = datetime.now(UTC).isoformat()
assert os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() == 'false'
reference = signed_read(OP/'runtime-reference.json')
source = read(OP/'source-verification.json')
assert source['revision'] == REVISION and len(source['files']) == 580
for name, meta in source['files'].items():
    p = BASE/'releases'/REVISION/name
    assert not p.is_symlink() and p.stat().st_size == meta['bytes'] and checksum(p) == meta['sha256'], name
with closing(h.ro(LOCAL/'workflow.sqlite')) as connection:
    connection.row_factory = sqlite3.Row
    job = dict(connection.execute('SELECT * FROM jobs WHERE id=?', (JOB,)).fetchone())
    events = [dict(r) for r in connection.execute('SELECT rowid,id,at,state FROM events WHERE id=? ORDER BY rowid', (JOB,))]
assert job['state'] == 'COMPLETE' and job['error'] is None
request, result = json.loads(job['request']), json.loads(job['result'])
assert request['spec']['model'] == 'linear'
assert request['spec']['data_binding']['data_version_id'] == OLD != CURRENT
assert request['spec']['data_binding']['actual_end'] == '2026-10-26'
assert events[0]['state'] == 'QUEUED' and events[-1]['state'] == 'COMPLETE'
archive = BASE/'workflow/jobs'/JOB/'output/runs'/result['run_id']
summary = verify_archive(archive)
manifest = signed_read(archive/'checksums.json')
actual = {p.relative_to(archive).as_posix() for p in archive.rglob('*') if p.is_file()}
assert actual == set(manifest['files']) | {'checksums.json'}
assert not any(p.is_symlink() for p in archive.rglob('*'))
definition = signed_read(archive/'strategy-definition.json')
assert definition == freeze_strategy(request['spec'], REVISION)
assert definition == summary['strategy_definition']
assert definition['strategy_version'] == request['expected_strategy_version'] == result['strategy_version']
assert summary['data_binding'] == request['spec']['data_binding']
assert summary['data_binding'] == ready_dataset(BASE/'unified-data/qlib-versions/versions'/OLD, LOCAL/'qlib-versions.sqlite')
assert summary['data_end'] == '2026-10-26' and summary['source_revision'] == REVISION
assert summary['dataset_sha256'] == definition['spec']['data_binding']['dataset_sha256']
assert summary['source_snapshot_sha256'] == definition['spec']['data_binding']['source_snapshot_sha256']
assert summary['strategy_version'] == result['strategy_version']
detail = signed_read(BASE/'workflow/public'/result['detail_file'])
assert detail['evidence_sha256'] == result['detail_sha256']
assert detail['strategy_definition'] == definition and detail['summary_sha256'] == summary['evidence_sha256']
bundle = verify_bundle(archive/'online-restore', archive/'provider')
restore = signed_read(archive/'independent-restore.json')
assert restore == summary['independent_restore']
assert restore['bundle_sha256'] == bundle['evidence_sha256'] == result['model_artifact_id']
assert restore['fitted_processors_unchanged'] and restore['fit_methods_forbidden']
assert restore['state'] == 'PARITY_VERIFIED_NOT_ACTIVATED' and restore['model_activated'] is False
assert restore['feature_count'] == 158 and restore['max_absolute_score_difference'] <= 1e-8
assert datetime.fromisoformat(restore['verified_at']) <= datetime.fromisoformat(summary['completed_at']) <= datetime.fromisoformat(job['updated']) <= datetime.now(UTC)
with closing(h.ro(LOCAL/'online-models.sqlite')) as connection:
    connection.row_factory = sqlite3.Row
    model = dict(connection.execute('SELECT * FROM models WHERE model_id=?', (result['model_artifact_id'],)).fetchone())
    states = [list(r) for r in connection.execute('SELECT model_id,state,activated_at,activation_count FROM models ORDER BY model_id')]
    policy = json.loads(connection.execute('SELECT document FROM model_promotion_policy WHERE singleton=1').fetchone()[0])
assert model['activation_count'] == 0 and model['activated_at'] is None
assert model['state'] in {'TRAINED', 'VALIDATING', 'STAGED'}
model_definition = json.loads(model['definition'])
verify_signed(model_definition)
assert model_definition['training_data_end'] == '2026-10-26'
assert model_definition['training_code_revision'] == REVISION
assert model_definition['restore_proof_sha256'] == restore['evidence_sha256']
with closing(h.ro(LOCAL/'strategies.sqlite')) as connection:
    connection.row_factory = sqlite3.Row
    association = [dict(r) for r in connection.execute('SELECT j.job_id,s.strategy_id,s.status,s.role,s.version,c.contract_json FROM strategy_research_jobs j JOIN strategies s USING(strategy_id) JOIN strategy_research_contracts c USING(strategy_id) WHERE j.job_id=?', (JOB,))]
assert len(association) == 1
link = association[0]
assert json.loads(link['contract_json']) == definition
assert link['strategy_id'] == result['strategy_id'] and link['version'] == definition['strategy_version']
assert link['status'] == 'EXPERIMENT' and link['role'] == 'NONE'
context = h.context()
for key in ['active', 'approval_sha256', 'data_version', 'data_event_id']:
    assert context[key] == reference['context'][key], key
assert context['data_version'] == CURRENT
for old_model in reference['models']:
    assert old_model in states, old_model
assert h.fingerprint() == reference['forward_fingerprints']
assert h.service_tokens() == reference['services']
assert h.actual_instance()['instance_id'] == reference['forward_instance_id']
assert policy['evidence_sha256'] == reference['policy_id'] and policy['policy']['allowed_models'] == ['lightgbm']
proof = signed({**AUTHORITY, 'kind': 'ACTUAL_NON_HEAD_IMMUTABLE_VERSION_RESEARCH_REPLAY',
    'started_at': started, 'checked_at': datetime.now(UTC).isoformat(), 'state': 'PASSED',
    'job_id': JOB, 'worker_completed_at': job['updated'], 'run_id': summary['run_id'],
    'archive': str(archive), 'archive_files_verified': len(manifest['files']),
    'archive_manifest_sha256': manifest['evidence_sha256'], 'summary_sha256': summary['evidence_sha256'],
    'source_revision': REVISION, 'source_files_verified': 580,
    'source_verification_file_sha256': checksum(OP/'source-verification.json'),
    'operator_sha256': checksum(Path(__file__)), 'strategy_version': definition['strategy_version'],
    'strategy_id': definition['strategy_id'], 'data_binding': summary['data_binding'],
    'current_serving_head_unchanged': CURRENT, 'active_model_unchanged': context['active'],
    'existing_model_lifecycles_unchanged': True, 'approved_model_approval_unchanged': True,
    'forward_book_fingerprints_unchanged': True, 'forward_owner_and_instance_unchanged': True,
    'other_service_owners_unchanged': True, 'policy_unchanged': True,
    'strategy_status': 'EXPERIMENT', 'model_id': model['model_id'],
    'model_state_at_check': model['state'], 'model_activation_count': 0,
    'independent_restore': restore, 'metrics': summary['metrics'],
    'retrospective_replay_only': True, 'prospective_later_day_validation_claimed': False,
    'browser_verified': False, 'whole_step1_accepted': False,
    'known_limits': ['CURRENT_UNIVERSE_NON_PIT', 'PUBLIC_ADJUSTMENT_NOT_INDEPENDENTLY_CERTIFIED',
        'DAILY_EXECUTION_PROXY', 'NOT_A_NEW_DAY_OR_UNSEEN_FORWARD_VALIDATION']})
for name, path in [('summary.json', archive/'summary.json'), ('strategy-definition.json', archive/'strategy-definition.json'),
                   ('independent-restore.json', archive/'independent-restore.json')]:
    save(name, path.read_bytes())
save('job-and-registry.json', (json.dumps({'job': {k: v for k, v in job.items() if k not in {'request', 'result'}},
    'request': request, 'result': result, 'events': events,
    'model': {k: v for k, v in model.items() if k != 'definition'},
    'strategy_association': [{k: v for k, v in link.items() if k != 'contract_json'}]}, sort_keys=True, indent=2)+'\n').encode())
write_json_immutable(OP/'verification.json', proof)
print(json.dumps({'state': proof['state'], 'job_id': JOB, 'archive_files': len(manifest['files']),
    'historical_data_end': summary['data_end'], 'model_state': model['state'],
    'model_id': model['model_id'], 'restore_max_difference': restore['max_absolute_score_difference']}))
