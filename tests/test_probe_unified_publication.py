import json
import os
import subprocess
import sys
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha1, sha256
from types import SimpleNamespace
from urllib.error import HTTPError

import pytest
from fastapi import Response

from qtrade.api import unified_data as catalog_api
from qtrade.api import unified_market as series_api
from qtrade.instruments.observed import SCHEMA, instrument_id
from qtrade.market_data.sina_incremental import PRICE_BASIS
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research import unified_market as contract
from scripts import probe_unified_publication as module

NOW = datetime(2026, 10, 27, 3, 45, tzinfo=UTC)
REVISION = 'b'*40
VERSION = 'a'*64
COMMIT = 'c'*40


def resign(value):
    return signed({key: item for key, item in value.items() if key != 'evidence_sha256'})


@pytest.fixture
def runtime(tmp_path, monkeypatch):
    class Clock(datetime):
        @classmethod
        def now(cls, tz=None):
            return NOW.astimezone(tz or UTC)

    monkeypatch.setattr(module, 'utc_now', lambda: NOW)
    monkeypatch.setattr(catalog_api, 'datetime', Clock)
    monkeypatch.setattr(series_api, 'datetime', Clock)
    monkeypatch.setenv('LIVE_TRADING_ENABLED', 'false')
    calendar = Calendar(module.delivery.REPO/'configs/observer_calendar_2026.json')
    published = NOW-timedelta(seconds=30)
    symbols = [f'{number:06}' for number in range(600000, 601497)]
    symbols += ['000032', '688365', '512580']
    rows, entries, objects = [], [], {}
    ends = {freq: calendar.expected(NOW, 'daily' if freq == 'day' else 'minute').isoformat()
            for freq in ('day', '5min')}
    for number, symbol in enumerate(symbols):
        kind = 'ETF' if symbol == '512580' else 'STOCK'
        identity = instrument_id(symbol, kind)
        row = {'symbol': symbol, 'instrument_id': identity, 'security_type': kind,
            'exchange': identity.split('.')[0], 'universe_version': VERSION, 'name': symbol,
            'available_at': None, 'in_target': True, 'status': 'ACTIVE_OBSERVED', 'issues': [],
            'frequencies': {freq: {'state': 'PLANNED'} for freq in ends}, 'incremental': {}}
        for freq in ends:
            observed = ends[freq] if number < 1496 else None
            row['incremental'][freq] = {'state': 'CONTENT_COVERAGE_VERIFIED' if observed
                else 'PLANNED', 'independent_source_reconciled': False,
                'historical_pit_certified': False, 'bar_count': 1 if observed else 0,
                'expected_end': ends[freq], 'observed_end': observed, 'observed_start': observed,
                'last_success_at': published.isoformat() if observed else None}
            entry = {key: row[key] for key in ('symbol', 'instrument_id', 'security_type', 'name')}
            entry.update(frequency=freq, state='NO_DATA', issues=[], shards=[])
            # Samples include current latest data with older gaps, an ETF gap and no data.
            if (symbol == '000032' or symbol == '512580' and freq == '5min'):
                stamp = ends[freq] if symbol == '000032' else '2026-10-27T09:35:00+08:00'
                bar = {'symbol': symbol, 'exchange': row['exchange'], 'frequency': freq,
                    'timestamp': stamp, 'trading_day': stamp[:10], 'open': '10', 'high': '11',
                    'low': '9', 'close': '10', 'volume': '100', 'amount': None, 'vwap': None,
                    'amount_basis': 'VENDOR_CLAIM_UNVERIFIED', 'price_basis': PRICE_BASIS,
                    'historical_available_at': None}
                shard = signed({**AUTHORITY, 'schema_version': 'qtrade.unified-market-shard.v1',
                    'universe_version': VERSION, 'symbol': symbol, 'security_type': kind,
                    'instrument_id': identity, 'frequency': freq, 'price_basis': PRICE_BASIS,
                    'bars': [bar], 'source_batches': [{'batch_id': '1'*32,
                        'manifest_sha256': '2'*64, 'raw_sha256': '3'*64,
                        'normalized_sha256': '4'*64}]})
                raw = contract.encoded(shard)
                digest = sha256(raw).hexdigest()
                objects[digest] = raw
                entry.update(state='AVAILABLE', shards=[{'sha256': digest,
                    'evidence_sha256': shard['evidence_sha256'], 'bytes': len(raw),
                    'start': stamp, 'end': stamp, 'count': 1}])
            entries.append(entry)
        rows.append(row)
    master = signed({**AUTHORITY, 'schema_version': SCHEMA, 'universe_version': VERSION,
        'master_count': 1500, 'target_actual': 1500, 'target_by_type': {'STOCK': 1499, 'ETF': 1},
        'historical_pit_certified_count': 0, 'securities': rows,
        'frequency_counts': {freq: {'PLANNED': 1500} for freq in ends},
        'incremental_counts': {freq: {'CONTENT_COVERAGE_VERIFIED': 1496, 'PLANNED': 4}
                               for freq in ends}, 'collection': {'calendar': calendar.document}})
    catalog = signed({**AUTHORITY, 'schema_version': 'qtrade.unified-catalog.v1',
        'built_at': published.isoformat(), 'master': master})
    index = signed({**AUTHORITY, 'schema_version': 'qtrade.unified-market-index.v1',
        'universe_version': VERSION, 'master_sha256': master['evidence_sha256'],
        'published_at': published.isoformat(), 'calendar': calendar.document,
        'windows': {freq: contract.sessions(calendar, calendar.expected(published,
            'daily' if freq == 'day' else 'minute').date(), 20 if freq == 'day' else 3)
            for freq in ends}, 'entries': entries, 'total_bytes': sum(map(len, objects.values())),
        'total_files': len(objects), 'limitations': contract.LIMITATIONS,
        'implementation_sources': {'scripts/publish_unified_market.py': '5'*64,
                                  'src/qtrade/research/unified_market.py': '6'*64}})
    deployment = {'classification': 'PUBLIC_BUILD_PROVENANCE', 'provider': 'VERCEL',
        'status': 'AVAILABLE', 'target_environment': 'production', 'git_commit_sha': REVISION,
        'git_commit_ref': 'main', 'external_order_transmission': False}
    state = SimpleNamespace(index=index, catalog=catalog, deployment=deployment, objects=objects,
        report=tmp_path/'report.json', requests=[], pins=[], captures=[], damage=None)
    def pinned_index(revision):
        assert revision == COMMIT
        return state.index
    monkeypatch.setattr(series_api, '_index', pinned_index)
    api_objects = dict(objects)
    def pinned_object(digest, revision):
        assert revision == COMMIT
        return api_objects[digest]
    monkeypatch.setattr(series_api, '_object', pinned_object)
    monkeypatch.setattr(catalog_api, 'catalog', lambda: state.catalog)

    def catalog_raw():
        return json.dumps(state.catalog).encode()

    def catalog_source():
        raw = catalog_raw()
        return {'commit_sha': COMMIT,
                'git_blob_sha1': sha1(b'blob '+str(len(raw)).encode()+b'\0'+raw).hexdigest(),
                'sha256': sha256(raw).hexdigest(), 'bytes': len(raw)}

    monkeypatch.setattr(catalog_api, '_capture', lambda: (state.catalog, catalog_source()),
                        raising=False)
    state.catalog_source = catalog_source

    def transport(resource, deadline, version, publication_revision):
        state.requests.append((resource, version))
        state.pins.append((resource, publication_revision))
        if resource == 'deployment':
            value = state.deployment
        elif resource == 'index':
            value = state.index
        elif resource == 'catalog':
            value = state.catalog
        elif resource == 'summary':
            value = catalog_api.summary(Response())
        elif resource.startswith('bars:'):
            _, symbol, freq = resource.split(':')
            value = series_api.bars(Response(), symbol, freq, None, None, version, 2000,
                                    publication_revision)
        else:
            value = json.loads(state.objects[resource[6:]])
        value = deepcopy(value)
        if state.damage:
            state.damage(resource, value)
        raw = (state.objects[resource[6:]] if resource.startswith('shard:')
               else catalog_raw() if resource == 'catalog'
               else json.dumps(value).encode())
        url = (module.ORIGIN+'/api/deployment' if resource == 'deployment' else
               module._route(resource, publication_revision)[0])
        capture = {'url': url+('&version='+version if version else ''),
            'status_code': 200, 'content_type': 'application/json', 'cache_control': 'no-store',
            'requested_at': NOW.isoformat(), 'received_at': NOW.isoformat(),
            'body_bytes': len(raw), 'body_sha256': sha256(raw).hexdigest(),
            'body_utf8': raw.decode(), 'proxy_configuration_mode': 'SIMULATED'}
        state.captures.append((resource, capture))
        return capture
    state.transport = transport
    return state


