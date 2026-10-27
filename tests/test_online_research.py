from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from test_qlib_workflow import client as research_client  # noqa: F401

from qtrade.api import online_research as api
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.workflow_queue import unseal
from scripts.publish_research_snapshot import online_publication


def observation():
    now = datetime.now(UTC)
    definition = signed({**AUTHORITY, 'schema_version': 'qtrade.online-model.v1',
        'model_id': 'a'*64, 'feature_count': 158, 'historical_pit_certified': False,
        'fitted_restore_verified': True})
    return signed({**AUTHORITY, 'schema_version': 'qtrade.online-catalog.v1',
        'published_at': now.isoformat(),
        'worker': {'heartbeat_at': now.isoformat(), 'state': 'MODEL_CONTROL_READY'},
        'models': [{'definition': definition, 'state': 'STAGED'}], 'active_model': None,
        'data_version': {'version_id': 'd'*64, 'historical_pit_certified': False,
            'external_execution_ready': False, 'current_symbols': 982, 'historical_symbols': 990,
            'blocked_symbols': [str(i) for i in range(8)], 'actual_end': '2026-10-26'},
        'validations': [{'validation_id': '1'*64, 'model_bundle_sha256': 'a'*64,
            'feature_count': 158, 'score_count': 888,
            'state': 'FORWARD_INPUT_VALIDATED_NOT_ACTIVATED', 'contract': {
                'data_version_id': 'd'*64, 'data_end': '2026-10-26',
                'generated_at': (now-timedelta(minutes=1)).isoformat(),
                'expected_execution_at': (now+timedelta(hours=1)).isoformat(),
                'forward_return_observed': False}}], 'commands': [], 'events': []})


def resign(value):
    return signed({k: v for k, v in value.items() if k != 'evidence_sha256'})


def command():
    return {'request_id': 'f'*32, 'model_id': 'a'*64, 'operation': 'ACTIVATE',
            'expected_active': None, 'data_version_id': 'd'*64, 'validation_id': '1'*64}


@pytest.fixture
def online(research_client, monkeypatch):  # noqa: F811
    http, password = research_client
    http.app.include_router(api.router)
    value, sent = observation(), []
    monkeypatch.setattr(api, 'read_public', lambda *a, **kw: resign(value))
    monkeypatch.setattr(api, 'research_clock', lambda: '2026-10-26')
    queue = type('Queue', (), {'append': lambda self, *args: sent.append(args)})()
    monkeypatch.setattr(api.GitQueue, 'from_environment', lambda: queue)
    return http, password, value, sent


def login(http, password):
    assert http.post('/research/login', json={
        'user': 'research-test', 'password': password}).status_code == 200


def test_standing_model_policy_requires_auth_scope_cas_and_seals_human_actor(online):
    http, password, value, sent = online
    value['model_promotion'] = {'policy_id': 'b'*64, 'policy': {
        'mode': 'OFF', 'allowed_model_ids': [], 'allowed_models': []}}
    body = {'request_id': 'e'*32, 'expected_policy_id': 'b'*64,
        'policy': {'mode': 'ENABLED', 'allowed_model_ids': [], 'allowed_models': ['lightgbm']},
        'explicit_human_authorization': True}
    route = '/research/online/model-promotion-policy'
    assert http.post(route, json=body).status_code == 401
    login(http, password)
    assert http.post(route, json=body, headers={'origin': 'https://invalid.example'}
                     ).status_code == 403
    assert http.post(route, json={**body, 'actor': 'spoof'}).status_code == 422
    assert http.post(route, json={**body, 'explicit_human_authorization': False}
                     ).status_code == 422
    assert http.post(route, json=body).status_code == 202
    payload = unseal(sent[0][2], 'x'*40)
    assert payload['kind'] == 'QLIB_MODEL_PROMOTION_POLICY'
    assert payload['command']['actor'] == 'research-test'
    assert payload['command']['authorization']['quote'] == '都允许批准'
    assert payload['live_trading_enabled'] is False
    value['model_promotion']['policy_id'] = 'c'*64
    assert http.post(route, json=body).status_code == 409
    assert len(sent) == 1


def test_public_read_and_authentication_same_origin_write_boundary(online):
    http, password, _, sent = online
    view = http.get('/research/online/catalog')
    assert view.status_code == 200 and view.json()['worker_fresh'] is True
    assert view.headers['cache-control'] == 'no-store'
    assert http.post('/research/online/actions', json=command()).status_code == 401
    login(http, password)
    assert http.post('/research/online/actions', json=command(),
                     headers={'origin': 'https://attacker.invalid'}).status_code == 403
    assert sent == []


