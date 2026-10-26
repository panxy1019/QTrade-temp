from __future__ import annotations

import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from types import SimpleNamespace

import pytest
from test_session_acceptance import transition  # noqa: F401

from qtrade.api import online_research as api
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.session_acceptance import assess_transition
from scripts import qlib_public_delivery as delivery

NOW = datetime(2026, 10, 27, 11, tzinfo=UTC)
REVISION = '9'*40
WORKER_REVISION = '8'*40


def resign(value):
    return signed({key: item for key, item in value.items() if key != 'evidence_sha256'})


def reseal_catalog(value):
    flags = {key: value[key] for key in delivery.API_FIELDS}
    return {**signed({key: item for key, item in value.items()
                      if key not in delivery.API_FIELDS | {'evidence_sha256'}}), **flags}


@pytest.fixture
def runtime(transition, monkeypatch):  # noqa: F811
    previous, current, completion, prediction, signals, admission = deepcopy(transition)
    prediction['bindings'].update(code_revision=WORKER_REVISION,
                                  observation_universe_version='7'*64)
    prediction['prediction_id'] = signed(prediction['bindings'])['evidence_sha256']
    prediction['contract'] = signed({**AUTHORITY, **prediction['contract'],
        'model_bundle_sha256': prediction['bindings']['model_id'],
        'forward_return_observed': False})
    prediction.update(feature_count=158, score_count=2)
    prediction = resign(prediction)
    signals['bindings']['code_revision'] = WORKER_REVISION
    signals['signal_family_id'] = signed(signals['bindings'])['evidence_sha256']
    signals = resign(signals)
    calendar = Calendar(delivery.REPO/'configs/observer_calendar_2026.json')
    private = assess_transition(previous, current, completion, prediction, signals, admission,
                                calendar, now=NOW)
    clock = NOW-timedelta(seconds=60)
    data_catalog = signed({**AUTHORITY, 'schema_version': 'qtrade.qlib-data-catalog.v1',
        'head_version_id': current['version_id'], 'head_event_id': admission['evidence_sha256'],
        'head_sequence': 1, 'total_versions': 1, 'versions_truncated': False, 'history': [],
        'next_before_sequence': None, 'rollback_requires_authenticated_command': True,
        'receipts': [], 'versions': [{'version_id': current['version_id'],
            'actual_end': current['actual_end'], 'admitted_at': admission['admitted_at'],
            'manifest_sha256': current['evidence_sha256'], 'is_head': True,
            'current_symbols': 2, 'historical_symbols': 2, 'blocked_symbols': 0,
            'historical_pit_certified': False, 'external_execution_ready': False,
            'verification': 'MANIFEST_IDENTITY_ONLY_FULL_BYTES_CHECKED_BEFORE_ROLLBACK'}]})
    definition = signed({**AUTHORITY, 'schema_version': 'qtrade.online-model.v1',
        'model_id': private['approved_model_id'], 'feature_count': 158,
        'historical_pit_certified': False, 'fitted_restore_verified': True})
    row = {key: prediction[key] for key in ('prediction_id', 'bindings', 'contract',
                                           'feature_count', 'score_count', 'evidence_sha256')}
    row['state'] = 'COMPLETE'
    catalog = signed({**AUTHORITY, 'schema_version': 'qtrade.online-catalog.v1',
        'published_at': clock.isoformat(), 'worker': {'heartbeat_at': clock.isoformat(),
            'source_revision': WORKER_REVISION, 'state': 'MODEL_CONTROL_READY'},
        'data_error': None, 'data_catalog': data_catalog, 'models': [
            {'state': 'ACTIVE', 'definition': definition}],
        'validations': [], 'commands': [], 'events': [],
        'active_model': private['approved_model_id'],
        'data_version': {'version_id': current['version_id'], 'actual_end': current['actual_end'],
            'evidence_sha256': current['evidence_sha256'],
            'data_event_id': private['data_event_id'],
            'current_symbols': 2, 'historical_symbols': 2, 'blocked_symbols': [],
            'historical_pit_certified': False, 'external_execution_ready': False},
        'data_schedule': {'heartbeat_at': clock.isoformat(), 'fresh': True},
        'auto_predict': {'schema_version': 'qtrade.auto-prediction.v1', 'enabled': True,
            'state': 'COMPLETE', 'predictions': [row],
            'current_prediction_id': prediction['prediction_id'], 'forward_return_observed': False},
        'auto_train': {'mode': 'OFF', 'state': 'UNAVAILABLE'}})
    catalog.update(publication_available=True, worker_fresh=True, data_schedule_fresh=True,
                   current_prediction_usable=True, expected_complete_day=current['actual_end'])
    bodies = {
        delivery.DEPLOYMENT: {'classification': 'PUBLIC_BUILD_PROVENANCE', 'provider': 'VERCEL',
            'status': 'AVAILABLE', 'target_environment': 'production', 'git_commit_sha': REVISION,
            'git_commit_ref': 'main', 'external_order_transmission': False},
        delivery.CATALOG: catalog,
        delivery.VERSIONS: {**AUTHORITY, 'publication_available': True, 'worker_fresh': True,
                            'catalog': data_catalog},
        delivery.SIGNALS: {**AUTHORITY, 'publication_available': True, 'current_input': True,
                           'manifest': signals, 'execution_admitted': False,
                           'forward_return': None}}
    requests = []

    def fetch(path, deadline):
        requests.append(path)
        value = bodies[path]
        if path == delivery.CATALOG and isinstance(value, list):
            value = value[0] if requests.count(path) == 1 else value[1]
        raw = json.dumps(value).encode()
        return {'url': delivery.ORIGIN+path, 'status_code': 200,
            'cache_control': 'no-store', 'content_type': 'application/json',
            'requested_at': NOW.isoformat(), 'received_at': NOW.isoformat(),
            'body_bytes': len(raw), 'body_sha256': sha256(raw).hexdigest(),
            'body_utf8': raw.decode()}

    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz or UTC)

    monkeypatch.setenv('LIVE_TRADING_ENABLED', 'false')
    monkeypatch.setattr(api, 'datetime', Clock)
    monkeypatch.setattr(delivery, 'utc_now', lambda: NOW)
    monkeypatch.setattr(delivery, '_get', fetch)
    return SimpleNamespace(private=private, current=current, prediction=prediction, signals=signals,
        bodies=bodies, requests=requests, documents=[previous, current, completion, prediction,
                                                    signals, admission])


