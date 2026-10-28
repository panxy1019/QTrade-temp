import json
import sqlite3
from datetime import UTC, datetime, timedelta

import pytest
from test_data_quality import issuer, observation, task
from test_unified_queue import CALENDAR, VERSION, claim, master, receipt

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.collection_retry_policy import MODE, SCHEMA, retry_deferrals
from qtrade.research.data_quality import classify_task, retry_task_identity
from scripts.unified_queue import UnifiedCollectionQueue

NOW = datetime(2026, 10, 28, 2, tzinfo=UTC)


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value))


def approved(base):
    policy = signed({**AUTHORITY, 'schema_version': SCHEMA, 'mode': MODE,
        'state': 'APPROVED', 'universe_version': 'b'*64, 'execution_admitted': False,
        'paper_constraints_changed': False, 'model_input_admission_changed': False,
        'symbols': ['002827'], 'max_deferral_seconds': 21_600,
        'max_status_age_seconds': 86_400, 'quality_max_age_seconds': 1_200,
        'approved_at': NOW.isoformat(), 'effective_from_day': '2026-10-28',
        'authorization_kind': 'EXPLICIT_HUMAN_ACTION', 'approved_by': 'test-only-human',
        'authorization_message_sha256': 'f'*64})
    write(base/'data-quality/approved-retry-policy.json', policy)
    row = task()
    row['universe_version'] = 'b'*64
    case = classify_task(row, [observation()], [issuer()], now=NOW)
    value = signed({**AUTHORITY, 'schema_version': 'qtrade.data-quality-observation.v1',
        'observed_at': NOW.isoformat(), 'universe_version': 'b'*64, 'source_revision': 'a'*40,
        'production_admission_effect': 'NONE', 'execution_admitted': False,
        'cases': [case], 'case_counts': {'SUSPENDED': 1}, 'source_issues': [],
        'recent_status': {'available': False, 'content_verified_symbols': 0}})
    write(base/'data-quality/public/quality.json', value)
    return policy, value


def check(base, *, now=NOW, day='2026-10-27', frequency='day', universe='b'*64):
    return retry_deferrals(base, universe, frequency, day, now=now)


def test_default_does_not_defer_and_needs_no_quality_channel(tmp_path):
    assert check(tmp_path)['deferred_symbols'] == []
    assert check(tmp_path)['state'] == 'NO_APPROVED_RETRY_POLICY'


def test_approval_only_defers_exact_vendor_day_and_frequency(tmp_path):
    policy, quality = approved(tmp_path)
    result = check(tmp_path)
    assert result['deferred_symbols'] == ['002827']
    assert result['policy_sha256'] == policy['evidence_sha256']
    assert result['quality_sha256'] == quality['evidence_sha256']
    assert check(tmp_path, day='2026-10-28')['deferred_symbols'] == []
    assert check(tmp_path, frequency='5min')['deferred_symbols'] == []
    assert check(tmp_path, universe='e'*64)['deferred_symbols'] == []


@pytest.mark.parametrize('defect', [
    'unapproved', 'tampered', 'future_approval', 'paper', 'expired_quality',
    'expired_status', 'partial_price', 'corrupt_optional', 'unsigned',
])
def test_bad_optional_retry_control_resumes_existing_retry_without_global_failure(tmp_path, defect):
    policy, quality = approved(tmp_path)
    now = NOW
    if defect == 'unapproved':
        policy['state'] = 'PROPOSED'
    elif defect == 'future_approval':
        policy['approved_at'] = (NOW+timedelta(seconds=1)).isoformat()
    elif defect == 'paper':
        policy['paper_constraints_changed'] = True
    elif defect == 'expired_quality':
        now += timedelta(seconds=1_201)
    elif defect == 'expired_status':
        quality['cases'][0]['source_observed_at'] = (NOW-timedelta(days=2)).isoformat()
    elif defect == 'partial_price':
        quality['cases'][0]['completed_end'] = '2026-10-27T01:55:00+00:00'
        quality['cases'][0]['coverage'] = 'PARTIAL'
    elif defect == 'corrupt_optional':
        policy = []
    if isinstance(policy, dict) and defect != 'unsigned':
        policy = signed({k: v for k, v in policy.items() if k != 'evidence_sha256'})
    if defect == 'tampered':
        policy['symbols'].append('600621')
    if defect == 'unsigned':
        policy.pop('evidence_sha256')
    quality = signed({k: v for k, v in quality.items() if k != 'evidence_sha256'})
    write(tmp_path/'data-quality/approved-retry-policy.json', policy)
    write(tmp_path/'data-quality/public/quality.json', quality)
    assert check(tmp_path, now=now)['deferred_symbols'] == []


