"""Independent installed-Qlib checks before a day data version becomes ready."""

from __future__ import annotations

import argparse
import json
import os
from datetime import UTC, datetime
from importlib import import_module
from importlib.metadata import version
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.data_versions import content_id, file_hash
from scripts.atomic_json import write_json_immutable
from scripts.prepare_qlib_incremental import load_signed


def reload_provider(root: Path) -> dict[str, Any]:
    import numpy as np

    if version('pyqlib') != '0.9.7':
        raise ValueError('PROVIDER_RELOAD_REQUIRES_AUDITED_QLIB_VERSION')
    qlib = import_module('qlib')
    D = import_module('qlib.data').D
    pq = import_module('pyarrow.parquet')
    dataset = load_signed(root/'dataset.json')
    provider = root/'provider'
    qlib.init(provider_uri=str(provider), region='cn', kernels=2)
    current = dataset['incremental']['current_symbols']
    blocked = dataset['incremental']['blocked_symbols']
    codes = {r['symbol']: ('SH' if r['symbol'].startswith('6') else 'SZ')+r['symbol']
             for r in dataset['stocks']}
    fields = ['$open/$factor', '$close', '$volume', '$factor', '$vwap*$volume', '$amount']
    frame = D.features(list(codes.values()), fields, start_time=dataset['end'],
                       end_time=dataset['end']).droplevel('datetime')
    checked = 0
    for stock in dataset['stocks']:
        symbol, code = stock['symbol'], codes[stock['symbol']]
        if symbol not in current:
            if code in frame.index and not frame.loc[code].isna().all():
                raise ValueError('PROVIDER_UNKNOWN_CURRENT_PRICE_WAS_FILLED')
            continue
        if code not in frame.index:
            raise ValueError('PROVIDER_CURRENT_SYMBOL_MISSING')
        values = frame.loc[code].to_numpy(dtype=float)
        table = pq.read_table(root/'stocks'/symbol/'daily.parquet')
        expected = table.slice(table.num_rows-1, 1).to_pylist()[0]
        if (not np.isfinite(values).all() or min(values) <= 0
                or not np.allclose(values, [expected['raw_open'], expected['close'],
                                           expected['volume'], expected['factor'],
                                           expected['amount'], expected['amount']],
                                   atol=1e-6, rtol=2e-6)):
            raise ValueError('PROVIDER_CURRENT_FIELDS_OR_CASH_TURNOVER_CHANGED')
        checked += 1
    dates = (provider/'calendars/day.txt').read_text().splitlines()
    boundaries = [d for d in dates if d > dataset['end']]
    if not boundaries:
        raise ValueError('PROVIDER_BOUNDARY_SESSIONS_REQUIRED')
    future = D.features(list(codes.values()), fields, start_time=boundaries[0],
                        end_time=boundaries[-1])
    if not future.isna().all().all():
        raise ValueError('PROVIDER_PLACEHOLDER_HAS_FABRICATED_OBSERVATIONS')
    benchmark = D.features(['SH000300'], ['$open/$factor', '$close/$factor', '$volume'],
                           start_time=dataset['end'], end_time=dataset['end'])
    expected_benchmark = json.loads((root/'benchmark/raw.json').read_bytes())['rows'][-1]
    if (len(benchmark) != 1 or not np.allclose(benchmark.iloc[0].to_numpy(),
            [expected_benchmark[k] for k in ('open', 'close', 'volume')], atol=0.011, rtol=2e-6)):
        raise ValueError('PROVIDER_CURRENT_BENCHMARK_CHANGED')
    provider_files = {p.relative_to(root).as_posix(): file_hash(p)
                      for p in provider.rglob('*') if p.is_file()}
    result = signed({
        **AUTHORITY, 'schema_version': 'qtrade.qlib-provider-reload.v1',
        'checked_at': datetime.now(UTC).isoformat(), 'actual_end': dataset['end'],
        'dataset_sha256': file_hash(root/'dataset.json'),
        'provider_sha256': content_id(provider_files, {}), 'checked_current_symbols': checked,
        'blocked_current_symbols': blocked, 'calendar_boundaries': boundaries,
        'checks_passed': checked == len(current), 'placeholder_all_missing': True,
        'unknown_current_prices_missing': True, 'qlib_version': version('pyqlib'),
        'historical_pit_certified': False, 'external_execution_ready': False,
    })
    write_json_immutable(root/'provider-reload.json', result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--root', type=Path, required=True)
    args = parser.parse_args()
    if os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false':
        raise RuntimeError('LIVE_TRADING_MUST_REMAIN_DISABLED')
    print(json.dumps(reload_provider(args.root)))


if __name__ == '__main__':
    main()