def verify(runtime):
    return delivery.verify_public_delivery(runtime.private, runtime.current,
        runtime.prediction, runtime.signals, REVISION)


def test_actual_public_projection_is_bound_to_prior_private_acceptance(runtime):
    original = deepcopy(runtime.bodies)
    result = verify(runtime)
    assert result['production_publication_verified'] is True
    assert result['private_transition_sha256'] == runtime.private['evidence_sha256']
    assert runtime.requests == [delivery.DEPLOYMENT, delivery.CATALOG, delivery.VERSIONS,
                                delivery.SIGNALS, delivery.CATALOG]
    assert runtime.bodies == original
    for response in result['responses']:
        raw = response['body_utf8'].encode()
        assert len(raw) == response['body_bytes']
        assert sha256(raw).hexdigest() == response['body_sha256']
    for key in ('whole_step1_accepted', 'browser_verified', 'candidate_validation_verified',
                'etf_forward_verified', 'paper_forward_ledger_verified'):
        assert result[key] is False


@pytest.mark.parametrize('damage', ['version', 'epoch', 'manifest', 'active', 'old_prediction',
    'prediction_hash', 'signal', 'catalog_hash', 'deployment_sha', 'deployment_ref',
    'stale', 'future', 'false_fresh', 'execution_passed', 'http_error', 'deployment_bool',
    'nested_authority'])
