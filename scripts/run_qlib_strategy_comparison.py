"""Compare frozen OOS scores and causal factors through the same native Qlib Exchange."""
from __future__ import annotations

import argparse
import json
import os
import re
from datetime import UTC, datetime
from importlib import import_module
from pathlib import Path
from typing import Any

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.data_versions import file_hash
from qtrade.research.signal_baselines import signal_family
from qtrade.research.strategy_comparison import (
    FAMILIES,
    LIMITATIONS,
    bind_archives,
    verify_comparison,
)
from scripts.atomic_json import write_json_immutable
from scripts.build_qlib_daily_version import move_durable, sync_directory

REPO = Path(__file__).resolve().parents[1]


def common_panel(close: Any, predictions: dict[str, Any], days: list[str],
                 test: list[str]) -> Any:
    import numpy as np
    import pandas as pd

    for score in predictions.values():
        if (score.index.names != ['datetime', 'instrument'] or score.index.has_duplicates
                or not np.isfinite(score.to_numpy()).all()):
            raise ValueError('COMPARISON_INVALID_MODEL_SCORES')
    rows = []
    for offset, day in enumerate(days):
        if day < test[0] or day > test[1]:
            continue
        if offset < 20:
            raise ValueError('COMPARISON_FACTOR_WARMUP_MISSING')
        window = days[offset-20:offset+1]
        histories = {}
        for instrument in close.index.get_level_values('instrument').unique():
            frame = close.xs(instrument, level='instrument').reindex(pd.to_datetime(window))
            histories[instrument] = [{'date': d, 'close': value}
                                    for d, value in zip(window, frame.iloc[:, 0], strict=True)]
        model_day = {}
        for name, score in predictions.items():
            if pd.Timestamp(day) not in score.index.get_level_values('datetime'):
                raise ValueError('COMPARISON_MISSING_MODEL_SESSION:'+day)
            model_day[name] = score.xs(pd.Timestamp(day), level='datetime')
        supported = set(histories).intersection(*(set(s.index) for s in model_day.values()))
        if not supported:
            raise ValueError('COMPARISON_EMPTY_COMMON_SESSION:'+day)
        family = signal_family({code: histories[code] for code in supported}, window,
                               as_of=day, security_type='STOCK')
        common = set(family['eligible'])
        if not common:
            raise ValueError('COMPARISON_EMPTY_COMMON_SESSION:'+day)
        factors = {name: {r['instrument']: r['score'] for r in scores}
                   for name, scores in family['signals'].items()}
        for code in sorted(common):
            rows.append({'datetime': pd.Timestamp(day), 'instrument': code,
                         **{name: scores[code] for name, scores in factors.items()},
                         **{name: float(score.loc[code]) for name, score in model_day.items()}})
    if not rows:
        raise ValueError('COMPARISON_NO_COMMON_OOS_DATES')
    return pd.DataFrame(rows).set_index(['datetime', 'instrument']).sort_index()


def evaluate_metrics(report: Any, score: Any, labels: Any) -> tuple[dict[str, Any], list[Any]]:
    import numpy as np
    import pandas as pd

    from scripts.run_qlib_baseline import finite

    joined = pd.concat([score.rename('score'), labels.rename('label')], axis=1).dropna()
    diagnostics = []
    for day, frame in joined.groupby(level='datetime'):
        varying = frame['score'].nunique() > 1 and frame['label'].nunique() > 1
        diagnostics.append({'date': str(day.date()), 'stocks': len(frame),
                            'ic': finite(frame['score'].corr(frame['label'])) if varying else None,
                            'rank_ic': finite(frame['score'].rank().corr(frame['label'].rank()))
                            if varying else None})
    if not diagnostics or report.empty:
        raise ValueError('COMPARISON_EMPTY_EVALUATION')
    net = report['return']-report['cost']
    equity = (1+net).cumprod()
    drawdown = equity/equity.cummax().clip(lower=1)-1
    metric = {key: finite(pd.Series([r[key] for r in diagnostics], dtype=float).mean())
              for key in ('ic', 'rank_ic')}
    metric.update({'total_return_net': finite(equity.iloc[-1]-1),
                   'annual_return_net': finite(equity.iloc[-1]**(252/len(net))-1),
                   'max_drawdown': finite(drawdown.min()),
                   'mean_daily_turnover': finite(report['turnover'].mean()),
                   'sharpe_net': finite(net.mean()/net.std()*np.sqrt(252)) if net.std() else None,
                   'benchmark_return': finite((1+report['bench']).prod()-1)})
    return metric, diagnostics


