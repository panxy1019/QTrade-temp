import json
import os
import sqlite3
from datetime import UTC, datetime

import pytest
from test_model_registry import DATA, NOW, command, definition, proof

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts import online_model_control as adapter


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    value = adapter.OnlineModels(tmp_path, tmp_path/'models.sqlite', tmp_path/'data.sqlite')
    with sqlite3.connect(value.data_database) as connection:
        connection.execute('CREATE TABLE data_heads(frequency TEXT, version_id TEXT)')
        connection.execute('INSERT INTO data_heads VALUES(?,?)', ('day', DATA))
    if os.name == 'nt':
        # Production durability is exercised by the full Linux suite.
        def write(path, payload):
            path.parent.mkdir(parents=True, exist_ok=True)
            with path.open('x') as handle:
                json.dump(payload, handle)
        monkeypatch.setattr(adapter, 'write_json_immutable', write)
    model = definition()
    model['model_run_id'] = 'frozen-run'
    model = signed({k: v for k, v in model.items() if k != 'evidence_sha256'})
    validation = proof()
    validation.update(model_run_id='frozen-run', feature_count=158, score_count=888,
                      max_reference_score_difference=0.0)
    validation['contract'] = signed({**{k: v for k, v in validation['contract'].items()
        if k != 'evidence_sha256'}, 'data_end': '2026-10-26', 'forward_return_observed': False})
    validation = signed({k: v for k, v in validation.items() if k != 'evidence_sha256'})
    value.registry.register(model, now=NOW)
    value.registry.record_validation(validation, now=NOW)
    value.remember(value.root/'models'/('a'*64+'.json'), signed({**AUTHORITY,
        'archive': 'archives/frozen', 'definition': model}))
    value.remember(value.root/'validations'/('1'*64+'.json'), signed({**AUTHORITY,
        'archive': 'validation/frozen', 'manifest_sha256': validation['evidence_sha256']}))
    monkeypatch.setattr(adapter, 'verify_archive', lambda p: {'run_id': 'frozen-run'})
    monkeypatch.setattr(adapter, 'verify_bundle', lambda *args: {'evidence_sha256': 'a'*64})
    monkeypatch.setattr(adapter, 'verify_validation', lambda p: validation)
    data = {'version_id': DATA, 'actual_end': '2026-10-26'}
    monkeypatch.setattr(value, 'data_head', lambda **kw: data)

    class Clock(datetime):
        @classmethod
        def now(cls, tz=UTC):
            return NOW
    monkeypatch.setattr(adapter, 'datetime', Clock)
    return value, validation, data


def test_control_applies_once_and_retry_does_not_read_changed_artifacts(runtime, monkeypatch):
    value, _, _ = runtime
    result = value.execute(command(), now=NOW)
    assert result['state'] == 'APPLIED' and result['active_model'] == 'a'*64
    monkeypatch.setattr(adapter, 'verify_archive', lambda p: pytest.fail('replayed mutation'))
    assert value.execute(command(), now=NOW) == result
    assert len(value.registry.snapshot()['commands']) == 1


@pytest.mark.parametrize('damage', ['artifact', 'binding', 'stale_day', 'head_race'])
def test_control_reverifies_artifacts_and_head_under_commit_lease(runtime, monkeypatch, damage):
    value, validation, data = runtime
    if damage == 'artifact':
        def broken(path):
            raise ValueError('ARCHIVE_BYTES_CHANGED')
        monkeypatch.setattr(adapter, 'verify_archive', broken)
    elif damage == 'binding':
        monkeypatch.setattr(adapter, 'verify_validation', lambda p: {
            **validation, 'model_bundle_sha256': 'b'*64})
    elif damage == 'stale_day':
        data['actual_end'] = '2026-10-23'
    else:
        def raced(**kwargs):
            with sqlite3.connect(value.data_database) as connection:
                connection.execute('UPDATE data_heads SET version_id=?', ('e'*64,))
            return data
        monkeypatch.setattr(value, 'data_head', raced)
    result = value.execute(command(), now=NOW)
    assert result['state'] == 'REJECTED'
    assert value.registry.snapshot()['active_model'] is None
    assert value.registry.snapshot()['models'][0]['state'] == 'STAGED'
    assert value.execute(command(), now=NOW) == result