def test_wrong_or_expired_public_delivery_fails_closed(runtime, monkeypatch, damage):
    catalog = runtime.bodies[delivery.CATALOG]
    if damage == 'version':
        catalog['data_version']['version_id'] = '0'*64
    elif damage == 'epoch':
        catalog['data_version']['data_event_id'] = '0'*64
    elif damage == 'manifest':
        versions = runtime.bodies[delivery.VERSIONS]['catalog']
        versions['versions'][0]['manifest_sha256'] = '0'*64
        runtime.bodies[delivery.VERSIONS]['catalog'] = resign(versions)
    elif damage == 'active':
        catalog['active_model'] = None
        catalog['models'][0]['state'] = 'RETIRED'
    elif damage == 'old_prediction':
        catalog['auto_predict']['current_prediction_id'] = None
    elif damage == 'prediction_hash':
        catalog['auto_predict']['predictions'][0]['evidence_sha256'] = '0'*64
    elif damage == 'signal':
        manifest = runtime.bodies[delivery.SIGNALS]['manifest']
        manifest['generated_at'] = '2026-10-27T10:04:00+00:00'
        runtime.bodies[delivery.SIGNALS]['manifest'] = resign(manifest)
    elif damage == 'catalog_hash':
        catalog['evidence_sha256'] = '0'*64
    elif damage == 'deployment_sha':
        runtime.bodies[delivery.DEPLOYMENT]['git_commit_sha'] = '0'*40
    elif damage == 'deployment_ref':
        runtime.bodies[delivery.DEPLOYMENT]['git_commit_ref'] = 'codex/preview'
    elif damage == 'deployment_bool':
        runtime.bodies[delivery.DEPLOYMENT]['external_order_transmission'] = 0
    elif damage == 'nested_authority':
        versions = runtime.bodies[delivery.VERSIONS]['catalog']
        versions['live_trading_enabled'] = 0
        runtime.bodies[delivery.VERSIONS]['catalog'] = resign(versions)
    elif damage in {'stale', 'future'}:
        stamp = NOW+timedelta(seconds=1) if damage == 'future' else NOW-timedelta(seconds=721)
        catalog['worker']['heartbeat_at'] = stamp.isoformat()
        catalog['published_at'] = stamp.isoformat()
    elif damage == 'false_fresh':
        catalog['worker_fresh'] = False
    elif damage == 'execution_passed':
        monkeypatch.setattr(delivery, 'utc_now', lambda: datetime(2026, 10, 28, 2, tzinfo=UTC))
    else:
        monkeypatch.setattr(delivery, '_get', lambda *args: (_ for _ in ()).throw(
            ValueError('PUBLIC_DELIVERY_HTTP_FETCH_FAILED')))
    if damage != 'catalog_hash':
        runtime.bodies[delivery.CATALOG] = reseal_catalog(catalog)
    with pytest.raises(ValueError):
        verify(runtime)


def test_even_a_valid_same_version_different_epoch_between_requests_is_blocked(runtime):
    before = runtime.bodies[delivery.CATALOG]
    after = deepcopy(before)
    after['data_version']['data_event_id'] = '0'*64
    after = reseal_catalog(after)
    runtime.bodies[delivery.CATALOG] = [before, after]
    with pytest.raises(ValueError, match='PUBLICATION_ROUND_CHANGED'):
        verify(runtime)


def test_a_different_heartbeat_does_not_require_equal_whole_catalog_bytes(runtime):
    before = runtime.bodies[delivery.CATALOG]
    after = deepcopy(before)
    after['published_at'] = NOW.isoformat()
    after['worker']['heartbeat_at'] = NOW.isoformat()
    runtime.bodies[delivery.CATALOG] = [before, reseal_catalog(after)]
    assert verify(runtime)['production_publication_verified'] is True


