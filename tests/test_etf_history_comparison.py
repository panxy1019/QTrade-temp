from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, date, datetime
from decimal import ROUND_DOWN, ROUND_UP, Decimal, Inexact, localcontext
from pathlib import Path
from tempfile import TemporaryDirectory

import pytest

from qtrade.monitoring.data_freshness import AUTHORITY, Calendar, signed
from qtrade.research import etf_trend
from qtrade.research.data_versions import file_hash
from qtrade.research.multi_asset import DailyMultiAssetBacktester
from qtrade.research.qlib_publication import verify_signed
from scripts import audit_etf_history as auditor
from scripts import run_etf_history_comparison as runner
from scripts.unified_store import UnifiedBatchStore

REVISION = 'c'*40


@pytest.fixture
def inputs(monkeypatch):
    directory = Path(runner.REPO.anchor) if os.name == 'nt' else None
    with TemporaryDirectory(prefix='etfh-', dir=directory) as temporary:
        root = Path(temporary)
        calendar = Calendar(runner.REPO/'configs/observer_calendar_2026.json')
        monkeypatch.setenv('LIVE_TRADING_ENABLED', 'false')
        monkeypatch.setattr('scripts.unified_store._fsync_directory', lambda _: None)

        class Clock(datetime):
            @classmethod
            def now(cls, tz=None):
                return datetime(2026, 10, 30, 12, tzinfo=UTC).astimezone(tz or UTC)

        monkeypatch.setattr(etf_trend, 'datetime', Clock)
        if os.name == 'nt':
            writer = runner.write_json_immutable

            def write(path, value):
                try:
                    writer(path, value)
                except PermissionError:
                    assert json.loads(path.read_bytes()) == value

            monkeypatch.setattr(runner, 'write_json_immutable', write)
        counter = [0]

        def create(*, count=55, negative=False, change=None):
            days = auditor._days(calendar, date(2026, 8, 1), date(2026, 10, 26))[-count:]
            captures = []
            symbols = ('159915', '510300', '510500', '512010')
            for offset, symbol in enumerate(symbols):
                rows = []
                for index, day in enumerate(days):
                    step = Decimal('-.01') if negative or offset == 3 else (
                        Decimal('.01')*(offset+1))
                    close = Decimal(10)+index*step
                    if offset == 2 and not negative and index > 25:
                        close -= Decimal('.06')*(index-25)
                    opening = close-Decimal('.005')
                    rows.append({'day': day, 'open': str(opening), 'high': str(close+Decimal('.1')),
                                 'low': str(opening-Decimal('.1')), 'close': str(close),
                                 'volume': '10000'})
                if change:
                    rows = change(symbol, rows)
                captures.append({'source_id': 'sina_klines', 'request': {
                    'symbol': ('sh' if symbol.startswith('5') else 'sz')+symbol,
                    'scale': '240', 'ma': 'no', 'datalen': '63'},
                    'received_at': '2026-10-26T09:00:00+00:00', 'body': json.dumps(rows).encode(),
                    'status_code': 200, 'transport_error': None, 'body_complete': True})
            bindings = {'master_sha256': 'a'*64, 'as_of': '2026-10-26',
                        'code_revision': 'b'*40, 'contract': 'ETF_CLOSE_DIAGNOSTIC_V1',
                        'calendar_sha256': signed(calendar.document)['evidence_sha256']}
            archive = root/str(counter[0])/signed(bindings)['evidence_sha256']
            counter[0] += 1
            archive.mkdir(parents=True)
            raw = UnifiedBatchStore(archive/'capture-store').capture(captures)
            (archive/'receipt.json').write_text(json.dumps(signed({**AUTHORITY,
                'bindings': bindings, 'raw': raw})))
            value = etf_trend.admit(captures, list(symbols), calendar, date(2026, 10, 26),
                                    datetime(2026, 10, 26, 9, tzinfo=UTC))
            value.pop('evidence_sha256')
            value.update(bindings=bindings, receipt_sha256=file_hash(archive/'receipt.json'))
            (archive/'diagnostic.json').write_text(json.dumps(signed(value)))
            audit = auditor.audit_etf_history(archive, calendar, REVISION)
            path = root/f'audit-{counter[0]}.json'
            path.write_text(json.dumps(audit))
            return archive, path, audit

        yield root, calendar, create


