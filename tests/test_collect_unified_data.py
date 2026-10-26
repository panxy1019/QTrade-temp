import json
from datetime import UTC, datetime
from unittest.mock import Mock
from uuid import uuid4

import pytest

# Shared store fixture exercises real Parquet and immutable files on Linux.
from test_unified_store import store as platform_store

from qtrade.market_data.sina_minute import SINA_FIVE_MINUTE_SESSION_TIMES
from qtrade.monitoring.data_freshness import Calendar
from scripts import collect_unified_data as collector
from scripts.collect_unified_data import collect_batch

store = platform_store

NOW = datetime(2026, 10, 26, 9, tzinfo=UTC)
CALENDAR = Calendar({"valid_from": "2026-10-08", "valid_through": "2026-10-31",
                     "weekday_closures": []})


def batch(freq='day', after=None, target='2026-10-26T15:00:00+08:00'):
    return {"batch_id": uuid4().hex, "security_types": {'600036': 'STOCK', '000001': 'STOCK'},
            "spec": {"frequency": freq, "target_end": target, "universe_version": 'a' * 64,
                     "symbols": ['600036', '000001'], "code_revision": 'b' * 40,
                     "after_by_symbol": {'600036': after, '000001': after}}}


def fake_source(change=None):
    calls = []

    def source(source_id, params):
        calls.append((source_id, params))
        if source_id == 'sina_quote':
            lines = []
            for code in params['list'].split(','):
                fields = [''] * 34
                for index, value in {0: 'Stock', 1: '10', 3: '10', 4: '11', 5: '9', 8: '100',
                                     9: '1000', 30: '2026-10-26', 31: '15:34:59', 32: '00'}.items():
                    fields[index] = value
                lines.append(f'var hq_str_{code}="' + ','.join(fields) + '";')
            body = '\n'.join(lines).encode()
        else:
            row = {'day': '2026-10-26', 'open': '10', 'high': '11', 'low': '9', 'close': '10',
                   'volume': '100'}
            rows = [row] if params['scale'] == '240' else [
                {**row, 'day': f'2026-10-26 {t}', 'amount': '1000'}
                for t in SINA_FIVE_MINUTE_SESSION_TIMES]
            body = json.dumps(rows).encode()
        capture = {'source_id': source_id, 'request': params, 'received_at': NOW.isoformat(),
                   'status_code': 200, 'transport_error': None, 'body_complete': True, 'body': body}
        return change(capture) if change else capture
    return source, calls


def test_daily_fetch_archives_raw_before_parsing_and_attaches_verified_same_vendor_amount(store):
    source, calls = fake_source()
    result = collect_batch(store, batch(), CALENDAR, source=source)
    assert len(calls) == 3 and result['raw']['captures'] == 3
    assert result['bar_count'] == 2
    assert result['stage_counts'] == {
        'requested': 3, 'downloaded': 3, 'parsed_symbols': 2, 'admitted_symbols': 2,
        'admitted_rows': 2, 'same_vendor_endpoint_matched': 2, 'independent_source_reconciled': 0,
    }
    assert all(r['missing_amount_bars'] == 0 for r in result['outcomes'].values())
    assert store.load(result['batch_id']) == result
    assert result['historical_pit_certified'] is False


def test_malformed_symbol_response_retains_raw_and_does_not_block_other_symbol(store):
    def change(capture):
        if capture['request'].get('symbol') == 'sz000001':
            capture['body'] = b'invalid-json'
        return capture
    source, _ = fake_source(change)
    result = collect_batch(store, batch(), CALENDAR, source=source)
    assert result['outcomes']['600036']['state'] == 'SUCCEEDED'
    assert result['outcomes']['000001']['state'] == 'FAILED'
    assert any(c['body'] == b'invalid-json' for c in store.read_captures(result['raw']))
    assert result['stage_counts']['downloaded'] == 3
    assert result['stage_counts']['parsed_symbols'] == 1


