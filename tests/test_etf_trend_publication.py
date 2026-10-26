from __future__ import annotations

import json
import os
from datetime import UTC, date, datetime
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.data_versions import file_hash
from qtrade.research.etf_trend import (
    admit,
    sessions,
    validate_etf_trend_publication,
    verify_etf_archive,
)
from scripts.unified_store import UnifiedBatchStore


def calendar():
    return Calendar(Path(__file__).resolve().parents[1]/'configs/observer_calendar_2026.json')


def capture():
    window = sessions(calendar(), date(2026, 10, 26))
    rows = [{'day': day, 'open': str(2+i*.01), 'high': str(2.1+i*.01),
             'low': str(1.9+i*.01), 'close': str(2+i*.01), 'volume': '1000'}
            for i, day in enumerate(window)]
    return {'source_id': 'sina_klines', 'request': {'symbol': 'sh510300', 'scale': '240',
            'ma': 'no', 'datalen': '63'}, 'received_at': '2026-10-26T12:00:00+00:00',
            'body': json.dumps(rows).encode(), 'status_code': 200,
            'transport_error': None, 'body_complete': True}


def public_result():
    value = admit([capture()], ['510300'], calendar(), date(2026, 10, 26),
                  datetime(2026, 10, 26, 13, tzinfo=UTC))
    bindings = {'master_sha256': 'a'*64, 'as_of': '2026-10-26', 'code_revision': 'b'*40,
                'calendar_sha256': signed(calendar().document)['evidence_sha256'],
                'contract': 'ETF_CLOSE_DIAGNOSTIC_V1'}
    return signed({**{k: v for k, v in value.items() if k != 'evidence_sha256'},
                   'bindings': bindings, 'receipt_sha256': 'c'*64})


def resign(value):
    return signed({k: v for k, v in value.items() if k != 'evidence_sha256'})


def test_valid_diagnostic_has_recomputed_positive_scores_and_actual_calendar():
    value = public_result()
    assert validate_etf_trend_publication(value) == value
    assert len(value['sessions']) == 21
    assert value['scores'] == [{'instrument': '510300', 'score': .1}]
    assert value['price_basis'] == 'SINA_NATIVE_ADJUSTMENT_UNCERTIFIED'


def test_qualified_nonpositive_trend_is_retained_without_an_invented_score():
    value = public_result()
    value['close_histories']['510300'][-1]['close'] = '1.9'
    value['scores'] = []
    assert validate_etf_trend_publication(resign(value))['qualified_close_symbols'] == 1


def test_unavailable_diagnostic_preserves_failed_target_coverage():
    value = public_result()
    value.update(state='INPUT_UNAVAILABLE', qualified_close_symbols=0,
                 close_histories={}, scores=[], excluded=[{'symbol': '510300',
                 'reason': 'INCOMPLETE_OR_INVALID_21_SESSION_SOURCE'}])
    assert validate_etf_trend_publication(resign(value))['required_symbols'] == 1


@pytest.mark.parametrize('change', ['authority', 'execution', 'adjusted', 'fills', 'forward',
    'bar', 'basis', 'count', 'bool_count', 'calendar', 'source', 'asof', 'dates', 'missing_close',
    'zero_close', 'nan_close', 'score', 'bool_score', 'missing_score', 'limits', 'size'])
def test_publication_rejects_resigned_invalid_contract(change):
    value = public_result()
    if change == 'authority':
        value['live_trading_enabled'] = 0
    elif change == 'execution':
        value['execution_admitted'] = True
    elif change == 'adjusted':
        value['return_adjustment_certified'] = True
    elif change == 'fills':
        value['fills'] = [{'price': 2}]
    elif change == 'forward':
        value['forward_return'] = 0
    elif change == 'bar':
        value['full_bar_qualification'] = True
    elif change == 'basis':
        value['price_basis'] = 'ADJUSTED'
    elif change == 'count':
        value['required_symbols'] = 51
    elif change == 'bool_count':
        value['required_symbols'] = True
    elif change == 'calendar':
        value['bindings']['calendar_sha256'] = '0'*64
    elif change == 'source':
        value['bindings']['code_revision'] = 'short'
    elif change == 'asof':
        value['as_of'] = '2026-10-23'
    elif change == 'dates':
        value['sessions'][0] = value['sessions'][1]
    elif change == 'missing_close':
        value['close_histories']['510300'].pop()
    elif change == 'zero_close':
        value['close_histories']['510300'][0]['close'] = '0'
    elif change == 'nan_close':
        value['close_histories']['510300'][0]['close'] = 'NaN'
    elif change == 'score':
        value['scores'][0]['score'] = .2
    elif change == 'bool_score':
        value['scores'][0]['score'] = True
    elif change == 'missing_score':
        value['scores'] = []
    elif change == 'limits':
        value['limitations'] = []
    else:
        value['padding'] = 'x'*300_000
    with pytest.raises(ValueError):
        validate_etf_trend_publication(resign(value))


def archive(tmp_path):
    value = public_result()
    root = tmp_path/signed(value['bindings'])['evidence_sha256']
    raw = UnifiedBatchStore(root/'capture-store').capture([capture()])
    receipt = signed({**AUTHORITY, 'bindings': value['bindings'], 'raw': raw})
    (root/'receipt.json').write_text(json.dumps(receipt))
    value['receipt_sha256'] = file_hash(root/'receipt.json')
    value = resign(value)
    (root/'diagnostic.json').write_text(json.dumps(value))
    return root, value


@pytest.fixture
def sealed_archive(monkeypatch):
    # Short paths keep the real content-addressed raw store below Windows MAX_PATH.
    if os.name == 'nt':
        # The fixture's Linux directory-fsync writer is unrelated to archive verification.
        monkeypatch.setattr('scripts.unified_store._fsync_directory', lambda path: None)
    with TemporaryDirectory(prefix='etf-') as directory:
        yield archive(Path(directory))


def test_archive_replays_old_frozen_raw_receipts_without_current_asof(sealed_archive):
    root, value = sealed_archive
    assert verify_etf_archive(root, calendar()) == value


def test_archive_rejects_resigned_close_and_score_drift(sealed_archive):
    root, value = sealed_archive
    value['close_histories']['510300'][-1]['close'] = '2.4'
    value['scores'][0]['score'] = .2
    value = resign(value)
    assert validate_etf_trend_publication(value) == value
    (root/'diagnostic.json').write_text(json.dumps(value))
    with pytest.raises(ValueError, match='RAW_REPLAY_MISMATCH'):
        verify_etf_archive(root, calendar())


def test_archive_rejects_modified_raw_pack_and_receipt(sealed_archive):
    root, _ = sealed_archive
    blob = next((root/'capture-store/objects').iterdir())
    original = blob.read_bytes()
    blob.write_bytes(bytes([original[0] ^ 1])+original[1:])
    with pytest.raises(ValueError, match='CHECKSUM_MISMATCH'):
        verify_etf_archive(root, calendar())
    blob.write_bytes(original)
    (root/'receipt.json').write_text('{}')
    with pytest.raises(ValueError):
        verify_etf_archive(root, calendar())
