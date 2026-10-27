"""Read-only actual clock/model validation capture; never claims a new forward day."""
import importlib.util
import json
import os
import sqlite3
from contextlib import closing
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.forward_prediction import verify_validation
from qtrade.research.qlib_publication import verify_signed

BASE = Path('/root/siton-data-ecd30b0dd03a4b6c86d4ad3b98458111/qtrade')
LOCAL = Path('/root/qtrade-local')
SOURCE = '096387b8088e4361c9e5cd9cd2c165e21819fead'
RIDGE = '98d27455a7cb224ef3ecbd7cf850455f386fbb93baf8137a4d789e142ab91795'
ACTIVE = '29589b4486a09159eb3a07153c54fba609fbc80b52c168dd665cd46660be8487'
DATA = '09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407'
STARTED = datetime.now(UTC)
OUT = LOCAL/'operator-evidence'/('model-calendar-boundary-'+STARTED.strftime('%Y%m%dT%H%M%SZ'))
OUT.mkdir(exist_ok=False)
spec = importlib.util.spec_from_file_location('handover_read_helpers',
    LOCAL/'operator-evidence/forward-handover-9846a7f-20261028/directed_handover.py')
h = importlib.util.module_from_spec(spec)
spec.loader.exec_module(h)


def read_signed(path):
    value = json.loads(path.read_bytes())
    verify_signed(value)
    return value


def save(name, value):
    with (OUT/name).open('xb') as handle:
        handle.write((json.dumps(value, sort_keys=True, indent=2)+'\n').encode())


assert os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() == 'false'
reference = read_signed(LOCAL/'operator-evidence/archived-version-578bb610-20261028/runtime-reference.json')
source = json.loads((LOCAL/'operator-evidence/archived-version-578bb610-20261028/source-verification.json').read_bytes())
assert source['revision'] == SOURCE
with closing(h.ro(LOCAL/'online-models.sqlite')) as db:
    db.row_factory = sqlite3.Row
    row = dict(db.execute('SELECT * FROM models WHERE model_id=?', (RIDGE,)).fetchone())
    definition = json.loads(row['definition'])
    validations = [dict(r) for r in db.execute('SELECT * FROM model_validations WHERE model_id=?', (RIDGE,))]
    events = [dict(r) for r in db.execute('SELECT * FROM model_events WHERE model_id=? ORDER BY sequence', (RIDGE,))]
    policy = json.loads(db.execute('SELECT document FROM model_promotion_policy WHERE singleton=1').fetchone()[0])
verify_signed(definition)
verify_signed(policy)
assert row['state'] == 'STAGED' and row['activation_count'] == 0 and row['activated_at'] is None
assert len(validations) == 1 and policy['policy']['allowed_models'] == ['lightgbm']
assert policy['policy']['allowed_model_ids'] == [] and policy['policy']['mode'] == 'ENABLED'
proof = json.loads(validations[0]['proof'])
index = read_signed(BASE/'online-research/control/validations'/(proof['validation_id']+'.json'))
archive = BASE/index['archive']
assert not Path(index['archive']).is_absolute() and archive.resolve().is_relative_to(BASE.resolve())
assert not archive.is_symlink() and verify_validation(archive) == proof
assert proof['evidence_sha256'] == index['manifest_sha256']
assert proof['model_bundle_sha256'] == RIDGE
assert proof['contract']['data_version_id'] == DATA and proof['contract']['data_end'] == '2026-10-27'
assert proof['contract']['model_reference_end'] == definition['training_data_end'] == '2026-10-26'
assert proof['contract']['forward_return_observed'] is False
assert proof['model_activated'] is False and proof['auto_predict_enabled'] is False
for name, digest in proof['bindings']['code'].items():
    assert digest == source['files'][name]['sha256'], name
    assert sha256((BASE/'releases'/SOURCE/name).read_bytes()).hexdigest() == digest, name
bindings = {'kind': 'QLIB_MODEL_VALIDATION', 'model_id': RIDGE,
            'data_version_id': DATA, 'source_revision': SOURCE}
job_id = signed(bindings)['evidence_sha256'][:32]
with closing(h.ro(LOCAL/'workflow.sqlite')) as db:
    db.row_factory = sqlite3.Row
    job = dict(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())
request = json.loads(job['request'])
verify_signed(request)
assert all(request[k] == v for k, v in bindings.items())
assert job['state'] == 'COMPLETE' and job['error'] is None
context = h.context()
assert context['active'] == ACTIVE and context['data_version'] == DATA
assert context['approval_sha256'] == reference['context']['approval_sha256']
assert context['data_event_id'] == reference['context']['data_event_id']
with closing(h.ro(LOCAL/'qlib-versions.sqlite')) as db:
    event = json.loads(db.execute('SELECT payload FROM data_events WHERE event_id=?', (context['data_event_id'],)).fetchone()[0])
