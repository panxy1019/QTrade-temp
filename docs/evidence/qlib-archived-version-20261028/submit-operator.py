"""One authorized retrospective research job through the real production API.

Private login credentials and cookies remain only in this process's memory.
This does not activate a model or admit/recover any PAPER account.
"""
import hashlib
import http.cookiejar
import json
import re
import sys
import uuid
from datetime import UTC, datetime
from pathlib import Path
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener

sys.path[:0] = ['E:/QTrade-git/src', 'E:/QTrade-git']
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.strategy_contract import bound_default_spec
from scripts.qlib_public_delivery import _NoRedirect, _proxy_handler

ORIGIN = 'https://q-trade-temp.vercel.app'
OLD = 'd33a74663373b1d4a0d7cf1ecf1622b34657a6fda08aacc7bacadb70d47ec4fa'
CURRENT = '09a67b135def5dee766ea23716e5405aeb060c28a93720402cb2ebf4542ee407'
REVISION = '096387b8088e4361c9e5cd9cd2c165e21819fead'
root = Path('E:/QTrade-temp/cluster-evidence')/('archived-version-auth-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ'))
root.mkdir()
proxy, proxy_mode = _proxy_handler()
opener = build_opener(proxy, _NoRedirect(), HTTPCookieProcessor(http.cookiejar.CookieJar()))
records = []

def write(name, raw):
    with (root/name).open('xb') as f:
        f.write(raw)

def call(name, endpoint, value=None, *, private=False):
    request = Request(ORIGIN+endpoint, data=None if value is None else json.dumps(value).encode(),
        headers={'Origin': ORIGIN, 'Content-Type': 'application/json',
                 'Cache-Control': 'no-cache', 'Accept-Encoding': 'identity'})
    at = datetime.now(UTC).isoformat()
    try:
        response = opener.open(request, timeout=15)
    except HTTPError as error:
        response = error
    with response:
        raw = response.read(3_000_001)
        assert len(raw) <= 3_000_000 and response.geturl() == ORIGIN+endpoint
        headers = {k: v for k, v in response.headers.items()
                   if k.lower() in {'date', 'content-type', 'cache-control', 'x-vercel-id'}}
        record = {'name': name, 'endpoint': endpoint, 'method': request.get_method(),
            'requested_at': at, 'received_at': datetime.now(UTC).isoformat(),
            'status': response.status, 'body_bytes': len(raw),
            'body_sha256': hashlib.sha256(raw).hexdigest(), 'headers': headers,
            'private_response_body_saved': False, 'proxy_mode': proxy_mode}
        if not private:
            write(name+'.json', raw)
            record['body_file'] = name+'.json'
        records.append(record)
        assert response.status in {200, 202}, (name, response.status)
        return json.loads(raw)

attempt = {**AUTHORITY, 'kind': 'ACTUAL_AUTHENTICATED_ARCHIVED_VERSION_RESEARCH',
    'started_at': datetime.now(UTC).isoformat(), 'state': 'STARTED',
    'model_family': 'linear', 'historical_version': OLD, 'observed_current_version': CURRENT,
    'retrospective_replay_only': True, 'prospective_validation_claimed': False,
    'model_activation_authorized_by_this_job': False, 'paper_admission_authorized': False,
    'credentials_saved': False, 'session_tokens_saved': False, 'browser_verified': False,
    'whole_step1_accepted': False}
try:
    catalog = call('catalog-before', '/api/research/catalog')
    assert catalog['worker']['source_revision'] == REVISION
    assert catalog['default_spec']['data_binding']['data_version_id'] == CURRENT
    candidates = [d for d in catalog['datasets'] if d.get('data_binding', {}).get('data_version_id') == OLD]
    assert len(candidates) == 1 and candidates[0]['data_binding']['actual_end'] == '2026-10-26'
    spec = bound_default_spec(candidates[0]['data_binding'], 'linear')
    spec['name'] = 'archived-oct26-ridge-binding-check-20261028'
    assert spec['dataset_id'] == 'qlib-version-'+OLD and spec['data_binding']['data_version_id'] != CURRENT
    text = Path('C:/Users/panxy1019/AppData/Local/QTrade/research-private/research-login.txt').read_text(encoding='utf-8')
    match = re.search(r'^Password\s*:\s*(\S+)\s*$', text, re.M)
    assert match is not None, 'PRIVATE_CREDENTIAL_FORMAT_UNSUPPORTED'
    login = call('login', '/api/research/login', {'user': 'panxy1019', 'password': match.group(1)}, private=True)
    del text, match
    assert login.get('authenticated') is True
    preview = call('specification', '/api/research/specification', {'spec': spec})
    assert preview['source_revision'] == REVISION
    assert preview['strategy_definition']['spec']['data_binding']['data_version_id'] == OLD
    identity = uuid.uuid4().hex
    request = {'request_id': identity, 'spec': spec, 'expected_strategy_version': preview['expected_strategy_version']}
    write('submission-intent.json', (json.dumps(request, sort_keys=True, indent=2)+'\n').encode())
    attempt['job_id'] = identity
    result = call('submit', '/api/research/jobs', request)
    assert result['job_id'] == identity and result['state'] == 'QUEUED'
    replay = call('idempotent-replay', '/api/research/jobs', request)
    assert replay == result
    after = call('catalog-after', '/api/research/catalog')
    assert after['default_spec']['data_binding']['data_version_id'] == CURRENT
    attempt.update(state='ARCHIVED_VERSION_ACCEPTED_THROUGH_PRODUCTION_API',
        strategy_version=preview['expected_strategy_version'], source_revision=REVISION)
except Exception as error:
    attempt.update(state='FAILED_OR_UNCERTAIN', error_type=type(error).__name__)
finally:
    attempt.update(finished_at=datetime.now(UTC).isoformat(), observations=records)
    write('attempt.json', (json.dumps(signed(attempt), sort_keys=True, indent=2)+'\n').encode())
    print(json.dumps({'root': str(root), 'state': attempt['state'], 'job_id': attempt.get('job_id'),
        'strategy_version': attempt.get('strategy_version'), 'error_type': attempt.get('error_type')}))
