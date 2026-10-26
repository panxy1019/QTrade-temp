"""Fixed-origin, GET-only acceptance of the C serving chain's public projection."""

from __future__ import annotations

import json
import os
import re
import time
from datetime import UTC, datetime
from hashlib import sha256
from http.client import HTTPException
from pathlib import Path
from typing import Any
from urllib.error import URLError
from urllib.request import HTTPRedirectHandler, ProxyHandler, Request, build_opener

from qtrade.api.online_research import validate_catalog
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.data_version_control import validate_public_catalog
from qtrade.research.model_registry import checked_document

ORIGIN = 'https://q-trade-temp.vercel.app'
DEPLOYMENT = '/api/deployment'
CATALOG = '/api/research/online/catalog'
VERSIONS = '/api/research/online/data-versions'
SIGNALS = '/api/research/online/signals'
LIMITS = {DEPLOYMENT: 16_000, CATALOG: 3_000_000, VERSIONS: 1_100_000,
          SIGNALS: 3_000_000}
API_FIELDS = {'publication_available', 'worker_fresh', 'data_schedule_fresh',
              'current_prediction_usable', 'expected_complete_day'}
REPO = Path(__file__).resolve().parents[1]


def utc_now() -> datetime:
    return datetime.now(UTC)


def require(condition: bool, code: str) -> None:
    if not condition:
        raise ValueError('PUBLIC_DELIVERY_' + code)


class _NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req: Any, fp: Any, code: int, msg: str,
                         headers: Any, newurl: str) -> None:
        return None


def _get(path: str, deadline: float) -> dict[str, Any]:
    """No credentials, cookies, redirects, arbitrary URLs, retries or write methods."""
    require(path in LIMITS, 'PATH_NOT_ALLOWED')
    remaining = deadline-time.monotonic()
    require(remaining > 0, 'HTTP_DEADLINE_EXCEEDED')
    started = utc_now()
    url = ORIGIN+path
    request = Request(url, method='GET', headers={
        'Accept': 'application/json', 'Accept-Encoding': 'identity',
        'Cache-Control': 'no-cache', 'User-Agent': 'QTrade-readonly-C-acceptance/1'})
    try:
        with build_opener(ProxyHandler({}), _NoRedirect()).open(
                request, timeout=min(10, remaining)) as response:
            require(response.status == 200 and response.geturl() == url,
                    'HTTP_STATUS_OR_REDIRECT_INVALID')
            content_type = response.headers.get('Content-Type', '')
            require(content_type.split(';')[0].strip().lower() == 'application/json',
                    'HTTP_CONTENT_TYPE_INVALID')
            require(response.headers.get('Content-Encoding', 'identity').lower() == 'identity',
                    'HTTP_CONTENT_ENCODING_INVALID')
            cache = response.headers.get('Cache-Control', '')
            if path != DEPLOYMENT:
                require('no-store' in {part.strip().lower() for part in cache.split(',')},
                        'HTTP_NO_STORE_REQUIRED')
            length = response.headers.get('Content-Length')
            if length is not None:
                require(length.isdigit() and 0 < int(length) <= LIMITS[path],
                        'HTTP_BODY_BOUND_INVALID')
            chunks, size = [], 0
            while True:
                require(time.monotonic() < deadline, 'HTTP_DEADLINE_EXCEEDED')
                chunk = response.read1(min(65536, LIMITS[path]+1-size))
                if not chunk:
                    break
                size += len(chunk)
                require(size <= LIMITS[path], 'HTTP_BODY_BOUND_INVALID')
                chunks.append(chunk)
            raw = b''.join(chunks)
            require(bool(raw) and (length is None or len(raw) == int(length)),
                    'HTTP_BODY_INCOMPLETE')
            text = raw.decode('utf-8')
            json.dumps(json.loads(text), allow_nan=False)
    except (OSError, URLError, HTTPException, UnicodeError) as error:
        raise ValueError('PUBLIC_DELIVERY_HTTP_FETCH_FAILED') from error
    return {'url': url, 'status_code': 200, 'content_type': content_type,
            'cache_control': cache, 'requested_at': started.isoformat(),
            'received_at': utc_now().isoformat(), 'body_bytes': len(raw),
            'body_sha256': sha256(raw).hexdigest(), 'body_utf8': text}


def _authority(value: dict[str, Any]) -> None:
    require(all(value.get(key) == expected and type(value.get(key)) is type(expected)
                for key, expected in AUTHORITY.items()), 'AUTHORITY_INVALID')