def artifacts(root, report, strategy):
    directory = root/'results'/report['simulation_id']/strategy
    return {name: json.loads((directory/(name+'.json')).read_bytes())['records']
            for name in ('ledger', 'trades', 'targets')}


def execute(inputs, **kwargs):
    root, calendar, create = inputs
    archive, audit_path, audit = create(**kwargs)
    before = {path.relative_to(archive): path.read_bytes()
              for path in archive.rglob('*') if path.is_file()}
    report = runner.run(archive, audit_path, calendar, REVISION, root/'results')
    after = {path.relative_to(archive): path.read_bytes()
             for path in archive.rglob('*') if path.is_file()}
    assert before == after
    return archive, audit_path, audit, report


def test_same_grid_warmup_costs_and_causal_daily_fixed_pool_comparison(inputs):
    root, _, _ = inputs
    _, _, audit, report = execute(inputs)
    verify_signed(report)
    assert report['execution_admitted'] is False and report['paper_admission'] == 'BLOCKED'
    assert report['whole_step1_certified'] is False and report['shadow_contract_modified'] is False
    assert report['bindings']['cohort'] == audit['cohort']
    assert len(report['bindings']['sessions']) == 55
    assert len(report['bindings']['warmup_sessions']) == 21
    assert len(report['bindings']['evaluation_sessions']) == 34
    assert report['assumptions']['risk_degree_both_strategies'] == '0.95'
    for strategy in runner.STRATEGIES:
        value = artifacts(root, report, strategy)
        assert len(value['ledger']) == 55 and len(value['targets']) == 35
        assert all(row['holdings'] == [] and Decimal(row['net_asset_value']) == Decimal('1000000')
                   for row in value['ledger'][:21])
        assert value['trades'][0]['session'] == audit['comparison_grid'][21]
        assert value['trades'][0]['signal_as_of'] == audit['comparison_grid'][20]
        assert value['targets'][-1]['next_execution_session'] is None
        assert value['targets'][-1]['execution_state'] == 'NO_NEXT_OPEN_IN_INPUT'
        assert all(fill['signal_as_of'] != audit['comparison_grid'][-1]
                   for fill in value['trades'])
        assert report['strategies'][strategy]['number_of_periods'] == '34'
    baseline = artifacts(root, report, runner.STRATEGIES[1])
    assert all({weight['symbol'] for weight in row['weights']} == set(audit['cohort'])
               for row in baseline['targets'])
    assert all({weight['weight'] for weight in row['weights']} == {'0.237500000000'}
               for row in baseline['targets'])
    selected = artifacts(root, report, runner.STRATEGIES[0])
    assert len(selected['targets'][0]['weights']) == 3
    assert {row['symbol'] for row in selected['targets'][0]['weights']} == {
        '159915', '510300', '510500'}
    assert any(fill['side'] == 'SELL' for fill in selected['trades'])


def test_cash_holdings_fees_slippage_nav_turnover_and_drawdown_reconcile(inputs):
    root, _, _ = inputs
    _, _, _, report = execute(inputs)
    for strategy in runner.STRATEGIES:
        value = artifacts(root, report, strategy)
        fees = slippage = Decimal(0)
        peak = previous = Decimal('1000000')
        for row in value['ledger']:
            nav = Decimal(row['net_asset_value'])
            holdings = sum((Decimal(item['close'])*item['quantity'] for item in row['holdings']),
                           start=Decimal(0))
            assert abs(nav-Decimal(row['cash'])-holdings) < Decimal('1e-18')
            assert Decimal(row['cash']) >= 0
            assert all(item['quantity'] % 100 == 0 for item in row['holdings'])
            peak = max(peak, nav)
            assert Decimal(row['drawdown']) == nav/peak-1
            assert Decimal(row['daily_return']) == nav/previous-1
            assert row['reconciliation'] == 'PASSED'
            fees += Decimal(row['assumed_fees'])
            slippage += Decimal(row['assumed_slippage_cost'])
            previous = nav
        summary = report['strategies'][strategy]
        assert fees == Decimal(summary['total_assumed_fees']) > 0
        assert slippage == Decimal(summary['total_assumed_slippage_cost']) > 0
        assert Decimal(summary['terminal_cash']) == Decimal(value['ledger'][-1]['cash'])
        assert Decimal(summary['terminal_net_asset_value']) == previous
        assert Decimal(summary['total_return']) == pytest.approx(
            previous/Decimal('1000000')-1, abs=Decimal('1e-24'))