def run(runtime):
    return module.probe(REVISION, runtime.report, _transport=runtime.transport)


def test_fresh_transport_with_partial_and_no_data_has_sample_only_integrity(runtime):
    limits = dict(module.delivery.LIMITS)
    result = run(runtime)
    assert result['transport_mode'] == 'SIMULATED_TRANSPORT'
    assert result['state'] == 'FRESH_TRANSPORT' and result['fresh_publication'] is True
    assert result['target_count'] == 1500 and result['index_entries'] == 3000
    assert result['catalog_source'] == runtime.catalog_source()
    assert result['publication_revision'] == COMMIT
    assert [resource for resource, _ in runtime.requests[:4]] == [
        'deployment', 'summary', 'catalog', 'index']
    assert all(revision == COMMIT for resource, revision in runtime.pins
               if resource in {'catalog', 'index'} or resource.startswith(('bars:', 'shard:')))
    assert result['incremental_freshness']['5min']['status'] == 'PARTIAL'
    assert result['incremental_freshness']['5min']['ready_symbols'] == 1496
    assert {sample['state'] for sample in result['samples']} == {'PARTIAL', 'NO_DATA'}
    assert len(result['verified_shards']) == 3
    assert result['all_series_bytes_verified'] is False
    assert result['whole_step1_accepted'] is False
    assert result['completed_session_cde_accepted'] is False
    assert result['browser_rendered_verified'] is False
    assert all(result[key] == value for key, value in AUTHORITY.items())
    assert limits == module.delivery.LIMITS
    assert json.loads(runtime.report.read_text()) == result
    assert runtime.report.stat().st_size < 100_000
    for capture in result['responses']:
        assert 'body_utf8' not in capture
        assert len(capture['body_sha256']) == 64
        assert capture['body_bytes'] > 0
        assert capture['received_at'] == NOW.isoformat()
    assert len([resource for resource, _ in runtime.requests if resource.startswith('bars:')]) == 5


