"""Verify public control bytes and deployed asset content, without claiming UI rendering."""
import hashlib
import json
import re
import shutil
import sys
from datetime import UTC, datetime
from pathlib import Path
from urllib.request import Request, build_opener

sys.path[:0] = ['E:/QTrade-git/src', 'E:/QTrade-git']
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.forward_publication import validate_forward_publication
from scripts.qlib_public_delivery import _NoRedirect, _proxy_handler

EXPECTED = 'ae33a7c8c27ea721eb3be1e4430b562a44b67f91'
ORIGIN = 'https://q-trade-temp.vercel.app'
base = Path('E:/QTrade-temp/cluster-evidence')
capture = base/'stock-public-20261027T171505Z-b87ed47d'
meta = json.loads((capture/'capture.json').read_bytes())
assert signed({k: v for k, v in meta.items() if k != 'evidence_sha256'}) == meta
for row in meta['captures']:
    raw = (capture/row['body_file']).read_bytes()
    assert row['status_code'] == 200 and hashlib.sha256(raw).hexdigest() == row['body_sha256']
before = json.loads((capture/'deployment-before.json').read_bytes())
assert before == json.loads((capture/'deployment-after.json').read_bytes())
assert before['git_commit_sha'] == EXPECTED
wrapper = json.loads((capture/'stock-forward.json').read_bytes())
pub = json.loads((capture/'publication.json').read_bytes())
assert wrapper['publication'] == pub and wrapper['service_fresh'] and wrapper['publication_available']
validate_forward_publication(pub, now=datetime.now(UTC))
assert pub['state'] == 'RUNNING' and not pub['errors'] and len(pub['books']) == 14
assert pub['source_revision'] == '9846a7f30774d6ae4b4d41f02b493c5246be87d9'
retired_id = 'approved_model_2d78cc4cc257653a088b1a43bc1a65ec719c570680b9698eb05e48df1b7e0293'
papers = [b for b in pub['books'] if b['mode'] == 'PAPER' and b['strategy_id'] != retired_id]
retired = [b for b in pub['books'] if b['strategy_id'] == retired_id]
assert len(papers) == 6 and all(b['running'] and b['risk_state'] == 'NORMAL' and not b['recovery_required'] for b in papers)
assert len(retired) == 2 and all(not b['running'] for b in retired)
proof = signed({**AUTHORITY, 'kind': 'ACTUAL_PRODUCTION_AE33_FORWARD_CONTROL_DELIVERY',
    'at': datetime.now(UTC).isoformat(), 'state': 'PASSED_PUBLIC_CONTROL_DELIVERY',
    'capture_sha256': meta['evidence_sha256'], 'publication_sha256': pub['evidence_sha256'],
    'production_revision': EXPECTED, 'forward_source_revision': pub['source_revision'],
    'all_books': 14, 'current_paper_running': 6, 'retired_books_stopped': 2,
    'browser_verified': False, 'full_day_transition_accepted': False, 'whole_step1_accepted': False})
with (capture/'control-delivery-verification.json').open('xb') as f:
    f.write((json.dumps(proof, sort_keys=True, indent=2)+'\n').encode())

assets = base/('handover-asset-delivery-'+datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ'))
assets.mkdir()
records = []
def fetch(name, path, limit):
    assert path == '/' or path == '/api/deployment' or re.fullmatch(r'/assets/[A-Za-z0-9._-]+\.js', path)
    proxy, mode = _proxy_handler()
    req = Request(ORIGIN+path, headers={'Accept-Encoding': 'identity', 'Cache-Control': 'no-cache'})
    with build_opener(proxy, _NoRedirect()).open(req, timeout=12) as response:
        assert response.status == 200 and response.geturl() == ORIGIN+path
        raw = response.read(limit+1)
        assert raw and len(raw) <= limit
        headers = {k: v for k, v in response.headers.items() if k.lower() in {'content-type', 'content-length', 'cache-control', 'date', 'x-vercel-id'}}
    with (assets/name).open('xb') as f:
        f.write(raw)
    records.append({'url': ORIGIN+path, 'file': name, 'status': 200, 'headers': headers,
        'bytes': len(raw), 'sha256': hashlib.sha256(raw).hexdigest(),
        'received_at': datetime.now(UTC).isoformat(), 'proxy_mode': mode})
    return raw

attempt = {'started_at': datetime.now(UTC).isoformat(), 'state': 'STARTED'}
try:
    d1 = json.loads(fetch('deployment-before.json', '/api/deployment', 16_000))
    assert d1['git_commit_sha'] == EXPECTED
    html = fetch('index.html', '/', 256_000).decode()
    entry = re.findall(r'<script\b[^>]*\bsrc="(/assets/[A-Za-z0-9._-]+\.js)"', html)
    assert len(entry) == 1
    main = fetch('entry.js', entry[0], 3_000_000).decode()
    chunk = sorted(set(re.findall(r'ForwardResearchPage-[A-Za-z0-9_-]+\.js', main)))
    assert len(chunk) == 1
    page = fetch('forward-page.js', '/assets/'+chunk[0], 500_000).decode()
    labels = ['全部账本保留', '退休模型账本已停止', '保持停止，不自动恢复']
    matches = {s: s in page or json.dumps(s, ensure_ascii=True)[1:-1] in page for s in labels}
    assert all(matches.values()), matches
    d2 = json.loads(fetch('deployment-after.json', '/api/deployment', 16_000))
    assert d1 == d2
    attempt.update(state='VERIFIED_DEPLOYED_ASSET_CONTENT_ONLY', labels_present=matches,
                   production_revision=EXPECTED)
except Exception as exc:
    attempt.update(state='FAILED', error_type=type(exc).__name__)
finally:
    attempt.update(finished_at=datetime.now(UTC).isoformat(), records=records,
                   browser_rendered_verified=False, whole_step1_accepted=False,
                   raw_public_asset_directory=str(assets))
    value = signed({**AUTHORITY, **attempt})
    with (assets/'verification.json').open('xb') as f:
        f.write((json.dumps(value, sort_keys=True, ensure_ascii=False, indent=2)+'\n').encode())
print(json.dumps({'control_delivery': proof['state'], 'asset_directory': str(assets),
                  'asset_state': attempt['state'], 'error': attempt.get('error_type')}))
