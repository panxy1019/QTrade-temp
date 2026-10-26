import json
from copy import deepcopy
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from qtrade.api import online_research as api
from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.data_versions import file_hash
from qtrade.research.signal_publication import (
    validate_signal_publication,
    verify_signal_archive,
)


@pytest.fixture
def archive(tmp_path):
    now = datetime.now(UTC)
    bindings = {'data_version_id': 'a'*64, 'code_revision': 'b'*40}
    scores = [{'instrument': f'SH{600000+i}', 'score': i/100} for i in range(110)]
    signals = signed({**AUTHORITY, 'bindings': bindings, 'as_of': '2026-10-26',
        'future_prices_consumed': False, 'signals': {'momentum20': scores}})
    target = {'positions': [{'instrument': r['instrument'], 'target_weight': '0.019'}
                           for r in reversed(scores[-50:])],
        'allocation_method': 'TOPK_EQUAL_WEIGHT',
        'holdings_reconciled': False, 'turnover_constraints_applied': False,
        'execution_constraints_applied': False, 'fills': [], 'forward_return': None}
    targets = signed({**AUTHORITY, 'generated_at': (now-timedelta(minutes=1)).isoformat(),
        'expected_execution_at': (now+timedelta(hours=1)).isoformat(),
        'targets': {'momentum20': target}})
    (tmp_path/'signals.json').write_text(json.dumps(signals))
    (tmp_path/'indicative-targets.json').write_text(json.dumps(targets))
    (tmp_path/'close-input.parquet').write_bytes(b'checksum-bound-input')
    manifest = signed({**AUTHORITY, 'schema_version': 'qtrade.signal-baselines.v1',
        'signal_family_id': signed(bindings)['evidence_sha256'], 'bindings': bindings,
        'data_end': '2026-10-26', 'generated_at': targets['generated_at'],
        'expected_execution_at': targets['expected_execution_at'],
        'forward_return_observed': False, 'portfolio_state_reconciled': False,
        'qualified_instruments': 110, 'signal_count': 1,
        'files': {p.name: file_hash(p) for p in tmp_path.iterdir()}})
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    return tmp_path


@pytest.mark.parametrize('damage', ['extra', 'missing', 'bytes', 'manifest'])
def test_signal_archive_requires_exact_immutable_inventory(archive, damage):
    if damage == 'extra':
        (archive/'unexpected').write_bytes(b'new')
    elif damage == 'missing':
        (archive/'close-input.parquet').unlink()
    elif damage == 'bytes':
        (archive/'signals.json').write_text('{}')
    else:
        m = json.loads((archive/'manifest.json').read_text())
        m['bindings']['data_version_id'] = 'c'*64
        (archive/'manifest.json').write_text(json.dumps(m))
    with pytest.raises(ValueError):
        verify_signal_archive(archive)


@pytest.mark.parametrize('damage', ['fills', 'weight', 'held', 'returns', 'duplicate', 'nan'])
def test_signal_observations_cannot_claim_execution_or_invalid_scores(archive, damage):
    value = deepcopy(verify_signal_archive(archive))
    target = value['targets']['momentum20']
    if damage == 'fills':
        target['fills'] = [{}]
    elif damage == 'weight':
        target['positions'][0]['target_weight'] = '1'
    elif damage == 'held':
        target['holdings_reconciled'] = True
    elif damage == 'returns':
        target['forward_return'] = 0.5
    elif damage == 'duplicate':
        value['signals']['momentum20'][1] = value['signals']['momentum20'][0]
    else:
        value['signals']['momentum20'][0]['score'] = float('nan')
    with pytest.raises(ValueError):
        validate_signal_publication(signed({k: v for k, v in value.items()
                                            if k != 'evidence_sha256'}))


def test_signal_api_bounds_pages_and_rechecks_current_context(archive, monkeypatch):
    value = verify_signal_archive(archive)
    monkeypatch.setattr(api, 'read_public', lambda *a, **kw: value)
    online = {'data_version': {'version_id': 'a'*64}, 'worker': {
        'source_revision': 'b'*40, 'state': 'MODEL_CONTROL_READY',
        'heartbeat_at': datetime.now(UTC).isoformat()}}
    monkeypatch.setattr(api, 'listing', lambda: online)
    monkeypatch.setattr(api, 'research_clock', lambda: '2026-10-26')
    app = FastAPI()
    app.include_router(api.router)
    with TestClient(app) as http:
        result = http.get('/research/online/signals?strategy=momentum20&offset=100').json()
        assert len(result['rows']) == 10 and result['total'] == 110
        assert result['current_input'] is True and result['execution_admitted'] is False
        assert result['forward_return'] is None
        assert http.get('/research/online/signals?limit=101').status_code == 422
        assert http.get('/research/online/signals?strategy=unknown').status_code == 404
        online['data_version']['version_id'] = 'c'*64
        result = http.get('/research/online/signals').json()
        assert result['publication_available'] is True and result['current_input'] is False
        online['data_version']['version_id'] = 'a'*64
        online['worker']['heartbeat_at'] = (datetime.now(UTC)-timedelta(hours=1)).isoformat()
        assert http.get('/research/online/signals').json()['current_input'] is False