@pytest.mark.parametrize(('index_age', 'catalog_age', 'fresh'), [
    (180, 1200, True), (181, 30, False), (30, 1201, False)])
def test_independent_freshness_limits_do_not_relabel_coverage(
        runtime, index_age, catalog_age, fresh):
    runtime.index['published_at'] = (NOW-timedelta(seconds=index_age)).isoformat()
    runtime.index = resign(runtime.index)
    runtime.catalog['built_at'] = (NOW-timedelta(seconds=catalog_age)).isoformat()
    runtime.catalog = resign(runtime.catalog)
    result = run(runtime)
    assert result['fresh_publication'] is fresh
    assert result['index_fresh'] is (index_age <= 180)
    assert result['catalog_fresh'] is (catalog_age <= 1200)
    assert result['series_states']['NO_DATA'] == 2997
    assert result['sample_series_integrity_verified'] is True


@pytest.mark.parametrize('damage', ['revision', 'catalog_hash', 'index_hash', 'universe',
    'membership', 'metadata', 'summary_identity', 'summary_coverage', 'api_bars',
    'api_version', 'api_authority', 'api_count', 'shard_checksum'])
def test_wrong_identities_or_bytes_fail_without_report(runtime, damage):
    if damage == 'revision':
        runtime.deployment['git_commit_sha'] = 'c'*40
    elif damage == 'catalog_hash':
        runtime.catalog['master']['target_actual'] = 1499
    elif damage == 'index_hash':
        runtime.index['total_bytes'] += 1
    elif damage == 'universe':
        runtime.index['universe_version'] = 'c'*64
        runtime.index = resign(runtime.index)
    elif damage == 'membership':
        runtime.index['entries'] = [row for row in runtime.index['entries']
                                    if row['symbol'] != '600000']
        runtime.index = resign(runtime.index)
    elif damage == 'metadata':
        for row in runtime.index['entries']:
            if row['symbol'] == '600000':
                row['name'] = 'wrong'
        runtime.index = resign(runtime.index)
    elif damage == 'shard_checksum':
        digest = next(iter(runtime.objects))
        runtime.objects[digest] = runtime.objects[digest].replace(
            b'"close": "10"', b'"close": "11"')
    else:
        def mutate(resource, value):
            if resource == 'summary':
                if damage == 'summary_identity':
                    value['evidence_sha256'] = 'f'*64
                if damage == 'summary_coverage':
                    value['incremental_freshness']['5min']['status'] = 'FRESH'
            if resource.startswith('bars:'):
                if damage == 'api_bars' and value['bars']:
                    value['bars'][0]['close'] = '11'
                if damage == 'api_version':
                    value['index_id'] = 'e'*64
                if damage == 'api_authority':
                    value['live_trading_enabled'] = 0
                if damage == 'api_count':
                    value['count'] += 1
        runtime.damage = mutate
    with pytest.raises((ValueError, KeyError)):
        run(runtime)
    assert not runtime.report.exists()