def test_one_unavailable_quote_keeps_valid_daily_prices_with_explicit_missing_amount(store):
    def change(capture):
        if capture['source_id'] == 'sina_quote':
            lines = capture['body'].splitlines()
            capture['body'] = lines[0] + b'\nvar hq_str_sz000001="";'
        return capture
    source, _ = fake_source(change)
    result = collect_batch(store, batch(), CALENDAR, source=source)
    assert result['bar_count'] == 2
    row = result['outcomes']['000001']
    assert row['missing_amount_bars'] == 1
    assert row['warnings'] == ['QUOTE_AMOUNT_UNAVAILABLE']
    assert result['stage_counts']['same_vendor_endpoint_matched'] == 1


def test_price_volume_disagreement_quarantines_daily_input(store):
    def change(capture):
        if capture['source_id'] == 'sina_quote':
            lines = capture['body'].splitlines()
            lines[1] = lines[1].replace(b',100,1000,', b',101,1000,')
            capture['body'] = b'\n'.join(lines)
        return capture
    source, _ = fake_source(change)
    result = collect_batch(store, batch(), CALENDAR, source=source)
    assert result['outcomes']['000001']['error_code'] == 'SAME_VENDOR_DAILY_QUOTE_MISMATCH'
    assert result['outcomes']['600036']['state'] == 'SUCCEEDED'
    assert result['bar_count'] == 1


def test_first_minute_window_and_incremental_append_are_bounded_without_full_history_copy(store):
    source, calls = fake_source()
    initial = collect_batch(store, batch('5min'), CALENDAR, source=source)
    assert initial['bar_count'] == 96
    assert all(p['datalen'] == '96' for _, p in calls)
    source, calls = fake_source()
    later = collect_batch(store, batch('5min', '2026-10-26T14:55:00+08:00'),
                          CALENDAR, source=source)
    assert later['bar_count'] == 2
    assert all(p['datalen'] == '3' for _, p in calls)
    assert store.load(initial['batch_id']) == initial
    assert store.read_blob(later['normalized']) != store.read_blob(initial['normalized'])


def test_missing_minute_grid_and_failed_transport_cannot_create_ready_rows(store):
    def change(capture):
        if capture['request'].get('symbol') == 'sh600036':
            rows = json.loads(capture['body'])
            capture['body'] = json.dumps(rows[:-1]).encode()
        else:
            capture.update(status_code=None, transport_error='TimeoutError', body_complete=False,
                           body=b'')
        return capture
    source, _ = fake_source(change)
    result = collect_batch(store, batch('5min'), CALENDAR, source=source)
    assert result['normalized'] is None
    assert result['outcomes']['600036']['error_code'] == 'MINUTE_WINDOW_GAP_REASON_UNKNOWN'
    assert result['outcomes']['000001']['error_code'] == 'SOURCE_TRANSPORT_ERROR'
    assert result['stage_counts']['downloaded'] == 1


def test_retry_of_sealed_batch_does_not_fetch_or_rewrite_any_old_artifact(store):
    source, _ = fake_source()
    job = batch()
    result = collect_batch(store, job, CALENDAR, source=source)
    forbidden = Mock(side_effect=AssertionError('must replay sealed batch'))
    assert collect_batch(store, job, CALENDAR, source=forbidden) == result
    forbidden.assert_not_called()


def test_fetch_uses_quote_url_contract_and_records_actual_response_completeness(monkeypatch):
    requested = []

    class Response:
        status = 200

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def read(self, limit):
            return b'captured'

    def open_request(request, timeout):
        requested.append(request.full_url)
        return Response()
    monkeypatch.setattr(collector, 'urlopen', open_request)
    value = collector.fetch('sina_quote', {'list': 'sh600036,sz000001'})
    assert requested == ['https://hq.sinajs.cn/list=sh600036,sz000001']
    assert value['body_complete'] and value['transport_error'] is None
    with pytest.raises(ValueError, match='UNAPPROVED'):
        collector.fetch('external_source', {})
