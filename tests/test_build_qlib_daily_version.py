import json
import os
from copy import deepcopy

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from test_data_versions import version as sealed_version
from test_prepare_qlib_incremental import inputs as prepare_inputs
from test_prepare_qlib_incremental import windows_atomic_writes as windows_write_fixture

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from scripts import build_qlib_daily_version as builder
from scripts import prepare_qlib_incremental as preparation

inputs = prepare_inputs
windows_atomic_writes = windows_write_fixture


@pytest.fixture(autouse=True)
def json_durability_on_windows(monkeypatch):
    if os.name == 'nt':
        monkeypatch.setattr(builder, 'sync_directory', lambda _: None)
        original = builder.write_json_immutable

        def write(path, value):
            try:
                original(path, value)
            except PermissionError:
                assert path.is_file() and json.loads(path.read_text()) == value

        monkeypatch.setattr(builder, 'write_json_immutable', write)
        original_atomic = builder.write_json_atomic

        def atomic(path, value):
            try:
                original_atomic(path, value)
            except PermissionError:
                assert path.is_file() and json.loads(path.read_text()) == value

        monkeypatch.setattr(builder, 'write_json_atomic', atomic)


def benchmark():
    return [{'date': '2026-10-23T00:00:00.000Z', 'open': 3992.904, 'high': 4109.518,
             'low': 3958.138, 'close': 4105.329, 'volume': 23132521300, 'amount': 558204983057}]


def body():
    return json.dumps([
        {**benchmark()[0], 'day': '2026-10-23'},
        {'day': '2026-10-26', 'open': 4078.570, 'high': 4107.794, 'low': 4057.563,
         'close': 4103.543, 'volume': 19214715500},
    ]).encode()


def test_benchmark_only_appends_observed_index_and_preserves_unknown_turnover():
    frozen = benchmark()
    original = deepcopy(frozen)
    result = builder.append_benchmark(frozen, body(), end='2026-10-26')
    assert frozen == original and result[:-1] == original
    assert result[-1]['close'] == 4103.543 and result[-1]['amount'] is None


@pytest.mark.parametrize('change', ['price', 'volume', 'missing', 'duplicate', 'bad_ohlc'])
def test_benchmark_basis_and_scope_drift_are_rejected(change):
    values = json.loads(body())
    if change == 'price':
        values[0]['open'] += 1
    elif change == 'volume':
        values[0]['volume'] += 1
    elif change == 'missing':
        values.pop(0)
    elif change == 'duplicate':
        values[1]['day'] = values[0]['day']
    else:
        values[1]['high'] = 1
    with pytest.raises(ValueError):
        builder.append_benchmark(benchmark(), json.dumps(values).encode(), end='2026-10-26')


def old_dataset(base, row):
    value = signed({**AUTHORITY, 'stocks': [row], 'end': row['end'], 'accepted_symbols': 1,
                    'requested_symbols': 1, 'rows': row['rows'], 'limitations': ['NON_PIT']})
    builder.write_json_immutable(base/'dataset.json', value)
    return value


def test_dataset_materializes_verified_current_partition_without_mutating_original(
    inputs, tmp_path,
):
    base, prepared, row, batch, store, frozen, _ = inputs
    preparation.prepare_stock(base, prepared, row, batch, store, preparation.Calendar(
        {'valid_from': '2026-10-01', 'valid_through': '2026-10-31', 'weekday_closures': []}))
    dataset = old_dataset(base, row)
    request = signed({**AUTHORITY, 'base_dataset_sha256': dataset['evidence_sha256'],
                      'batches': {'600036': batch['batch_id']}, 'day': '2026-10-26',
                      'universe_version': 'b'*64})
    stage = tmp_path/'version-stage'
    value = builder.copy_dataset(base, prepared, stage, request=request)
    assert value['end'] == '2026-10-26' and value['incremental']['current_symbols'] == ['600036']
    assert value['ancestor_dataset_sha256'] == [dataset['evidence_sha256']]
    assert value['stocks'][0]['rows'] == 3
    assert pq.read_table(stage/'stocks/600036/daily.parquet').to_pylist()[:2] == frozen
    assert pq.read_table(base/'stocks/600036/daily.parquet').to_pylist() == frozen