def test_report_overwrite_or_invalid_configuration_never_fetches(runtime, monkeypatch):
    runtime.report.write_text('preserve this')
    with pytest.raises(ValueError, match='NEW_REPORT_PATH_REQUIRED'):
        run(runtime)
    assert runtime.report.read_text() == 'preserve this' and not runtime.requests
    runtime.report.unlink()
    with pytest.raises(ValueError, match='CONFIGURATION_INVALID'):
        module.probe('bad', runtime.report, _transport=runtime.transport)
    monkeypatch.setenv('LIVE_TRADING_ENABLED', 'true')
    with pytest.raises(ValueError, match='CONFIGURATION_INVALID'):
        run(runtime)
    assert not runtime.requests


@pytest.mark.parametrize('damage', ['capture_hash', 'capture_size', 'future_capture',
                                   'naive_capture', 'inverted_capture', 'api_clock'])
def test_capture_identity_and_real_clock_bounds_fail_without_receipt(runtime, damage):
    transport = runtime.transport

    def damaged(resource, deadline, version, publication_revision):
        capture = transport(resource, deadline, version, publication_revision)
        if damage == 'capture_hash':
            capture['body_sha256'] = '0'*64
        elif damage == 'capture_size':
            capture['body_bytes'] += 1
        elif damage == 'future_capture':
            capture['received_at'] = (NOW+timedelta(seconds=1)).isoformat()
        elif damage == 'naive_capture':
            capture['requested_at'] = NOW.replace(tzinfo=None).isoformat()
        elif damage == 'inverted_capture':
            capture['received_at'] = (NOW-timedelta(seconds=1)).isoformat()
        elif resource == 'summary':
            value = json.loads(capture['body_utf8'])
            value['checked_at'] = (NOW+timedelta(seconds=61)).isoformat()
            raw = json.dumps(value).encode()
            capture.update(body_utf8=raw.decode(), body_bytes=len(raw),
                           body_sha256=sha256(raw).hexdigest())
        return capture
    with pytest.raises(ValueError, match='CAPTURE_|API_CLOCK_'):
        module.probe(REVISION, runtime.report, _transport=damaged)
    assert not runtime.report.exists()


def test_deployment_change_during_probe_requires_rerun_without_receipt(runtime):
    calls = 0

    def mutate(resource, value):
        nonlocal calls
        if resource == 'deployment':
            calls += 1
            if calls == 2:
                value['git_commit_sha'] = 'c'*40
    runtime.damage = mutate
    with pytest.raises(ValueError, match='DEPLOYMENT_DRIFT_RETRY_REQUIRED'):
        run(runtime)
    assert not runtime.report.exists()


class TransportResponse:
    def __init__(self, raw=b'{}', *, url=None, headers=None, status=200):
        self.raw, self.url, self.status = raw, url, status
        self.headers = headers or {'Content-Type': 'application/json', 'Cache-Control': 'no-store'}

    def __enter__(self):
        return self

    def __exit__(self, *_):
        pass

    def geturl(self):
        return self.url

    def read1(self, size):
        chunk, self.raw = self.raw[:size], self.raw[size:]
        return chunk


@pytest.mark.parametrize('damage', ['redirect', 'status', 'length', 'oversize', 'incomplete',
                                   'gzip', 'cache', 'html', 'nan', 'deadline', '409'])
def test_get_rejects_unsafe_or_incomplete_transport(monkeypatch, damage):
    url, _, _ = module._route('summary')
    response = TransportResponse(url=url)
    calls = []
    if damage == 'redirect':
        response.url = 'https://other.invalid/'
    if damage == 'status':
        response.status = 302
    if damage == 'length':
        response.headers['Content-Length'] = '2000001'
    if damage == 'oversize':
        response.raw = b' '*2_000_001
    if damage == 'incomplete':
        response.headers['Content-Length'] = '3'
    if damage == 'gzip':
        response.headers['Content-Encoding'] = 'gzip'
    if damage == 'cache':
        response.headers['Cache-Control'] = 'public'
    if damage == 'html':
        response.headers['Content-Type'] = 'text/html'
    if damage == 'nan':
        response.raw = b'{"x":NaN}'

    def open_response(*args, **kwargs):
        calls.append(args)
        if damage == '409':
            raise HTTPError(url, 409, 'changed', {}, None)
        return response
    monkeypatch.setattr(module, 'build_opener', lambda *_: SimpleNamespace(open=open_response))
    with pytest.raises(ValueError,
                       match='VERSION_DRIFT_RETRY_REQUIRED' if damage == '409' else None):
        module._get('summary', module.time.monotonic()+(-1 if damage == 'deadline' else 30))
    assert len(calls) == (0 if damage == 'deadline' else 1)


