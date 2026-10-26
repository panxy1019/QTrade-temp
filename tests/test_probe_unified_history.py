import json
import os
import sys
from datetime import UTC, date, datetime
from types import SimpleNamespace
from unittest.mock import Mock

import pytest

from qtrade.market_data.baostock_five_minute import BAOSTOCK_FIVE_MINUTE_FIELDS
from qtrade.market_data.sina_minute import SINA_FIVE_MINUTE_SESSION_TIMES
from scripts import probe_unified_history as module
from scripts import vendor_session

NOW = datetime(2026, 10, 26, 11, tzinfo=UTC)
DAY = date(2026, 10, 23)


@pytest.fixture(autouse=True)
def directory_fsync_on_windows(monkeypatch):
    if os.name == "nt":
        original = module.write_json_immutable

        def write(path, value):
            try:
                original(path, value)
            except PermissionError:
                assert path.is_file() and json.loads(path.read_text()) == value

        monkeypatch.setattr(module, "write_json_immutable", write)


def rows():
    return [[str(DAY), datetime.combine(DAY, t).strftime("%Y%m%d%H%M%S") + "000",
             "sh.600036", "10", "11", "9", "10", "100", "1000", "3"]
            for t in SINA_FIVE_MINUTE_SESSION_TIMES]


def api(values=None):
    raw = {"fields": list(BAOSTOCK_FIVE_MINUTE_FIELDS), "vendor_error_code": "0",
           "rows": rows() if values is None else values}
    result = Mock()
    result.query_history_k_data_plus.return_value = module.RecordedResult(raw)
    return result


def run(tmp_path, client, **kwargs):
    return module.probe(client, tmp_path, symbol="600036", security_type="STOCK", start=DAY,
                        end=kwargs.pop("end", DAY), now=NOW, revision="a" * 40, **kwargs)


def test_exact_raw_rows_are_sealed_before_parser_and_never_grant_admission(tmp_path, monkeypatch):
    original = module.fetch_baostock_five_minute_history

    def parse(*args, **kwargs):
        raw = json.loads(next(tmp_path.glob('*/raw.json')).read_text())
        assert raw['rows'] == rows()
        assert 'NOT_TCP_WIRE_BYTES' in raw['capture_basis']
        return original(*args, **kwargs)

    monkeypatch.setattr(module, 'fetch_baostock_five_minute_history', parse)
    result = run(tmp_path, api())
    assert result['state'] == 'SOURCE_WINDOW_OBSERVED_NOT_ADMITTED'
    assert result['observed_bars'] == 48 and result['full_grid_sessions'] == 1
    assert result['amount_checked_rows'] == 48
    assert result['admitted_bar_count'] == 0
    assert result['ten_year_history_certified'] is False
    assert result['historical_available_at'] is None


@pytest.mark.parametrize('change', ['amount', 'identity', 'adjustflag', 'duplicate', 'fields'])
def test_failed_content_retains_raw_and_zero_admission(tmp_path, change):
    values = rows()
    if change == 'amount':
        values[0][8] = '100000000'
    if change == 'identity':
        values[0][2] = 'sz.000001'
    if change == 'adjustflag':
        values[0][9] = '2'
    if change == 'duplicate':
        values.append(values[0])
    client = api(values)
    if change == 'fields':
        client.query_history_k_data_plus.return_value.fields = ['wrong']
    result = run(tmp_path, client)
    assert result['state'] == 'FAILED' and result['admitted_bar_count'] == 0
    raw = json.loads(next(tmp_path.glob('*/raw.json')).read_text())
    assert raw['rows'] == values
    assert next(tmp_path.glob('*/receipt.json')).exists()


def test_partial_window_reports_partial_grid_without_filling_or_inferencing_halt(tmp_path):
    result = run(tmp_path, api(rows()[:3]))
    assert result['partial_sessions'] == {str(DAY): 3}
    assert result['full_grid_sessions'] == 0 and result['observed_bars'] == 3
    assert result['state'] == 'SOURCE_WINDOW_OBSERVED_NOT_ADMITTED'
    assert result['missing_sessions_reason'].startswith('UNKNOWN')


def test_transport_failure_and_empty_source_remain_failed_audits(tmp_path):
    client = api()
    client.query_history_k_data_plus.side_effect = TimeoutError('do not retain message')
    result = run(tmp_path, client)
    assert result['state'] == 'FAILED'
    raw = json.loads((tmp_path / result['attempt_id'] / 'raw.json').read_text())
    assert raw['transport_error'] == 'TimeoutError' and raw['body_complete'] is False
    assert 'do not retain message' not in json.dumps(raw)
    assert run(tmp_path, api([]))['state'] == 'FAILED'


def test_probes_are_new_immutable_attempts_and_queries_are_bounded(tmp_path):
    first = run(tmp_path, api())
    saved = {p: p.read_bytes() for p in (tmp_path / first['attempt_id']).glob('*.json')}
    second = run(tmp_path, api())
    assert first['attempt_id'] != second['attempt_id']
    assert all(p.read_bytes() == value for p, value in saved.items())
    with pytest.raises(ValueError, match='BOUND'):
        run(tmp_path, api(), end=date(2026, 11, 25))
    with pytest.raises(ValueError, match='CLOCK'):
        run(tmp_path, api(), end=NOW.date())


@pytest.mark.parametrize('blocking', [True, False])
def test_shared_session_reuses_one_lease_and_low_priority_never_waits(
    tmp_path, monkeypatch, blocking,
):
    locks = []
    monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace(
        LOCK_EX=1, LOCK_NB=2, flock=lambda lease, flags: locks.append((lease.name, flags)),
    ))
    monkeypatch.setattr(vendor_session, 'Path', lambda _: tmp_path)
    with vendor_session.baostock_session(blocking=blocking):
        assert locks == [(str(tmp_path / 'baostock-session.lock'), 1 if blocking else 3)]


def test_busy_lease_prevents_low_priority_body_from_running(tmp_path, monkeypatch):
    def busy(*args):
        raise BlockingIOError

    monkeypatch.setitem(sys.modules, 'fcntl', SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=busy))
    monkeypatch.setattr(vendor_session, 'Path', lambda _: tmp_path)
    with pytest.raises(BlockingIOError), vendor_session.baostock_session(blocking=False):
        pytest.fail('vendor query must not run')
