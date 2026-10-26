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
