"""Small causal signal families, independent of allocation and execution."""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from qtrade.research.factors import PriceFactorSpec

FACTORS = {
    'momentum20': PriceFactorSpec('momentum', 20),
    'reversal5': PriceFactorSpec('reversal', 5),
    'lowvol20': PriceFactorSpec('low_volatility', 20),
}
STRATEGIES = ('momentum20', 'reversal5', 'lowvol20', 'multifactor_equal_rank',
              'equal_weight_universe', 'etf_positive_trend20')


def average_ranks(values: dict[str, Decimal]) -> dict[str, Decimal]:
    ordered = sorted(values, key=lambda k: (values[k], k))
    result: dict[str, Decimal] = {}
    start = 0
    while start < len(ordered):
        end = start+1
        while end < len(ordered) and values[ordered[end]] == values[ordered[start]]:
            end += 1
        rank = Decimal(start+end-1)/2
        percentile = rank/Decimal(len(ordered)-1) if len(ordered) > 1 else Decimal('0.5')
        for instrument in ordered[start:end]:
            result[instrument] = percentile
        start = end
    return result


def signal_family(histories: dict[str, list[dict[str, Any]]], sessions: list[str], *,
                  as_of: str, security_type: str) -> dict[str, Any]:
    """Input is a single frozen common 21-session close grid; never forward-filled."""
    if (security_type not in {'STOCK', 'ETF'} or len(histories) > 6000
            or len(sessions) != 21 or sessions != sorted(set(sessions)) or sessions[-1] != as_of):
        raise ValueError('SIGNAL_VERIFIED_COMMON_GRID_REQUIRED')
    factors: dict[str, dict[str, Decimal]] = {key: {} for key in FACTORS}
    rejected = []
    for instrument, rows in sorted(histories.items()):
        if [r.get('date') for r in rows] != sessions:
            rejected.append({'instrument': instrument, 'reason': 'INCOMPLETE_COMMON_CLOSE_GRID'})
            continue
        try:
            prices = tuple(Decimal(str(r['close'])) for r in rows)
            if any(not p.is_finite() or p <= 0 for p in prices):
                raise ValueError('INVALID_CLOSE')
            for key, spec in FACTORS.items():
                factors[key][instrument] = spec.value(prices)
        except (ValueError, ArithmeticError):
            rejected.append({'instrument': instrument, 'reason': 'INVALID_CLOSE'})
            for values in factors.values():
                values.pop(instrument, None)
    eligible = sorted(factors['momentum20'])
    if not eligible:
        raise ValueError('SIGNAL_NO_COMMON_QUALIFIED_INPUTS')
    ranks = {name: average_ranks(values) for name, values in factors.items()}
    signals = {**factors, 'multifactor_equal_rank': {instrument: sum(
        (values[instrument] for values in ranks.values()), Decimal(0))/len(ranks)
        for instrument in eligible}, 'equal_weight_universe': {s: Decimal(1) for s in eligible}}
    if security_type == 'ETF':
        signals['etf_positive_trend20'] = {s: value for s, value in factors['momentum20'].items()
                                          if value > 0}
    return {'as_of': as_of, 'security_type': security_type, 'eligible': eligible,
            'rejected': rejected, 'signals': {name: [
                {'instrument': s, 'score': float(values[s])} for s in sorted(values)
            ] for name, values in signals.items()},
            'history_sessions': 21, 'future_prices_consumed': False,
            'unavailable': [] if security_type == 'ETF' else ['etf_positive_trend20']}


def allocation(scores: list[dict[str, Any]], *, method: str, topk: int = 50,
               risk_degree: Decimal = Decimal('0.95')) -> list[dict[str, Any]]:
    """Indicative weights only. Do not invent next-open prices, units, fills or returns."""
    if (method not in {'TOPK_EQUAL_WEIGHT', 'EQUAL_WEIGHT_ALL'}
            or type(topk) is not int or not 1 <= topk <= 100
            or not risk_degree.is_finite() or not Decimal(0) < risk_degree <= Decimal('0.95')):
        raise ValueError('SIGNAL_ALLOCATION_POLICY_INVALID')
    if len({r['instrument'] for r in scores}) != len(scores) or any(
            not Decimal(str(r['score'])).is_finite() for r in scores):
        raise ValueError('SIGNAL_SCORE_IDENTITY_OR_VALUE_INVALID')
    ranked = sorted(scores, key=lambda r: (-Decimal(str(r['score'])), r['instrument']))
    selected = ranked[:topk] if method == 'TOPK_EQUAL_WEIGHT' else ranked
    if not selected:
        return []
    weight = risk_degree/Decimal(len(selected))
    return [{'instrument': r['instrument'], 'target_weight': str(weight)} for r in selected]
