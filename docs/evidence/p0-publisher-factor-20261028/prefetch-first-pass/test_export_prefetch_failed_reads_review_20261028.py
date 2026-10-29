"""Independent fault reproducer; modifies only isolated pytest fixture objects."""
import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO = Path('E:/QTrade-git')
sys.path[:0] = [str(REPO/'src'), str(REPO)]
spec = importlib.util.spec_from_file_location('prefetch_review_fixture', REPO/'tests/test_unified_market.py')
fixture = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = fixture
spec.loader.exec_module(fixture)
source, export, exporter = fixture.source, fixture.export, fixture.exporter


@pytest.mark.parametrize('failure', ['manifest_json', 'raw_checksum', 'normalized_checksum'])
def test_failed_physical_constructor_reads_cannot_bypass_source_budget(source, monkeypatch, failure):
    base, _, _, _, store, add = source
    manifest = add()
    export(source)
    pointer = base/'unified-data/market-public/unified-market.json'
    state = base/'unified-data/market-export-state.json'
    before = pointer.read_bytes(), state.read_bytes()
    if failure == 'manifest_json':
        path = store.batch_path(manifest['batch_id'])
        path.write_bytes(b'{' + b' '*400)
    else:
        ref = manifest['raw' if failure == 'raw_checksum' else 'normalized']
        path = store._blob_path(ref['sha256'], ref['suffix'])
        raw = path.read_bytes()
        path.write_bytes(bytes([raw[0] ^ 1])+raw[1:])
    actual_reads = []
    original = Path.read_bytes
    def read(path):
        raw = original(path)
        if path.is_relative_to(store.root):
            actual_reads.append((str(path), len(raw)))
        return raw
    monkeypatch.setattr(Path, 'read_bytes', read)
    monkeypatch.setattr(exporter, 'SOURCE_BYTE_BUDGET', 1)
    result = None
    try:
        result = export(source)
    except ValueError as error:
        assert str(error) == 'UNIFIED_MARKET_SOURCE_BYTE_BUDGET'
        assert (pointer.read_bytes(), state.read_bytes()) == before
        return
    actual = sum(count for _, count in actual_reads)
    print(json.dumps({'failure': failure, 'actual_source_bytes_read': actual,
                      'reported_source_bytes_read': result['statistics']['source_bytes_read'],
                      'test_source_byte_budget': 1,
                      'published_state': [e['state'] for e in result['index']['entries']]}))
    pytest.fail('Physical failed-constructor reads bypassed the one-byte source budget')