def _same(left: Any, right: Any) -> bool:
    return json.dumps(left, sort_keys=True, allow_nan=False) == json.dumps(
        right, sort_keys=True, allow_nan=False)


def _fresh(value: Any, now: datetime) -> None:
    stamp = datetime.fromisoformat(value)
    require(stamp.tzinfo is not None and 0 <= (now-stamp).total_seconds() <= 720,
            'PUBLICATION_STALE_OR_FUTURE')


def _catalog(value: dict[str, Any], now: datetime, day: str) -> dict[str, Any]:
    _authority(value)
    require(all(value.get(key) is True for key in (
        'publication_available', 'worker_fresh', 'data_schedule_fresh',
        'current_prediction_usable')), 'CATALOG_NOT_CURRENT')
    require(value['expected_complete_day'] == day and value.get('data_error') is None,
            'CATALOG_DAY_OR_DATA_ERROR')
    # The API adds these computed fields after sealing the publication.
    original = {key: item for key, item in value.items() if key not in API_FIELDS}
    validate_catalog(original)
    require(original['worker']['state'] in {'MODEL_CONTROL_READY', 'PREDICTING', 'RUNNING'},
            'WORKER_NOT_READY')
    for stamp in (original['published_at'], original['worker']['heartbeat_at'],
                  original['data_schedule']['heartbeat_at']):
        _fresh(stamp, now)
    require(original['data_schedule'].get('fresh') is True, 'DATA_SERVICE_NOT_FRESH')
    return original


def _stable(value: dict[str, Any]) -> tuple[Any, ...]:
    return (value['data_version']['version_id'], value['data_version']['data_event_id'],
            value['active_model'], value['auto_predict']['current_prediction_id'],
            value['worker']['source_revision'])


def _check_catalog(value: dict[str, Any], transition: dict[str, Any],
                   current: dict[str, Any], prediction: dict[str, Any]) -> None:
    data = value['data_version']
    require(data['version_id'] == current['version_id']
            and data['actual_end'] == transition['data_end']
            and data['evidence_sha256'] == current['evidence_sha256']
            and data['data_event_id'] == transition['data_event_id'], 'DATA_IDENTITY_MISMATCH')
    require(value['active_model'] == transition['approved_model_id'], 'ACTIVE_MODEL_CHANGED')
    require(value['worker']['source_revision'] == prediction['bindings']['code_revision'],
            'WORKER_SOURCE_CHANGED')
    automatic = value['auto_predict']
    require(automatic['current_prediction_id'] == prediction['prediction_id'],
            'CURRENT_PREDICTION_CHANGED')
    row = next((item for item in automatic['predictions']
                if item['prediction_id'] == prediction['prediction_id']), None)
    require(row is not None and row['state'] == 'COMPLETE', 'PREDICTION_NOT_COMPLETE')
    assert row is not None
    for key in ('bindings', 'contract', 'score_count', 'feature_count', 'evidence_sha256'):
        require(_same(row[key], prediction[key]), 'PREDICTION_PROJECTION_MISMATCH')
    _check_versions(value['data_catalog'], transition, current)


def _check_versions(value: dict[str, Any], transition: dict[str, Any],
                    current: dict[str, Any]) -> None:
    _authority(value)
    validate_public_catalog(value)
    require(value['head_version_id'] == current['version_id']
            and value['head_event_id'] == transition['data_event_id'], 'DATA_HEAD_OR_EPOCH_CHANGED')
    row = next((item for item in value['versions']
                if item['version_id'] == current['version_id']), None)
    require(row is not None and row['is_head'] is True
            and row['manifest_sha256'] == current['evidence_sha256']
            and row['actual_end'] == transition['data_end'], 'VERSION_MANIFEST_MISMATCH')


