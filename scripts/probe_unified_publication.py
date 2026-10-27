"""Bounded, fixed-origin GET probe of unified publication; no C/D/E acceptance."""
from __future__ import annotations

import argparse
import json
import os
import re
import time
from collections import Counter
from datetime import UTC, datetime, timedelta
from hashlib import sha256
from http.client import HTTPException
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, build_opener

from qtrade.api.unified_data import collection_freshness, validate_publication
from qtrade.market_data.sina_incremental import PRICE_BASIS
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.unified_market import INDEX_LIMIT, SHARD_LIMIT, grid, validate_index
from qtrade.research.unified_market import verify_shard_bytes as verify_shard
from scripts import qlib_public_delivery as delivery

ORIGIN = 'https://q-trade-temp.vercel.app'
RAW = 'https://raw.githubusercontent.com/panxy1019/QTrade-observations/main/'
SAMPLES = (('000032', 'day'), ('000032', '5min'), ('688365', '5min'),
           ('512580', '5min'), ('600621', '5min'))
TARGETS = 1500
MAX_SECONDS = 180
MAX_CAPTURE_BYTES = 48_000_000


def utc_now() -> datetime:
    return datetime.now(UTC)


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError('UNIFIED_PROBE_'+code)


def _route(resource: str) -> tuple[str, int, bool]:
    """Only internally named routes; neither URLs nor paths are CLI inputs."""
    if resource == 'summary':
        return ORIGIN+'/api/unified/summary', 2_000_000, True
    if resource in {'index', 'catalog'}:
        name = 'unified-market.json' if resource == 'index' else 'unified.json'
        return (RAW+name+'?v='+str(int(utc_now().timestamp())//60),
                INDEX_LIMIT if resource == 'index' else 12_000_000, False)
    if re.fullmatch('shard:[a-f0-9]{64}', resource):
        return RAW+'unified-market/objects/'+resource[6:]+'.json', SHARD_LIMIT, False
    for symbol, frequency in SAMPLES:
        if resource == f'bars:{symbol}:{frequency}':
            # The pinned index is supplied separately below, never as a user URL.
            return ORIGIN+'/api/unified/bars?'+urlencode({
                'symbol': symbol, 'frequency': frequency, 'limit': 2000}), 2_000_000, True
    raise ValueError('UNIFIED_PROBE_RESOURCE_NOT_ALLOWED')


def _get(resource: str, deadline: float, version: str | None = None) -> dict[str, Any]:
    """Reuse the existing credential-free proxy/no-redirect transport safeguards."""
    if resource == 'deployment':
        require(version is None and delivery.ORIGIN == ORIGIN, 'ORIGIN_INVALID')
        return delivery._get(delivery.DEPLOYMENT, deadline)
    url, bound, api = _route(resource)
    if resource.startswith('bars:'):
        if not isinstance(version, str) or re.fullmatch('[a-f0-9]{64}', version) is None:
            raise ValueError('UNIFIED_PROBE_VERSION_REQUIRED')
        url += '&version='+version
    else:
        require(version is None, 'UNEXPECTED_VERSION')
    remaining = deadline-time.monotonic()
    require(remaining > 0, 'HTTP_DEADLINE_EXCEEDED')
    started = utc_now()
    proxy, mode = delivery._proxy_handler()
    request = Request(url, method='GET', headers={
        'Accept': 'application/json', 'Accept-Encoding': 'identity',
        'Cache-Control': 'no-cache', 'User-Agent': 'QTrade-readonly-unified-probe/1'})
    try:
        with build_opener(proxy, delivery._NoRedirect()).open(
                request, timeout=min(10, remaining)) as response:
            require(response.status == 200 and response.geturl() == url,
                    'HTTP_STATUS_OR_REDIRECT_INVALID')
            content_type = response.headers.get('Content-Type', '')
            require(content_type.split(';')[0].strip().lower() in (
                {'application/json'} if api else {'application/json', 'text/plain'}),
                'HTTP_CONTENT_TYPE_INVALID')
            require(response.headers.get('Content-Encoding', 'identity').lower() == 'identity',
                    'HTTP_CONTENT_ENCODING_INVALID')
            cache = response.headers.get('Cache-Control', '')
            require(not api or 'no-store' in {part.strip().lower() for part in cache.split(',')},
                    'HTTP_NO_STORE_REQUIRED')
            length = response.headers.get('Content-Length')
            require(length is None or length.isdigit() and 0 < int(length) <= bound,
                    'HTTP_BODY_BOUND_INVALID')
            chunks, size = [], 0
            while True:
                require(time.monotonic() < deadline, 'HTTP_DEADLINE_EXCEEDED')
                chunk = response.read1(min(65536, bound+1-size))
                if not chunk:
                    break
                size += len(chunk)
                require(size <= bound, 'HTTP_BODY_BOUND_INVALID')
                chunks.append(chunk)
            raw = b''.join(chunks)
            require(bool(raw) and (length is None or len(raw) == int(length)),
                    'HTTP_BODY_INCOMPLETE')
            text = raw.decode('utf-8')
            json.dumps(json.loads(text), allow_nan=False)
    except HTTPError as error:
        if error.code == 409:
            raise ValueError('UNIFIED_PROBE_VERSION_DRIFT_RETRY_REQUIRED') from None
        raise ValueError('UNIFIED_PROBE_HTTP_FETCH_FAILED') from None
    except (OSError, URLError, HTTPException, UnicodeError):
        raise ValueError('UNIFIED_PROBE_HTTP_FETCH_FAILED') from None
    return {'url': url, 'status_code': 200, 'content_type': content_type,
            'cache_control': cache, 'requested_at': started.isoformat(),
            'received_at': utc_now().isoformat(), 'body_bytes': len(raw),
            'body_sha256': sha256(raw).hexdigest(), 'body_utf8': text,
            'proxy_configuration_mode': mode}


def _authority(value: dict[str, Any]) -> None:
    require(all(type(value.get(key)) is type(expected) and value.get(key) == expected
                for key, expected in AUTHORITY.items()), 'AUTHORITY_INVALID')


def _checked(value: dict[str, Any], capture: dict[str, Any]) -> datetime:
    stamp = datetime.fromisoformat(value['checked_at'])
    start, end = (datetime.fromisoformat(capture[key])
                  for key in ('requested_at', 'received_at'))
    require(stamp.tzinfo is not None and start-timedelta(seconds=60) <= stamp
            <= end+timedelta(seconds=60), 'API_CLOCK_INVALID')
    return stamp


def _sample(series: dict[str, Any], entry: dict[str, Any], index: dict[str, Any],
            rows: list[dict[str, Any]], checked: datetime) -> dict[str, Any]:
    _authority(series)
    calendar, frequency = Calendar(index['calendar']), entry['frequency']
    expected = calendar.expected(checked, 'daily' if frequency == 'day' else 'minute')
    window = index['windows'][frequency]
    present = {datetime.fromisoformat(row['timestamp']) for row in rows}
    missing = [stamp for stamp in grid(calendar, frequency, window, checked)
               if datetime.fromisoformat(stamp) not in present]
    state = ('QUARANTINED' if entry['state'] == 'QUARANTINED' else 'NO_DATA' if not rows
             else 'PARTIAL' if missing else 'AVAILABLE')
    age = (checked-datetime.fromisoformat(index['published_at'])).total_seconds()
    freshness = ('STALE_PUBLICATION' if age > 180 else 'QUARANTINED'
                 if state == 'QUARANTINED' else 'NO_DATA' if not rows else 'FRESH'
                 if datetime.fromisoformat(rows[-1]['timestamp']) >= expected else 'PARTIAL')
    wanted = {'schema_version': 'qtrade.unified-market-series.v1', 'publication_available': True,
        **{key: entry[key] for key in ('symbol', 'frequency', 'instrument_id', 'security_type')},
        'index_id': index['evidence_sha256'], 'version': index['evidence_sha256'],
        'universe_version': index['universe_version'], 'published_at': index['published_at'],
        'manifest_sha256': signed(entry)['evidence_sha256'] if entry['shards'] else None,
        'window_start': window[0], 'window_end': window[-1],
        'available_start': rows[0]['timestamp'] if rows else None,
        'available_end': rows[-1]['timestamp'] if rows else None,
        'actual_end': rows[-1]['timestamp'] if rows else None,
        'expected_end': expected.isoformat(), 'price_basis': PRICE_BASIS,
        'state': state, 'freshness': freshness,
        'gaps': {'missing_timestamps': missing, 'requested_outside_window': False},
        'limitations': index['limitations']+entry['issues'], 'bars': rows[-2000:],
        'count': len(rows[-2000:]), 'total_count': len(rows), 'truncated': len(rows) > 2000}
    require(all(type(series.get(key)) is type(value) and delivery._same(series.get(key), value)
                for key, value in wanted.items()), 'API_SERIES_MISMATCH')
    return {key: series[key] for key in ('symbol', 'frequency', 'state', 'freshness', 'count',
        'actual_end', 'expected_end', 'published_at', 'manifest_sha256')}


def probe(expected_revision: str, report_path: Path, *, _transport: Any = None) -> dict[str, Any]:
    """Injected transport is always labeled simulated; CLI exposes no overrides."""
    require(re.fullmatch('[a-f0-9]{40}', expected_revision) is not None
            and os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() == 'false',
            'CONFIGURATION_INVALID')
    require(not report_path.exists() and not report_path.is_symlink()
            and report_path.parent.is_dir(), 'NEW_REPORT_PATH_REQUIRED')
    started, deadline = utc_now(), time.monotonic()+MAX_SECONDS
    captures: list[dict[str, Any]] = []
    transport = _get if _transport is None else _transport

    def get(resource: str, version: str | None = None) -> tuple[dict[str, Any], dict[str, Any]]:
        require(time.monotonic() < deadline and len(captures) < 25, 'CAPTURE_BUDGET')
        capture = transport(resource, deadline, version)
        raw = capture['body_utf8'].encode('utf-8')
        require(capture['status_code'] == 200 and len(raw) == capture['body_bytes']
                and sha256(raw).hexdigest() == capture['body_sha256'], 'CAPTURE_INVALID')
        start, end = (datetime.fromisoformat(capture[key])
                      for key in ('requested_at', 'received_at'))
        require(start.tzinfo is not None and end.tzinfo is not None
                and started <= start <= end <= utc_now(), 'CAPTURE_CLOCK_INVALID')
        require(sum(row['body_bytes'] for row in captures)+len(raw) <= MAX_CAPTURE_BYTES,
                'CAPTURE_BUDGET')
        captures.append(capture)
        return json.loads(raw), capture

    deployment, _ = get('deployment')
    expected_deployment = {'classification': 'PUBLIC_BUILD_PROVENANCE', 'provider': 'VERCEL',
        'status': 'AVAILABLE', 'target_environment': 'production',
        'git_commit_sha': expected_revision,
        'git_commit_ref': 'main', 'external_order_transmission': False}
    require(all(type(deployment.get(key)) is type(value) and deployment.get(key) == value
                for key, value in expected_deployment.items()), 'DEPLOYMENT_MISMATCH')
    raw_index, _ = get('index')
    index = validate_index(raw_index, utc_now())
    raw_catalog, _ = get('catalog')
    catalog = validate_publication(raw_catalog)
    _authority(catalog)
    _authority(catalog['master'])
    targets = {row['symbol']: row for row in catalog['master']['securities'] if row['in_target']}
    entries = {(row['symbol'], row['frequency']): row for row in index['entries']}
    require(len(targets) == TARGETS and len(entries) == TARGETS*2
            and set(entries) == {(symbol, freq) for symbol in targets for freq in ('day', '5min')}
            and catalog['master']['universe_version'] == index['universe_version'],
            'UNIVERSE_MISMATCH')
    require(all(all(entry[key] == targets[symbol][key]
                    for key in ('instrument_id', 'security_type', 'name'))
                for (symbol, _), entry in entries.items()), 'ENTRY_IDENTITY_MISMATCH')
    summary, capture = get('summary')
    _authority(summary)
    checked = _checked(summary, capture)
    require(summary.get('evidence_sha256') == catalog['evidence_sha256']
            and summary.get('built_at') == catalog['built_at']
            and delivery._same(summary.get('master'), {
                key: value for key, value in catalog['master'].items() if key != 'securities'}),
            'CATALOG_DRIFT_RETRY_REQUIRED')
    require(summary.get('read_only') is True
            and isinstance(summary.get('incremental_freshness'), dict)
            and set(summary['incremental_freshness']) == {'day', '5min'}
            and delivery._same(summary['incremental_freshness'],
                               collection_freshness(catalog, now=checked)), 'SUMMARY_MISMATCH')
    samples, verified = [], []
    for symbol, frequency in SAMPLES:
        require((symbol, frequency) in entries, 'SAMPLE_TARGET_MISSING')
        series, capture = get(f'bars:{symbol}:{frequency}', index['evidence_sha256'])
        require(series.get('index_id') == index['evidence_sha256'],
                'VERSION_DRIFT_RETRY_REQUIRED')
        entry, rows = entries[(symbol, frequency)], []
        for ref in entry['shards']:
            _, shard_capture = get('shard:'+ref['sha256'])
            shard = verify_shard(shard_capture['body_utf8'].encode('utf-8'), ref, index,
                                 entry, utc_now())
            rows.extend(shard['bars'])
            verified.append({'sha256': ref['sha256'], 'evidence_sha256': ref['evidence_sha256'],
                             'symbol': symbol, 'frequency': frequency, 'count': ref['count']})
        rows.sort(key=lambda row: datetime.fromisoformat(row['timestamp']))
        require(len(rows) == len({datetime.fromisoformat(row['timestamp']) for row in rows}),
                'CONFLICTING_SHARDS')
        samples.append(_sample(series, entry, index, rows, _checked(series, capture)))
    final_deployment, _ = get('deployment')
    require(all(type(final_deployment.get(key)) is type(value)
                and final_deployment.get(key) == value
                for key, value in expected_deployment.items()), 'DEPLOYMENT_DRIFT_RETRY_REQUIRED')
    checked = utc_now()
    index_age = (checked-datetime.fromisoformat(index['published_at'])).total_seconds()
    catalog_age = (checked-datetime.fromisoformat(catalog['built_at'])).total_seconds()
    require(index_age >= 0 and catalog_age >= 0, 'PUBLICATION_CLOCK_INVALID')
    index_fresh, catalog_fresh = index_age <= 180, catalog_age <= 1200
    report: dict[str, Any] = signed({
        **AUTHORITY, 'schema_version': 'qtrade.unified-production-probe.v1',
        'state': 'FRESH_TRANSPORT' if index_fresh and catalog_fresh else 'STALE_TRANSPORT',
        'transport_mode': 'SIMULATED_TRANSPORT' if _transport is not None else 'ACTUAL_HTTP_GET',
        'started_at': started.isoformat(), 'checked_at': checked.isoformat(),
        'production_revision': expected_revision, 'catalog_id': catalog['evidence_sha256'],
        'catalog_master_sha256': catalog['master']['evidence_sha256'],
        'index_id': index['evidence_sha256'], 'index_master_sha256': index['master_sha256'],
        'universe_version': index['universe_version'], 'target_count': len(targets),
        'index_entries': len(entries), 'target_by_type': catalog['master']['target_by_type'],
        'index_published_at': index['published_at'], 'catalog_built_at': catalog['built_at'],
        'index_age_seconds': index_age, 'catalog_age_seconds': catalog_age,
        'index_fresh': index_fresh, 'catalog_fresh': catalog_fresh,
        'fresh_publication': index_fresh and catalog_fresh,
        'freshness_limits_seconds': {'index': 180, 'catalog': 1200},
        'series_states': dict(Counter(entry['state'] for entry in entries.values())),
        'incremental_freshness': collection_freshness(catalog, now=checked),
        'samples': samples, 'verified_shards': verified,
        'responses': [{key: value for key, value in capture.items() if key != 'body_utf8'}
                      for capture in captures],
        'integrity_checks_passed': True, 'sample_series_integrity_verified': True,
        'all_series_bytes_verified': False, 'browser_rendered_verified': False,
        'whole_step1_accepted': False, 'completed_session_cde_accepted': False,
        'limitations': ['ALL_1500_TARGET_IDENTITIES_AND_3000_INDEX_ENTRIES_VALIDATED',
            'ONLY_FIVE_FIXED_API_SERIES_COMPARED_WITH_THEIR_PUBLIC_IMMUTABLE_SHARDS',
            'COMPACT_SAMPLE_VERIFICATION_RECEIPT_NOT_A_STANDALONE_RAW_REPLAY_BUNDLE',
            'PRIVATE_RAW_VENDOR_BATCHES_NOT_REPLAYED', 'FRESH_TRANSPORT_IS_NOT_COMPLETE_DATA',
            'NO_RENDERED_UI_OR_COMPLETED_SESSION_CDE_ACCEPTANCE',
            'HISTORICAL_PIT_AND_PRICE_ADJUSTMENT_UNCERTIFIED']})
    with report_path.open('x', encoding='utf-8') as handle:
        json.dump(report, handle, ensure_ascii=False, indent=2, allow_nan=False)
        handle.write('\n')
    return report


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-production-revision', required=True)
    parser.add_argument('--report', type=Path, required=True)
    args = parser.parse_args()
    try:
        result = probe(args.expected_production_revision, args.report)
    except Exception as error:
        # Do not disclose network/proxy exception details or claim a passed report.
        message = str(error) if re.fullmatch('UNIFIED_PROBE_[A-Z_]+', str(error)) else (
            'UNIFIED_PROBE_REJECTED_'+type(error).__name__)
        parser.exit(1, message+'\n')
    print(json.dumps({key: result[key] for key in (
        'state', 'production_revision', 'index_id', 'catalog_id', 'fresh_publication',
        'transport_mode', 'incremental_freshness')}))
    if not result['fresh_publication']:
        parser.exit(2)


if __name__ == '__main__':
    main()