def test_get_uses_approved_routes_proxy_redirect_guard_get_and_actual_hashes(monkeypatch):
    calls = []

    def opener(*handlers):
        assert isinstance(handlers[1], module.delivery._NoRedirect)
        def open_response(request, **kwargs):
            calls.append((request, kwargs))
            return TransportResponse(b'{ "x": 1 }\n', url=request.full_url,
                                     headers={'Content-Type': 'text/plain'})
        return SimpleNamespace(open=open_response)
    monkeypatch.setattr(module, 'build_opener', opener)
    result = module._get('shard:'+'a'*64, module.time.monotonic()+30,
                         publication_revision=COMMIT)
    request, options = calls[0]
    assert request.full_url == module.RAW+COMMIT+'/unified-market/objects/'+'a'*64+'.json'
    assert request.get_method() == 'GET' and request.data is None
    assert not {'authorization', 'cookie'} & {key.lower() for key in request.headers}
    assert 0 < options['timeout'] <= 10
    assert result['body_sha256'] == sha256(b'{ "x": 1 }\n').hexdigest()
    for forbidden in ('https://other.invalid', '../unified.json', 'shard:../x',
                      'bars:600000:day', '/api/unified/summary'):
        with pytest.raises(ValueError, match='RESOURCE_NOT_ALLOWED'):
            module._get(forbidden, module.time.monotonic()+30)
    assert len(calls) == 1


def test_credentials_in_discovered_proxy_fail_before_network(monkeypatch):
    monkeypatch.setattr(module.delivery, 'getproxies', lambda: {
        'https': 'http://TEST_ONLY_USER:TEST_ONLY_PASSWORD@proxy.invalid'})
    monkeypatch.setattr(module, 'build_opener', lambda *_: pytest.fail('unexpected network'))
    with pytest.raises(ValueError, match='PUBLIC_DELIVERY_PROXY_CONFIGURATION_INVALID'):
        module._get('summary', module.time.monotonic()+30)


@pytest.mark.parametrize('damage', ['missing', 'extra', 'not_dict', 'sha1_type', 'sha1_length',
    'sha1_uppercase', 'sha1_path', 'sha256_type', 'sha256_length', 'sha256_uppercase',
    'bytes_bool', 'bytes_float', 'bytes_string', 'bytes_zero', 'bytes_negative', 'bytes_oversize',
    'commit_missing', 'commit_type', 'commit_length', 'commit_uppercase', 'commit_path',
    'commit_zero'])
def test_summary_source_must_be_exact_and_bounded_before_catalog_request(runtime, damage):
    def mutate(resource, value):
        if resource != 'summary':
            return
        source = value['catalog_source']
        if damage == 'missing':
            del value['catalog_source']
        elif damage == 'extra':
            source['url'] = 'https://unapproved.invalid/'
        elif damage == 'not_dict':
            value['catalog_source'] = [source]
        elif damage == 'commit_missing':
            del source['commit_sha']
        elif damage.startswith('commit_'):
            source['commit_sha'] = {'commit_type': None, 'commit_length': 'a'*39,
                'commit_uppercase': 'A'*40, 'commit_path': '../main', 'commit_zero': '0'*40}[damage]
        elif damage.startswith('sha1_'):
            source['git_blob_sha1'] = {'sha1_type': 1, 'sha1_length': 'a'*39,
                                     'sha1_uppercase': 'A'*40, 'sha1_path': '../private'}[damage]
        elif damage.startswith('sha256_'):
            source['sha256'] = {'sha256_type': None, 'sha256_length': 'a'*63,
                                'sha256_uppercase': 'A'*64}[damage]
        else:
            source['bytes'] = {'bytes_bool': True, 'bytes_float': 1.0, 'bytes_string': '1',
                'bytes_zero': 0, 'bytes_negative': -1, 'bytes_oversize': 12_000_001}[damage]
    runtime.damage = mutate
    with pytest.raises(ValueError, match='CATALOG_SOURCE_INVALID'):
        run(runtime)
    assert [resource for resource, _ in runtime.requests] == ['deployment', 'summary']
    assert not runtime.report.exists()


@pytest.mark.parametrize('damage', ['sha1', 'sha256', 'length', 'reserialized_bytes',
                                   'wrong_document'])
