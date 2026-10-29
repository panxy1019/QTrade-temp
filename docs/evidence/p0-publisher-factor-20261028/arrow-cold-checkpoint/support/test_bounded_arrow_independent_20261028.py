"""Independent bounded Arrow contract review; temporary workspace only."""
from __future__ import annotations

import io
import struct
import sys
from pathlib import Path

sys.dont_write_bytecode = True
REPO = Path(r'E:\QTrade-git')
sys.path[:0] = [str(REPO), str(REPO / 'src'), str(REPO / 'tests')]

import pyarrow as pa
import pyarrow.parquet as pq
import pytest
from scripts import unified_store as m
from test_unified_store import BATCH, capture, commit, store


def typed(value):
    if isinstance(value, dict):
        return (type(value), tuple((type(k), k, typed(v)) for k, v in value.items()))
    if isinstance(value, (list, tuple)):
        return type(value), tuple(map(typed, value))
    return type(value), value


def outcome(fn):
    try:
        return 'OK', typed(fn())
    except Exception as error:
        return type(error), error.args, str(error)


def compare(table):
    assert table.schema.equals(m.FIELDS, check_metadata=False)
    assert outcome(lambda: m._export_rows(table)) == outcome(table.to_pylist)


def rows(count):
    values = [None, '', '\x00', 'a\x00b', '\r\n\t', '\x7f\x80\u07ff\u0800',
              '\ud7ff\ue000\uffff', '\U00010000\U0010ffff', '\ufeff\u2028\u2029',
              'A\u030a\u034f', '\U0001f469\u200d\U0001f4bb', '汉字', '0', '-0',
              '1.00', '1e999999', 'Infinity', 'NaN', '\\"']
    return [{name: ([values[(row+i) % len(values)] for i in range(row % 7)]
                   if row % 3 else None) if name == 'source_hashes'
             else values[(row+column) % len(values)]
             for column, name in enumerate(m.FIELDS.names)} for row in range(count)]


@pytest.mark.parametrize('count', [0, 1, 17, 53, 2400])
@pytest.mark.parametrize('start', [0, 1, 5])
@pytest.mark.parametrize('stride', [1, 2, 7])
def test_slice_chunk_layout_and_types(count, start, stride):
    whole = pa.Table.from_pylist(rows(count+10), schema=m.FIELDS)
    selection = whole.slice(start, count)
    pieces = [selection.slice(i, min(stride, count-i)) for i in range(0, count, stride)]
    empty = whole.slice(0, 0)
    table = pa.concat_tables([empty, *pieces, empty]).replace_schema_metadata({b'note': b'review'})
    compare(table)
    assert typed(m._export_rows(table)) == typed(selection.to_pylist())


def bad_table(field, bad, mode):
    data = b'before'+bad+b'after'
    validity = b'\x05' if mode == 'null_child' else None
    invalid = pa.Array.from_buffers(pa.string(), 3, [
        pa.py_buffer(validity) if validity is not None else None,
        pa.py_buffer(struct.pack('<4i', 0, 6, 6+len(bad), len(data))), pa.py_buffer(data)])
    columns = []
    for name in m.FIELDS.names:
        if name == 'source_hashes':
            if name == field:
                column = pa.ListArray.from_arrays(pa.array([0, 1, 2, 3]), invalid,
                    mask=pa.array([False, mode == 'null_parent', False]))
            else:
                column = pa.array([['one'], None, ['three']], type=pa.list_(pa.string()))
        else:
            column = invalid if name == field else pa.array(['one', None, 'three'])
        columns.append(column)
    return pa.Table.from_arrays(columns, schema=m.FIELDS)


@pytest.mark.parametrize('field', m.FIELDS.names)
@pytest.mark.parametrize('bad', [b'\xff', b'\xed\xa0\x80', b'\xf0\x90', b'\xc0\x80'])
@pytest.mark.parametrize('mode', ['plain', 'null_child', 'null_parent'])
def test_invalid_hidden_and_selected_offsets(field, bad, mode):
    table = bad_table(field, bad, mode)
    for start, length in [(0, 3), (0, 1), (1, 1), (2, 1), (3, 0)]:
        sliced = table.slice(start, length)
        compare(sliced)
        # Preserve empty chunk and independent nonzero array offsets.
        compare(pa.concat_tables([table.slice(0, 0), sliced, table.slice(0, 0)]))


