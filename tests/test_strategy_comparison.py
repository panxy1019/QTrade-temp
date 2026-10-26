from __future__ import annotations

import json

import pandas as pd
import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, signed
from qtrade.research.data_versions import file_hash
from qtrade.research.strategy_comparison import FAMILIES, bind_archives, verify_comparison
from scripts.run_qlib_strategy_comparison import common_panel, evaluate_metrics


def panel_inputs():
    days = pd.bdate_range('2024-01-01', periods=23).strftime('%Y-%m-%d').tolist()
    index = pd.MultiIndex.from_product([['SH600001', 'SH600002'], pd.to_datetime(days)],
                                      names=['instrument', 'datetime'])
    close = pd.DataFrame({'close': [float(i+10) for i in range(46)]}, index=index)
    score_index = pd.MultiIndex.from_product([pd.to_datetime(days[20:]),
                                             ['SH600001', 'SH600002']],
                                            names=['datetime', 'instrument'])
    predictions = {name: pd.Series(range(6), index=score_index, dtype=float)
                   for name in ('ridge', 'lightgbm')}
    return close, predictions, days


def test_common_panel_causal_and_shared():
    close, predictions, days = panel_inputs()
    panel = common_panel(close, predictions, days, [days[20], days[-1]])
    changed = close.copy()
    changed.loc[('SH600001', pd.Timestamp(days[-1])), 'close'] = 99999
    changed_panel = common_panel(changed, predictions, days, [days[20], days[-1]])
    pd.testing.assert_frame_equal(panel.loc[:pd.Timestamp(days[-2])],
                                  changed_panel.loc[:pd.Timestamp(days[-2])])
    assert set(panel.columns) == set(FAMILIES)
    assert len(panel) == 6


def test_missing_session_rejected_not_silently_shortened():
    close, predictions, days = panel_inputs()
    predictions['ridge'] = predictions['ridge'].iloc[2:]
    with pytest.raises(ValueError, match='MISSING_MODEL_SESSION'):
        common_panel(close, predictions, days, [days[20], days[-1]])


def test_invalid_scores_rejected():
    close, predictions, days = panel_inputs()
    predictions['ridge'].iloc[0] = float('inf')
    with pytest.raises(ValueError, match='INVALID_MODEL_SCORES'):
        common_panel(close, predictions, days, [days[20], days[-1]])


def test_no_warmup_or_no_dates_rejected():
    close, predictions, days = panel_inputs()
    with pytest.raises(ValueError, match='WARMUP'):
        common_panel(close, predictions, days, [days[0], days[-1]])
    with pytest.raises(ValueError, match='NO_COMMON_OOS'):
        common_panel(close, predictions, days, ['2030-01-01', '2030-01-02'])


def test_missing_price_excluded_every_family():
    close, predictions, days = panel_inputs()
    close.loc[('SH600002', pd.Timestamp(days[0])), 'close'] = float('nan')
    panel = common_panel(close, predictions, days, [days[20], days[-1]])
    assert list(panel.loc[pd.Timestamp(days[20])].index) == ['SH600001']


def test_metrics_subtract_cost_and_initial_drawdown():
    _, predictions, days = panel_inputs()
    report = pd.DataFrame({'return': [-.1, .02], 'cost': [.01, .01],
                           'bench': [0., 0.], 'turnover': [.5, .2]})
    metrics, _ = evaluate_metrics(report, predictions['ridge'], predictions['ridge']*2)
    assert metrics['total_return_net'] == pytest.approx(.89*1.01-1)
    assert metrics['max_drawdown'] == pytest.approx(-.11)
    constant, _ = evaluate_metrics(report, predictions['ridge']*0+1, predictions['ridge'])
    assert constant['ic'] is None and constant['rank_ic'] is None