def test_public_source_bars_match_full_audited_grid_for_both_strategies(inputs):
    root, _, _ = inputs
    _, _, audit, report = execute(inputs)
    expected = {(row['date'], capture['symbol']): row
                for capture in audit['captures'] for row in capture['normalized']}
    ledgers = [artifacts(root, report, strategy)['ledger'] for strategy in runner.STRATEGIES]
    for left, right in zip(*ledgers, strict=True):
        assert left['source_bars'] == right['source_bars']
        assert [bar['symbol'] for bar in left['source_bars']] == audit['cohort']
        assert len(left['source_bars']) <= 50
        for bar in left['source_bars']:
            original = expected[(left['session'], bar['symbol'])]
            assert set(bar) == {'symbol', 'open', 'high', 'low', 'close', 'volume'}
            assert all(bar[field] == original[field]
                       for field in ('open', 'high', 'low', 'close', 'volume'))


def test_public_marks_reconstruct_opening_nav_and_separate_price_pnl_components(inputs):
    root, _, _ = inputs
    _, _, _, report = execute(inputs)
    for strategy in runner.STRATEGIES:
        value = artifacts(root, report, strategy)
        previous = {}
        for row in value['ledger']:
            market = {bar['symbol']: bar for bar in row['source_bars']}
            opening = Decimal(row['opening_cash'])+sum((quantity*Decimal(market[symbol]['open'])
                for symbol, quantity in row['opening_positions'].items()), start=Decimal(0))
            price_pnl = sum((quantity*(Decimal(market[symbol]['close'])-previous[symbol])
                for symbol, quantity in row['opening_positions'].items()), start=Decimal(0))
            trade_pnl = sum(((1 if trade['side'] == 'BUY' else -1)*trade['quantity']*(
                Decimal(market[trade['symbol']]['close'])-Decimal(market[trade['symbol']]['open']))
                for trade in value['trades'] if trade['session'] == row['session']),
                start=Decimal(0))
            assert abs(opening-Decimal(row['equity_at_open'])) < Decimal('1e-18')
            assert abs(price_pnl-Decimal(row['prior_holdings_price_pnl'])) < Decimal('1e-18')
            assert abs(trade_pnl-Decimal(row['trade_open_to_close_pnl'])) < Decimal('1e-18')
            previous = {symbol: Decimal(bar['close']) for symbol, bar in market.items()}


def test_positive20_targets_are_reproducible_from_only_public_full_pool_close_marks(inputs):
    root, _, _ = inputs
    _, _, audit, report = execute(inputs)
    value = artifacts(root, report, runner.STRATEGIES[0])
    history = {symbol: [] for symbol in audit['cohort']}
    decisions = {row['session_index']: row for row in value['targets']}
    for index, row in enumerate(value['ledger']):
        for bar in row['source_bars']:
            history[bar['symbol']].append(Decimal(bar['close']))
        if index < 20:
            continue
        scores = {symbol: closes[-1]/closes[-21]-1 for symbol, closes in history.items()}
        selected = sorted((symbol for symbol, score in scores.items() if score > 0),
                           key=lambda symbol: (-scores[symbol], symbol))[:3]
        weight = str((Decimal('.95')/len(selected)).quantize(
            Decimal('0.000000000001'), rounding=ROUND_DOWN)) if selected else None
        assert decisions[index]['weights'] == [
            {'symbol': symbol, 'weight': weight} for symbol in sorted(selected)]


def test_source_replay_and_identical_output_are_immutable(inputs):
    root, calendar, _ = inputs
    archive, audit_path, _, report = execute(inputs)
    directory = root/'results'/report['simulation_id']
    before = {path.relative_to(directory): path.read_bytes()
              for path in directory.rglob('*') if path.is_file()}
    assert runner.run(archive, audit_path, calendar, REVISION, root/'results') == report
    after = {path.relative_to(directory): path.read_bytes()
             for path in directory.rglob('*') if path.is_file()}
    assert before == after
    for name, binding in report['artifacts'].items():
        assert binding['file_sha256'] == file_hash(directory/name)
        value = json.loads((directory/name).read_bytes())
        verify_signed(value)
        assert value['evidence_sha256'] == binding['evidence_sha256']