@pytest.mark.parametrize('field', m.FIELDS.names)
def test_multiple_invalid_columns_keep_original_first_error(field):
    table = bad_table(field, b'\xf0\x90', 'plain')
    other = 'source_hashes' if field != 'source_hashes' else 'symbol'
    extra = bad_table(other, b'\xc0\x80', 'plain').column(other)
    table = table.set_column(m.FIELDS.get_field_index(other), m.FIELDS.field(other), extra)
    original = outcome(table.to_pylist)
    assert original[0] is UnicodeDecodeError
    assert outcome(lambda: m._export_rows(table)) == original


@pytest.mark.parametrize('dictionary', [True, False, m.FIELDS.names])
@pytest.mark.parametrize('row_group', [1, 11, 1000])
@pytest.mark.parametrize('page_version', ['1.0', '2.0'])
def test_parquet_encoding_independent_of_arrow_contract(dictionary, row_group, page_version):
    table = pa.Table.from_pylist(rows(79), schema=m.FIELDS)
    raw = io.BytesIO()
    pq.write_table(table, raw, use_dictionary=dictionary, row_group_size=row_group,
                   data_page_version=page_version, compression='zstd')
    file = pq.ParquetFile(io.BytesIO(raw.getvalue()))
    assert file.schema_arrow.equals(m.FIELDS, check_metadata=False)
    decoded = file.read()
    compare(decoded)
    assert typed(m._export_rows(decoded)) == typed(table.to_pylist())


@pytest.mark.parametrize('damage', ['rename', 'drop', 'reorder', 'large_string', 'dictionary',
                                   'nonnullable'])
def test_schema_gate_precedes_fast_path(store, monkeypatch, damage):
    manifest = commit(store, capture(store))
    table = pq.ParquetFile(io.BytesIO(store.read_blob(manifest['normalized']))).read()
    if damage == 'rename':
        table = table.rename_columns(['renamed', *m.FIELDS.names[1:]])
    elif damage == 'drop':
        table = table.drop_columns(['symbol'])
    elif damage == 'reorder':
        table = table.select([*m.FIELDS.names[1:], 'symbol'])
    elif damage == 'large_string':
        table = table.set_column(0, 'symbol', table.column(0).cast(pa.large_string()))
    elif damage == 'dictionary':
        table = table.set_column(0, 'symbol', table.column(0).dictionary_encode())
    elif damage == 'nonnullable':
        schema = m.FIELDS.set(0, m.FIELDS.field(0).with_nullable(False))
        table = table.cast(schema)
    raw = io.BytesIO()
    pq.write_table(table, raw, compression='zstd')
    manifest['normalized'] = store._put_blob(raw.getvalue(), '.parquet')
    manifest = m.signed({k: v for k, v in manifest.items() if k != 'evidence_sha256'})
    store.batch_path(BATCH).write_bytes(m.canonical(manifest))
    monkeypatch.setattr(m, '_export_rows', lambda _: pytest.fail('schema gate bypassed'))
    material = store._batch_material(BATCH)
    material.check_export_bounds()
    with pytest.raises(ValueError, match='NORMALIZED_INCREMENTAL_SCHEMA_OR_COUNT_MISMATCH'):
        material.validate()


def test_numpy_absence_uses_original_without_full_validation(monkeypatch):
    class Sentinel:
        def to_pylist(self):
            return [{'sentinel': object()}]
        def validate(self, **kwargs):
            pytest.fail('optional NumPy absence must retain original decoder')
    monkeypatch.setattr(m.importlib.util, 'find_spec', lambda _: None)
    assert 'sentinel' in m._export_rows(Sentinel())[0]
