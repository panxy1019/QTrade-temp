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
    return validate_comparison_publication(dict(result))


def validate_comparison_publication(value: dict[str, Any]) -> dict[str, Any]:
    """Reject malformed public historical results without granting trading authority."""
    import math
    import re
    from datetime import UTC, date, datetime

    from qtrade.research.source_constraints import POLICY

    try:
        if len(json.dumps(value, allow_nan=False, ensure_ascii=False,
                          separators=(',', ':')).encode()) > 3_000_000:
            raise ValueError('COMPARISON_PUBLICATION_TOO_LARGE')
        verify_signed(value)
        if (value.get('schema_version') != 'qtrade.strategy-comparison-result.v1'
                or value.get('state') != 'COMPLETE_DIAGNOSTIC'
                or any(value.get(k) != v or type(value.get(k)) is not type(v)
                       for k, v in AUTHORITY.items())
                or any(value.get(k) is not False for k in ('historical_pit_certified',
                    'historical_available_at_known', 'forward_return_observed'))):
            raise ValueError('COMPARISON_PUBLIC_AUTHORITY_INVALID')
        bindings = value['bindings']
        if signed(bindings)['evidence_sha256'] != value['comparison_id']:
            raise ValueError('COMPARISON_PUBLIC_IDENTITY_INVALID')
        for key in ('dataset_sha256', 'source_snapshot_sha256', 'provider_inventory_sha256'):
            if re.fullmatch('[a-f0-9]{64}', bindings[key]) is None:
                raise ValueError('COMPARISON_PUBLIC_HASH_INVALID')
        for key in ('code_revision', 'training_source_revision'):
            if re.fullmatch('[a-f0-9]{40}', bindings[key]) is None:
                raise ValueError('COMPARISON_PUBLIC_REVISION_INVALID')
        if bindings['comparison_contract'] != 'COMMON_CAUSAL_PANEL_NATIVE_EXCHANGE_V1':
            raise ValueError('COMPARISON_PUBLIC_PROTOCOL_INVALID')
        archives = bindings['model_archives']
        if set(archives) != {'ridge', 'lightgbm'}:
            raise ValueError('COMPARISON_PUBLIC_ARCHIVES_INVALID')
        for archive in archives.values():
            if (not isinstance(archive['run_id'], str) or not 1 <= len(archive['run_id']) <= 200
                    or re.fullmatch('[a-f0-9]{64}', archive['manifest_sha256']) is None):
                raise ValueError('COMPARISON_PUBLIC_ARCHIVE_IDENTITY_INVALID')
        protocol = bindings['protocol']
        if set(protocol) != set(PROTOCOL_KEYS):
            raise ValueError('COMPARISON_PUBLIC_PROTOCOL_KEYS_INVALID')
        portfolio, exchange = protocol['portfolio'], protocol['exchange']
        if (not isinstance(portfolio, dict) or set(portfolio) != {
                'topk', 'n_drop', 'hold_thresh', 'risk_degree', 'only_tradable',
                'forbid_all_trade_at_limit'} or not isinstance(exchange, dict)
                or set(exchange) != {'freq', 'deal_price', 'trade_unit', 'limit_threshold',
                    'volume_threshold', 'open_cost', 'close_cost', 'min_cost', 'impact_cost'}):
            raise ValueError('COMPARISON_PUBLIC_EXECUTION_FIELDS_INVALID')
        for key, lower, upper in (('topk', 10, 100), ('n_drop', 1, 20), ('hold_thresh', 2, 20)):
            if type(portfolio[key]) is not int or not lower <= portfolio[key] <= upper:
                raise ValueError('COMPARISON_PUBLIC_PORTFOLIO_INVALID')
        if (portfolio['n_drop'] > portfolio['topk']
                or portfolio['only_tradable'] is not False
                or portfolio['forbid_all_trade_at_limit'] is not False
                or exchange['freq'] != 'day' or exchange['deal_price'] != 'open'
                or type(exchange['trade_unit']) is not int or exchange['trade_unit'] != 100
                or exchange['limit_threshold'] != ['$buy_block', '$sell_block']
                or exchange['volume_threshold'] != ['cum', '$capacity']
                or protocol['benchmark'] != 'SH000300'
                or protocol['universe_policy'] != 'supported_a_share'
                or protocol['source_constraint_policy'] != POLICY
                or protocol['label'] != 'Ref($open,-2)/Ref($open,-1)-1'):
            raise ValueError('COMPARISON_PUBLIC_EXECUTION_POLICY_INVALID')
        for number, lower_float, upper_float in (
                (portfolio['risk_degree'], .1, .95), (protocol['account'], 100_000, 100_000_000),
                (exchange['open_cost'], .0001, .01), (exchange['close_cost'], .0001, .01),
                (exchange['min_cost'], 0, 100), (exchange['impact_cost'], 0, 0)):
            if (type(number) not in (float, int) or not math.isfinite(number)
                    or not lower_float <= number <= upper_float):
                raise ValueError('COMPARISON_PUBLIC_EXECUTION_NUMBER_INVALID')
        split = protocol['segments']
        for key in ('train', 'valid', 'test'):
            if (len(split[key]) != 2 or any(str(date.fromisoformat(d)) != d for d in split[key])
                    or split[key][0] > split[key][1]):
                raise ValueError('COMPARISON_PUBLIC_SPLITS_INVALID')
        if not split['train'][1] < split['valid'][0] <= split['valid'][1] < split['test'][0]:
            raise ValueError('COMPARISON_PUBLIC_SPLIT_LEAKAGE')
        created = datetime.fromisoformat(value['created_at'])
        if (value['signal_start'] != split['test'][0] or value['signal_end'] != split['test'][1]
                or type(value['common_rows']) is not int
                or not 1 <= value['common_rows'] <= 30_000_000
                or created.tzinfo is None or created > datetime.now(UTC)):
            raise ValueError('COMPARISON_PUBLIC_RANGE_INVALID')
        if (not isinstance(value['limitations'], list) or len(value['limitations']) > 50
                or not set(LIMITATIONS) <= set(value['limitations'])
                or any(not isinstance(s, str) or len(s) > 500 for s in value['limitations'])
                or value['unavailable'] != ['etf_positive_trend20']):
            raise ValueError('COMPARISON_PUBLIC_LIMITATIONS_INVALID')
        strategies = value['strategies']
        if set(strategies) != set(FAMILIES):
            raise ValueError('COMPARISON_PUBLIC_FAMILIES_INVALID')
        metric_keys = {'ic', 'rank_ic', 'total_return_net', 'annual_return_net', 'max_drawdown',
                       'mean_daily_turnover', 'sharpe_net', 'benchmark_return'}
        dates = None
        for name, strategy in strategies.items():
            if strategy['signal_rows'] != value['common_rows'] or strategy['allocation'] != (
                    'EQUAL_WEIGHT_ALL' if name == 'equal_weight_universe'
                    else 'TOPK_DROPOUT_COMMON_CONFIG'):
                raise ValueError('COMPARISON_PUBLIC_ALLOCATION_INVALID')
            metrics = strategy['metrics']
            if set(metrics) != metric_keys or any(
                    n is not None and (type(n) not in (int, float) or not math.isfinite(n))
                    for n in metrics.values()):
                raise ValueError('COMPARISON_PUBLIC_METRICS_INVALID')
            curve = strategy['curve']
            if not isinstance(curve, list) or not 1 <= len(curve) <= 5000:
                raise ValueError('COMPARISON_PUBLIC_CURVE_SIZE_INVALID')
            current_dates = [row['date'] for row in curve]
            if (current_dates != sorted(set(current_dates)) or any(
                    str(date.fromisoformat(day)) != day or not split['test'][0] <= day <=
                    split['test'][1] for day in current_dates)
                    or dates is not None and dates != current_dates):
                raise ValueError('COMPARISON_PUBLIC_CURVE_DATES_INVALID')
            dates = current_dates
            for row in curve:
                if any(type(row[k]) not in (int, float) or not math.isfinite(row[k])
                       or row[k] < 0 for k in ('equity', 'turnover', 'cost')):
                    raise ValueError('COMPARISON_PUBLIC_CURVE_VALUES_INVALID')
        return value
    except (KeyError, TypeError, AttributeError, OverflowError) as error:
        raise ValueError('COMPARISON_PUBLICATION_INVALID') from error