def test_explicit_failed_input_keeps_only_history_and_no_current_eligibility(inputs, tmp_path):
    base, prepared, row, batch, _, frozen, _ = inputs
    dataset = old_dataset(base, row)
    request = signed({**AUTHORITY, 'base_dataset_sha256': dataset['evidence_sha256'],
                      'batches': {'600036': batch['batch_id']}, 'day': '2026-10-26',
                      'universe_version': 'b'*64})
    value = builder.copy_dataset(base, prepared, tmp_path/'stage', request=request,
                                 ready_symbols=set())
    assert value['incremental']['current_symbols'] == []
    assert value['incremental']['blocked_symbols'] == ['600036']
    assert value['stocks'][0]['end'] == '2026-10-23'
    assert pq.read_table(tmp_path/'stage/stocks/600036/daily.parquet').to_pylist() == frozen


def test_rehashed_prepared_file_cannot_rewrite_the_old_normalized_prefix(inputs, tmp_path):
    base, prepared, row, batch, store, _, _ = inputs
    result = preparation.prepare_stock(base, prepared, row, batch, store, preparation.Calendar(
        {'valid_from': '2026-10-01', 'valid_through': '2026-10-31', 'weekday_closures': []}))
    dataset = old_dataset(base, row)
    file = prepared/'stocks/600036'/result['daily_file']
    rows = pq.read_table(file).to_pylist()
    rows[0]['close'] += 0.01
    pq.write_table(pa.Table.from_pylist(rows), file)
    result['files'][result['daily_file']] = builder.file_hash(file)
    result = signed({k: v for k, v in result.items() if k != 'evidence_sha256'})
    (file.parent/'manifest.json').write_text(json.dumps(result))
    request = signed({**AUTHORITY, 'base_dataset_sha256': dataset['evidence_sha256'],
                      'batches': {'600036': batch['batch_id']}, 'day': '2026-10-26',
                      'universe_version': 'b'*64})
    with pytest.raises(ValueError, match='PREFIX_CONTENT_CHANGED'):
        builder.copy_dataset(base, prepared, tmp_path/'stage', request=request)


def test_interrupted_initialization_preserves_original_evidence_and_restarts_cleanly(tmp_path):
    stage = tmp_path/'staging'/'a'
    (stage/'benchmark').mkdir(parents=True)
    raw = stage/'benchmark/forward-source.json'
    raw.write_bytes(b'captured-before-interruption')
    builder.preserve_incomplete_stage(stage)
    assert not stage.exists()
    retained = list((stage.parent/'incomplete').glob('a-*/benchmark/forward-source.json'))
    assert len(retained) == 1 and retained[0].read_bytes() == b'captured-before-interruption'
    stage.mkdir()
    (stage/'dataset.json').write_text('completion marker')
    builder.preserve_incomplete_stage(stage)
    assert stage.is_dir() and len(list((stage.parent/'incomplete').iterdir())) == 1


def test_sealed_build_recovers_before_receipt_and_after_move_without_duplicate_events(tmp_path):
    archive, database = tmp_path/'archive', tmp_path/'catalog.sqlite'
    identity, root, value = sealed_version(archive)
    stage = archive/'staging'/'build-a'
    stage.parent.mkdir()
    root.rename(stage)
    before = {p.relative_to(stage): p.read_bytes() for p in stage.rglob('*') if p.is_file()}
    result = builder.publish_sealed(stage, archive, database, 'build-a', value['bindings'])
    assert result['version_id'] == identity and not stage.exists()
    assert {p.relative_to(root): p.read_bytes() for p in root.rglob('*') if p.is_file()} == before
    replay = builder.publish_sealed(root, archive, database, 'build-a', value['bindings'])
    assert replay == result
    assert len(builder.DataVersionCatalog(database, archive).events()) == 1
    with pytest.raises(ValueError, match='BINDING_CHANGED'):
        builder.publish_sealed(root, archive, database, 'build-a', {'nonce': 'changed'})
