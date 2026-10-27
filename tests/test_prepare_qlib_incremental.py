import json
import os
from datetime import UTC, datetime
from decimal import Decimal
from hashlib import sha256
from types import SimpleNamespace
from unittest.mock import Mock

import pyarrow as pa
import pyarrow.parquet as pq
import pytest

from qtrade.data import Bar
from qtrade.market_data.akshare import SHANGHAI
from qtrade.market_data.sina_incremental import IncrementalBar
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.qlib_adapter import normalize_daily
from scripts import prepare_qlib_incremental as module
from scripts import unified_store

NOW = datetime(2026, 10, 26, 9, tzinfo=UTC)
CALENDAR = Calendar({'valid_from': '2026-10-01', 'valid_through': '2026-10-31',
                     'weekday_closures': []})


@pytest.fixture(autouse=True)
def windows_atomic_writes(monkeypatch):
    monkeypatch.setattr(module.shutil, 'disk_usage', lambda _: SimpleNamespace(free=200*1024**3))
    if os.name == 'nt':
        monkeypatch.setattr(unified_store, '_fsync_directory', lambda _: None)
        for target, name in ((module, 'write_json_immutable'), (module, 'write_json_atomic'),
                             (unified_store, 'write_json_immutable')):
            original = getattr(target, name)

            def write(path, value, _original=original):
                try:
                    _original(path, value)
                except PermissionError:
                    assert path.is_file() and json.loads(path.read_text()) == value

            monkeypatch.setattr(target, name, write)
        from scripts import pit_collection_queue

        monkeypatch.setattr(pit_collection_queue, 'write_json_atomic', module.write_json_atomic)


def raw(day, price):
    return {'date': day, 'open': price, 'high': price+1, 'low': price-1, 'close': price,
            'volume': 1000, 'amount': price*1000}


@pytest.fixture
def inputs(tmp_path, monkeypatch):
    base, output = tmp_path/'old', tmp_path/'new'
    stock = base/'stocks/600036'
    stock.mkdir(parents=True)
    frozen = normalize_daily([raw('2026-10-22', 10), raw('2026-10-23', 11)],
                             [{'d': '2010-01-01', 'f': 2}], symbol='600036')
    pq.write_table(pa.Table.from_pylist(frozen), stock/'daily.parquet')
    (stock/'sh600036-raw.js').write_text('preserved original source')
    row = signed({'symbol': '600036', 'name': 'test', 'rows': 2, 'start': '2026-10-22',
                  'end': '2026-10-23', 'files': {
                      p.name: sha256(p.read_bytes()).hexdigest() for p in stock.iterdir()}})
    store = unified_store.UnifiedBatchStore(tmp_path/'lake')
    raw_rows = [{**raw(day, price), 'day': day} for day, price in (
        ('2026-10-23', 11), ('2026-10-26', 12))]
    captured = store.capture([{
        'source_id': 'sina_klines', 'request': {'symbol': 'sh600036', 'scale': '240',
                                             'ma': 'no', 'datalen': '2'},
        'received_at': NOW.isoformat(), 'status_code': 200, 'body_complete': True,
        'transport_error': None, 'body': json.dumps(raw_rows).encode(),
    }])
    stamp = datetime(2026, 10, 26, 15, tzinfo=SHANGHAI)
    record = IncrementalBar(Bar('600036', 'SSE', stamp, stamp.date(),
                               Decimal(12), Decimal(13), Decimal(11), Decimal(12), Decimal(1000)),
                            'day', Decimal(12000), Decimal(12), stamp)
    batch = store.commit('a'*32, {
        'frequency': 'day', 'target_end': stamp.isoformat(), 'universe_version': 'b'*64,
        'symbols': ['600036'], 'code_revision': 'c'*40, 'after_by_symbol': {'600036': None},
    }, captured, [record], {'600036': {'state': 'SUCCEEDED'}}, completed_at=NOW)
    fetch = Mock(return_value=b'var f={"data":[{"d":"2010-01-01","f":2}]};')
    monkeypatch.setattr(module, 'fetch_fixed', fetch)
    return base, output, row, batch, store, frozen, fetch


def test_real_partition_preserves_prefix_and_raw_inputs_and_resumes_without_download(inputs):
    base, output, row, batch, store, frozen, fetch = inputs
    original = (base/'stocks/600036/daily.parquet').read_bytes()
    value = module.prepare_stock(base, output, row, batch, store, CALENDAR)
    assert value['end'] == '2026-10-26' and value['rows'] == 3
    rows = pq.read_table(output/'stocks/600036'/value['daily_file']).to_pylist()
    assert rows[:2] == frozen and rows[-1]['close'] == pytest.approx(1.2)
    assert (base/'stocks/600036/daily.parquet').read_bytes() == original
    assert module.prepare_stock(base, output, row, batch, store, CALENDAR) == value
    fetch.assert_called_once()
    assert value['factor_capture_file'] in value['files']
    assert value['normalization']['corporate_actions_certified'] is False


