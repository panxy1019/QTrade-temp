"""Replay an audited fixed ETF cohort through two assumption-based historical simulations."""

from __future__ import annotations

import argparse
import json
import os
import re
from collections import defaultdict
from dataclasses import asdict, dataclass
from datetime import UTC, date, datetime, time
from decimal import (
    ROUND_DOWN,
    ROUND_HALF_EVEN,
    Context,
    Decimal,
    DivisionByZero,
    InvalidOperation,
    Overflow,
    localcontext,
)
from pathlib import Path
from typing import Any

from qtrade.data import Bar
from qtrade.market_data.akshare import SHANGHAI
from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research.data_versions import file_hash
from qtrade.research.etf_research_contract import EtfAllocationPolicy, etf_contract
from qtrade.research.metrics import evaluate_returns
from qtrade.research.multi_asset import (
    DailyMultiAssetBacktester,
    MomentumAllocation,
    MultiAssetBacktestResult,
)
from scripts.atomic_json import write_json_immutable
from scripts.audit_etf_history import verify_etf_history_audit

REPO = Path(__file__).resolve().parents[1]
WARMUP = 21
RISK_DEGREE = Decimal('0.95')
STRATEGIES = ('positive20_top3_daily', 'fixed_pool_equal_weight_daily')
LIMITATIONS = [
    'ASSUMPTION_BASED_HISTORICAL_SIMULATION_NOT_PAPER_OR_FORWARD_EVIDENCE',
    'CURRENT_DIAGNOSTIC_COHORT_NOT_HISTORICAL_PIT_MEMBERSHIP',
    'SINA_NATIVE_PRICES_ADJUSTMENT_AND_DISTRIBUTIONS_SPLITS_NOT_RECONCILED',
    'LOT_FEE_SLIPPAGE_AND_NEXT_OPEN_FILL_RULES_ARE_SIMULATION_ASSUMPTIONS',
    'NO_CAPACITY_PARTICIPATION_SPREAD_LIMIT_SUSPENSION_OR_SETTLEMENT_ENFORCEMENT',
    'LONG_ONLY_NO_BORROWING_CASH_INTEREST_ZERO_NO_TERMINAL_LIQUIDATION',
    'RETROSPECTIVELY_CAPTURED_HISTORY_HAS_NO_HISTORICAL_AVAILABLE_AT_PROOF',
    'SHORT_COMMON_WINDOW_ANNUALIZED_METRICS_ARE_DESCRIPTIVE_NOT_CERTIFICATION',
    'NO_LEGACY_STITCHING_NO_FILL_FORWARD_NO_AUTOMATIC_SHADOW_METRIC_UPDATE',
]


@dataclass(frozen=True)
class Assumptions:
    initial_cash: Decimal = Decimal('1000000')
    lot_size: int = 100
    transaction_cost_rate: Decimal = Decimal('0.001')
    slippage_rate: Decimal = Decimal('0.0005')

    def document(self) -> dict[str, Any]:
        if (type(self.lot_size) is not int or not 1 <= self.lot_size <= 10000
                or any(not isinstance(value, Decimal) or not value.is_finite() for value in (
                    self.initial_cash, self.transaction_cost_rate, self.slippage_rate))
                or not Decimal('1000') <= self.initial_cash <= Decimal('1000000000000')
                or not 0 <= self.transaction_cost_rate <= Decimal('.1')
                or not 0 <= self.slippage_rate < Decimal('.1')):
            raise ValueError('ETF_COMPARISON_ASSUMPTIONS_INVALID')
        return {**{key: str(value) if isinstance(value, Decimal) else value
                   for key, value in asdict(self).items()},
                'status': 'ASSUMED_NOT_EXECUTION_ADMITTED', 'currency': 'CNY',
                'risk_degree_both_strategies': str(RISK_DEGREE), 'cash_interest_rate': '0',
                'timing': 'CLOSE_DECISION_NEXT_SESSION_OPEN', 'warmup_closes': WARMUP,
                'execution_order': 'SELL_BEFORE_BUY_THEN_SYMBOL_ASCENDING',
                'cost_basis': 'FILLED_NOTIONAL_TIMES_RATE_EACH_SIDE_NO_MINIMUM_OR_STAMP_TAX',
                'slippage_basis': 'OPEN_TIMES_ONE_PLUS_BUY_OR_MINUS_SELL_RATE',
                'capacity': 'UNLIMITED_ASSUMED_NO_VOLUME_OR_LIMIT_ADMISSION',
                'decimal_context': {'precision': 28, 'rounding': 'ROUND_HALF_EVEN',
                    'emin': -999999, 'emax': 999999, 'capitals': 1, 'clamp': 0,
                    'traps': ['InvalidOperation', 'DivisionByZero', 'Overflow']},
                'weight_precision': '0.000000000001', 'weight_rounding': 'ROUND_DOWN',
                'terminal_liquidation': False,
                'long_only': True, 'borrowing': False}