def test_no_network_before_private_c_pass_or_with_unauthorized_live_mode(runtime, monkeypatch):
    runtime.private['state'] = 'BLOCKED'
    runtime.private = resign(runtime.private)
    with pytest.raises(ValueError, match='PRIVATE_C_ACCEPTANCE_REQUIRED'):
        verify(runtime)
    assert not runtime.requests
    monkeypatch.setenv('LIVE_TRADING_ENABLED', 'true')
    with pytest.raises(ValueError, match='CONFIGURATION_INVALID'):
        verify(runtime)
    assert not runtime.requests


class Response:
    def __init__(self, raw=b'{}', *, status=200, path=delivery.CATALOG, headers=None):
        self.raw, self.status, self.path = raw, status, path
        self.headers = headers if headers is not None else {
            'Content-Type': 'application/json', 'Cache-Control': 'no-store'}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def geturl(self):
        return delivery.ORIGIN+self.path

    def read1(self, size):
        value, self.raw = self.raw[:size], self.raw[size:]
        return value


@pytest.mark.parametrize('damage', ['oversize', 'length', 'incomplete', 'cache', 'html',
                                   'gzip', 'status', 'redirect', 'network', 'deadline', 'nan'])
def test_get_is_fixed_bounded_uncached_and_fails_closed(monkeypatch, damage):
    response = Response()
    if damage == 'oversize':
        monkeypatch.setitem(delivery.LIMITS, delivery.CATALOG, 2)
        response.raw = b'{} '
    elif damage == 'length':
        response.headers['Content-Length'] = '99999999'
    elif damage == 'incomplete':
        response.headers['Content-Length'] = '3'
    elif damage == 'cache':
        response.headers['Cache-Control'] = 'public,max-age=60'
    elif damage == 'html':
        response.headers['Content-Type'] = 'text/html'
    elif damage == 'gzip':
        response.headers['Content-Encoding'] = 'gzip'
    elif damage == 'status':
        response.status = 503
    elif damage == 'redirect':
        response.path = '/elsewhere'
    elif damage == 'nan':
        response.raw = b'{"value":NaN}'
    def open_response(*args, **kwargs):
        if damage == 'network':
            raise OSError('private internal details')
        return response
    monkeypatch.setattr(delivery, 'build_opener', lambda *_: SimpleNamespace(open=open_response))
    with pytest.raises(ValueError):
        delivery._get(delivery.CATALOG, delivery.time.monotonic()+(
            -1 if damage == 'deadline' else 30))


def test_transport_sends_only_get_without_credentials_and_records_actual_bytes(monkeypatch):
    calls = []
    response = Response(raw=b'{ "x": 1 }\n')
    def open_response(request, **kwargs):
        calls.append((request, kwargs))
        return response
    monkeypatch.setattr(delivery, 'build_opener', lambda *_: SimpleNamespace(open=open_response))
    value = delivery._get(delivery.CATALOG, delivery.time.monotonic()+30)
    request, options = calls[0]
    assert request.get_method() == 'GET' and request.full_url == delivery.ORIGIN+delivery.CATALOG
    assert request.data is None
    assert not {'authorization', 'cookie'} & {key.lower() for key in request.headers}
    assert 0 < options['timeout'] <= 10
    assert value['body_utf8'] == '{ "x": 1 }\n'
    assert value['body_sha256'] == sha256(b'{ "x": 1 }\n').hexdigest()
    with pytest.raises(ValueError, match='PATH_NOT_ALLOWED'):
        delivery._get('/api/research/online/actions', delivery.time.monotonic()+30)