def test_catalog_bytes_must_match_all_three_source_identities(runtime, damage):
    transport = runtime.transport

    def damaged(resource, deadline, version, publication_revision):
        capture = transport(resource, deadline, version, publication_revision)
        value = json.loads(capture['body_utf8'])
        if resource == 'summary' and damage in {'sha1', 'sha256', 'length'}:
            field = {'sha1': 'git_blob_sha1', 'sha256': 'sha256', 'length': 'bytes'}[damage]
            value['catalog_source'][field] = (capture['body_bytes'] if damage == 'length'
                                               else '0'*(40 if damage == 'sha1' else 64))
            raw = json.dumps(value).encode()
        elif resource == 'catalog' and damage == 'reserialized_bytes':
            raw = json.dumps(value, indent=2).encode()
        elif resource == 'catalog' and damage == 'wrong_document':
            raw = json.dumps({'not_the_original_signed_catalog': True}).encode()
            capture['content_type'] = 'application/json'
        else:
            return capture
        capture.update(body_utf8=raw.decode(), body_bytes=len(raw),
                       body_sha256=sha256(raw).hexdigest())
        return capture
    with pytest.raises(ValueError, match='CATALOG_SOURCE_BYTES_MISMATCH'):
        module.probe(REVISION, runtime.report, _transport=damaged)
    assert not runtime.report.exists()
    assert ('catalog', COMMIT) in runtime.pins


@pytest.mark.parametrize('resource', ['catalog', 'index', 'bars:000032:5min', 'shard:'+'a'*64])
def test_fixed_commit_get_never_uses_rest_or_unpinned_main(monkeypatch, resource):
    calls = []

    def opener(*handlers):
        assert isinstance(handlers[1], module.delivery._NoRedirect)
        def open_response(request, **kwargs):
            calls.append(request)
            return TransportResponse(b'{ "x": 1 }\n', url=request.full_url,
                headers={'Content-Type': 'application/json', 'Cache-Control': 'no-store'})
        return SimpleNamespace(open=open_response)
    monkeypatch.setattr(module, 'build_opener', opener)
    result = module._get(resource, module.time.monotonic()+30,
                         'a'*64 if resource.startswith('bars:') else None, COMMIT)
    request, = calls
    if resource.startswith('shard:'):
        assert request.full_url == module.RAW+COMMIT+'/unified-market/objects/'+'a'*64+'.json'
    elif resource in {'catalog', 'index'}:
        assert request.full_url == module.RAW+COMMIT+'/'+(
            'unified.json' if resource == 'catalog' else 'unified-market.json')
    else:
        assert 'publication_revision='+COMMIT in request.full_url
        assert 'version='+'a'*64 in request.full_url
    assert 'api.github.com' not in request.full_url and '/main/' not in request.full_url
    assert request.get_header('Accept') == 'application/json'
    assert request.get_method() == 'GET' and request.data is None
    assert not {'authorization', 'cookie'} & {key.lower() for key in request.headers}
    assert result['body_sha256'] == sha256(b'{ "x": 1 }\n').hexdigest()


@pytest.mark.parametrize('damage', ['403', '429', '404', 'network', 'wrong_vendor_media', 'html',
                                   'binary', 'redirect', 'oversize_header'])
def test_commit_transport_errors_are_strict_without_auth_or_fallback(monkeypatch, damage):
    resource = 'catalog'
    url, _, _ = module._route(resource, COMMIT)
    response = TransportResponse(url=url, headers={'Content-Type': 'application/json'})
    calls = []
    if damage == 'wrong_vendor_media':
        response.headers['Content-Type'] = 'application/vnd.github+json'
    elif damage == 'html':
        response.headers['Content-Type'] = 'text/html'
    elif damage == 'binary':
        response.headers['Content-Type'] = 'application/octet-stream'
    elif damage == 'redirect':
        response.url = 'https://unapproved.invalid/'
    elif damage == 'oversize_header':
        response.headers['Content-Length'] = '12000001'

    def open_response(request, **kwargs):
        calls.append(request)
        if damage.isdigit():
            raise HTTPError(url, int(damage), 'TEST_ONLY_PRIVATE_ERROR', {}, None)
        if damage == 'network':
            raise OSError('TEST_ONLY_PRIVATE_ERROR')
        return response
    monkeypatch.setattr(module, 'build_opener', lambda *_: SimpleNamespace(open=open_response))
    with pytest.raises(ValueError) as error:
        module._get(resource, module.time.monotonic()+30, publication_revision=COMMIT)
    assert 'TEST_ONLY_PRIVATE_ERROR' not in str(error.value)
    assert len(calls) == 1
    assert not {'authorization', 'cookie'} & {key.lower() for key in calls[0].headers}
    with pytest.raises(ValueError, match='PUBLICATION_REVISION_INVALID'):
        module._get('catalog', module.time.monotonic()+30)
    with pytest.raises(ValueError, match='RESOURCE_NOT_ALLOWED'):
        module._get('https://unapproved.invalid/arbitrary', module.time.monotonic()+30)
    assert len(calls) == 1