class CommonWarmupAllocation:
    def __init__(self, cohort: tuple[str, ...], strategy: str) -> None:
        self.cohort, self.strategy = cohort, strategy
        self.momentum = MomentumAllocation((20,), top_k=3, rebalance_every=1,
                                           reserve=Decimal('.05'))
        self.decisions: list[dict[str, Any]] = []

    def weights(self, history: dict[str, tuple[Decimal, ...]], *,
                session_index: int) -> dict[str, Decimal] | None:
        if (set(history) != set(self.cohort)
                or any(len(values) != session_index+1 for values in history.values())):
            raise ValueError('ETF_COMPARISON_HISTORY_NOT_RECTANGULAR')
        if session_index < WARMUP-1:
            return None
        weights = (self.momentum.weights(history, session_index=session_index)
                   if self.strategy == STRATEGIES[0]
                   else {symbol: RISK_DEGREE/len(self.cohort) for symbol in self.cohort})
        assert weights is not None
        weights = {symbol: weight.quantize(Decimal('0.000000000001'), rounding=ROUND_DOWN)
                   for symbol, weight in weights.items()}
        self.decisions.append({'session_index': session_index,
            'weights': [{'symbol': symbol, 'weight': str(weight)}
                        for symbol, weight in sorted(weights.items())]})
        return weights


def common_bars(audit: dict[str, Any], calendar: Calendar) -> list[Bar]:
    grid, cohort = audit['comparison_grid'], audit['cohort']
    if (len(grid) < WARMUP+1 or len(grid) > 3000 or not 1 <= len(cohort) <= 50
            or cohort != sorted(set(cohort)) or grid != sorted(set(grid))
            or grid[-1] != audit['bindings']['as_of']):
        raise ValueError('ETF_COMPARISON_INSUFFICIENT_FIXED_COHORT_GRID')
    start, end = date.fromisoformat(grid[0]), date.fromisoformat(grid[-1])
    expected = []
    day = start
    while day <= end:
        if calendar.is_open(day):
            expected.append(str(day))
        day = date.fromordinal(day.toordinal()+1)
    if expected != grid:
        raise ValueError('ETF_COMPARISON_GRID_NOT_CONTIGUOUS')
    indexed = {}
    for capture in audit['captures']:
        symbol = capture['symbol']
        for row in capture['normalized']:
            key = (row['date'], symbol)
            if key in indexed:
                raise ValueError('ETF_COMPARISON_DUPLICATE_SOURCE_ROW')
            indexed[key] = row
    bars = []
    for session in grid:
        trading_day = date.fromisoformat(session)
        for symbol in cohort:
            row = indexed[(session, symbol)]
            bars.append(Bar(symbol, 'SSE' if symbol.startswith('5') else 'SZSE',
                datetime.combine(trading_day, time(15), SHANGHAI), trading_day,
                *(Decimal(row[key]) for key in ('open', 'high', 'low', 'close', 'volume'))))
    return bars


