"""Strict protocol and immutable archive contract for historical comparisons."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.data_versions import file_hash
from qtrade.research.qlib_publication import verify_archive, verify_signed

FAMILIES = ('momentum20', 'reversal5', 'lowvol20', 'multifactor_equal_rank',
            'equal_weight_universe', 'ridge', 'lightgbm')
PROTOCOL_KEYS = ('segments', 'label', 'portfolio', 'exchange', 'account', 'benchmark',
                 'universe_policy', 'source_constraint_policy')
LIMITATIONS = ['CURRENT_POOL_SURVIVORSHIP_AND_SELECTION_BIAS', 'FULL_PIT_NOT_CERTIFIED',
               'HISTORICAL_AVAILABLE_AT_UNKNOWN', 'POST_HOC_OOS_NOT_FORWARD_PERFORMANCE',
               'DAILY_OPEN_CAPACITY_AND_LIMIT_PROXIES_NOT_AUCTION_REPLAY',
               'EQUAL_UNIVERSE_IS_ALLOCATION_CONTROL_NOT_IDENTICAL_TOPK_PORTFOLIO',
               'ETF_PROVIDER_UNAVAILABLE']


def bind_archives(ridge: Path, lightgbm: Path) -> dict[str, Any]:
    summaries = [verify_archive(p) for p in (ridge, lightgbm)]
    requests = [json.loads((p/'request.json').read_text()) for p in (ridge, lightgbm)]
    inventories = [json.loads((p/'checksums.json').read_text())['files']
                   for p in (ridge, lightgbm)]
    for root, files in zip((ridge, lightgbm), inventories, strict=True):
        actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
        if (actual != set(files) | {'checksums.json'}
                or any(p.is_symlink() for p in root.rglob('*'))):
            raise ValueError('COMPARISON_INPUT_INVENTORY_INVALID')
    for request, files, name, summary in zip(requests, inventories, ('Ridge', 'LightGBM'),
                                           summaries, strict=True):
        verify_signed(request)
        if not {'predictions.parquet', 'labels.parquet', 'request.json'} <= files.keys():
            raise ValueError('COMPARISON_REQUIRED_ARTIFACTS_MISSING')
        if summary['model'] != 'Alpha158 + '+name:
            raise ValueError('COMPARISON_MODEL_IDENTITY_MISMATCH')
    protocols = [{k: r['config'].get(k) for k in PROTOCOL_KEYS} for r in requests]
    for key in ('data_manifest_sha256', 'source_snapshot_sha256', 'source_revision'):
        if requests[0].get(key) != requests[1].get(key) or not requests[0].get(key):
            raise ValueError('COMPARISON_INPUT_IDENTITY_MISMATCH:'+key)
    if protocols[0] != protocols[1]:
        raise ValueError('COMPARISON_PROTOCOL_MISMATCH')
    providers = [{k: v for k, v in files.items() if k.startswith('provider/')}
                 for files in inventories]
    if not providers[0] or providers[0] != providers[1]:
        raise ValueError('COMPARISON_PROVIDER_MISMATCH')
    config = requests[0]['config']
    if config['segments']['valid'][1] >= config['segments']['test'][0]:
        raise ValueError('COMPARISON_OOS_SPLIT_REQUIRED')
    if not summaries[0].get('execution_constraints'):
        raise ValueError('COMPARISON_SOURCE_CONSTRAINTS_REQUIRED')
    return {'protocol': protocols[0], 'dataset_sha256': requests[0]['data_manifest_sha256'],
            'source_snapshot_sha256': requests[0]['source_snapshot_sha256'],
            'training_source_revision': requests[0]['source_revision'],
            'model_archives': {name: {'run_id': summary['run_id'],
                                    'manifest_sha256': file_hash(path/'checksums.json')}
                               for name, summary, path in zip(('ridge', 'lightgbm'), summaries,
                                                              (ridge, lightgbm), strict=True)},
            'provider_inventory_sha256': signed(providers[0])['evidence_sha256']}


def verify_comparison(root: Path) -> dict[str, Any]:
    manifest = json.loads((root/'manifest.json').read_text())
    verify_signed(manifest)
    if manifest.get('schema_version') != 'qtrade.strategy-comparison.v1' or any(
            manifest.get(k) != v for k, v in AUTHORITY.items()):
        raise ValueError('COMPARISON_AUTHORITY_INVALID')
    files = manifest.get('files', {})
    actual = {p.relative_to(root).as_posix() for p in root.rglob('*') if p.is_file()}
    if not files or len(files) > 100 or actual != set(files) | {'manifest.json'}:
        raise ValueError('COMPARISON_INVENTORY_INVALID')
    for name, digest in files.items():
        path = (root/name).resolve()
        if not path.is_relative_to(root.resolve()) or file_hash(path) != digest:
            raise ValueError('COMPARISON_ARTIFACT_MISMATCH')
    result = json.loads((root/'comparison.json').read_text())
    verify_signed(result)
    if result['comparison_id'] != manifest['comparison_id'] or set(result['strategies']) != set(
            FAMILIES) or result.get('historical_pit_certified') is not False:
        raise ValueError('COMPARISON_RESULT_INVALID')
    return dict(result)


