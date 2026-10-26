from datetime import date, timedelta
from decimal import Decimal

import pytest

from qtrade.research.signal_baselines import allocation, average_ranks, signal_family


def fixture():
    days = [(date(2026, 8, 1)+timedelta(days=i)).isoformat() for i in range(21)]
    histories = {s: [{'date': d, 'close': price(i)} for i, d in enumerate(days)]
        for s, price in {'SH600001': lambda i: 10+i, 'SH600002': lambda i: 30-i,
                         'SH600003': lambda i: 20}.items()}
    return days, histories


def test_signal_directions_and_common_scope_do_not_depend_on_allocation():
    days, histories = fixture()
    family = signal_family(histories, days, as_of=days[-1], security_type='STOCK')
    signals = family['signals']
    def winner(name):
        return allocation(signals[name], method='TOPK_EQUAL_WEIGHT', topk=1)[0]['instrument']
    assert winner('momentum20') == 'SH600001'
    assert winner('reversal5') == 'SH600002'
    assert winner('lowvol20') == 'SH600003'
    assert len(signals) == 5
    assert all({r['instrument'] for r in rows} == set(histories) for rows in signals.values())
    assert family['unavailable'] == ['etf_positive_trend20']
    assert family['future_prices_consumed'] is False
    equal = allocation(signals['equal_weight_universe'], method='EQUAL_WEIGHT_ALL', topk=1)
    assert len(equal) == 3  # An equal universe is never an arbitrary tied TopK subset.
    assert abs(sum(Decimal(p['target_weight']) for p in equal)-Decimal('.95')) < Decimal('1e-25')


def test_rank_ties_share_rank_without_code_order_bias():
    ranks = average_ranks({'a': Decimal(1), 'b': Decimal(1), 'c': Decimal(3)})
    assert ranks == {'a': Decimal('.25'), 'b': Decimal('.25'), 'c': Decimal(1)}
    assert average_ranks({'a': Decimal(0)}) == {'a': Decimal('.5')}


@pytest.mark.parametrize('damage', ['missing', 'duplicate', 'nan', 'zero', 'future'])
def test_missing_or_invalid_grid_is_excluded_from_every_signal(damage):
    days, histories = fixture()
    if damage == 'missing':
        histories['SH600001'].pop()
    elif damage == 'duplicate':
        histories['SH600001'][-1]['date'] = days[-2]
    elif damage == 'future':
        histories['SH600001'][-1]['date'] = '2026-09-01'
    else:
        histories['SH600001'][-1]['close'] = float('nan') if damage == 'nan' else 0
    result = signal_family(histories, days, as_of=days[-1], security_type='STOCK')
    assert result['eligible'] == ['SH600002', 'SH600003']
    assert len(result['rejected']) == 1
    assert all(len(rows) == 2 for rows in result['signals'].values())


def test_etf_trend_requires_positive_trend_and_can_hold_all_cash():
    days, histories = fixture()
    result = signal_family(histories, days, as_of=days[-1], security_type='ETF')
    assert result['signals']['etf_positive_trend20'] == [
        {'instrument': 'SH600001', 'score': 2.0}]
    del histories['SH600001']
    result = signal_family(histories, days, as_of=days[-1], security_type='ETF')
    assert allocation(result['signals']['etf_positive_trend20'], method='TOPK_EQUAL_WEIGHT') == []


def test_unsafe_allocation_and_ambiguous_scores_are_rejected():
    rows = [{'instrument': 'SH600001', 'score': -1.0}]
    assert allocation(rows, method='TOPK_EQUAL_WEIGHT')[0]['target_weight'] == '0.95'
    for kwargs in ({'method': 'leveraged'}, {'method': 'TOPK_EQUAL_WEIGHT', 'topk': True},
                   {'method': 'TOPK_EQUAL_WEIGHT', 'risk_degree': Decimal('1.5')}):
        with pytest.raises(ValueError):
            allocation(rows, **kwargs)
    for invalid in (rows*2, [{'instrument': 'SH600001', 'score': float('inf')}]):
        with pytest.raises(ValueError):
            allocation(invalid, method='TOPK_EQUAL_WEIGHT')