def reconcile(result: MultiAssetBacktestResult, bars: list[Bar], assumptions: Assumptions,
              decisions: list[dict[str, Any]]) -> dict[str, Any]:
    """Independent journal replay and P&L identity; never a second fill engine."""
    daily: dict[date, dict[str, Bar]] = defaultdict(dict)
    for bar in bars:
        daily[bar.trading_day][bar.symbol] = bar
    trades: dict[date, list[Any]] = defaultdict(list)
    for trade in result.trades:
        trades[trade.trading_day].append(trade)
    cash, previous_nav, peak = (assumptions.initial_cash,)*3
    positions: dict[str, int] = {}
    previous_closes: dict[str, Decimal] = {}
    ledger: list[dict[str, Any]] = []
    fills: list[dict[str, Any]] = []
    total_fees = total_notional = Decimal(0)
    tolerance = assumptions.initial_cash*Decimal('1e-18')

    def equal(left: Decimal, right: Decimal) -> None:
        if abs(left-right) > tolerance:
            raise ValueError('ETF_COMPARISON_ACCOUNTING_RECONCILIATION_FAILED')

    for index, session in enumerate(result.sessions):
        market = daily[session]
        opening_cash = cash
        opening_positions = dict(positions)
        opening_nav = cash+sum((quantity*market[symbol].open
                                for symbol, quantity in positions.items()), start=Decimal(0))
        fees = notional = slippage = trade_pnl = Decimal(0)
        market_pnl = sum((quantity*(market[symbol].close-previous_closes[symbol])
                          for symbol, quantity in positions.items()), start=Decimal(0))
        for trade in trades[session]:
            if (index < WARMUP or trade.side not in {'BUY', 'SELL'}
                    or trade.quantity <= 0 or trade.quantity % assumptions.lot_size):
                raise ValueError('ETF_COMPARISON_INVALID_SIMULATED_TRADE')
            bar = market[trade.symbol]
            sign = 1 if trade.side == 'BUY' else -1
            equal(trade.price, bar.open*(1+sign*assumptions.slippage_rate))
            value = trade.quantity*trade.price
            equal(trade.cost, value*assumptions.transaction_cost_rate)
            cash -= sign*value+trade.cost
            positions[trade.symbol] = positions.get(trade.symbol, 0)+sign*trade.quantity
            if cash < -tolerance or positions[trade.symbol] < 0:
                raise ValueError('ETF_COMPARISON_NEGATIVE_CASH_OR_POSITION')
            adverse = trade.quantity*abs(trade.price-bar.open)
            fees += trade.cost
            notional += value
            slippage += adverse
            trade_pnl += sign*trade.quantity*(bar.close-bar.open)
            fills.append({'trade_index': len(fills), 'session': str(session),
                'signal_as_of': str(result.sessions[index-1]), 'symbol': trade.symbol,
                'side': trade.side, 'quantity': trade.quantity, 'raw_open': str(bar.open),
                'execution_price': str(trade.price), 'notional': str(value),
                'assumed_fee': str(trade.cost), 'assumed_slippage_cost': str(adverse),
                'cash_delta': str(-sign*value-trade.cost)})
        holdings: list[dict[str, Any]] = [
                    {'symbol': symbol, 'quantity': quantity, 'close': str(market[symbol].close),
                     'market_value': str(quantity*market[symbol].close)}
                    for symbol, quantity in sorted(positions.items()) if quantity]
        position_value = sum((Decimal(row['market_value']) for row in holdings), start=Decimal(0))
        nav = cash+position_value
        equal(nav, result.equity_curve[index])
        equal(nav-previous_nav, market_pnl+trade_pnl-slippage-fees)
        equal(nav/previous_nav-1, result.returns[index])
        equal(notional/assumptions.initial_cash, result.turnover_curve[index])
        equal(fees/assumptions.initial_cash, result.cost_curve[index])
        equal(position_value/nav, result.exposure_curve[index])
        peak = max(peak, nav)
        ledger.append({'session': str(session),
            'phase': 'WARMUP' if index < WARMUP else 'EVALUATION',
            'executed_signal_as_of': str(result.sessions[index-1]) if index >= WARMUP else None,
            'opening_cash': str(opening_cash), 'opening_positions': opening_positions,
            'equity_at_open': str(opening_nav), 'cash': str(cash), 'holdings': holdings,
            'holdings_value': str(position_value), 'net_asset_value': str(nav),
            'daily_return': str(nav/previous_nav-1), 'drawdown': str(nav/peak-1),
            'exposure': str(position_value/nav), 'gross_traded_notional': str(notional),
            'turnover_initial_cash': str(notional/assumptions.initial_cash),
            'turnover_previous_nav': str(notional/previous_nav), 'assumed_fees': str(fees),
            'assumed_slippage_cost': str(slippage), 'prior_holdings_price_pnl': str(market_pnl),
            'trade_open_to_close_pnl': str(trade_pnl), 'reconciliation': 'PASSED'})
        previous_nav = nav
        previous_closes = {symbol: bar.close for symbol, bar in market.items()}
        total_fees += fees
        total_notional += notional
    equal(total_fees, result.total_cost)
    equal(total_notional/assumptions.initial_cash, result.turnover)
    if tuple(sorted((symbol, quantity) for symbol, quantity in positions.items() if quantity)) != (
            result.terminal_positions):
        raise ValueError('ETF_COMPARISON_TERMINAL_POSITIONS_MISMATCH')
    targets = [{**row, 'signal_as_of': str(result.sessions[row['session_index']]),
                'next_execution_session': str(result.sessions[row['session_index']+1])
                if row['session_index']+1 < len(result.sessions) else None,
                'execution_state': 'NO_NEXT_OPEN_IN_INPUT' if row['session_index']+1 == len(
                    result.sessions) else 'SIMULATED_NEXT_OPEN'} for row in decisions]
    return {'ledger': ledger, 'trades': fills, 'targets': targets,
            'terminal_cash': str(cash), 'total_assumed_fees': str(total_fees),
            'total_assumed_slippage_cost': str(sum((Decimal(row['assumed_slippage_cost'])
                for row in fills), start=Decimal(0))), 'reconciliation': 'PASSED'}