def test_training_policy_authenticated_queue_and_stale_policy_rejection(online):
    from qtrade.research.rolling_policy import default_policy

    http, password, value, sent = online
    policy = default_policy()
    value['auto_train'] = {'mode': 'OFF', 'state': 'CONTROL_READY', 'policy_id': 'b'*64,
        'policy': policy, 'tasks': [], 'receipts': [], 'automatic_activation': False}
    body = {'request_id': 'f'*32, 'expected_policy_id': 'b'*64,
            'policy': {**policy, 'mode': 'WEEKLY'}}
    assert http.post('/research/online/training-policy', json=body).status_code == 401
    login(http, password)
    assert http.post('/research/online/training-policy', json=body,
        headers={'origin': 'https://attacker.invalid'}).status_code == 403
    response = http.post('/research/online/training-policy', json=body)
    assert response.status_code == 202
    payload = unseal(sent[0][2], 'x'*40)
    assert payload['kind'] == 'QLIB_ROLLING_POLICY'
    assert payload['command'] == {**body, 'actor': 'research-test'}
    assert payload['live_trading_enabled'] is False
    receipt = signed({**AUTHORITY, 'request_id': body['request_id'], 'state': 'APPLIED',
        'command_sha256': signed(payload['command'])['evidence_sha256']})
    value['auto_train'].update(policy_id='c'*64, receipts=[receipt])
    assert http.post('/research/online/training-policy', json=body).json() == receipt
    assert http.post('/research/online/training-policy', json={
        **body, 'request_id': 'e'*32}).status_code == 409
    assert http.post('/research/online/training-policy', json={
        **body, 'actor': 'root'}).status_code == 422
    assert len(sent) == 1


@pytest.mark.parametrize('invalid', [None, {}, [{}]*51])
def test_candidate_validation_publication_is_bounded(online, invalid):
    from qtrade.research.rolling_policy import default_policy

    http, _, value, _ = online
    value['auto_train'] = {'mode': 'OFF', 'state': 'CONTROL_READY', 'policy_id': 'b'*64,
        'policy': default_policy(), 'tasks': [], 'receipts': [], 'automatic_activation': False,
        'validation_tasks': invalid}
    assert http.get('/research/online/catalog').json()['publication_available'] is False


def test_approval_only_queues_signed_allowlisted_action_and_actor_is_session_bound(online):
    http, password, value, sent = online
    login(http, password)
    response = http.post('/research/online/actions', json=command())
    assert response.status_code == 202 and response.json()['state'] == 'QUEUED'
    channel, identity, sealed = sent[0]
    payload = unseal(sealed, 'x'*40)
    assert (channel, identity) == ('requests', 'f'*32)
    assert payload['command'] == {**command(), 'actor': 'research-test'}
    assert payload['live_trading_enabled'] is False
    assert value['active_model'] is None  # Vercel has no local activation authority.
    for extra in ({'actor': 'root'}, {'command': 'shell'}, {'live_trading_enabled': True}):
        assert http.post('/research/online/actions',
                         json={**command(), **extra}).status_code == 422
    assert len(sent) == 1


@pytest.mark.parametrize('change,status', [
    ('stale_worker', 503), ('stale_day', 409), ('data_changed', 409),
    ('active_changed', 409), ('expired_proof', 409), ('missing_proof', 409),
    ('invalid_clock', 503), ('wrong_proof_day', 503), ('future_proof', 503),
])
def test_changed_or_invalid_approval_evidence_cannot_be_submitted(online, change, status):
    http, password, value, sent = online
    login(http, password)
    proof = value['validations'][0]['contract']
    if change == 'stale_worker':
        value['worker']['heartbeat_at'] = (datetime.now(UTC)-timedelta(hours=1)).isoformat()
    elif change == 'stale_day':
        value['data_version']['actual_end'] = proof['data_end'] = '2026-10-23'
    elif change == 'data_changed':
        value['data_version']['version_id'] = 'e'*64
    elif change == 'active_changed':
        value['active_model'] = 'a'*64
        value['models'][0]['state'] = 'ACTIVE'
    elif change == 'expired_proof':
        proof['generated_at'] = (datetime.now(UTC)-timedelta(hours=2)).isoformat()
        proof['expected_execution_at'] = (datetime.now(UTC)-timedelta(hours=1)).isoformat()
    elif change == 'missing_proof':
        value['validations'] = []
    elif change == 'invalid_clock':
        proof['expected_execution_at'] = '2026-10-27T09:30:00'
    elif change == 'wrong_proof_day':
        proof['data_end'] = '2026-10-23'
    else:
        proof['generated_at'] = (datetime.now(UTC)+timedelta(minutes=1)).isoformat()
    assert http.post('/research/online/actions', json=command()).status_code == status
    assert sent == []