def test_local_control_index_cannot_escape_base_or_change_immutable_mapping(runtime, tmp_path):
    value, _, _ = runtime
    with pytest.raises(ValueError, match='OUTSIDE_LOCAL_ARCHIVE'):
        value.local('../outside')
    with pytest.raises(ValueError, match='INDEX_CONFLICT'):
        value.remember(value.root/'models'/('a'*64+'.json'), signed({**AUTHORITY, 'changed': True}))
    if os.name != 'nt':
        (tmp_path/'escape').symlink_to(tmp_path.parent, target_is_directory=True)
        with pytest.raises(ValueError, match='OUTSIDE_LOCAL_ARCHIVE'):
            value.local('escape/file')


@pytest.mark.parametrize('state', ['QUEUE_UNAVAILABLE', 'RUNNING'])
def test_busy_or_disconnected_worker_cannot_advertise_ready_control(runtime, state):
    value, _, _ = runtime
    result = value.publication('a'*40, now=NOW, worker_state=state)
    assert result['worker']['state'] == state
    assert result['auto_predict']['enabled'] is False
    assert result['active_model'] is None


def test_daily_schedule_does_not_equate_live_process_with_qualified_data(runtime):
    value, _, _ = runtime
    folder = value.base/'unified-data/qlib-schedule'
    folder.mkdir(parents=True)
    (folder/'service-state.json').write_text(json.dumps(signed({**AUTHORITY,
        'state': 'WAITING', 'heartbeat_at': NOW.isoformat(), 'source_revision': 'a'*40})))
    (folder/'cycle.json').write_text(json.dumps(signed({**AUTHORITY,
        'state': 'BLOCKED_MISSED_SESSION_REQUIRES_BACKFILL',
        'required_day': '2026-10-26', 'data_end': '2026-10-22'})))
    schedule = value.publication('a'*40, now=NOW)['data_schedule']
    assert schedule['fresh'] is True
    assert schedule['last_cycle_state'] == 'BLOCKED_MISSED_SESSION_REQUIRES_BACKFILL'


@pytest.mark.parametrize('damage', [None, 'fit_methods_forbidden',
    'fitted_processors_unchanged', 'bundle_sha256'])
def test_trained_import_requires_restore_proof_and_never_promotes(runtime, monkeypatch, damage):
    value, _, _ = runtime
    original = value.registry.snapshot()['models'][0]['definition']
    summary = {**original, 'run_id': 'new-rolling-run', 'model': 'LightGBM',
        'data_end': '2026-10-26', 'dataset_sha256': 'd'*64,
        'source_revision': 'c'*40, 'completed_at': NOW.isoformat(), 'segments': {},
        'portfolio': {}, 'cost_assumptions': {}, 'metrics': {}, 'limitations': [],
        'source_snapshot_sha256': 'e'*64}
    restore = signed({**AUTHORITY, 'bundle_sha256': 'b'*64,
        'state': 'PARITY_VERIFIED_NOT_ACTIVATED', 'fitted_processors_unchanged': True,
        'fit_methods_forbidden': True})
    if damage:
        restore[damage] = 'c'*64 if damage == 'bundle_sha256' else False
        restore = signed({k: v for k, v in restore.items() if k != 'evidence_sha256'})
    monkeypatch.setattr(adapter, 'verify_archive', lambda p: summary)
    monkeypatch.setattr(adapter, 'verify_bundle', lambda *a: {'evidence_sha256': 'b'*64})
    load = adapter.load_signed
    monkeypatch.setattr(adapter, 'load_signed', lambda p: restore if (
        p.name == 'independent-restore.json') else load(p))
    archive = value.base/'archives/new'
    if damage:
        with pytest.raises(ValueError, match='PROOF_IDENTITY_CHANGED'):
            value.import_trained(archive, now=NOW)
        assert len(value.registry.snapshot()['models']) == 1
    else:
        assert value.import_trained(archive, now=NOW) == 'b'*64
        assert value.import_trained(archive, now=NOW) == 'b'*64
        state = value.registry.snapshot()
        new = next(m for m in state['models'] if m['definition']['model_id'] == 'b'*64)
        assert new['state'] == 'TRAINED'
        assert state['active_model'] is None