def _persist(path: Path, value: dict[str, Any]) -> None:
    if path.exists():
        if path.is_symlink() or path.stat().st_size > 64_000_000:
            raise ValueError('ETF_COMPARISON_ARTIFACT_BOUND_INVALID')
        old = json.loads(path.read_bytes())
        if json.dumps(old, sort_keys=True) != json.dumps(value, sort_keys=True):
            raise ValueError('ETF_COMPARISON_IMMUTABLE_REPLAY_MISMATCH')
    else:
        write_json_immutable(path, value)


def _run(archive: Path, audit_path: Path, calendar: Calendar, revision: str, output: Path, *,
         assumptions: Assumptions | None = None, legacy_manifest: Path | None = None,
         legacy_bars: Path | None = None) -> dict[str, Any]:
    if (os.environ.get('LIVE_TRADING_ENABLED', 'false').lower() != 'false'
            or re.fullmatch('[a-f0-9]{40}', revision) is None):
        raise ValueError('ETF_COMPARISON_RESEARCH_CONFIGURATION_REQUIRED')
    if (output.is_symlink() or output.resolve().is_relative_to(archive.resolve())
            or archive.resolve().is_relative_to(output.resolve())):
        raise ValueError('ETF_COMPARISON_OUTPUT_MUST_BE_SEPARATE')
    assumptions = assumptions or Assumptions()
    policy = assumptions.document()
    audit = verify_etf_history_audit(audit_path, archive, calendar,
                                    legacy_manifest=legacy_manifest, legacy_bars=legacy_bars)
    bars = common_bars(audit, calendar)
    grid, cohort = audit['comparison_grid'], tuple(audit['cohort'])
    sources = {name: file_hash(REPO/name) for name in (
        'scripts/run_etf_history_comparison.py', 'scripts/audit_etf_history.py',
        'src/qtrade/research/multi_asset.py', 'src/qtrade/research/metrics.py',
        'src/qtrade/research/etf_research_contract.py')}
    bindings = {'audit_sha256': audit['evidence_sha256'],
                'audit_file_sha256': file_hash(audit_path),
                'source_observed_at': max(datetime.fromisoformat(row['received_at'])
                    for row in audit['captures']).isoformat(),
                'source_bindings': audit['bindings'], 'implementation_revision': revision,
                'implementation_sources': sources, 'cohort': list(cohort), 'sessions': grid,
                'warmup_sessions': grid[:WARMUP], 'evaluation_sessions': grid[WARMUP:]}
    identity = signed({'bindings': bindings, 'assumptions': policy})['evidence_sha256']
    root = output/identity
    if root.is_symlink():
        raise ValueError('ETF_COMPARISON_OUTPUT_MUST_BE_SEPARATE')
    artifacts: dict[str, dict[str, Any]] = {}
    results = {}
    ledgers = {}
    with localcontext() as context:
        context.prec = 28
        for strategy in STRATEGIES:
            model = CommonWarmupAllocation(cohort, strategy)
            result = DailyMultiAssetBacktester(initial_cash=assumptions.initial_cash,
                lot_size=assumptions.lot_size,
                transaction_cost_rate=assumptions.transaction_cost_rate,
                slippage_rate=assumptions.slippage_rate, maximum_stale_sessions=0).run(bars, model)
            results[strategy] = result
            ledgers[strategy] = reconcile(result, bars, assumptions, model.decisions)
        benchmark = results[STRATEGIES[1]]
        summaries = {}
        for strategy, result in results.items():
            metrics = evaluate_returns(list(result.returns[WARMUP:]),
                benchmark_returns=list(benchmark.returns[WARMUP:]),
                exposure=sum(result.exposure_curve[WARMUP:], start=Decimal(0))/len(grid[WARMUP:]),
                turnover=result.turnover, trade_count=len(result.trades),
                transaction_costs=result.total_cost/assumptions.initial_cash)
            selected = ('total_return', 'annualized_return', 'annualized_volatility', 'sharpe',
                'maximum_drawdown', 'benchmark_return', 'excess_return', 'information_ratio',
                'number_of_periods', 'turnover', 'exposure', 'number_of_trades',
                'transaction_costs')
            summary = {key: str(getattr(metrics, key)) for key in selected}
            summary.update(terminal_cash=ledgers[strategy]['terminal_cash'],
                terminal_net_asset_value=str(result.equity_curve[-1]),
                total_assumed_fees=ledgers[strategy]['total_assumed_fees'],
                total_assumed_slippage_cost=ledgers[strategy]['total_assumed_slippage_cost'],
                reconciliation='PASSED')
            summaries[strategy] = summary
            for kind in ('ledger', 'trades', 'targets'):
                artifacts[f'{strategy}/{kind}.json'] = signed({**AUTHORITY,
                    'schema_version': f'qtrade.etf-historical-{kind}.v1',
                    'simulation_id': identity, 'strategy_id': strategy,
                    'historical_simulation_only': True, 'records': ledgers[strategy][kind]})
    for name, value in artifacts.items():
        if not (root/name).resolve().is_relative_to(root.resolve()):
            raise ValueError('ETF_COMPARISON_ARTIFACT_PATH_ESCAPE')
        _persist(root/name, value)
    report_path = root/'comparison.json'
    if (report_path.is_symlink() or report_path.exists()
            and report_path.stat().st_size > 64_000_000):
        raise ValueError('ETF_COMPARISON_ARTIFACT_PATH_ESCAPE')
    generated = (json.loads(report_path.read_bytes())['generated_at'] if report_path.exists()
                 else datetime.now(UTC).isoformat())
    if (datetime.fromisoformat(generated).tzinfo is None
            or not datetime.fromisoformat(bindings['source_observed_at'])
            <= datetime.fromisoformat(generated) <= datetime.now(UTC)):
        raise ValueError('ETF_COMPARISON_REPORT_CLOCK_INVALID')
    report = signed({**AUTHORITY, 'schema_version': 'qtrade.etf-history-comparison.v1',
        'simulation_id': identity, 'stage': 'ASSUMPTION_BASED_HISTORICAL_SIMULATION',
        'generated_at': generated, 'security_type': 'ETF', 'execution_admitted': False,
        'paper_admission': 'BLOCKED', 'shadow_contract_modified': False,
        'whole_step1_certified': False, 'return_adjustment_certified': False,
        'allocation_contract': etf_contract(EtfAllocationPolicy()),
        'allocation_contract_id': signed(etf_contract(EtfAllocationPolicy()))['evidence_sha256'],
        'bindings': bindings, 'assumptions': policy,
        'grid_selection': {'policy': 'LATEST_FULL_FIXED_COHORT_CONTIGUOUS_RUN_ENDING_AS_OF',
            'expected_sessions': audit['expected_sessions'],
            'common_sessions': audit['common_sessions'],
            'all_common_runs': audit['common_contiguous_runs'], 'selected_sessions': grid,
            'excluded_latest_diagnostic_symbols_retained_in_cohort': audit[
                'excluded_from_latest_diagnostic'],
            'no_dynamic_cohort_shrinking': True, 'legacy_rows_used': 0},
        'strategies': summaries,
        'artifacts': {name: {'evidence_sha256': value['evidence_sha256'],
                             'file_sha256': file_hash(root/name)}
                      for name, value in artifacts.items()},
        'limitations': LIMITATIONS, 'source_audit_missing_inputs': audit['missing_inputs']})
    _persist(report_path, report)
    return report


