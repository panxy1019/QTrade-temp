import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta
from hashlib import sha256
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
                            report=tmp_path/'report.json', requests=[], damage=None)
    monkeypatch.setattr(series_api, '_index', lambda _: state.index)
    api_objects = dict(objects)
    monkeypatch.setattr(series_api, '_object', lambda digest: api_objects[digest])
    monkeypatch.setattr(catalog_api, 'catalog', lambda: state.catalog)

    def transport(resource, deadline, version):
        state.requests.append((resource, version))
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
            value = series_api.bars(Response(), symbol, freq, None, None, version, 2000)
        else:
            value = json.loads(state.objects[resource[6:]])
        value = deepcopy(value)
        if state.damage:
            state.damage(resource, value)
        raw = (state.objects[resource[6:]] if resource.startswith('shard:')
               else json.dumps(value).encode())
        return {'url': module.ORIGIN+'/api/deployment' if resource == 'deployment' else
            module._route(resource)[0]+('&version='+version if version else ''),
            'status_code': 200, 'content_type': 'application/json', 'cache_control': 'no-store',
            'requested_at': NOW.isoformat(), 'received_at': NOW.isoformat(),
            'body_bytes': len(raw), 'body_sha256': sha256(raw).hexdigest(),
            'body_utf8': raw.decode(), 'proxy_configuration_mode': 'SIMULATED'}
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

    def damaged(resource, deadline, version):
        capture = transport(resource, deadline, version)
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
    result = module._get('shard:'+'a'*64, module.time.monotonic()+30)
    request, options = calls[0]
    assert request.full_url == module.RAW+'unified-market/objects/'+'a'*64+'.json'
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