def test_all_negative_trends_stay_simulated_cash_while_baseline_keeps_whole_pool(inputs):
    root, _, _ = inputs
    _, _, _, report = execute(inputs, negative=True)
    momentum = artifacts(root, report, runner.STRATEGIES[0])
    assert momentum['trades'] == []
    assert all(row['weights'] == [] for row in momentum['targets'])
    assert report['strategies'][runner.STRATEGIES[0]]['terminal_cash'] == '1000000'
    baseline = artifacts(root, report, runner.STRATEGIES[1])
    assert {trade['symbol'] for trade in baseline['trades']} == {
        '159915', '510300', '510500', '512010'}


def test_latest_missing_symbol_cannot_be_dropped_or_filled_forward(inputs):
    root, calendar, create = inputs
    archive, path, _ = create(change=lambda symbol, rows: rows[:-1] if symbol == '159915' else rows)
    with pytest.raises(ValueError, match='INSUFFICIENT_FIXED_COHORT_GRID'):
        runner.run(archive, path, calendar, REVISION, root/'results')
    assert not (root/'results').exists()


def test_gap_trim_remains_explicit_common_contiguous_not_dynamic_cohort(inputs):
    root, _, _ = inputs

    def missing(symbol, rows):
        return [row for index, row in enumerate(rows) if not (symbol == '159915' and index == 5)]

    _, _, audit, report = execute(inputs, change=missing)
    assert len(audit['common_contiguous_runs']) == 2
    assert len(report['bindings']['sessions']) == 49
    assert len(report['bindings']['cohort']) == 4
    assert report['grid_selection']['all_common_runs'] == audit['common_contiguous_runs']
    assert report['grid_selection']['legacy_rows_used'] == 0
    assert report['grid_selection']['no_dynamic_cohort_shrinking'] is True


def test_insufficient_warmup_fails_without_output(inputs):
    root, calendar, create = inputs
    archive, path, _ = create(count=21)
    with pytest.raises(ValueError, match='INSUFFICIENT_FIXED_COHORT_GRID'):
        runner.run(archive, path, calendar, REVISION, root/'results')
    assert not (root/'results').exists()


def test_minimum_matured_session_uses_next_open_not_signal_close_return(inputs):
    root, _, _ = inputs
    _, _, _, report = execute(inputs, count=22)
    baseline = artifacts(root, report, runner.STRATEGIES[1])
    assert report['strategies'][runner.STRATEGIES[1]]['number_of_periods'] == '1'
    assert len({trade['session'] for trade in baseline['trades']}) == 1
    final = baseline['ledger'][-1]
    expected = Decimal(final['trade_open_to_close_pnl'])-Decimal(final['assumed_fees'])-Decimal(
        final['assumed_slippage_cost'])
    assert abs(Decimal(final['net_asset_value'])-Decimal('1000000')-expected) < Decimal('1e-18')


def test_corrupt_audit_cannot_supply_fabricated_normalized_inputs(inputs):
    root, calendar, create = inputs
    archive, path, audit = create()
    audit['captures'][0]['normalized'][0]['close'] = '999'
    audit.pop('evidence_sha256')
    path.write_text(json.dumps(signed(audit)))
    with pytest.raises(ValueError, match='RAW_REPLAY_MISMATCH'):
        runner.run(archive, path, calendar, REVISION, root/'results')
    assert not (root/'results').exists()


def test_existing_artifact_tamper_is_not_overwritten(inputs):
    root, calendar, _ = inputs
    archive, path, _, report = execute(inputs)
    target = root/'results'/report['simulation_id']/runner.STRATEGIES[0]/'ledger.json'
    target.write_text('{}')
    with pytest.raises(ValueError, match='IMMUTABLE_REPLAY_MISMATCH'):
        runner.run(archive, path, calendar, REVISION, root/'results')
    assert target.read_text() == '{}'


def test_existing_report_timestamp_cannot_precede_verified_source_receipts(inputs):
    root, calendar, _ = inputs
    archive, path, _, report = execute(inputs)
    target = root/'results'/report['simulation_id']/'comparison.json'
    report['generated_at'] = '1999-01-01T00:00:00+00:00'
    report.pop('evidence_sha256')
    target.write_text(json.dumps(signed(report)))
    before = target.read_bytes()
    with pytest.raises(ValueError, match='REPORT_CLOCK_INVALID'):
        runner.run(archive, path, calendar, REVISION, root/'results')
    assert target.read_bytes() == before


