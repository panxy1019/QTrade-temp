from copy import deepcopy

import pytest

from qtrade.research.qlib_adapter import extend_normalized_daily, normalize_daily


def raw(day, price=10, volume=1000):
    return {'date': day, 'open': price, 'high': price+1, 'low': price-1, 'close': price,
            'volume': volume, 'amount': price*volume}


FACTORS = [{'d': '2010-01-01', 'f': 2}]


def old():
    return normalize_daily([raw('2026-10-22'), raw('2026-10-23', 11)], FACTORS,
                           symbol='600036')


def run(frozen=None, incoming=None, factors=None, overlap=None):
    return extend_normalized_daily(
        old() if frozen is None else frozen, [raw('2026-10-26', 12)] if incoming is None
        else incoming, FACTORS if factors is None else factors, symbol='600036',
        overlap=raw('2026-10-23', 11) if overlap is None else overlap,
    )


def test_incremental_append_preserves_anchor_old_rows_and_causal_gap_restrictions():
    frozen = old()
    original = deepcopy(frozen)
    value, proof = run(frozen=frozen)
    assert frozen == original and value[:len(frozen)] == frozen
    assert value[-1]['close'] == pytest.approx(1.2)  # Not renormalized to 1 on new day.
    assert value[-1]['buy_block'] == 1  # Gap uses the prior frozen adjusted close.
    assert value[-1]['sell_block'] == 0
    assert value[-1]['vwap'] * value[-1]['volume'] == pytest.approx(value[-1]['amount'])
    assert proof['frozen_prefix_unchanged'] is True
    assert proof['independent_price_basis_certified'] is False
    assert proof['historical_available_at'] is None


def test_split_on_new_day_changes_only_new_units_with_old_anchor():
    factors = FACTORS + [{'d': '2026-10-26', 'f': 4}]
    value, _ = run(incoming=[raw('2026-10-26', 5.5, 2000)], factors=factors)
    assert value[:2] == old()
    assert value[-1]['close'] == value[-2]['close']
    assert value[-1]['buy_block'] == 0 and value[-1]['sell_block'] == 0
    assert value[-1]['volume'] * value[-1]['factor'] == pytest.approx(2000)


@pytest.mark.parametrize('change', ['prices', 'volume', 'factor', 'anchor', 'day', 'symbol',
                                   'missing_factor', 'amount', 'duplicate', 'zero_volume'])
def test_unproven_joins_and_revised_history_fail_without_changing_old_rows(change):
    frozen = old()
    original = deepcopy(frozen)
    incoming = [raw('2026-10-26', 12)]
    factors = deepcopy(FACTORS)
    overlap = raw('2026-10-23', 11)
    if change == 'prices':
        overlap['close'] += 1
    elif change == 'volume':
        overlap['volume'] += 1
    elif change == 'factor':
        factors[0]['f'] = 3
    elif change == 'anchor':
        frozen[0]['factor'] += 0.01
    elif change == 'day':
        incoming[0]['date'] = '2026-10-23'
    elif change == 'symbol':
        frozen[0]['symbol'] = '000001'
    elif change == 'missing_factor':
        factors[0]['d'] = '2026-10-26'
    elif change == 'amount':
        incoming[0]['amount'] = 1
    elif change == 'duplicate':
        incoming *= 2
    elif change == 'zero_volume':
        incoming[0]['volume'] = 0
    inputs = deepcopy(frozen)
    with pytest.raises(ValueError):
        run(frozen, incoming, factors, overlap)
    assert frozen == inputs
    if change not in {'symbol', 'anchor'}:
        assert frozen == original