@pytest.mark.parametrize('revision', [None, '', 'main', 'a'*39, 'a'*41, 'A'*40, '0'*40,
                                     '../main', 'a'*40+'?private=1', 'https://other.invalid'])
@pytest.mark.parametrize('resource', ['catalog', 'index', 'bars:000032:5min', 'shard:'+'a'*64])
def test_commit_path_injection_rejected_before_network(monkeypatch, revision, resource):
    monkeypatch.setattr(module, 'build_opener', lambda *_: pytest.fail('unexpected network'))
    with pytest.raises(ValueError, match='PUBLICATION_REVISION_INVALID'):
        module._get(resource, module.time.monotonic()+30,
                    'a'*64 if resource.startswith('bars:') else None, revision)


@pytest.mark.parametrize('damage', ['missing', 'different', 'wrong_type'])
def test_every_series_must_return_the_requested_publication_commit(runtime, damage):
    def mutate(resource, value):
        if resource.startswith('bars:'):
            if damage == 'missing':
                del value['publication_revision']
            else:
                value['publication_revision'] = 'd'*40 if damage == 'different' else False
    runtime.damage = mutate
    with pytest.raises(ValueError, match='PUBLICATION_REVISION_DRIFT_RETRY_REQUIRED'):
        run(runtime)
    assert not runtime.report.exists()


def test_forged_well_formed_summary_commit_never_falls_back_to_main(runtime, monkeypatch):
    def mutate(resource, value):
        if resource == 'summary':
            value['catalog_source']['commit_sha'] = 'd'*40
    runtime.damage = mutate
    transport = runtime.transport

    def unavailable(resource, deadline, version, publication_revision):
        if resource == 'catalog':
            return module._get(resource, deadline, version, publication_revision)
        return transport(resource, deadline, version, publication_revision)

    urls = []
    def fail(request, **kwargs):
        urls.append(request.full_url)
        raise OSError('TEST_ONLY_UNAVAILABLE_COMMIT')
    monkeypatch.setattr(module, 'build_opener', lambda *_: SimpleNamespace(open=fail))
    with pytest.raises(ValueError, match='HTTP_FETCH_FAILED'):
        module.probe(REVISION, runtime.report, _transport=unavailable)
    assert urls == [module.RAW+'d'*40+'/unified.json']
    assert not runtime.report.exists()


def test_fresh_api_processes_keep_one_commit_when_current_heads_differ(runtime, tmp_path):
    run(runtime)
    payload = tmp_path/'snapshot.json'
    payload.write_text(json.dumps({'captures': dict(runtime.captures), 'index': runtime.index,
        'objects': {key: value.decode() for key, value in runtime.objects.items()},
        'clock': NOW.isoformat(), 'commit': COMMIT, 'revision': REVISION}))
    code = '''
import json, sys
from datetime import UTC, datetime
from hashlib import sha256
from pathlib import Path
from fastapi import Response
from qtrade.api import unified_market as api
from qtrade.api import unified_source
from scripts import probe_unified_publication as probe
data = json.loads(Path(sys.argv[1]).read_text())
now = datetime.fromisoformat(data['clock'])
class Clock(datetime):
    @classmethod
    def now(cls, tz=None):
        return now.astimezone(tz or UTC)
api.datetime = Clock
probe.utc_now = lambda: now
# Each independent API process sees a different mutable head, while the client
# pins the summary's commit. Any accidental default-head read must fail.
unified_source.current_revision = lambda: sys.argv[3]
api.current_revision = lambda: sys.argv[3]
def index(revision):
    assert revision == data['commit']
    return data['index']
api._index = index
def object_bytes(digest, revision):
    assert revision == data['commit']
    return data['objects'][digest].encode()
api._object = object_bytes
def transport(resource, deadline, version, publication_revision):
    if resource in {'catalog', 'index'} or resource.startswith(('bars:', 'shard:')):
        assert publication_revision == data['commit']
    capture = dict(data['captures'][resource])
    if resource.startswith('bars:'):
        _, symbol, frequency = resource.split(':')
        value = api.bars(Response(), symbol, frequency, None, None, version, 2000,
                         publication_revision)
        raw = json.dumps(value).encode()
        capture.update(body_utf8=raw.decode(), body_bytes=len(raw),
                       body_sha256=sha256(raw).hexdigest())
    return capture
result = probe.probe(data['revision'], Path(sys.argv[2]), _transport=transport)
print(json.dumps({'publication_revision': result['publication_revision'],
                  'index_id': result['index_id'], 'mode': result['transport_mode']}))
'''
    results = []
    for number, head in enumerate(('d'*40, 'e'*40)):
        completed = subprocess.run([sys.executable, '-c', code, str(payload),
            str(tmp_path/f'child-{number}.json'), head], capture_output=True, text=True, timeout=30,
            cwd=module.delivery.REPO, env={**os.environ, 'LIVE_TRADING_ENABLED': 'false',
                'PYTHONPATH': os.pathsep.join((str(module.delivery.REPO/'src'),
                                              str(module.delivery.REPO)))})
        assert completed.returncode == 0, completed.stderr
        results.append(json.loads(completed.stdout))
    assert results[0] == results[1] == {'publication_revision': COMMIT,
        'index_id': runtime.index['evidence_sha256'], 'mode': 'SIMULATED_TRANSPORT'}