def fixture_archives(tmp_path, monkeypatch):
    import qtrade.research.strategy_comparison as module

    protocol = {'segments': {'valid': ['2022-01-01', '2023-12-01'],
                             'test': ['2024-01-01', '2024-12-01']}, 'label': 'label',
                'portfolio': {'topk': 50}, 'exchange': {'open_cost': .001},
                'name': 'test'}
    roots = [tmp_path/'ridge', tmp_path/'lightgbm']
    summaries = {}
    for name, root in zip(('Ridge', 'LightGBM'), roots, strict=True):
        root.mkdir()
        request = signed({'config': protocol, 'data_manifest_sha256': 'a'*64,
                          'source_snapshot_sha256': 'b'*64, 'source_revision': 'c'*40})
        (root/'request.json').write_text(json.dumps(request))
        (root/'checksums.json').write_text(json.dumps({'files': {
            'predictions.parquet': 'd', 'labels.parquet': 'e', 'request.json': 'f',
            'provider/features/sz000001/close.day.bin': 'g'}}))
        for name_on_disk in ('predictions.parquet', 'labels.parquet',
                             'provider/features/sz000001/close.day.bin'):
            path = root/name_on_disk
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(b'test')
        summaries[root] = {'model': 'Alpha158 + '+name, 'run_id': name,
                           'execution_constraints': {'historical_pit_certified': False}}
    monkeypatch.setattr(module, 'verify_archive', lambda p: summaries[p])
    return roots


def test_bind_rejects_cost_and_provider_drift(tmp_path, monkeypatch):
    ridge, lgb = fixture_archives(tmp_path, monkeypatch)
    assert bind_archives(ridge, lgb)['protocol']['portfolio']['topk'] == 50
    request = json.loads((lgb/'request.json').read_text())
    request['config']['exchange']['open_cost'] = .002
    request.pop('evidence_sha256')
    (lgb/'request.json').write_text(json.dumps(signed(request)))
    with pytest.raises(ValueError, match='PROTOCOL'):
        bind_archives(ridge, lgb)


def test_sealed_comparison_rejects_extra_or_tampered_files(tmp_path):
    result = public_result()
    (tmp_path/'comparison.json').write_text(json.dumps(result))
    manifest = signed({**AUTHORITY, 'schema_version': 'qtrade.strategy-comparison.v1',
                       'comparison_id': result['comparison_id'],
                       'files': {'comparison.json': file_hash(tmp_path/'comparison.json')}})
    (tmp_path/'manifest.json').write_text(json.dumps(manifest))
    assert verify_comparison(tmp_path) == result
    (tmp_path/'extra.json').write_text('{}')
    with pytest.raises(ValueError, match='INVENTORY'):
        verify_comparison(tmp_path)
    (tmp_path/'extra.json').unlink()
    (tmp_path/'comparison.json').write_text('{}')
    with pytest.raises(ValueError, match='ARTIFACT'):
        verify_comparison(tmp_path)



def public_result():
    from pathlib import Path

    from qtrade.research.source_constraints import POLICY
    from qtrade.research.strategy_comparison import LIMITATIONS, PROTOCOL_KEYS
    config = json.loads((Path(__file__).resolve().parents[1]/
                         'configs/qlib_daily_baseline.json').read_text())
    protocol = {key: config.get(key) for key in PROTOCOL_KEYS}
    protocol.update({'segments': {'train': ['2020-01-01', '2021-12-31'],
                                  'valid': ['2022-01-01', '2023-12-31'],
                                  'test': ['2024-01-02', '2024-01-03']},
                     'universe_policy': 'supported_a_share', 'source_constraint_policy': POLICY})
    bindings = {'dataset_sha256': 'a'*64, 'source_snapshot_sha256': 'b'*64,
                'provider_inventory_sha256': 'c'*64, 'code_revision': 'd'*40,
                'training_source_revision': 'e'*40, 'protocol': protocol,
                'comparison_contract': 'COMMON_CAUSAL_PANEL_NATIVE_EXCHANGE_V1',
                'model_archives': {n: {'run_id': n, 'manifest_sha256': 'f'*64}
                                   for n in ('ridge', 'lightgbm')}}
    return signed({**AUTHORITY, 'schema_version': 'qtrade.strategy-comparison-result.v1',
        'state': 'COMPLETE_DIAGNOSTIC', 'bindings': bindings,
        'comparison_id': signed(bindings)['evidence_sha256'], 'common_rows': 20,
        'signal_start': '2024-01-02', 'signal_end': '2024-01-03',
        'created_at': '2026-10-26T00:00:00+08:00', 'limitations': LIMITATIONS,
        'unavailable': ['etf_positive_trend20'], 'historical_pit_certified': False,
        'historical_available_at_known': False, 'forward_return_observed': False,
        'strategies': {n: {'signal_rows': 20,
            'allocation': 'EQUAL_WEIGHT_ALL' if n == 'equal_weight_universe'
                          else 'TOPK_DROPOUT_COMMON_CONFIG',
            'metrics': dict.fromkeys(('ic', 'rank_ic', 'total_return_net', 'annual_return_net',
                                     'max_drawdown', 'mean_daily_turnover', 'sharpe_net',
                                     'benchmark_return')),
            'curve': [{'date': '2024-01-02', 'equity': 1., 'turnover': 0., 'cost': 0.}]}
            for n in FAMILIES}})