def run(ridge: Path, lightgbm: Path, output: Path, revision: str) -> dict[str, Any]:
    if os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false':
        raise ValueError('LIVE_TRADING_MUST_REMAIN_DISABLED')
    if not re.fullmatch('[a-f0-9]{40}', revision):
        raise ValueError('COMPARISON_FULL_REVISION_REQUIRED')
    import pandas as pd

    bindings = bind_archives(ridge, lightgbm)
    bindings['code_revision'] = revision
    bindings['comparison_contract'] = 'COMMON_CAUSAL_PANEL_NATIVE_EXCHANGE_V1'
    identity = signed(bindings)['evidence_sha256']
    target = output/identity
    if target.exists():
        return verify_comparison(target)
    qlib = import_module('qlib')
    if qlib.__version__ != '0.9.7':
        raise ValueError('COMPARISON_AUDITED_QLIB_VERSION_REQUIRED')
    qlib.init(provider_uri=str(lightgbm/'provider'), region='cn', kernels=2)
    D = import_module('qlib.data').D
    backtest = import_module('qlib.contrib.evaluate').backtest_daily
    native = import_module('qlib.contrib.strategy.signal_strategy')

    class EqualUniverse(native.WeightStrategyBase):  # type: ignore[name-defined, misc]
        def generate_target_weight_position(self, score: Any, current: Any,
                                            trade_start_time: Any, trade_end_time: Any) -> Any:
            selected = score.dropna().index
            return {code: 1/len(selected) for code in selected}

    config = bindings['protocol']
    test = config['segments']['test']
    days = (lightgbm/'provider/calendars/day.txt').read_text().splitlines()
    predictions = {name: pd.read_parquet(path/'predictions.parquet').iloc[:, 0]
                   for name, path in (('ridge', ridge), ('lightgbm', lightgbm))}
    # Normalize native Qlib's named levels without interpreting positional order.
    for name, score in predictions.items():
        predictions[name] = score.reorder_levels(['datetime', 'instrument']).sort_index()
    codes = sorted(set(predictions['ridge'].index.get_level_values('instrument')) |
                   set(predictions['lightgbm'].index.get_level_values('instrument')))
    start_index = days.index(test[0])
    if start_index < 20:
        raise ValueError('COMPARISON_FACTOR_WARMUP_MISSING')
    close = D.features(codes, ['$close'], start_time=days[start_index-20], end_time=test[1])
    panel = common_panel(close, predictions, days, test)
    labels = pd.read_parquet(lightgbm/'labels.parquet').iloc[:, 0].reorder_levels(
        ['datetime', 'instrument']).sort_index()
    ridge_labels = pd.read_parquet(ridge/'labels.parquet').iloc[:, 0].reorder_levels(
        ['datetime', 'instrument']).sort_index()
    if not labels.equals(ridge_labels):
        raise ValueError('COMPARISON_MODEL_LABELS_DIFFER')
    stage = output/(identity+'.'+datetime.now(UTC).strftime('%Y%m%dT%H%M%S%f')+'.partial')
    stage.mkdir(parents=True, exist_ok=False)
    panel.to_parquet(stage/'common-scores.parquet')
    labels.reindex(panel.index).to_frame().to_parquet(stage/'common-labels.parquet')
    results = {}
    execution_index = None
    for name in FAMILIES:
        signal = panel[name].rename('score')
        strategy = (EqualUniverse(signal=signal, risk_degree=config['portfolio']['risk_degree'])
                    if name == 'equal_weight_universe' else
                    native.TopkDropoutStrategy(signal=signal, **config['portfolio']))
        exchange = dict(config['exchange'])
        for field in ('limit_threshold', 'volume_threshold'):
            exchange[field] = tuple(exchange[field])
        report, positions = backtest(start_time=test[0], end_time=test[1], strategy=strategy,
                                     account=config['account'], benchmark=config['benchmark'],
                                     exchange_kwargs=exchange)
        if execution_index is not None and not report.index.equals(execution_index):
            raise ValueError('COMPARISON_EXECUTION_CALENDARS_DIFFER')
        execution_index = report.index
        report.to_parquet(stage/(name+'-report.parquet'))
        position_rows = []
        for day, position in positions.items():
            position_rows.append({'date': str(day.date()), 'cash': float(position.get_cash()),
                                  'holdings': {code: float(position.get_stock_amount(code))
                                               for code in position.get_stock_list()}})
        write_json_immutable(stage/(name+'-positions.json'), position_rows)
        metrics, diagnostics = evaluate_metrics(report, signal, labels.reindex(panel.index))
        write_json_immutable(stage/(name+'-diagnostics.json'), diagnostics)
        net = report['return']-report['cost']
        equity = (1+net).cumprod()
        results[name] = {'metrics': metrics, 'signal_rows': len(panel),
                         'allocation': 'EQUAL_WEIGHT_ALL' if name == 'equal_weight_universe'
                         else 'TOPK_DROPOUT_COMMON_CONFIG',
                         'curve': [{'date': str(day.date()), 'equity': float(equity.loc[day]),
                                    'turnover': float(report.loc[day, 'turnover']),
                                    'cost': float(report.loc[day, 'cost'])}
                                   for day in report.index]}
    result = signed({**AUTHORITY, 'schema_version': 'qtrade.strategy-comparison-result.v1',
                     'comparison_id': identity, 'bindings': bindings, 'strategies': results,
                     'created_at': datetime.now(UTC).isoformat(), 'state': 'COMPLETE_DIAGNOSTIC',
                     'signal_start': str(panel.index.get_level_values('datetime').min().date()),
                     'signal_end': str(panel.index.get_level_values('datetime').max().date()),
                     'common_rows': len(panel), 'historical_pit_certified': False,
                     'historical_available_at_known': False, 'forward_return_observed': False,
                     'unavailable': ['etf_positive_trend20'], 'limitations': LIMITATIONS})
    write_json_immutable(stage/'comparison.json', result)
    for path in ('scripts/run_qlib_strategy_comparison.py',
                 'src/qtrade/research/strategy_comparison.py',
                 'src/qtrade/research/signal_baselines.py', 'src/qtrade/research/factors.py'):
        destination = stage/'source'/path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes((REPO/path).read_bytes())
    write_json_immutable(stage/'manifest.json', signed({**AUTHORITY,
        'schema_version': 'qtrade.strategy-comparison.v1', 'comparison_id': identity,
        'files': {p.relative_to(stage).as_posix(): file_hash(p)
                  for p in stage.rglob('*') if p.is_file()}}))
    verify_comparison(stage)
    for path in stage.rglob('*'):
        if path.is_file():
            with path.open('rb') as handle:
                os.fsync(handle.fileno())
    for path in sorted((p for p in stage.rglob('*') if p.is_dir()),
                       key=lambda p: len(p.parts), reverse=True):
        sync_directory(path)
    sync_directory(stage)
    move_durable(stage, target)
    return result


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--ridge-run', type=Path, required=True)
    parser.add_argument('--lightgbm-run', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--source-revision', required=True)
    args = parser.parse_args()
    result = run(args.ridge_run, args.lightgbm_run, args.output, args.source_revision)
    print(json.dumps({key: result[key] for key in ('comparison_id', 'state', 'common_rows')}))



