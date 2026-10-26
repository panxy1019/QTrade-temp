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
    result = signed({**AUTHORITY, 'comparison_id': 'a'*64, 'strategies': dict.fromkeys(FAMILIES),
                     'historical_pit_certified': False})
    (tmp_path/'comparison.json').write_text(json.dumps(result))
    manifest = signed({**AUTHORITY, 'schema_version': 'qtrade.strategy-comparison.v1',
                       'comparison_id': 'a'*64,
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

