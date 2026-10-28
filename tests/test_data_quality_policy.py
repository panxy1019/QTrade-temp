# ruff: noqa: F811
import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path

import pytest
from test_daily_collection_binding import daily, stock_inputs, windows_atomic_writes  # noqa: F401

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.data_quality_policy import MODE, SCHEMA, USES, symbol_scope, validate_policy
from scripts import prepare_qlib_incremental as preparation


def approval(data):
    dataset = preparation.load_signed(data.previous/'dataset.json')
    return signed({**AUTHORITY, 'schema_version': SCHEMA, 'mode': MODE, 'state': 'APPROVED',
        'uses': sorted(USES), 'symbol_scope_sha256': symbol_scope(dataset),
        'universe_version': 'b'*64, 'paper_constraints_changed': False,
        'model_activation_authorized': False, 'approved_at': '2026-10-25T00:00:00+00:00',
        'effective_from_day': '2026-10-26', 'approved_by': 'test-only-human',
        'authorization_kind': 'EXPLICIT_HUMAN_ACTION',
        'authorization_message_sha256': 'a'*64})


def tasks(data, policy=None):
    return preparation.daily_tasks(data.base, preparation.load_signed(data.previous/'dataset.json'),
        '2026-10-26', 'b'*64, data.database, policy)


def test_default_waits_for_unrelated_etf_but_explicit_scoped_approval_does_not(daily):
    with sqlite3.connect(daily.database) as conn:
        conn.execute("UPDATE collection_tasks SET state='PLANNED' WHERE symbol='510050'")
    assert tasks(daily) is None
    result = tasks(daily, approval(daily))
    assert [r['symbol'] for r in result] == ['600036']


@pytest.mark.parametrize('defect', [
    'missing', 'pending', 'wrong_day', 'wrong_universe', 'bad_batch',
])
def test_scoped_approval_never_repairs_or_invents_a_required_input(daily, defect):
    with sqlite3.connect(daily.database) as conn:
        if defect == 'missing':
            conn.execute("DELETE FROM collection_tasks WHERE symbol='600036'")
        elif defect == 'pending':
            conn.execute("UPDATE collection_tasks SET state='RUNNING' WHERE symbol='600036'")
        elif defect == 'wrong_day':
            conn.execute("UPDATE collection_tasks SET trading_day='2026-10-27' "
                         "WHERE symbol='600036'")
        elif defect == 'wrong_universe':
            conn.execute("UPDATE collection_tasks SET universe_version=? WHERE symbol='600036'",
                         ('f'*64,))
        else:
            conn.execute("UPDATE collection_tasks SET last_success_batch='bad' "
                         "WHERE symbol='600036'")
    if defect == 'bad_batch':
        with pytest.raises(ValueError, match='BATCH_REQUIRED'):
            tasks(daily, approval(daily))
    else:
        assert tasks(daily, approval(daily)) is None


@pytest.mark.parametrize('defect', [
    'proposal', 'hash', 'scope', 'purpose', 'paper', 'clock', 'day',
])
def test_invalid_or_unapproved_scope_never_falls_back_to_a_weaker_contract(daily, defect):
    policy = approval(daily)
    policy.pop('evidence_sha256')
    if defect == 'proposal':
        policy['state'] = 'PROPOSED'
    elif defect == 'scope':
        policy['symbol_scope_sha256'] = 'f'*64
    elif defect == 'purpose':
        policy['uses'] = ['DAILY_RESEARCH']  # No implicit automatic-inference permission.
    elif defect == 'paper':
        policy['paper_constraints_changed'] = True
    elif defect == 'clock':
        policy['approved_at'] = '2099-01-01T00:00:00+00:00'
    elif defect == 'day':
        policy['effective_from_day'] = '2026-10-27'
    policy = signed(policy)
    if defect == 'hash':
        policy['approved_by'] = 'tampered'
    with pytest.raises(ValueError, match='QUALITY_SCOPE'):
        tasks(daily, policy)


def test_new_policy_creates_separate_frozen_scope_and_keeps_old_bytes(daily):
    # Bind only the source snapshot here; both modes still run the same verified batch reader.
    dataset = preparation.load_signed(daily.previous/'dataset.json')
    store = preparation.UnifiedBatchStore(daily.lake)
    kwargs = {'day': '2026-10-26', 'revision': 'c'*40, 'universe': 'b'*64,
              'collection_database': daily.database, 'store': store}
    old = preparation.preparation_request(daily.base, dataset, **kwargs)
    before = {str(p): p.read_bytes() for p in
              (daily.base/'unified-data/qlib-preparation/input-scopes').rglob('*.json')}
    new = preparation.preparation_request(daily.base, dataset,
                                         input_scope_policy=approval(daily), **kwargs)
    assert old['evidence_sha256'] != new['evidence_sha256']
    assert new['input_scope_policy_sha256'] == approval(daily)['evidence_sha256']
    for path, body in before.items():
        assert Path(path).read_bytes() == body


def test_scope_approval_explicitly_covers_inference_and_never_authorizes_model_activation(daily):
    dataset = json.loads((daily.previous/'dataset.json').read_bytes())
    result = validate_policy(approval(daily), dataset, 'b'*64, '2026-10-26',
                             datetime(2026, 10, 26, tzinfo=UTC))
    assert 'PREDICTION' in result['uses']
    assert result['model_activation_authorized'] is False
    assert result['real_data_paper_readiness'] is False