def verify_public_delivery(transition: dict[str, Any], current: dict[str, Any],
                           prediction: dict[str, Any], signals: dict[str, Any],
                           expected_revision: str) -> dict[str, Any]:
    """Accept only after raw-verified C lineage; preserve the actual HTTP bytes as evidence.

    The caller must first run verify_version, verify_prediction,
    verify_signal_archive and assess_transition. This adds public delivery only.
    """
    require(os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() == 'false'
            and re.fullmatch('[a-f0-9]{40}', expected_revision) is not None,
            'CONFIGURATION_INVALID')
    for document in (transition, current, prediction, signals):
        checked_document(document)
        _authority(document)
    require(transition['schema_version'] == 'qtrade.session-transition.v1'
            and transition['state'] == 'VERIFIED_VERSION_PREDICTION_SIGNAL_TRANSITION'
            and transition['whole_step1_accepted'] is False
            and transition['browser_verified'] is False
            and transition['production_publication_verified'] is False,
            'PRIVATE_C_ACCEPTANCE_REQUIRED')
    require(transition['version_id'] == current['version_id']
            and transition['prediction_id'] == prediction['prediction_id']
            and transition['prediction_sha256'] == prediction['evidence_sha256']
            and transition['signals_sha256'] == signals['evidence_sha256']
            and transition['signal_family_id'] == signals['signal_family_id'],
            'PRIVATE_C_BINDINGS_CHANGED')
    started = utc_now()
    calendar = Calendar(REPO/'configs/observer_calendar_2026.json')
    day = transition['data_end']
    require(str(calendar.expected(started, 'daily').date()) == day,
            'REAL_COMPLETED_DAY_REQUIRED')
    deadline = time.monotonic()+50
    captures = [_get(path, deadline) for path in (DEPLOYMENT, CATALOG, VERSIONS, SIGNALS, CATALOG)]
    require(time.monotonic() <= deadline, 'HTTP_DEADLINE_EXCEEDED')
    now = utc_now()
    require(now.tzinfo is not None and started <= now
            and str(calendar.expected(now, 'daily').date()) == day
            and now < datetime.fromisoformat(transition['expected_execution_at']),
            'CURRENT_DELIVERY_WINDOW_PASSED')
    for capture in captures:
        requested = datetime.fromisoformat(capture['requested_at'])
        received = datetime.fromisoformat(capture['received_at'])
        require(requested.tzinfo is not None and received.tzinfo is not None
                and started <= requested <= received <= now, 'HTTP_CAPTURE_CLOCK_INVALID')
    deployment, first, versions, public_signals, last = [
        json.loads(capture['body_utf8']) for capture in captures]
    require(_same(deployment, {
        'classification': 'PUBLIC_BUILD_PROVENANCE', 'provider': 'VERCEL',
        'status': 'AVAILABLE', 'target_environment': 'production',
        'git_commit_sha': expected_revision, 'git_commit_ref': 'main',
        'external_order_transmission': False}), 'PRODUCTION_REVISION_MISMATCH')
    before, after = _catalog(first, now, day), _catalog(last, now, day)
    require(_same(_stable(before), _stable(after)), 'PUBLICATION_ROUND_CHANGED')
    for catalog in (before, after):
        _check_catalog(catalog, transition, current, prediction)
    for wrapper in (versions, public_signals):
        _authority(wrapper)
        require(wrapper['publication_available'] is True, 'PUBLICATION_UNAVAILABLE')
    require(versions['worker_fresh'] is True and public_signals['current_input'] is True,
            'PUBLICATION_NOT_CURRENT')
    _check_versions(versions['catalog'], transition, current)
    checked_document(public_signals['manifest'])
    require(_same(public_signals['manifest'], signals), 'SIGNAL_MANIFEST_MISMATCH')
    require(public_signals['execution_admitted'] is False
            and public_signals['forward_return'] is None, 'SIGNAL_EXECUTION_FORBIDDEN')
    return signed({**AUTHORITY, 'schema_version': 'qtrade.qlib-public-delivery.v1',
        'state': 'VERIFIED_CURRENT_C_PUBLIC_DELIVERY', 'scope': 'C_PUBLIC_PROJECTION_ONLY',
        'started_at': started.isoformat(), 'checked_at': now.isoformat(),
        'production_base_url': ORIGIN, 'production_revision': expected_revision,
        'private_transition_sha256': transition['evidence_sha256'],
        'version_id': current['version_id'], 'data_event_id': transition['data_event_id'],
        'approved_model_id': transition['approved_model_id'],
        'prediction_id': prediction['prediction_id'],
        'signal_family_id': signals['signal_family_id'],
        'responses': captures, 'production_publication_verified': True,
        'whole_step1_accepted': False, 'browser_verified': False,
        'paper_forward_ledger_verified': False, 'candidate_validation_verified': False,
        'etf_forward_verified': False, 'historical_pit_certified': False,
        'limitations': ['PUBLIC_PREDICTION_PROJECTION_NOT_FULL_PREDICTION_PARQUET',
            'PRIVATE_RAW_ARCHIVES_VERIFIED_BY_CALLER_BEFORE_PUBLIC_DELIVERY_CHECK',
            'NO_BROWSER_PAPER_ETF_CANDIDATE_OR_WHOLE_STEP1_ACCEPTANCE']})