def test_independent_reconciliation_rejects_engine_equity_corruption(inputs):
    _, calendar, create = inputs
    archive, _, audit = create()
    assert archive.is_dir()
    bars = runner.common_bars(audit, calendar)
    model = runner.CommonWarmupAllocation(tuple(audit['cohort']), runner.STRATEGIES[0])
    result = DailyMultiAssetBacktester().run(bars, model)
    bad = replace(result, equity_curve=(*result.equity_curve[:-1], result.equity_curve[-1]+1))
    with pytest.raises(ValueError, match='ACCOUNTING_RECONCILIATION'):
        runner.reconcile(bad, bars, runner.Assumptions(), model.decisions)


@pytest.mark.parametrize('assumptions', [
    runner.Assumptions(initial_cash=Decimal('NaN')),
    runner.Assumptions(lot_size=0), runner.Assumptions(lot_size=True),
    runner.Assumptions(transaction_cost_rate=Decimal('-.01')),
    runner.Assumptions(slippage_rate=Decimal('.1')),
])
def test_invalid_assumptions_are_rejected(assumptions):
    with pytest.raises(ValueError, match='ASSUMPTIONS_INVALID'):
        assumptions.document()


def test_output_cannot_mutate_archive_or_live_configuration(inputs, monkeypatch):
    root, calendar, create = inputs
    archive, path, _ = create()
    with pytest.raises(ValueError, match='OUTPUT_MUST_BE_SEPARATE'):
        runner.run(archive, path, calendar, REVISION, archive/'results')
    monkeypatch.setenv('LIVE_TRADING_ENABLED', 'true')
    with pytest.raises(ValueError, match='CONFIGURATION_REQUIRED'):
        runner.run(archive, path, calendar, REVISION, root/'results')


def test_higher_explicit_fee_reduces_terminal_equity(inputs):
    root, calendar, create = inputs
    archive, path, _ = create(count=22)
    low = runner.run(archive, path, calendar, REVISION, root/'results',
                     assumptions=runner.Assumptions(transaction_cost_rate=Decimal('0')))
    high = runner.run(archive, path, calendar, REVISION, root/'results',
                      assumptions=runner.Assumptions(transaction_cost_rate=Decimal('.01')))
    for strategy in runner.STRATEGIES:
        assert Decimal(high['strategies'][strategy]['terminal_net_asset_value']) < Decimal(
            low['strategies'][strategy]['terminal_net_asset_value'])


def test_caller_decimal_precision_cannot_change_replay(inputs):
    root, calendar, _ = inputs
    archive, path, _, report = execute(inputs)
    with localcontext() as context:
        context.prec = 8
        context.rounding = ROUND_UP
        context.traps[Inexact] = True
        context.Emin, context.Emax = -50, 50
        context.clamp = 1
        assert runner.run(archive, path, calendar, REVISION, root/'results') == report
        assert context.prec == 8
        assert context.rounding == ROUND_UP and context.traps[Inexact]


def test_custom_lot_is_passed_to_engine_and_every_fill(inputs):
    root, calendar, create = inputs
    archive, path, _ = create(count=22)
    report = runner.run(archive, path, calendar, REVISION, root/'results',
                        assumptions=runner.Assumptions(lot_size=137))
    assert report['assumptions']['lot_size'] == 137
    for strategy in runner.STRATEGIES:
        fills = artifacts(root, report, strategy)['trades']
        assert fills and all(fill['quantity'] % 137 == 0 for fill in fills)
        assert any(fill['quantity'] % 100 != 0 for fill in fills)


def test_later_close_changes_cannot_change_earlier_decisions_or_fills(inputs):
    root, calendar, create = inputs
    archive, path, _ = create()
    first = runner.run(archive, path, calendar, REVISION, root/'results')

    def late_change(symbol, rows):
        rows[-1]['close'] = str(Decimal(rows[-1]['close'])+Decimal('.5'))
        rows[-1]['high'] = str(Decimal(rows[-1]['high'])+Decimal('.5'))
        return rows

    archive, path, _ = create(change=late_change)
    second = runner.run(archive, path, calendar, REVISION, root/'results')
    for strategy in runner.STRATEGIES:
        before, after = artifacts(root, first, strategy), artifacts(root, second, strategy)
        assert before['targets'][:-1] == after['targets'][:-1]
        assert before['trades'] == after['trades']
        assert before['ledger'][:-1] == after['ledger'][:-1]