verify_signed(event)
admitted = datetime.fromisoformat(event['admitted_at'])
trained = datetime.fromisoformat(definition['training_completed_at'])
generated = datetime.fromisoformat(proof['contract']['generated_at'])
assert admitted < trained <= generated <= datetime.now(UTC)
roster = read_signed(LOCAL/'forward-research/roster.json')['books']
assert len(roster) == 14
books = []
for identity, config in sorted(roster.items()):
    with closing(h.ro(LOCAL/'forward-research'/identity/'forward.sqlite')) as db:
        targets = []
        for target_id, payload, frozen_at in db.execute('SELECT id,payload,frozen_at FROM targets ORDER BY rowid'):
            target = json.loads(payload)
            targets.append({'target_id': target_id, 'generated_at': target['generated_at'],
                            'execution_at': target['execution_at'], 'frozen_at': frozen_at})
        sessions = [list(r) for r in db.execute('SELECT day,status FROM sessions ORDER BY day')]
    books.append({'book_id': identity, 'strategy_id': config['strategy_id'],
                  'mode': config['mode'], 'targets': targets, 'sessions': sessions})
active_books = [b for b in books if b['strategy_id'] == 'approved_model_'+ACTIVE]
assert len(active_books) == 2 and all(not b['sessions'] for b in active_books)
assert all(b['targets'] and all(datetime.fromisoformat(t['execution_at']) == datetime.fromisoformat('2026-10-28T09:30:00+08:00')
    for t in b['targets']) for b in active_books)
assert h.fingerprint() == reference['forward_fingerprints']
assert h.service_tokens() == reference['services']
instance = h.actual_instance()
assert instance['instance_id'] == reference['forward_instance_id']
assert policy['evidence_sha256'] == reference['policy_id']
receipt = signed({**AUTHORITY, 'kind': 'ACTUAL_MODEL_CALENDAR_BOUNDARY_READ_ONLY_CHECK',
    'started_at': STARTED.isoformat(), 'checked_at': datetime.now(UTC).isoformat(),
    'state': 'PASSED_COMPATIBILITY_BOUNDARY_ONLY', 'candidate_model': RIDGE,
    'candidate_state': row['state'], 'candidate_activation_count': row['activation_count'],
    'validation_id': proof['validation_id'], 'validation_job_id': job_id,
    'validation_job_state': job['state'], 'validation_archive': index['archive'],
    'validation_manifest_sha256': proof['evidence_sha256'], 'validation_files_verified': len(proof['files']),
    'validation_source_files_verified': len(proof['bindings']['code']),
    'source_revision': SOURCE, 'data_version_id': DATA,
    'training_reference_end': definition['training_data_end'], 'validation_data_end': proof['contract']['data_end'],
    'data_admitted_at': admitted.isoformat(), 'training_completed_at': trained.isoformat(),
    'validation_generated_at': generated.isoformat(), 'validation_data_existed_before_training': True,
    'strictly_unseen_forward_validation_claimed': False, 'forward_return_observed': False,
    'policy_excludes_ridge': True, 'active_model_unchanged': ACTIVE,
    'approval_and_data_epoch_unchanged': True, 'forward_fingerprints_unchanged': True,
    'service_owners_and_forward_instance_unchanged': True,
    'current_model_day_20261028': 'PROSPECTIVE_NO_OBSERVED_SESSION',
    'next_execution_at': proof['contract']['expected_execution_at'],
    'new_paper_recovery_authorized': False, 'operator_sha256': sha256(Path(__file__).read_bytes()).hexdigest(),
    'whole_step1_accepted': False, 'browser_verified': False,
    'limitations': ['LATER_DATA_DATE_IS_INPUT_COMPATIBILITY_NOT_UNSEEN_FORWARD_EVIDENCE',
        'POST_CLOSE_ACTIVE_TARGET_CANNOT_REPLACE_AN_OLDER_EXECUTION_DAY_OBSERVATION',
        'MODEL_PROMOTION_DOES_NOT_AUTHORIZE_PAPER_RECOVERY', 'HISTORICAL_PIT_NOT_CERTIFIED']})
save('verification.json', receipt)
save('validation.json', proof)
save('validation-index.json', index)
save('definition.json', definition)
save('validation-job.json', {**job, 'request': request, 'result': json.loads(job['result'])})
save('candidate-events.json', [{**r, 'payload': json.loads(r['payload'])} for r in events])
save('day-bindings.json', {'context': context, 'books': books, 'instance_id': instance['instance_id']})
print(json.dumps({'output': str(OUT), 'state': receipt['state'],
    'candidate_state': row['state'], 'files': len(proof['files']),
    'validation_data_existed_before_training': True, 'whole_step1_accepted': False}))
