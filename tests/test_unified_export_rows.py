"""Exact typed decoding and malformed-source behavior for bounded exports."""
from __future__ import annotations

import io
from copy import deepcopy

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from test_unified_store import BATCH, capture, commit
from test_unified_store import store as store

from scripts import unified_store as module


def tree(value):
    if isinstance(value, list):
        return type(value), [tree(item) for item in value]
    if isinstance(value, dict):
        return type(value), [(key, tree(item)) for key, item in value.items()]
    return type(value), value


def outcome(function):
    try:
        return 'OK', tree(function())
    except Exception as error:
        return type(error), str(error)


@pytest.mark.parametrize('count', [0, 1, 25, 50, 512, 2400])
@pytest.mark.parametrize('chunks', [1, 3])
def test_bounded_rows_match_values_types_order_and_parquet(count, chunks):
    values = ['10.0000', '1e999999', 'NaN', '样本😀', '\n\t"\\', '', None]
    rows = [{key: ([None, 'a'*64, '样本😀'] if index % 3 else None)
             if key == 'source_hashes' else values[(index+column) % len(values)]
             for column, key in enumerate(module.FIELDS.names)} for index in range(count)]
    table = pa.concat_tables([pa.Table.from_pylist(rows[start::chunks], schema=module.FIELDS)
                              for start in range(chunks)])
    expected = tree(table.to_pylist())
    assert tree(module._export_rows(table)) == expected
    stream = io.BytesIO()
    pq.write_table(table, stream, compression='zstd')
    decoded = pq.ParquetFile(io.BytesIO(stream.getvalue())).read()
    assert tree(module._export_rows(decoded)) == expected


@pytest.mark.parametrize('field', module.FIELDS.names)
@pytest.mark.parametrize('bad', [b'\xff', b'\xed\xa0\x80', b'\xf0\x90', b'\xc0\x80'])
def test_malformed_utf8_keeps_original_decode_exception(field, bad):
    invalid = pa.array([bad], type=pa.binary()).cast(pa.string(), safe=False)
    columns = []
    for name in module.FIELDS.names:
        if name == 'source_hashes':
            column = (pa.ListArray.from_arrays(pa.array([0, 1]), invalid)
                      if field == name else pa.array([[]], type=pa.list_(pa.string())))
        else:
            column = invalid if field == name else pa.array(['10'], type=pa.string())
        columns.append(column)
    table = pa.Table.from_arrays(columns, schema=module.FIELDS)
    expected = outcome(table.to_pylist)
    assert expected[0] is UnicodeDecodeError
    assert outcome(lambda: module._export_rows(table)) == expected


def test_optional_numpy_absent_retains_original_decoder(monkeypatch):
    table = pa.Table.from_pylist([], schema=module.FIELDS)
    monkeypatch.setattr(module.importlib.util, 'find_spec', lambda _: None)
    assert module._export_rows(table) == table.to_pylist()


def test_fast_path_is_only_used_after_export_bounds(store, monkeypatch):
    commit(store, capture(store))
    original = module._export_rows
    calls = []

    def spy(table):
        calls.append(table.num_rows)
        return original(table)

    monkeypatch.setattr(module, '_export_rows', spy)
    ordinary = store._batch_material(BATCH).validate()
    assert not calls
    bounded = store._batch_material(BATCH)
    bounded.check_export_bounds()
    assert bounded.validate() == ordinary
    assert calls == [1]
    assert bounded.validate() == ordinary and calls == [1]
    # Neither conversion returns an alias that can mutate the immutable source.
    rows = deepcopy(ordinary[2])
    ordinary[2][0]['close'] = 'invalid'
    assert store._batch_material(BATCH).validate()[2] == rows