def test_deferral_keeps_failure_bytes_and_original_backoff_while_other_symbols_progress(tmp_path):
    now = datetime(2026, 10, 26, 9, tzinfo=UTC)
    queue = UnifiedCollectionQueue(tmp_path/'queue.sqlite')
    queue.plan(master(3), CALENDAR, now)
    first = claim(queue, limit=3, now=now)
    queue.finish(receipt(first, first['spec']['symbols']), now=now)
    query = "SELECT * FROM collection_tasks WHERE symbol='600000' AND frequency='day'"
    with sqlite3.connect(queue.database) as conn:
        before = conn.execute(query).fetchone()
    record = next(r for r in queue.snapshot(VERSION)['tasks']
                  if r['symbol'] == '600000' and r['frequency'] == 'day')
    token = {**record, 'task_sha256': retry_task_identity(record),
             'quality_sha256': 'a'*64, 'policy_sha256': 'b'*64,
             'quality_observed_at': datetime.now(UTC).isoformat(),
             'source_observed_at': datetime.now(UTC).isoformat(),
             'expires_at': (datetime.now(UTC)+timedelta(minutes=20)).isoformat()}
    batch = queue.claim(VERSION, 'day', now.date(), now=now+timedelta(seconds=61),
                        code_revision='b'*40, limit=3, deferred_tasks=(token,))
    assert batch['spec']['symbols'] == ['600001', '600002']
    with sqlite3.connect(queue.database) as conn:
        assert conn.execute(query).fetchone() == before
    # No policy-derived terminal state is stored. Expiry restores the normal due retry.
    queue.finish(receipt(batch), now=now+timedelta(seconds=61))
    assert claim(queue, now=now+timedelta(seconds=62))['spec']['symbols'] == ['600000']


def test_deferral_does_not_skip_unattempted_or_partially_collected_task(tmp_path):
    now = datetime(2026, 10, 26, 9, tzinfo=UTC)
    queue = UnifiedCollectionQueue(tmp_path/'queue.sqlite')
    queue.plan(master(1), CALENDAR, now)
    record = next(r for r in queue.snapshot(VERSION)['tasks'] if r['frequency'] == 'day')
    token = {**record, 'task_sha256': retry_task_identity(record),
             'quality_sha256': 'a'*64, 'policy_sha256': 'b'*64,
             'quality_observed_at': datetime.now(UTC).isoformat(),
             'source_observed_at': datetime.now(UTC).isoformat(),
             'expires_at': (datetime.now(UTC)+timedelta(minutes=20)).isoformat()}
    first = queue.claim(VERSION, 'day', now.date(), now=now, code_revision='b'*40,
                        deferred_tasks=(token,))
    assert first is not None  # PLANNED is never suppressed by a retained FAILED diagnostic.
    queue.finish(receipt(first, ['600000']), now=now)
    with sqlite3.connect(queue.database) as conn:
        conn.execute("UPDATE collection_tasks SET completed_end=? WHERE frequency='day'",
                     ('2026-10-26T06:55:00+00:00',))
    record = next(r for r in queue.snapshot(VERSION)['tasks'] if r['frequency'] == 'day')
    token.update(record, task_sha256=retry_task_identity(record))
    assert queue.claim(VERSION, 'day', now.date(), now=now+timedelta(seconds=61),
                       code_revision='b'*40, deferred_tasks=(token,)) is not None