def test_receipt_retry_never_requeues_an_applied_switch(online):
    http, password, value, sent = online
    login(http, password)
    receipt = signed({**AUTHORITY, 'request_id': 'f'*32, 'state': 'APPLIED',
                      'command_sha256': signed({**command(), 'actor': 'research-test'})[
                          'evidence_sha256']})
    value['commands'] = [receipt]
    value['active_model'] = 'a'*64
    value['models'][0]['state'] = 'ACTIVE'
    assert http.post('/research/online/actions', json=command()).json() == receipt
    rebound = {**command(), 'operation': 'ROLLBACK'}
    assert http.post('/research/online/actions', json=rebound).status_code == 409
    assert sent == []


@pytest.mark.parametrize('damage', ['hash', 'scope', 'active', 'duplicate', 'unsafe', 'array'])
def test_invalid_optional_publication_fails_closed_instead_of_stalling_market(tmp_path, damage):
    import json

    value = deepcopy(observation())
    if damage == 'hash':
        value['evidence_sha256'] = '0'*64
    elif damage == 'scope':
        value['data_version']['blocked_symbols'] = []
    elif damage == 'active':
        value['active_model'] = 'a'*64
    elif damage == 'duplicate':
        value['validations'] *= 2
    elif damage == 'unsafe':
        value['live_trading_enabled'] = True
    else:
        value = []
    if isinstance(value, dict) and damage != 'hash':
        value = resign(value)
    source = tmp_path/'online.json'
    source.write_text(json.dumps(value))
    result = api.validate_catalog(online_publication(source))
    assert result['worker']['state'] == 'FAILED' and result['models'] == []
    assert result['live_trading_enabled'] is False
    assert api.worker_fresh(result) is False


def test_valid_optional_publication_preserves_actual_candidate_states(tmp_path):
    import json

    value = observation()
    source = tmp_path/'online.json'
    source.write_text(json.dumps(value))
    assert online_publication(source) == value


def add_prediction(value):
    now = datetime.now(UTC)
    value['worker']['source_revision'] = 'c'*40
    value['active_model'] = 'a'*64
    value['models'][0]['state'] = 'ACTIVE'
    bindings = {'model_id': 'a'*64, 'data_version_id': 'd'*64, 'approval_sha256': 'b'*64,
                'data_event_id': 'e'*64, 'observation_universe_version': 'f'*64,
                'code_revision': 'c'*40, 'data_end': '2026-10-26'}
    row = {'prediction_id': signed(bindings)['evidence_sha256'], 'state': 'COMPLETE',
           'bindings': bindings, 'feature_count': 158, 'score_count': 888,
           'contract': signed({**AUTHORITY, 'data_version_id': 'd'*64,
               'model_bundle_sha256': 'a'*64, 'data_end': '2026-10-26',
               'generated_at': (now-timedelta(minutes=1)).isoformat(),
               'expected_execution_at': (now+timedelta(hours=1)).isoformat(),
               'forward_return_observed': False})}
    value['auto_predict'] = {'schema_version': 'qtrade.auto-prediction.v1', 'enabled': True,
        'state': 'COMPLETE', 'predictions': [row], 'current_prediction_id': row['prediction_id'],
        'forward_return_observed': False}
    return row


def test_current_prediction_is_only_usable_with_fresh_matching_context(online):
    http, _, value, _ = online
    add_prediction(value)
    assert http.get('/research/online/catalog').json()['current_prediction_usable'] is True
    value['worker']['heartbeat_at'] = (datetime.now(UTC)-timedelta(hours=1)).isoformat()
    result = http.get('/research/online/catalog').json()
    assert result['publication_available'] is True
    assert result['current_prediction_usable'] is False


def test_return_to_same_version_does_not_revive_old_prediction_epoch(online):
    http, _, value, _ = online
    add_prediction(value)
    value['data_version']['data_event_id'] = '9'*64
    assert http.get('/research/online/catalog').json()['current_prediction_usable'] is False