@pytest.mark.parametrize('change', ['hash', 'authority', 'numeric_authority', 'metric',
                                   'curve', 'family', 'limits'])
def test_publication_rejects_resigned_invalid_contract(change):
    from qtrade.research.strategy_comparison import validate_comparison_publication
    value = public_result()
    assert validate_comparison_publication(value) == value
    if change == 'hash':
        value['comparison_id'] = '0'*64
    elif change == 'authority':
        value['live_trading_enabled'] = True
    elif change == 'numeric_authority':
        value['live_trading_enabled'] = 0
    elif change == 'metric':
        value['strategies']['ridge']['metrics']['ic'] = True
    elif change == 'curve':
        value['strategies']['ridge']['curve'].append(value['strategies']['ridge']['curve'][0])
    elif change == 'family':
        del value['strategies']['ridge']
    else:
        value['limitations'] = []
    value.pop('evidence_sha256')
    with pytest.raises(ValueError):
        validate_comparison_publication(signed(value))


@pytest.mark.parametrize('change', ['future', 'empty_portfolio', 'empty_exchange',
                                   'bool_cost', 'negative_cost', 'bad_limit', 'risk', 'cash',
                                   'bool_topk', 'too_many_drops', 'nonfinite_cost'])
def test_public_execution_protocol_fails_closed(change):
    from qtrade.research.strategy_comparison import validate_comparison_publication

    value = public_result()
    protocol = value['bindings']['protocol']
    if change == 'future':
        value['created_at'] = '2099-01-01T00:00:00+00:00'
    elif change == 'empty_portfolio':
        protocol['portfolio'] = {}
    elif change == 'empty_exchange':
        protocol['exchange'] = {}
    elif change == 'bool_cost':
        protocol['exchange']['min_cost'] = False
    elif change == 'negative_cost':
        protocol['exchange']['close_cost'] = -.001
    elif change == 'bad_limit':
        protocol['exchange']['limit_threshold'] = .1
    elif change == 'risk':
        protocol['portfolio']['risk_degree'] = 1.5
    elif change == 'bool_topk':
        protocol['portfolio']['topk'] = True
    elif change == 'too_many_drops':
        protocol['portfolio'].update(topk=10, n_drop=20)
    elif change == 'nonfinite_cost':
        protocol['exchange']['close_cost'] = float('inf')
    else:
        protocol['account'] = '1000000'
    value['comparison_id'] = signed(value['bindings'])['evidence_sha256']
    value.pop('evidence_sha256')
    with pytest.raises(ValueError):
        validate_comparison_publication(signed(value))


def test_publication_size_limit_matches_compact_utf8_transport():
    from qtrade.research.strategy_comparison import validate_comparison_publication

    value = public_result()
    value['padding'] = '\u7814' * 500_000
    value.pop('evidence_sha256')
    assert validate_comparison_publication(signed(value))['padding'] == value['padding']
    value['padding'] = 'x' * 3_000_000
    with pytest.raises(ValueError, match='COMPARISON_PUBLICATION_TOO_LARGE'):
        validate_comparison_publication(signed(value))