def test_revised_factor_snapshot_retained_but_partition_not_admitted(inputs):
    base, output, row, batch, store, _, fetch = inputs
    fetch.return_value = b'var f={"data":[{"d":"2010-01-01","f":3}]};'
    with pytest.raises(ValueError, match='REVISED_FROZEN'):
        module.prepare_stock(base, output, row, batch, store, CALENDAR)
    assert list((output/'stocks/600036').glob('factors-*.json'))
    assert not (output/'stocks/600036/manifest.json').exists()


def test_changed_checkpoint_and_corrupt_base_cannot_be_silently_reused(inputs):
    base, output, row, batch, store, _, _ = inputs
    module.prepare_stock(base, output, row, batch, store, CALENDAR)
    changed = {**batch, 'evidence_sha256': 'd'*64}
    with pytest.raises(ValueError, match='CHECKPOINT_CHANGED'):
        module.prepare_stock(base, output, row, changed, store, CALENDAR)
    (base/'stocks/600036/daily.parquet').write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='HASH_MISMATCH'):
        module.prepare_stock(base, output, row, batch, store, CALENDAR)


def test_failed_preparation_does_not_starve_unattempted_symbols_or_claim_version_ready(
    tmp_path, monkeypatch,
):
    previous = tmp_path/'old'
    previous.mkdir()
    stocks = [{'symbol': f'60000{i}'} for i in range(4)]
    module.write_json_immutable(previous/'dataset.json', signed({**AUTHORITY, 'stocks': stocks,
                                                               'end': '2026-10-23'}))
    progress_path = tmp_path/'unified-data/collection-progress.json'
    module.write_json_immutable(progress_path, signed({
        **AUTHORITY, 'universe_version': 'b'*64, 'required_through': {'day': NOW.isoformat()},
        'tasks': [{'symbol': r['symbol'], 'frequency': 'day', 'state': 'SUCCEEDED',
                   'trading_day': '2026-10-26', 'last_success_batch': 'a'*32} for r in stocks],
    }))
    seen = []

    def fail(base, output, row, *args):
        seen.append(row['symbol'])
        raise ValueError('SOURCE_UNAVAILABLE')

    monkeypatch.setattr(module, 'prepare_stock', fail)
    monkeypatch.setattr(module.UnifiedBatchStore, 'load', lambda *args: {
        'spec': {'universe_version': 'b'*64, 'frequency': 'day',
                 'target_end': '2026-10-26T15:00:00+08:00',
                 'symbols': [r['symbol'] for r in stocks]},
        'completed_at': NOW.isoformat(), 'normalized': {'sha256': 'd'*64},
        'evidence_sha256': 'e'*64,
        'outcomes': {r['symbol']: {'state': 'SUCCEEDED', 'missing_amount_bars': 0} for r in stocks},
    })
    first = module.run(tmp_path, previous, revision='c'*40, maximum=1, day=NOW.date().isoformat())
    second = module.run(tmp_path, previous, revision='c'*40, maximum=1, day=NOW.date().isoformat())
    assert seen == ['600000', '600001']
    assert first['attempted_new_symbols'] == second['attempted_new_symbols'] == 1
    assert second['provider_published'] is False and second['model_activated'] is False
    assert second['retry_queue']['waiting_retry_symbols'] == 2
    assert second['retry_queue']['unattempted_symbols'] == 2
    assert not list(tmp_path.glob('**/ready.json'))


def test_malformed_factor_capture_survives_successful_retry_without_overwrite(inputs):
    base, output, row, batch, store, _, fetch = inputs
    fetch.return_value = b'<html>temporary source response</html>'
    with pytest.raises(IndexError):
        module.prepare_stock(base, output, row, batch, store, CALENDAR)
    failed = next((output/'stocks/600036').glob('factors-*.json'))
    old_bytes = failed.read_bytes()
    fetch.return_value = b'var f={"data":[{"d":"2010-01-01","f":2}]};'
    result = module.prepare_stock(base, output, row, batch, store, CALENDAR)
    assert result['rows'] == 3 and failed.read_bytes() == old_bytes
    assert len(list((output/'stocks/600036').glob('factors-*.json'))) == 2
