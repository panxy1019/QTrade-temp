import sqlite3
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.model_registry import ModelRegistry, control_command

NOW = datetime(2026, 10, 26, 12, tzinfo=UTC)
DATA = 'd'*64


def definition(model='a'):
    return signed({**AUTHORITY, 'schema_version': 'qtrade.online-model.v1',
                   'model_id': model*64, 'feature_count': 158,
                   'historical_pit_certified': False, 'fitted_restore_verified': True})


def proof(model='a'):
    contract = signed({**AUTHORITY, 'model_bundle_sha256': model*64, 'data_version_id': DATA,
                       'generated_at': (NOW-timedelta(minutes=1)).isoformat(),
                       'expected_execution_at': (NOW+timedelta(hours=12)).isoformat()})
    return signed({**AUTHORITY, 'schema_version': 'qtrade.qlib-forward-validation.v1',
                   'state': 'FORWARD_INPUT_VALIDATED_NOT_ACTIVATED',
                   'model_bundle_sha256': model*64,
                   'validation_id': ('1' if model == 'a' else '2')*64,
                   'contract': contract, 'fitted_processors_unchanged': True,
                   'fit_methods_forbidden': True, 'model_activated': False,
                   'auto_predict_enabled': False})


def command(model='a', *, expected=None, operation='ACTIVATE', request='1'):
    return {'request_id': request*32, 'operation': operation, 'model_id': model*64,
            'expected_active': expected, 'data_version_id': DATA, 'actor': 'test-operator',
            'validation_id': ('1' if model=='a' else '2')*64}


@pytest.fixture
def registry(tmp_path):
    return ModelRegistry(tmp_path/'models.sqlite')


def stage(registry, model='a'):
    registry.register(definition(model), now=NOW)
    registry.record_validation(proof(model), now=NOW)


def test_model_import_and_validation_never_implicitly_activate_and_replay_is_inert(registry):
    stage(registry)
    before = registry.snapshot()
    assert before['active_model'] is None and before['models'][0]['state'] == 'STAGED'
    assert [r['kind'] for r in reversed(before['events'])] == [
        'TRAINED', 'VALIDATING', 'VALIDATED', 'STAGED']
    stage(registry)
    assert registry.snapshot()['events'] == before['events']


def test_switch_and_rollback_are_atomic_and_replay_does_not_switch_back(registry):
    stage(registry)
    stage(registry, 'b')
    first = registry.apply(command(), current_data_version=DATA, now=NOW)
    assert first['state'] == 'APPLIED'
    second = registry.apply(command('b', expected='a'*64, request='2'),
                            current_data_version=DATA, now=NOW)
    assert second['active_model'] == 'b'*64
    assert registry.apply(command(), current_data_version=DATA, now=NOW) == first
    assert registry.snapshot()['active_model'] == 'b'*64
    result = registry.apply(command(expected='b'*64, operation='ROLLBACK', request='3'),
                            current_data_version=DATA, now=NOW)
    assert result['state'] == 'APPLIED' and registry.snapshot()['active_model'] == 'a'*64
    states = {r['definition']['model_id']: r['state'] for r in registry.snapshot()['models']}
    assert states == {'a'*64: 'ACTIVE', 'b'*64: 'RETIRED'}
    restored = ModelRegistry(registry.database)
    assert restored.snapshot()['active_model'] == 'a'*64
    assert len(restored.snapshot()['commands']) == 3


@pytest.mark.parametrize('change,error', [
    ('head', 'ACTIVE_MODEL_CHANGED'), ('data', 'READY_DATA_CHANGED'),
    ('proof', 'CURRENT_INPUT_VALIDATION_REQUIRED'), ('expiry', 'VALIDATION_TIME_EXPIRED'),
    ('rollback', 'MODEL_STATE_NOT_ELIGIBLE'),
])
def test_invalid_approval_retains_active_head_and_persists_rejection(registry, change, error):
    stage(registry)
    request, data, clock = command(), DATA, NOW
    if change == 'head':
        request['expected_active'] = 'b'*64
    elif change == 'data':
        data = 'e'*64
    elif change == 'proof':
        request['validation_id'] = '3'*64
    elif change == 'expiry':
        clock += timedelta(days=1)
    else:
        request['operation'] = 'ROLLBACK'
    result = registry.apply(request, current_data_version=data, now=clock)
    assert result['state'] == 'REJECTED' and error in result['error_code']
    assert registry.snapshot()['active_model'] is None
    assert registry.snapshot()['models'][0]['state'] == 'STAGED'
    assert len(registry.snapshot()['commands']) == 1


def test_event_write_failure_rolls_back_head_states_and_receipt(registry):
    stage(registry)
    with registry.connect() as c:
        c.execute("CREATE TRIGGER fail_event BEFORE INSERT ON model_events "
                  "BEGIN SELECT RAISE(ABORT,'injected failure'); END")
    with pytest.raises(sqlite3.IntegrityError):
        registry.apply(command(), current_data_version=DATA, now=NOW)
    view = registry.snapshot()
    assert view['active_model'] is None and view['models'][0]['state'] == 'STAGED'
    assert view['commands'] == []
    with registry.connect() as c:
        c.execute('DROP TRIGGER fail_event')
    assert registry.apply(command(), current_data_version=DATA, now=NOW)['state'] == 'APPLIED'


def test_request_identity_cannot_be_rebound_to_another_action(registry):
    stage(registry)
    registry.apply(command(), current_data_version=DATA, now=NOW)
    with pytest.raises(ValueError, match='COMMAND_CONFLICT'):
        registry.apply(command(operation='ROLLBACK'), current_data_version=DATA, now=NOW)


def test_model_and_validation_definitions_are_immutable(registry):
    stage(registry)
    changed = {k: v for k, v in definition().items() if k != 'evidence_sha256'}
    changed['name'] = 'changed'
    with pytest.raises(ValueError, match='DEFINITION_CONFLICT'):
        registry.register(signed(changed), now=NOW)
    changed = {k: v for k, v in proof().items() if k != 'evidence_sha256'}
    changed['extra'] = 'changed'
    with pytest.raises(ValueError, match='VALIDATION_CONFLICT'):
        registry.record_validation(signed(changed), now=NOW)


def test_candidate_failure_cannot_replace_or_fail_the_active_model(registry):
    stage(registry)
    registry.apply(command(), current_data_version=DATA, now=NOW)
    registry.register(definition('b'), now=NOW)
    registry.fail_candidate('b'*64, 'INPUT_CHECK_FAILED', now=NOW)
    assert registry.snapshot()['active_model'] == 'a'*64
    with pytest.raises(ValueError, match='PENDING_CANDIDATE'):
        registry.fail_candidate('a'*64, 'INPUT_CHECK_FAILED', now=NOW)
    with pytest.raises(ValueError, match='NOT_REGISTERED_OR_FAILED'):
        registry.record_validation(proof('b'), now=NOW)


def test_controls_reject_arbitrary_paths_code_and_implicit_operator():
    for key, value in [('model_id', '../pickle'), ('actor', ''), ('operation', 'RUN_CODE')]:
        changed = deepcopy(command())
        changed[key] = value
        with pytest.raises(ValueError):
            control_command(changed)
    with pytest.raises(ValueError):
        control_command({**command(), 'code': 'arbitrary'})