def test_data_rollback_auth_epoch_and_idempotent_receipt(online, tmp_path):
    from test_data_version_control import setup

    from qtrade.research.data_version_control import apply, read_catalog

    http, password, value, sent = online
    database, archive, ledger, command, _ = setup(tmp_path)
    value['data_catalog'] = read_catalog(database, archive)
    body = {k: v for k, v in command.items() if k != 'actor'}
    assert http.post('/research/online/data-rollback', json=body).status_code == 401
    login(http, password)
    assert http.get('/research/online/data-versions').json()['publication_available']
    assert http.post('/research/online/data-rollback', json={
        **body, 'expected_event_id': '0'*64}).status_code == 409
    assert http.post('/research/online/data-rollback', json={
        **body, 'actor': 'forged'}).status_code == 422
    assert http.post('/research/online/data-rollback', json=body).status_code == 202
    envelope = unseal(sent[0][2], 'x'*40)
    assert envelope['kind'] == 'QLIB_DATA_ROLLBACK'
    assert envelope['command']['actor'] == 'research-test'
    receipt = apply(ledger, envelope['command'], now=datetime.now(UTC))
    value['data_catalog'] = read_catalog(database, archive)
    assert http.post('/research/online/data-rollback', json=body).json() == receipt
    assert len(sent) == 1


def test_historical_comparison_is_immutable_research_not_current_signal(online, monkeypatch):
    from test_strategy_comparison import public_result

    http, _, _, _ = online
    result = public_result()
    monkeypatch.setattr(api, 'read_public', lambda *args, **kwargs: result)
    response = http.get('/research/online/strategy-comparison')
    assert response.headers['cache-control'] == 'no-store'
    assert response.json()['comparison']['forward_return_observed'] is False
    result['strategies']['ridge']['metrics']['ic'] = True
    result = resign(result)
    assert http.get('/research/online/strategy-comparison').json()['publication_available'] is False


@pytest.mark.parametrize('age,fresh', [(0, True), (719, True), (721, False)])
def test_forward_service_freshness_is_read_time_not_execution_permission(online, monkeypatch,
                                                                      age, fresh):
    http, _, _, _ = online
    stamp = (datetime.now(UTC)-timedelta(seconds=age)).isoformat()
    value = signed({**AUTHORITY, 'schema_version': 'qtrade.forward-service.v1',
        'generated_at': stamp, 'heartbeat_at': stamp, 'source_revision': 'c'*40,
        'state': 'HUMAN_RECOVERY_REQUIRED', 'books': [], 'errors': [],
        'limitations': ['No production forward acceptance in this test']})
    monkeypatch.setattr(api, 'read_public', lambda *args, **kwargs: value)
    result = http.get('/research/online/forward-books').json()
    assert result['publication_available'] is True and result['service_fresh'] is fresh
    assert result['publication']['state'] == 'HUMAN_RECOVERY_REQUIRED'
    value['live_trading_enabled'] = True
    value = resign(value)
    assert http.get('/research/online/forward-books').json()['publication_available'] is False


@pytest.mark.parametrize('state,fresh', [('PREDICTING', True), ('RUNNING', True),
    ('STOPPED', False), ('QUEUE_UNAVAILABLE', False), ('FAILED', False), ('unknown', False)])
def test_working_process_is_not_mislabeled_as_expired_heartbeat(online, state, fresh):
    http, _, value, _ = online
    value['worker']['state'] = state
    assert http.get('/research/online/catalog').json()['worker_fresh'] is fresh


@pytest.mark.parametrize('age,fresh', [(0, True), (719, True), (721, False), (-60, False)])
def test_schedule_freshness_expires_at_read_time(online, age, fresh):
    http, _, value, _ = online
    value['data_schedule'] = {'fresh': True, 'heartbeat_at': (
        datetime.now(UTC)-timedelta(seconds=age)).isoformat()}
    result = http.get('/research/online/catalog').json()
    assert result['data_schedule_fresh'] is fresh
    assert result['data_schedule'] == value['data_schedule']  # Keep signed source unchanged.


@pytest.mark.parametrize('damage', ['identity', 'data', 'model', 'clock', 'returns', 'state'])
def test_invalid_current_prediction_fails_closed(online, damage):
    http, _, value, _ = online
    row = add_prediction(value)
    if damage == 'identity':
        row['prediction_id'] = '0'*64
    elif damage == 'data':
        row['contract']['data_version_id'] = 'e'*64
    elif damage == 'model':
        row['contract']['model_bundle_sha256'] = 'b'*64
    elif damage == 'clock':
        row['contract']['expected_execution_at'] = '2026-10-26T12:00:00'
    elif damage == 'returns':
        row['contract']['forward_return_observed'] = True
    else:
        row['state'] = 'SUPERSEDED'
    row['contract'] = resign(row['contract'])
    assert http.get('/research/online/catalog').json()['publication_available'] is False