def run(archive: Path, audit_path: Path, calendar: Calendar, revision: str, output: Path, *,
        assumptions: Assumptions | None = None, legacy_manifest: Path | None = None,
        legacy_bars: Path | None = None) -> dict[str, Any]:
    # Caller-wide Decimal settings cannot change source replay, sizing or accounting.
    with localcontext(Context(prec=28, rounding=ROUND_HALF_EVEN, Emin=-999999, Emax=999999,
                              capitals=1, clamp=0,
                              traps=[InvalidOperation, DivisionByZero, Overflow])):
        return _run(archive, audit_path, calendar, revision, output, assumptions=assumptions,
                    legacy_manifest=legacy_manifest, legacy_bars=legacy_bars)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--archive', type=Path, required=True)
    parser.add_argument('--audit', type=Path, required=True)
    parser.add_argument('--calendar', type=Path, required=True)
    parser.add_argument('--revision', required=True)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--initial-cash', type=Decimal, default=Decimal('1000000'))
    parser.add_argument('--lot-size', type=int, default=100)
    parser.add_argument('--transaction-cost-rate', type=Decimal, default=Decimal('.001'))
    parser.add_argument('--slippage-rate', type=Decimal, default=Decimal('.0005'))
    parser.add_argument('--legacy-manifest', type=Path)
    parser.add_argument('--legacy-bars', type=Path)
    args = parser.parse_args(argv)
    result = run(args.archive, args.audit, Calendar(args.calendar), args.revision, args.output,
        assumptions=Assumptions(args.initial_cash, args.lot_size, args.transaction_cost_rate,
                                args.slippage_rate),
        legacy_manifest=args.legacy_manifest, legacy_bars=args.legacy_bars)
    print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