@pytest.mark.parametrize('main_state', ['unavailable', 'lagging'])
def test_new_shards_are_verified_at_pinned_commit_when_main_cannot_serve_them(
        runtime, monkeypatch, main_state):
    transport = runtime.transport
    urls = []

    def open_response(request, **kwargs):
        urls.append(request.full_url)
        if '/main/' in request.full_url:
            if main_state == 'unavailable':
                raise HTTPError(request.full_url, 404, 'not present at stale main', {}, None)
            return TransportResponse(b'{}', url=request.full_url)
        prefix = module.RAW+COMMIT+'/unified-market/objects/'
        assert request.full_url.startswith(prefix)
        digest = request.full_url[len(prefix):-5]
        return TransportResponse(runtime.objects[digest], url=request.full_url)

    monkeypatch.setattr(module, 'build_opener', lambda *_: SimpleNamespace(open=open_response))

    def fixed_shards(resource, deadline, version, publication_revision):
        if resource.startswith('shard:'):
            return module._get(resource, deadline, version, publication_revision)
        return transport(resource, deadline, version, publication_revision)
    report = module.probe(REVISION, runtime.report, _transport=fixed_shards)
    assert report['sample_series_integrity_verified'] is True
    assert report['transport_mode'] == 'SIMULATED_TRANSPORT'
    assert len(urls) == 3
    assert all('/'+COMMIT+'/' in url and '/main/' not in url for url in urls)


def test_unavailable_shard_commit_never_falls_back_to_main(monkeypatch):
    urls = []

    def unavailable(request, **kwargs):
        urls.append(request.full_url)
        raise HTTPError(request.full_url, 404, 'TEST_ONLY_PRIVATE_ERROR', {}, None)
    monkeypatch.setattr(module, 'build_opener', lambda *_: SimpleNamespace(open=unavailable))
    with pytest.raises(ValueError, match='HTTP_FETCH_FAILED'):
        module._get('shard:'+'a'*64, module.time.monotonic()+30,
                    publication_revision='d'*40)
    assert urls == [module.RAW+'d'*40+'/unified-market/objects/'+'a'*64+'.json']


@pytest.mark.parametrize('late_read', ['eof', 'final_data'])
def test_deadline_is_rechecked_after_blocking_final_read(monkeypatch, late_read):
    clock = [0.0]
    url, _, _ = module._route('summary')
    response = TransportResponse(url=url)
    calls = []

    def read1(size):
        calls.append(size)
        if len(calls) == 1:
            if late_read == 'final_data':
                clock[0] = 9.0
            return b'{}'
        clock[0] = 9.0
        return b''
    monkeypatch.setattr(response, 'read1', read1)
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    monkeypatch.setattr(module, 'build_opener',
                        lambda *_: SimpleNamespace(open=lambda *a, **k: response))
    with pytest.raises(ValueError, match='HTTP_DEADLINE_EXCEEDED'):
        module._get('summary', 8.0)


def test_final_deployment_cannot_complete_after_total_probe_deadline(runtime, monkeypatch):
    clock, deployments = [0.0], [0]
    transport = runtime.transport

    def delayed(resource, deadline, version, publication_revision):
        capture = transport(resource, deadline, version, publication_revision)
        if resource == 'deployment':
            deployments[0] += 1
            if deployments[0] == 2:
                clock[0] = module.MAX_SECONDS+1.0
        return capture
    monkeypatch.setattr(module.time, 'monotonic', lambda: clock[0])
    with pytest.raises(ValueError, match='CAPTURE_BUDGET'):
        module.probe(REVISION, runtime.report, _transport=delayed)
    assert not runtime.report.exists()