@pytest.mark.parametrize('scheme', ['http', 'https'])
def test_discovered_credential_free_proxy_is_used_only_for_fixed_https_target(monkeypatch, scheme):
    route = scheme+'://proxy.invalid:8080/'
    monkeypatch.setattr(delivery, 'getproxies', lambda: {
        'https': route, 'http': 'http://ignored.invalid', 'all': 'socks5://ignored.invalid'})
    calls = []
    def opener(*handlers):
        calls.append(handlers)
        return SimpleNamespace(open=lambda request, **kwargs: Response())
    monkeypatch.setattr(delivery, 'build_opener', opener)
    value = delivery._get(delivery.CATALOG, delivery.time.monotonic()+30)
    proxy, redirect = calls[0]
    assert proxy.proxies == {'https': route}
    assert isinstance(redirect, delivery._NoRedirect)
    assert value['proxy_configuration_mode'] == 'CONFIGURED_CREDENTIAL_FREE_PROXY'
    assert route not in json.dumps(value) and 'proxy.invalid' not in json.dumps(value)
    assert redirect.redirect_request(None, None, 302, '', None, 'https://elsewhere.invalid') is None


def test_no_discovered_https_proxy_uses_direct_without_promoting_http_or_all(monkeypatch):
    monkeypatch.setattr(delivery, 'getproxies', lambda: {
        'http': 'http://ignored.invalid', 'all': 'socks5://ignored.invalid'})
    proxy, mode = delivery._proxy_handler()
    assert proxy.proxies == {} and mode == 'DIRECT'


@pytest.mark.parametrize('route', [
    '', None, 'proxy.invalid:8080', 'socks5://proxy.invalid:1080',
    'http://TEST_ONLY_USER:TEST_ONLY_PASSWORD@proxy.invalid',
    'http://TEST_ONLY_USER@proxy.invalid', 'http://proxy.invalid?query',
    'http://proxy.invalid?', 'http://proxy.invalid#fragment', 'http://proxy.invalid#',
    'http://proxy.invalid/not-root', 'http:///no-host', 'https://',
    'http://proxy.invalid:99999', 'http://proxy.invalid:bad',
    ' http://proxy.invalid', 'http://proxy.invalid\n',
    'http://%75ser%3Apassword%40proxy.invalid', 'http://proxy.invalid\\hidden',
])
def test_invalid_proxy_discovery_fails_closed_without_open_or_address_disclosure(
        monkeypatch, route):
    monkeypatch.setattr(delivery, 'getproxies', lambda: {'https': route})
    monkeypatch.setattr(delivery, 'build_opener', lambda *_: pytest.fail('unexpected network'))
    with pytest.raises(ValueError, match='^PUBLIC_DELIVERY_PROXY_CONFIGURATION_INVALID$') as error:
        delivery._get(delivery.CATALOG, delivery.time.monotonic()+30)
    assert error.value.__suppress_context__ is True
    assert 'proxy.invalid' not in str(error.value) and 'TEST_ONLY' not in str(error.value)


def test_proxy_discovery_error_is_sanitized_without_falling_back(monkeypatch):
    def broken():
        raise OSError('TEST_ONLY_PRIVATE_PROXY_CONFIGURATION')
    monkeypatch.setattr(delivery, 'getproxies', broken)
    with pytest.raises(ValueError, match='^PUBLIC_DELIVERY_PROXY_CONFIGURATION_INVALID$') as error:
        delivery._proxy_handler()
    assert error.value.__suppress_context__ is True
    assert 'TEST_ONLY' not in str(error.value)


def test_configured_proxy_keeps_urllib_bypass_semantics(monkeypatch):
    import urllib.request

    monkeypatch.setattr(delivery, 'getproxies', lambda: {'https': 'http://proxy.invalid:8080'})
    monkeypatch.setattr(urllib.request, 'proxy_bypass', lambda host: True)
    proxy, mode = delivery._proxy_handler()
    request = delivery.Request(delivery.ORIGIN+delivery.CATALOG, method='GET')
    assert proxy.proxy_open(request, proxy.proxies['https'], 'https') is None
    assert request.full_url == delivery.ORIGIN+delivery.CATALOG
    assert not request.has_header('Proxy-authorization')
    assert mode == 'CONFIGURED_CREDENTIAL_FREE_PROXY'
