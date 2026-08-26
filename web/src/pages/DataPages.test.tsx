import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { BacktestPreview, PlatformSnapshot } from '../types'
import { cancelPaperOrder, controlRuntime, loadLongFiveMinuteBarSeries, loadMinuteBarSeries, runBacktestPreview } from '../api/client'
import { BacktestPage, DataCenterPage, ExperimentPage, MarketPage, MonitorPage, OrdersPage, PaperPage, PositionsPage, ResearchPage, RiskPage, StrategyPage } from './DataPages'

vi.mock('../charts/ChartRenderer', () => ({ default: () => <div data-testid="equity-chart" /> }))
vi.mock('../charts/MinuteBacktestChart', () => ({ MinuteBacktestChart: () => <div data-testid="minute-backtest-chart" /> }))
vi.mock('../charts/ReservedTailChart', () => ({ ReservedTailChart: () => <div data-testid="reserved-tail-chart" /> }))
vi.mock('../charts/MinuteHistoryChart', () => ({ MinuteHistoryChart: ({ loading = false }: { loading?: boolean }) => <div data-testid="minute-history-chart">{loading ? '正在加载已验证历史分时线…' : null}</div> }))
vi.mock('../api/client', () => ({
  prometheusMetricsUrl: '/metrics/prometheus',
  loadHistoricalBarSeries: vi.fn(async (symbol: string) => ({
    symbol, data_version: 'akshare-bars-v1', dataset_checksum: 'abc', observation_type: 'HISTORICAL_DAILY_BAR_SERIES',
    total_count: 2, truncated: false,
    bars: [
      { symbol, exchange: 'SSE', timestamp: '2026-08-11T15:00:00+08:00', trading_day: '2026-08-11', open: '4', high: '4.2', low: '3.9', close: '4.1', volume: '100', data_version: 'akshare-bars-v1', observation_type: 'HISTORICAL_DAILY_BAR' },
      { symbol, exchange: 'SSE', timestamp: '2026-08-12T15:00:00+08:00', trading_day: '2026-08-12', open: '4.1', high: '4.3', low: '4', close: '4.2', volume: '200', data_version: 'akshare-bars-v1', observation_type: 'HISTORICAL_DAILY_BAR' },
    ],
  })),
  loadMinuteBarSeries: vi.fn(async (symbol: string) => ({
    symbol, data_version: 'sina-minute-v2', dataset_checksum: 'minute-checksum',
    observation_type: 'HISTORICAL_MINUTE_BAR_SERIES', interval: '1m', total_count: 2, truncated: false,
    bars: [
      { symbol, exchange: 'SSE', timestamp: '2026-08-25T09:31:00+08:00', trading_day: '2026-08-25', open: '4', high: '4.1', low: '3.9', close: '4.05', volume: '100', data_version: 'sina-minute-v2', observation_type: 'HISTORICAL_MINUTE_BAR' },
      { symbol, exchange: 'SSE', timestamp: '2026-08-25T09:32:00+08:00', trading_day: '2026-08-25', open: '4.05', high: '4.1', low: '4', close: '4.08', volume: '120', data_version: 'sina-minute-v2', observation_type: 'HISTORICAL_MINUTE_BAR' },
    ],
  })),
  loadFiveMinuteBarSeries: vi.fn(async (symbol: string) => ({
    symbol, data_version: 'sina-five-minute-v1', dataset_checksum: 'five-minute-checksum',
    observation_type: 'HISTORICAL_FIVE_MINUTE_BAR_SERIES', interval: '5m', total_count: 2, truncated: false,
    bars: [
      { symbol, exchange: 'SSE', timestamp: '2026-08-25T09:35:00+08:00', trading_day: '2026-08-25', open: '18', high: '18.1', low: '17.9', close: '18.05', volume: '100', data_version: 'sina-five-minute-v1', observation_type: 'HISTORICAL_FIVE_MINUTE_BAR' },
      { symbol, exchange: 'SSE', timestamp: '2026-08-25T09:40:00+08:00', trading_day: '2026-08-25', open: '18.05', high: '18.2', low: '18', close: '18.1', volume: '120', data_version: 'sina-five-minute-v1', observation_type: 'HISTORICAL_FIVE_MINUTE_BAR' },
    ],
  })),
  loadLongFiveMinuteBarSeries: vi.fn(async (symbol: string, tradingDay: string) => ({
    symbol, data_version: 'baostock-five-minute-long-v1', dataset_checksum: 'long-five-minute-checksum',
    observation_type: 'HISTORICAL_FIVE_MINUTE_BAR_SERIES', interval: '5m', selected_trading_day: tradingDay, total_count: 2, truncated: false,
    bars: [
      { symbol, exchange: 'SSE', timestamp: `${tradingDay}T09:35:00+08:00`, trading_day: tradingDay, open: '41', high: '41.1', low: '40.9', close: '41.05', volume: '100', data_version: 'baostock-five-minute-long-v1', observation_type: 'HISTORICAL_FIVE_MINUTE_BAR' },
      { symbol, exchange: 'SSE', timestamp: `${tradingDay}T09:40:00+08:00`, trading_day: tradingDay, open: '41.05', high: '41.2', low: '41', close: '41.1', volume: '120', data_version: 'baostock-five-minute-long-v1', observation_type: 'HISTORICAL_FIVE_MINUTE_BAR' },
    ],
  })),
  runBacktestPreview: vi.fn(),
  controlRuntime: vi.fn(),
  cancelPaperOrder: vi.fn(),
}))

const longExecutionFixture = {
  symbol_count: 75,
  observation_count: 864000,
  reduced_exposure_observation_count: 691486,
  exposure_sum: 544471.25,
  mean_exposure: 0.6301750579,
  reduced_exposure_ratio: 0.8003310185,
  turnover: 38391.75,
  mean_turnover_per_symbol: 511.89,
  transaction_cost: 20.15579246,
  mean_transaction_cost_per_symbol: 0.2687438995,
  reason_counts: { INTRADAY_DRAWDOWN_LIMIT: 127941, MINUTE_TREND_WEAK: 433024, MINUTE_VOLATILITY_LIMIT: 68314, RECOVERY_COOLDOWN: 184646 },
}

const foldExecutionFixture = {
  ...longExecutionFixture,
  observation_count: 144000,
  reduced_exposure_observation_count: 113824,
  exposure_sum: 94259.5,
  mean_exposure: 0.6545798611,
  reduced_exposure_ratio: 0.7848888889,
  turnover: 6572.75,
  mean_turnover_per_symbol: 87.63666667,
  transaction_cost: 3.457655249,
  mean_transaction_cost_per_symbol: 0.04610207,
  reason_counts: { INTRADAY_DRAWDOWN_LIMIT: 18398, MINUTE_TREND_WEAK: 70334, MINUTE_VOLATILITY_LIMIT: 11252, RECOVERY_COOLDOWN: 31443 },
}

const data = {
  system: { mode: 'PAPER', running: true, risk_state: 'NORMAL' },
  health: { healthy: true, runtime_mode: 'PAPER', risk_state: 'NORMAL', gateway_connected: true, reconciliation_status: 'OK' },
  account: { cash: '98999', equity: null, valuation_status: 'MARKET_PRICE_UNAVAILABLE' },
  pnl: { realized: '0' },
  positions: [{ symbol: '510300', quantity: 100, sellable_quantity: 0, as_of_date: '2026-08-12', average_cost: '10.01', realized_pnl: '0', cost_basis_value: '1001', market_price: null, market_value: null, unrealized_pnl: null, valuation_status: 'MARKET_PRICE_UNAVAILABLE' }],
  orders: [{ internal_order_id: 'ORDER-1', broker_order_id: 'PAPER-1', strategy_id: 'trend@1.0.0', portfolio_id: 'paper-etf', symbol: '510300', side: 'BUY', price: '10', status: 'FILLED', quantity: 100, filled_quantity: 100, rejection_reason: null, created_at: '2026-08-12T09:31:00+08:00', updated_at: '2026-08-12T09:32:00+08:00' }],
  trades: [{ trade_id: 'PAPER-TRADE-1', internal_order_id: 'ORDER-1', strategy_id: 'trend@1.0.0', portfolio_id: 'paper-etf', symbol: '510300', side: 'BUY', price: '10', quantity: 100, fee: '1', timestamp: '2026-08-12T09:32:00+08:00', execution_type: 'PAPER_SIMULATED_FILL' }],
  risk: { state: 'NORMAL', cash: '98999', position_cost_basis: '1001', open_buy_notional: '0', gross_exposure: '1001', exposure_basis: 'COST_BASIS_PLUS_OPEN_BUY_LIMIT_PRICE', largest_position_concentration: '1', open_order_count: 0, realized_pnl: '0', drawdown: null, valuation_status: 'MARKET_PRICE_UNAVAILABLE', gateway_connected: true, reconciliation_status: 'OK' },
  metrics: { market_data_lag_seconds: '1.25', event_queue_depth: 2, orders_per_minute: 3, reject_rate: '0.1', fill_rate: '0.9', last_order_latency_seconds: '0.04', gateway_connected: false, reconciliation_status: 'UNKNOWN', monitoring_degraded: false },
  audit: [
    { timestamp: '2026-08-12T09:32:00+08:00', event_type: 'PAPER_INCIDENT', severity: 'ERROR', message: 'gateway disconnected', order_id: 'ORDER-1' },
    { timestamp: '2026-08-12T09:33:00+08:00', event_type: 'RISK_LIMIT', severity: 'CRITICAL', message: 'drawdown threshold reached', order_id: null },
  ],
  strategies: [{
    strategy_id: 'momentum-vol-slow', name: 'momentum-volatility', version: 'abc123def456',
    family: 'ETF_MOMENTUM_VOLATILITY', status: 'NOT_ELIGIBLE', role: 'NONE',
    metrics: { sharpe: '0.4412', maximum_drawdown: '-0.0909' }, scenario_type: 'SENSITIVITY',
    data_version: 'akshare-bars-v1', git_commit: '3bdd29f', evidence_status: 'VERIFIED_NOT_ELIGIBLE',
    lineage_status: 'NOT_REGISTERED',
  }],
  experiments: {
    classification: 'exploratory_noncommercial_current_universe_not_pit',
    data_version: 'akshare-bars-v1', git_commit: '3bdd29f', promotion: 'NOT_ELIGIBLE',
    promotion_reasons: ['CURRENT_UNIVERSE_SURVIVORSHIP_BIAS'], lineage: [], lineage_status: 'NOT_REGISTERED',
    experiments: [{
      experiment_id: 'momentum-vol-slow', spec_hash: 'a', result_hash: 'b', scenario_type: 'SENSITIVITY',
      metrics: { total_return: '0.0335', sharpe: '0.4412', maximum_drawdown: '-0.0909' },
      model: 'momentum-volatility', parameters: { lookback: '120' },
      split: { train_start: '2024-01-02', train_end: '2024-12-31', validation_start: '2025-01-01', validation_end: '2025-12-31', test_start: '2026-01-01', test_end: '2026-08-12' },
      universe_size: 30, evidence_status: 'VERIFIED_NOT_ELIGIBLE',
    }],
  },
  market: {
    provider_connected: false,
    execution_gateway_connected: true,
    mode: 'PAPER',
    snapshot_readiness: false,
    historical: {
      provider: 'AKShare',
      provider_version: '1.18.84',
      classification: 'noncommercial_research_historical_only',
      data_version: 'akshare-bars-v1',
      quality_status: 'PASSED',
      checksum: 'abc',
      bar_count: 18960,
      observed_start: '2024-01-02T15:00:00+08:00',
      observed_end: '2026-08-12T15:00:00+08:00',
      snapshot_readiness: false,
      real_data_paper_readiness: false,
      universe: { point_in_time: false, symbols: ['510300'] },
    },
  },
  minuteOverlay: {
    classification: 'CAUSAL_MINUTE_RISK_OVERLAY_PAPER_SHADOW_ONLY',
    readiness: 'NOT_READY', provider: null, provider_connected: false, bar_interval: '1m',
    symbol_count: 0, ready_symbol_count: 0, external_order_transmission: false,
    promotion: 'NOT_ELIGIBLE', reasons: ['NO_AUTHORIZED_REALTIME_TICK_PROVIDER'], symbols: [],
  },
  minuteResearch: {
    classification: 'noncommercial_recent_historical_1m_non_pit_not_eligible', promotion: 'NOT_ELIGIBLE',
    promotion_reasons: ['ONLY_FIVE_RECENT_SESSIONS'],
    minute_dataset: {
      provider: 'Sina public web historical endpoint', data_version: 'sina-minute-v2', bar_interval: '1m', checksum: 'minute-checksum', evidence_sha256: 'e'.repeat(64),
      bar_count: 115430, symbol_count: 97, etf_count: 22, stock_count: 75, sector_count: 15,
      session_count: 5, session_bar_count_per_symbol: 238,
      trading_days: ['2026-08-19', '2026-08-20', '2026-08-21', '2026-08-24', '2026-08-25'],
      observed_start: '2026-08-19T09:31:00+08:00', observed_end: '2026-08-25T15:00:00+08:00',
      eligible_symbols: ['510300'],
      instruments: [{ symbol: '510300', instrument_type: 'ETF', name: '沪深300ETF', sector_code: null, sector_name: null }],
      point_in_time: false, realtime_readiness: false, real_data_paper_readiness: false, external_order_transmission: false,
    },
    selected: {
      policy_id: 'drawdown-tight-v1', policy_hash: 'a'.repeat(64), policy: {}, cost_bps: 5,
      test_base: { total_return: -0.0100176, maximum_drawdown: -0.0172556, realized_minute_volatility: 0.00046, point_count: 476 },
      test_overlay: { total_return: -0.0031825, maximum_drawdown: -0.0039331, realized_minute_volatility: 0.00011, point_count: 476 },
      test_excess_return: 0.0068351, mean_turnover: 12.046, mean_transaction_cost: 0.00599, reason_counts: {},
    },
    split: { train_days: ['2026-08-19', '2026-08-20', '2026-08-21'], test_days: ['2026-08-24', '2026-08-25'], candidate_count: 7 },
    causal_contract: { signal_observed_at: 'BAR_CLOSE_T', exposure_change_at: 'BAR_OPEN_T_PLUS_1', same_bar_signal_execution: false, maximum_exposure: 1, external_order_transmission: false },
    candidates: [],
    subgroups: [
      { group: 'ETF', symbol_count: 22, base: { total_return: -0.00869, maximum_drawdown: -0.01, realized_minute_volatility: 0, point_count: 476 }, overlay: { total_return: -0.00505, maximum_drawdown: -0.00632, realized_minute_volatility: 0, point_count: 476 } },
      { group: 'STOCK', symbol_count: 75, base: { total_return: -0.0104, maximum_drawdown: -0.02, realized_minute_volatility: 0, point_count: 476 }, overlay: { total_return: -0.00264, maximum_drawdown: -0.00338, realized_minute_volatility: 0, point_count: 476 } },
    ],
    leave_one_sector_out: [
      { excluded_sector_code: '801030', excluded_sector_name: '基础化工', excluded_symbols: ['600309', '000792', '000408', '600160', '600989'], excluded_symbol_count: 5, retained_symbol_count: 92, overlay: { total_return: -0.00337, maximum_drawdown: -0.00395, realized_minute_volatility: 0, point_count: 476 }, return_delta_vs_all: -0.00019 },
    ],
    cost_stress: [
      { cost_bps: 0, base: { total_return: -0.007587, maximum_drawdown: -0.015, realized_minute_volatility: 0, point_count: 476 }, overlay: { total_return: -0.000814, maximum_drawdown: -0.0031, realized_minute_volatility: 0, point_count: 476 }, mean_turnover: 12, mean_transaction_cost: 0 },
      { cost_bps: 20, base: { total_return: -0.007587, maximum_drawdown: -0.015, realized_minute_volatility: 0, point_count: 476 }, overlay: { total_return: -0.011464, maximum_drawdown: -0.012, realized_minute_volatility: 0, point_count: 476 }, mean_turnover: 12, mean_transaction_cost: 0.02 },
    ],
    daily: [
      { trading_day: '2026-08-19', partition: 'TRAIN', base: { total_return: -0.01, maximum_drawdown: -0.01, realized_minute_volatility: 0, point_count: 238 }, overlay: { total_return: -0.001, maximum_drawdown: -0.002, realized_minute_volatility: 0, point_count: 238 } },
      { trading_day: '2026-08-24', partition: 'TEST', base: { total_return: -0.009, maximum_drawdown: -0.01, realized_minute_volatility: 0, point_count: 238 }, overlay: { total_return: -0.0015, maximum_drawdown: -0.003, realized_minute_volatility: 0, point_count: 238 } },
    ],
    curve: [{ timestamp: '2026-08-24T09:31:00+08:00', base_equity: 1, overlay_equity: 1 }],
  },
  fiveMinuteResearch: {
    promotion: 'NOT_ELIGIBLE',
    promotion_reasons: ['FIVE_MINUTE_INTERVAL_DOES_NOT_PROVE_ONE_MINUTE_READINESS'],
    minute_dataset: {
      provider: 'Sina public web historical endpoint', data_version: 'sina-five-minute-v1', bar_interval: '5m', checksum: 'five-minute-checksum', evidence_sha256: 'f'.repeat(64),
      bar_count: 193920, symbol_count: 101, etf_count: 26, stock_count: 75, sector_count: 15,
      session_count: 40, session_bar_count_per_symbol: 48,
      trading_days: ['2026-07-01', '2026-08-25'], observed_start: '2026-07-01T09:35:00+08:00', observed_end: '2026-08-25T15:00:00+08:00',
      eligible_symbols: ['601166'], instruments: [{ symbol: '601166', instrument_type: 'STOCK', name: '兴业银行', sector_code: '801780', sector_name: '银行' }],
      point_in_time: false, realtime_readiness: false, real_data_paper_readiness: false, external_order_transmission: false,
    },
    causal_contract: { signal_observed_at: 'FIVE_MINUTE_BAR_CLOSE_T', exposure_change_at: 'NEXT_FIVE_MINUTE_BAR_OPEN', same_bar_signal_execution: false, candidate_state: 'ALL_CANDIDATES_CAUSALLY_SHADOW_WARMED', maximum_exposure: 1, external_order_transmission: false },
    walk_forward: { initial_train_sessions: 16, test_sessions_per_fold: 6, fold_count: 4, candidate_count: 7, test_windows_disjoint: true, selection_objective: 'frozen', selected_policy_sequence: ['five-drawdown-tight-v1', 'five-reduction-soft-v1', 'five-reduction-soft-v1', 'five-reduction-soft-v1'] },
    aggregate: {
      cost_bps: 5,
      base: { total_return: -0.0192485, maximum_drawdown: -0.0445126, realized_minute_volatility: 0.00122, point_count: 1152 },
      overlay: { total_return: -0.0462336, maximum_drawdown: -0.0497758, realized_minute_volatility: 0.00067, point_count: 1152 },
      excess_return: -0.0269851, positive_test_fold_count: 1,
    },
    candidates: [],
    folds: [
      { fold: 1, train_days: ['2026-07-01'], test_days: ['2026-07-23', '2026-07-30'], selected_policy_id: 'five-drawdown-tight-v1', selected_policy_hash: 'a'.repeat(64), test_base: { total_return: -0.0120448, maximum_drawdown: -0.02931, realized_minute_volatility: 0.00146, point_count: 288 }, test_overlay: { total_return: -0.0111291, maximum_drawdown: -0.0155, realized_minute_volatility: 0.00054, point_count: 288 }, test_excess_return: 0.0009157 },
      { fold: 2, train_days: ['2026-07-01'], test_days: ['2026-07-31', '2026-08-07'], selected_policy_id: 'five-reduction-soft-v1', selected_policy_hash: 'b'.repeat(64), test_base: { total_return: 0.0241142, maximum_drawdown: -0.01, realized_minute_volatility: 0.001, point_count: 288 }, test_overlay: { total_return: 0.0004566, maximum_drawdown: -0.01, realized_minute_volatility: 0.0006, point_count: 288 }, test_excess_return: -0.0236576 },
      { fold: 3, train_days: ['2026-07-01'], test_days: ['2026-08-10', '2026-08-17'], selected_policy_id: 'five-reduction-soft-v1', selected_policy_hash: 'b'.repeat(64), test_base: { total_return: 0.0004818, maximum_drawdown: -0.01, realized_minute_volatility: 0.001, point_count: 288 }, test_overlay: { total_return: -0.0052247, maximum_drawdown: -0.01, realized_minute_volatility: 0.0006, point_count: 288 }, test_excess_return: -0.0057065 },
      { fold: 4, train_days: ['2026-07-01'], test_days: ['2026-08-18', '2026-08-25'], selected_policy_id: 'five-reduction-soft-v1', selected_policy_hash: 'b'.repeat(64), test_base: { total_return: -0.0311331, maximum_drawdown: -0.04, realized_minute_volatility: 0.001, point_count: 288 }, test_overlay: { total_return: -0.0308764, maximum_drawdown: -0.04, realized_minute_volatility: 0.0006, point_count: 288 }, test_excess_return: 0.0002567 },
    ],
    cost_stress: [{ cost_bps: 0, base: { total_return: -0.0192485, maximum_drawdown: -0.0445, realized_minute_volatility: 0.0012, point_count: 1152 }, overlay: { total_return: -0.0198981, maximum_drawdown: -0.0332, realized_minute_volatility: 0.00067, point_count: 1152 }, excess_return: -0.0006495 }],
    subgroups: [{ group: 'ETF', symbol_count: 26, base: { total_return: 0.0017161, maximum_drawdown: -0.0416, realized_minute_volatility: 0.00127, point_count: 1152 }, overlay: { total_return: -0.0400335, maximum_drawdown: -0.0515, realized_minute_volatility: 0.00079, point_count: 1152 } }],
    leave_one_sector_out: [], daily: [],
    curve: [{ timestamp: '2026-07-23T09:35:00+08:00', base_equity: 1, overlay_equity: 1 }],
  },
  longFiveMinuteResearch: {
    promotion: 'NOT_ELIGIBLE',
    promotion_reasons: ['QFQ_MAY_USE_FUTURE_ADJUSTMENT_KNOWLEDGE'],
    minute_dataset: {
      provider: 'BaoStock', provider_version: '0.9.3', data_version: 'baostock-five-minute-long-v1', bar_interval: '5m', checksum: 'long-five-minute-checksum', evidence_sha256: '1'.repeat(64),
      bar_count: 1324800, symbol_count: 75, etf_count: 0, stock_count: 75, sector_count: 15,
      session_count: 368, session_bar_count_per_symbol: 48,
      trading_days: ['2025-01-02', '2026-08-25'], observed_start: '2025-01-02T09:35:00+08:00', observed_end: '2026-08-25T15:00:00+08:00',
      eligible_symbols: ['600036'], instruments: [{ symbol: '600036', instrument_type: 'STOCK', name: '招商银行', sector_code: '801780', sector_name: '银行' }],
      point_in_time: false, future_adjustment_knowledge_possible: true, realtime_readiness: false, real_data_paper_readiness: false, external_order_transmission: false,
    },
    causal_contract: { signal_observed_at: 'FIVE_MINUTE_BAR_CLOSE_T', exposure_change_at: 'NEXT_FIVE_MINUTE_BAR_OPEN', same_bar_signal_execution: false, candidate_state: 'ALL_CANDIDATES_CAUSALLY_SHADOW_WARMED', maximum_exposure: 1, external_order_transmission: false },
    walk_forward: { initial_train_sessions: 120, test_sessions_per_fold: 40, fold_count: 6, candidate_count: 7, test_windows_disjoint: true, selection_objective: 'frozen', selected_policy_sequence: Array(6).fill('five-reduction-soft-v1'), used_session_count: 360, unused_trailing_sessions: 8, processed_session_count: 360, processed_bar_count: 1296000, trailing_sessions_entered_backtest: false },
    aggregate: {
      cost_bps: 5,
      base: { total_return: 0.4082847, maximum_drawdown: -0.1510855, realized_minute_volatility: 0.00223, point_count: 11520 },
      overlay: { total_return: 0.010438, maximum_drawdown: -0.1218057, realized_minute_volatility: 0.00097, point_count: 11520 },
      excess_return: -0.3978466, positive_test_fold_count: 2, execution: longExecutionFixture,
    },
    candidates: [],
    folds: Array.from({ length: 6 }, (_, index) => ({
      fold: index + 1, train_days: ['2025-01-02'], test_days: [`2025-0${index + 2}-01`, `2025-0${index + 2}-28`], selected_policy_id: 'five-reduction-soft-v1', selected_policy_hash: '2'.repeat(64),
      test_base: { total_return: 0.05, maximum_drawdown: -0.05, realized_minute_volatility: 0.001, point_count: 1920 },
      test_overlay: { total_return: 0.01, maximum_drawdown: -0.03, realized_minute_volatility: 0.0005, point_count: 1920 }, test_excess_return: -0.04, execution: foldExecutionFixture,
    })),
    cost_stress: [{ cost_bps: 0, base: { total_return: 0.4082847, maximum_drawdown: -0.151, realized_minute_volatility: 0.0022, point_count: 11520 }, overlay: { total_return: 0.3073387, maximum_drawdown: -0.12, realized_minute_volatility: 0.001, point_count: 11520 }, excess_return: -0.100946 }],
    subgroups: [{ group: 'STOCK', symbol_count: 75, base: { total_return: 0.4082847, maximum_drawdown: -0.151, realized_minute_volatility: 0.0022, point_count: 11520 }, overlay: { total_return: 0.010438, maximum_drawdown: -0.1218, realized_minute_volatility: 0.001, point_count: 11520 } }],
    leave_one_sector_out: [], daily: [],
    curve: [{ timestamp: '2025-07-01T09:35:00+08:00', base_equity: 1, overlay_equity: 1 }],
  },
  reservedTailDiagnostic: {
    classification: 'PREREGISTERED_PARTIALLY_OBSERVED_RESERVED_TAIL_DIAGNOSTIC',
    promotion: 'NOT_ELIGIBLE',
    promotion_reasons: ['ONLY_EIGHT_RESERVED_SESSIONS', 'TAIL_PRICES_PARTIALLY_OBSERVED_DURING_FRONTEND_QA'],
    minute_dataset: null,
    candidate: { policy_id: 'five-conservative-high-exposure-v1', policy_hash: '3'.repeat(64) },
    comparator: { policy_id: 'five-reduction-soft-v1', policy_hash: '4'.repeat(64) },
    warmup_session_count: 360,
    test_session_count: 8,
    test_days: ['2026-08-14', '2026-08-17', '2026-08-18', '2026-08-19', '2026-08-20', '2026-08-21', '2026-08-24', '2026-08-25'],
    all_required_gates_passed: false,
    sector_win_count: 6,
    five_bps: {
      cost_bps: 5,
      base: { total_return: -0.0309598, maximum_drawdown: -0.0676873, realized_minute_volatility: 0.0022, point_count: 384 },
      candidate: { total_return: -0.0306553, maximum_drawdown: -0.0503371, realized_minute_volatility: 0.00151, point_count: 384 },
      comparator: { total_return: -0.0301575, maximum_drawdown: -0.0391873, realized_minute_volatility: 0.00091, point_count: 384 },
      candidate_execution: { ...longExecutionFixture, observation_count: 28800, mean_exposure: 0.8250625, reduced_exposure_ratio: 0.6896875, mean_turnover_per_symbol: 3.0826667, mean_transaction_cost_per_symbol: 0.002958 },
      comparator_execution: { ...longExecutionFixture, observation_count: 28800, mean_exposure: 0.6436458, reduced_exposure_ratio: 0.7946875, mean_turnover_per_symbol: 18.04, mean_transaction_cost_per_symbol: 0.0089825 },
    },
    gates: [
      { gate_id: 'CANDIDATE_BEATS_COMPARATOR_AT_5_BPS', passed: false },
      { gate_id: 'CANDIDATE_DRAWDOWN_NOT_WORSE_THAN_BASELINE_AT_5_BPS', passed: true },
      { gate_id: 'MEAN_EXPOSURE_AT_LEAST_85_PERCENT', passed: false },
      { gate_id: 'TURNOVER_AT_MOST_HALF_COMPARATOR', passed: true },
      { gate_id: 'POSITIVE_AT_ZERO_AND_FIVE_BPS', passed: false },
      { gate_id: 'LEAVE_ONE_SECTOR_OUT_MAJORITY', passed: false },
    ],
    cost_stress: [],
    daily: [{
      trading_day: '2026-08-14',
      base: { total_return: 0.00632, maximum_drawdown: -0.00757, realized_minute_volatility: 0.00156, point_count: 48 },
      candidate: { total_return: -0.00018, maximum_drawdown: -0.00556, realized_minute_volatility: 0.00083, point_count: 48 },
      comparator: { total_return: -0.00308, maximum_drawdown: -0.00610, realized_minute_volatility: 0.00067, point_count: 48 },
    }],
    leave_one_sector_out: Array.from({ length: 15 }, (_, index) => ({
      excluded_sector_code: String(801000 + index), excluded_sector_name: `行业${index + 1}`,
      excluded_symbol_count: 5, retained_symbol_count: 70,
      candidate: { total_return: -0.03, maximum_drawdown: -0.05, realized_minute_volatility: 0.0015, point_count: 384 },
      comparator: { total_return: -0.029, maximum_drawdown: -0.04, realized_minute_volatility: 0.0009, point_count: 384 },
      candidate_beats_comparator: index < 6,
    })),
    curve: [{ timestamp: '2026-08-14T09:35:00+08:00', base_equity: 1, candidate_equity: 1, comparator_equity: 1 }],
    external_order_transmission: false,
  },
  developmentPolicySearch: {
    classification: 'RETROSPECTIVE_DEVELOPMENT_ONLY_POLICY_SEARCH_NOT_INDEPENDENT',
    generated_at: '2026-08-26T09:01:25+08:00', candidate_count: 4,
    development_contract: { session_count: 360, observed_start: '2025-01-02', observed_end: '2026-08-13', processed_bar_count: 1296000, cost_bps: 5 },
    reserved_tail: { session_count: 8, trading_days: [], entered_strategy_engine: false, entered_candidate_metrics: false },
    candidates: Array.from({ length: 4 }, (_, index) => ({
      policy_id: index === 3 ? 'dev-floor-098-slow12x48-v1' : `development-candidate-${index + 1}`,
      policy_hash: String(index + 1).repeat(64), policy: {},
      base: { total_return: 0.6210147, maximum_drawdown: -0.1510855 },
      overlay: { total_return: index === 3 ? 0.5989259 : 0.55, maximum_drawdown: index === 3 ? -0.1241326 : -0.12 },
      return_delta_vs_base: index === 3 ? -0.0220888 : -0.0710147,
      drawdown_improvement: index === 3 ? 0.0269529 : 0.0310855,
      execution: { mean_exposure: index === 3 ? 0.9132435 : 0.9, mean_turnover_per_symbol: index === 3 ? 50.5237 : 70 },
    })),
    observed_leader: { policy_id: 'dev-floor-098-slow12x48-v1', selection_timing: 'POST_HOC_DEVELOPMENT_ONLY' },
    future_observation_lock: { locked_at: '2026-08-26T09:01:25+08:00', policy_id: 'dev-floor-098-slow12x48-v1', first_eligible_trading_day: '2026-08-26', minimum_complete_future_sessions: 20, partial_same_day_observations_enter_metrics: false, parameter_changes_before_completion: false },
    promotion: 'NOT_ELIGIBLE', external_order_transmission: false,
  },
  forwardObservation: {
    classification: 'LOCKED_FORWARD_OBSERVATION_PREREGISTRATION',
    preregistration_id: 'baostock-locked-forward-observation-v1',
    preregistration_sha256: '9'.repeat(64),
    created_at: '2026-08-26T11:22:38+08:00',
    status: 'LOCKED_AFTER_FIRST_SESSION_OPEN_BEFORE_TARGET_SESSION_FETCH',
    timing_disclosure: { development_policy_locked_before_first_session_open: true, evaluation_contract_fixed_after_first_session_open: true, target_session_data_fetched_before_this_lock: false, claim_as_blind_preregistration: false },
    policy_id: 'dev-floor-098-slow12x48-v1', policy_hash: '4'.repeat(64),
    parameters: { short_window: '12', long_window: '48' },
    first_eligible_trading_day: '2026-08-26', future_session_count: 0,
    minimum_complete_future_sessions: 20, required_symbol_count: 75,
    required_sector_count: 15, required_bars_per_symbol_session: 48,
    observation_status: 'INCOMPLETE', all_required_gates_passed: false,
    gates: [
      { gate_id: 'OVERLAY_DRAWDOWN_NOT_WORSE_AT_5_BPS', passed: null },
      { gate_id: 'RETURN_SHORTFALL_WITHIN_50_BPS_AT_5_BPS', passed: null },
      { gate_id: 'MEAN_EXPOSURE_AT_LEAST_90_PERCENT', passed: null },
      { gate_id: 'MEAN_TURNOVER_PER_SYMBOL_AT_MOST_FIVE', passed: null },
      { gate_id: 'LEAVE_ONE_SECTOR_OUT_STABILITY', passed: null },
    ],
    cost_stress: [], leave_one_sector_out: [], promotion: 'NOT_ELIGIBLE',
    promotion_reasons: ['FUTURE_TWENTY_SESSION_OBSERVATION_INCOMPLETE'],
    realtime_readiness: false, real_data_paper_readiness: false,
    external_order_transmission: false,
  },
  publicMinuteAudit: {
    classification: 'PUBLIC_READ_ONLY_MINUTE_BAR_AVAILABILITY_AUDIT',
    generated_at: '2026-08-26T07:57:24+08:00',
    base_url: 'https://qtrade.erapi0neer.xyz',
    data_version: 'baostock-five-minute-long-v1',
    dataset_checksum: 'long-five-minute-checksum',
    sector_count: 15,
    sampled_trading_days: ['2025-01-02', '2025-11-10', '2026-08-25'],
    first_round: { request_count: 45, all_valid: true, latency_seconds: { minimum: 1.676, median: 4.669, p95: 6.248, maximum: 6.417 }, vercel_cache_counts: { MISS: 45 }, failures: [] },
    repeat_round: { request_count: 45, all_valid: true, latency_seconds: { minimum: 0.156, median: 0.179, p95: 0.274, maximum: 0.320 }, vercel_cache_counts: { HIT: 45 }, failures: [] },
    public_write_boundary: { request_count: 7, all_blocked: true, checks: [
      { path: '/system/start', status: 503, blocked: true },
      { path: '/system/stop', status: 503, blocked: true },
      { path: '/system/pause', status: 503, blocked: true },
      { path: '/system/resume', status: 503, blocked: true },
      { path: '/system/kill', status: 503, blocked: true },
      { path: '/system/recover', status: 503, blocked: true },
      { path: '/orders/cancel', status: 503, blocked: true },
    ] },
    all_valid: true,
    promotion: 'NOT_ELIGIBLE',
    external_order_transmission: false,
  },
  intradayObservation: {
    classification: 'FREE_HISTORICAL_SOURCE_INTRADAY_OBSERVATION_NOT_REALTIME_ELIGIBLE',
    provider: 'Sina CN_MarketDataService.getKLineData', bar_interval: '1m',
    started_at: '2026-08-26T01:32:00+00:00', completed_at: '2026-08-26T01:32:02+00:00',
    session_phase: 'MORNING_SESSION', data_version: 'baostock-five-minute-long-v1',
    dataset_checksum: 'long-five-minute-checksum', evidence_sha256: '5'.repeat(64),
    symbol_count: 15, sector_count: 15, same_day_symbol_count: 15,
    synchronized_finalized_timestamp_count: 1,
    latest_synchronized_finalized_timestamp: '2026-08-26T09:32:00+08:00',
    finalization_guard_seconds: 60,
    observations: Array.from({ length: 15 }, (_, index) => ({
      symbol: index === 0 ? '600036' : String(600100 + index), name: `代表股${index + 1}`,
      sector_code: String(801000 + index), sector_name: `行业${index + 1}`,
      observed_at: '2026-08-26T01:32:02+00:00', session_phase: 'MORNING_SESSION',
      source_row_count: 239, latest_observed_timestamp: '2026-08-26T09:32:00+08:00',
      latest_finalized_timestamp: '2026-08-26T09:31:00+08:00', same_day_finalized_bar_count: 1,
      forming_or_uncertain_bar_excluded: true,
      bars: [{ symbol: index === 0 ? '600036' : String(600100 + index), exchange: 'SSE', timestamp: '2026-08-26T09:31:00+08:00', trading_day: '2026-08-26', open: '40', high: '40.1', low: '39.9', close: '40.05', volume: '100' }],
    })),
    realtime_readiness: false, real_data_paper_readiness: false,
    promotion: 'NOT_ELIGIBLE', external_order_transmission: false,
  },
  intradayBreadth: {
    classification: 'FREE_HISTORICAL_SOURCE_INTRADAY_BREADTH_NOT_REALTIME_ELIGIBLE',
    coverage_scope: 'ALL_FROZEN_STOCKS_15_SECTORS_X5',
    provider: 'Sina CN_MarketDataService.getKLineData', bar_interval: '1m',
    started_at: '2026-08-26T01:32:00+00:00', completed_at: '2026-08-26T01:32:22+00:00',
    session_phase: 'MORNING_SESSION', data_version: 'baostock-five-minute-long-v1',
    dataset_checksum: 'long-five-minute-checksum', evidence_sha256: '6'.repeat(64),
    symbol_count: 75, sector_count: 15, symbols_per_sector: 5,
    same_day_symbol_count: 75, synchronized_finalized_timestamp_count: 2,
    latest_synchronized_finalized_timestamp: '2026-08-26T09:32:00+08:00',
    source_request_window_seconds: 22, all_symbols_same_day_coverage: true,
    maximum_same_day_latest_age_seconds: 82, finalization_guard_seconds: 60,
    directional_breadth: {
      eligible_symbol_count: 75, above_open_count: 45,
      unchanged_from_open_count: 5, below_open_count: 25,
      latest_minute_eligible_symbol_count: 75, latest_minute_up_count: 40,
      latest_minute_unchanged_count: 10, latest_minute_down_count: 25,
      equal_weight_session_return: '0.0012',
      equal_weight_latest_minute_return: '0.0003',
    },
    observations: Array.from({ length: 75 }, (_, index) => ({
      symbol: String(600100 + index), name: `股票${index + 1}`,
      sector_code: String(801000 + Math.floor(index / 5)),
      sector_name: `行业${Math.floor(index / 5) + 1}`,
      observed_at: '2026-08-26T01:32:22+00:00', session_phase: 'MORNING_SESSION',
      source_row_count: 476, latest_observed_timestamp: '2026-08-26T09:32:00+08:00',
      latest_finalized_timestamp: '2026-08-26T09:32:00+08:00',
      same_day_finalized_bar_count: 2,
      latest_same_day_finalized_timestamp: '2026-08-26T09:32:00+08:00',
      latest_same_day_finalized_age_seconds: 82,
      forming_or_uncertain_bar_excluded: true,
      first_same_day_open: '10', previous_same_day_close: '10',
      latest_same_day_close: '10.01', session_return: '0.001',
      latest_minute_return: '0.001',
    })),
    sector_summaries: Array.from({ length: 15 }, (_, index) => ({
      sector_code: String(801000 + index), sector_name: `行业${index + 1}`,
      symbol_count: 5, same_day_symbol_count: 5,
      synchronized_finalized_timestamp_count: 2,
      latest_synchronized_finalized_timestamp: '2026-08-26T09:32:00+08:00',
      maximum_same_day_latest_age_seconds: 82,
      directional_breadth: {
        eligible_symbol_count: 5, above_open_count: 3,
        unchanged_from_open_count: 0, below_open_count: 2,
        latest_minute_eligible_symbol_count: 5, latest_minute_up_count: 3,
        latest_minute_unchanged_count: 0, latest_minute_down_count: 2,
        equal_weight_session_return: '0.001',
        equal_weight_latest_minute_return: '0.001',
      },
    })),
    realtime_readiness: false, real_data_paper_readiness: false,
    promotion: 'NOT_ELIGIBLE', external_order_transmission: false,
  },
  intradayContinuity: {
    classification: 'SIGNED_INTRADAY_SOURCE_CONTINUITY_NOT_REALTIME_ELIGIBLE',
    trading_day: '2026-08-26', checkpoint_count: 1,
    all_checkpoints_monotonic: true, evidence_sha256: '7'.repeat(64),
    checkpoints: [{
      sequence: 1, captured_at: '2026-08-26T01:32:22+00:00', trading_day: '2026-08-26',
      detail: {
        evidence_sha256: '5'.repeat(64), completed_at: '2026-08-26T01:32:02+00:00',
        session_phase: 'MORNING_SESSION', symbol_count: 15, sector_count: 15,
        same_day_symbol_count: 15, synchronized_finalized_timestamp_count: 1,
        latest_synchronized_finalized_timestamp: '2026-08-26T09:31:00+08:00',
        source_request_window_seconds: 2, maximum_same_day_latest_age_seconds: 62,
        all_symbols_same_day_coverage: true,
      },
      breadth: {
        evidence_sha256: '6'.repeat(64), completed_at: '2026-08-26T01:32:22+00:00',
        session_phase: 'MORNING_SESSION', symbol_count: 75, sector_count: 15,
        same_day_symbol_count: 75, synchronized_finalized_timestamp_count: 1,
        latest_synchronized_finalized_timestamp: '2026-08-26T09:31:00+08:00',
        source_request_window_seconds: 22, maximum_same_day_latest_age_seconds: 82,
        all_symbols_same_day_coverage: true,
      },
      monotonic_vs_previous: true, previous_checkpoint_sha256: null,
      checkpoint_sha256: '8'.repeat(64),
    }],
    realtime_readiness: false, real_data_paper_readiness: false,
    promotion: 'NOT_ELIGIBLE', external_order_transmission: false,
  },
  reports: [{ report_id: 'paper-20260812', trading_day: '2026-08-12', generated_at: '2026-08-12T16:00:00+08:00', data_version: 'ticks-v1', strategy_versions: ['trend@1.0.0'], sha256: 'a'.repeat(64) }],
  reporting: {
    classification: 'TRUSTED_PAPER_EOD_REPORTING', readiness: 'CONFIGURED', automatic: true,
    state: 'GENERATED', venue: 'SSE', timezone: 'Asia/Shanghai', cutoff: '15:30',
    data_version: 'ticks-v1', strategy_versions: ['trend@1.0.0'],
    last_attempt_day: '2026-08-12', last_generated_day: '2026-08-12',
    last_report_id: 'paper-20260812', last_sha256: 'a'.repeat(64), error_code: null,
    caller_supplied_identity: false, external_order_transmission: false,
  },
  historicalBars: [
    {
      symbol: '510300', exchange: 'SSE', timestamp: '2026-08-12T07:00:00+00:00',
      trading_day: '2026-08-12', open: '4.1', high: '4.3', low: '4.0', close: '4.2',
      volume: '200000000', data_version: 'akshare-bars-v1',
      observation_type: 'HISTORICAL_DAILY_CLOSE',
    },
  ],
  robustness: {
    classification: 'post_rejection_diagnostic_reuses_observations_not_new_heldout',
    promotion: 'NOT_ELIGIBLE',
    promotion_reasons: ['NEGATIVE_HELD_OUT_WALK_FORWARD'],
    result: {
      worst_sharpe: '-1.1429',
      dispersion: '1.6704',
      any_negative_sharpe: true,
      scenario_metrics: [['base', [['sharpe', '-0.0964']]]],
    },
  },
  factorSearch: {
    classification: 'bounded_factor_search_current_universe_non_pit',
    promotion: 'NOT_ELIGIBLE',
    promotion_reasons: ['CURRENT_UNIVERSE_SURVIVORSHIP_BIAS'],
    factor_selection: {
      passing_factor_ids: ['momentum-252-skip-20', 'reversal-5', 'low_volatility-60'],
      discovery_start: '2023-10-01', discovery_end: '2024-12-31',
    },
    portfolio_selection: {
      selected_candidate_id: 'reversal-20-top3-20', validation_start: '2025-01-01',
      validation_end: '2025-12-31', benchmark_excluded_from_active_selection: true,
    },
    confirmatory_test: { start: '2026-01-01', end: '2026-08-21', period_previously_viewed_by_project: true },
    factor_diagnostics: [],
    validation: { selected_candidate_id: 'reversal-20-top3-20', candidate_count: 13, scores: [['reversal-20-top3-20', '0.7299']] },
    test: {
      selected_candidate_id: 'reversal-20-top3-20',
      selected_metrics: { total_return: '-0.123894', sharpe: '-0.5513', maximum_drawdown: '-0.2418' },
      buy_hold_metrics: { total_return: '-0.046176', sharpe: '-0.3055', maximum_drawdown: '-0.1490' },
      terminal_excess_return: '-0.077718',
    },
  },
  robustFactorSearch: {
    classification: 'post_selection_robust_factor_search_current_universe_non_pit',
    promotion: 'NOT_ELIGIBLE',
    promotion_reasons: ['POST_SELECTION_REUSE_NOT_NEW_HELD_OUT_EVIDENCE'],
    selection: {
      candidate_count: 36,
      window_ids: ['2024Q1', '2024Q2', '2024Q3', '2024Q4', '2025Q1', '2025Q2', '2025Q3', '2025Q4'],
      best_observed_candidate_id: 'low-volatility-60-top3-40',
      window_gate_eligible_candidate_ids: ['reversal-10-top3-20'],
      robust_candidate_id: 'reversal-10-top3-20',
      audited_candidate_id: 'reversal-10-top3-20',
    },
    robustness_gate: {
      passed: true,
      conclusion: 'ROBUST_CANDIDATE_PASSED_PREDECLARED_GATE',
      candidate_results: [],
    },
    candidate_summaries: [{
      candidate_id: 'reversal-10-top3-20', selection_rank: 5,
      positive_window_ratio: '0.75', median_window_sharpe: '0.6362',
      worst_window_sharpe: '-0.9007', full_period_return: '0.3884',
    }],
    stress_tests: {
      evaluations: [{
        candidate_id: 'reversal-10-top3-20',
        cost_stresses: [{ scenario_id: 'double_cost', metrics: { total_return: '0.309869' } }],
        parameter_neighbors: { positive_ratio: '0.888888' },
        universe_subsets: { positive_ratio: '1' },
      }],
    },
    bootstrap: {
      evaluations: [{
        candidate_id: 'reversal-10-top3-20', probability_positive: '0.853',
        p05_total_return: '-0.1376', median_total_return: '0.3362', p95_total_return: '1.3802',
      }],
    },
    polluted_audit: {
      classification: 'POLLUTED_AUDIT_ONLY',
      audited_candidate_id: 'reversal-10-top3-20',
      selected_metrics: { total_return: '-0.004437', maximum_drawdown: '-0.178406' },
      buy_hold_metrics: { total_return: '-0.046176' },
      terminal_excess_return: '0.041739',
    },
  },
  stockSectorTransfer: {
    classification: 'locked_etf_factor_stock_sector_transfer_non_pit',
    promotion: 'NOT_ELIGIBLE',
    promotion_reasons: ['CURRENT_2026_CONSTITUENTS_PROJECTED_BACKWARD'],
    data: { bar_count: 149903, symbol_count: 75, sector_count: 15, stocks_per_sector: 5, adjustment: 'hfq', point_in_time_universe: false },
    locked_strategy: { candidate_id: 'reversal-10-top3-20', parameter_tuning_on_stock_results: false },
    formal_period: {
      start: '2019-01-01', end: '2025-12-31',
      full_metrics: { total_return: '3.1575', annualized_return: '0.2353', sharpe: '0.8096', maximum_drawdown: '-0.3420' },
      positive_year_count: 7, positive_sector_only_count: 15, positive_leave_one_sector_out_count: 15,
      doubled_cost_metrics: { total_return: '2.5614' },
    },
    transfer_gate: { passed: true, criteria: {} },
    yearly: [],
    sector_only: [{ sector_code: '801120', sector_name: '食品饮料', symbols: ['a', 'b', 'c', 'd', 'e'], metrics: { total_return: '0.4628' } }],
    leave_one_sector_out: [],
    cost_stress: {},
    bootstrap: { probability_positive: '0.969', p05_total_return: '0.201' },
    polluted_audit: { strategy_metrics: { total_return: '0.0190' }, equal_weight_metrics: { total_return: '-0.0095' }, terminal_excess_return: '0.0285' },
  },
  walkForward: {
    classification: 'held_out_walk_forward',
    promotion: 'NOT_ELIGIBLE',
    promotion_reasons: ['NEGATIVE_HELD_OUT_WALK_FORWARD'],
    selection_contract: {
      candidate_selection: 'validation_sharpe_only',
      candidate_test_access_before_selection: false,
      fold_ranges_disjoint: true,
      execution_timing: 'close_decision_next_observed_open',
    },
    result: {
      fold_pass_ratio: '0',
      aggregate_metrics: { total_return: '-0.2044', sharpe: '-1.1005', maximum_drawdown: '-0.2862' },
      folds: [
        { fold: 1, selected_candidate_id: 'buy-hold', validation_sharpe: '2.78', split: { train_start: '2024-01-02', train_end: '2024-06-28', validation_start: '2024-07-01', validation_end: '2024-09-30', test_start: '2024-10-01', test_end: '2024-12-31' }, test_metrics: { total_return: '-0.0210', sharpe: '-0.1518', maximum_drawdown: '-0.1152', number_of_trades: 0 } },
        { fold: 2, selected_candidate_id: 'buy-hold', validation_sharpe: '3.47', split: { train_start: '2025-01-01', train_end: '2025-06-30', validation_start: '2025-07-01', validation_end: '2025-09-30', test_start: '2025-10-01', test_end: '2025-12-31' }, test_metrics: { total_return: '-0.0110', sharpe: '-0.2406', maximum_drawdown: '-0.0692', number_of_trades: 0 } },
        { fold: 3, selected_candidate_id: 'momentum-trend', validation_sharpe: '1.80', split: { train_start: '2026-01-01', train_end: '2026-03-31', validation_start: '2026-04-01', validation_end: '2026-05-29', test_start: '2026-06-01', test_end: '2026-08-12' }, test_metrics: { total_return: '-0.1783', sharpe: '-2.6533', maximum_drawdown: '-0.2173', number_of_trades: 14 } },
      ],
    },
  },
  backtestAnalytics: {
    classification: 'exploratory_noncommercial_current_universe_not_pit',
    promotion: 'NOT_ELIGIBLE',
    leader: {
      experiment_id: 'momentum-vol-slow', spec_hash: 'a', result_hash: 'b', scenario_type: 'SENSITIVITY',
      metrics: { total_return: '0.0335', sharpe: '0.4412', maximum_drawdown: '-0.0909', test_exposure: '0.45', test_turnover: '2.01', test_transaction_cost: '0.003' },
    },
    benchmark: {
      experiment_id: 'buy-hold', spec_hash: 'c', result_hash: 'd', scenario_type: 'PRIMARY',
      metrics: { total_return: '-0.0289', sharpe: '-0.1559', maximum_drawdown: '-0.1499' },
    },
    split: { test_start: '2026-01-01', test_end: '2026-08-12' },
    curve: [
      { session: '2024-01-02', equity: '1000000', drawdown: '0', benchmark_equity: '1000000', benchmark_drawdown: '0', excess_return: '0' },
      { session: '2024-01-03', equity: '999000', drawdown: '-0.001', benchmark_equity: '998000', benchmark_drawdown: '-0.002', excess_return: '0.001' },
    ],
    monthly_returns: [{ month: '2024-01', return: '-0.001' }],
    trades: [{ symbol: '510300', side: 'BUY', trading_day: '2024-01-03', quantity: 100, price: '4.1', cost: '1.0', notional: '410' }],
    positions: [{ symbol: '510300', quantity: 100 }],
    risk_summary: { maximum_drawdown: '-0.1', transaction_costs: '100', turnover: '2' },
    run_metadata: { engine: 'DailyMultiAssetBacktester', timing: 'close decision, next open execution', session_count: 632 },
  },
  availability: { failures: {} },
} as unknown as PlatformSnapshot

describe('durable monitoring', () => {
  it('shows persisted audit alerts without claiming hash-chain integrity', () => {
    render(<MonitorPage data={data} />)
    expect(screen.getByText('PAPER_INCIDENT')).toBeInTheDocument()
    expect(screen.getByText('gateway disconnected')).toBeInTheDocument()
    expect(screen.getByText(/不是哈希链或 WORM/)).toBeInTheDocument()
    expect(screen.getByText('分钟修正：NOT_READY')).toBeInTheDocument()
    expect(screen.getByText('只减仓，不加仓')).toBeInTheDocument()
    expect(screen.getByText('封闭分钟线后的下一 Tick')).toBeInTheDocument()
    expect(screen.getByText('禁止')).toBeInTheDocument()
    expect(screen.getByText('1.25')).toBeInTheDocument()
    expect(screen.getByText('3')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: 'Prometheus 指标' })).toHaveAttribute('href', '/metrics/prometheus')
    expect(screen.getByRole('button', { name: /导出当前审计视图/ })).toBeInTheDocument()
  })

  it('filters durable audit events without changing the source snapshot', () => {
    render(<MonitorPage data={data} />)
    fireEvent.change(screen.getByLabelText('搜索审计事件'), { target: { value: 'drawdown' } })

    expect(screen.getByText('RISK_LIMIT')).toBeInTheDocument()
    expect(screen.getByText('drawdown threshold reached')).toBeInTheDocument()
    expect(screen.queryByText('gateway disconnected')).not.toBeInTheDocument()
    expect(screen.getByText('显示 1/2')).toBeInTheDocument()
  })

  it('distinguishes unavailable audit history from zero alerts', () => {
    render(<MonitorPage data={{ ...data, audit: null }} />)
    expect(screen.getAllByText('持久化审计历史不可用').length).toBeGreaterThan(0)
    expect(screen.getByText(/不能据此声称当前没有告警/)).toBeInTheDocument()
    expect(screen.getByText('Service Health')).toBeInTheDocument()
    expect(screen.getByText('分钟级时间序列修正')).toBeInTheDocument()
  })

  it('does not report zero alerts before the first platform snapshot', () => {
    render(<MonitorPage data={null} />)
    expect(screen.getAllByText('持久化审计历史不可用').length).toBeGreaterThan(0)
    expect(screen.queryByText('暂无持久化审计事件')).not.toBeInTheDocument()
  })

  it('shows per-symbol causal minute corrections', () => {
    render(<MonitorPage data={{ ...data, minuteOverlay: { ...data.minuteOverlay, symbol_count: 1, ready_symbol_count: 1, readiness: 'READY', symbols: [{ symbol: '510300', ready: true, trading_day: '2026-08-12', last_bar_timestamp: '2026-08-12T10:01:00+08:00', base_target_quantity: 1000, corrected_target_quantity: 600, multiplier: '0.6', reasons: ['WEAK_TREND'], minute_volatility: '0.012', intraday_drawdown: '-0.021', cooldown_bars_remaining: 3, strategy_version: 'minute-overlay-v1' }] } }} />)
    expect(screen.getByText('510300')).toBeInTheDocument()
    expect(screen.getByText('1000')).toBeInTheDocument()
    expect(screen.getByText('600')).toBeInTheDocument()
    expect(screen.getByText('WEAK_TREND')).toBeInTheDocument()
    expect(screen.getByText('2026-08-12T10:01:00+08:00')).toBeInTheDocument()
  })
})

describe('persistent PAPER account pages', () => {
  it('shows persistent PAPER account facts without inventing equity', () => {
    render(<PaperPage data={data} />)
    expect(screen.getByText('98999')).toBeInTheDocument()
    expect(screen.getByText('实时市价不可用')).toBeInTheDocument()
    expect(screen.getByText(/没有实时市价时不计算或展示虚构权益/)).toBeInTheDocument()
  })

  it('keeps runtime controls off the public read-only node', () => {
    cleanup()
    render(<PaperPage data={data} publicNode />)
    expect(screen.getByText('公开节点只读')).toBeInTheDocument()
    expect(screen.queryByLabelText('本地控制令牌')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '安全熔断' })).not.toBeInTheDocument()
  })

  it('sends a local runtime action only with an in-memory control token', async () => {
    cleanup()
    vi.mocked(controlRuntime).mockResolvedValueOnce({ mode: 'PAPER', running: true, risk_state: 'NORMAL' })
    render(<PaperPage data={data} />)
    fireEvent.click(screen.getByRole('button', { name: /启.*动/ }))
    expect(screen.getByText(/请输入本地控制令牌/)).toBeInTheDocument()
    expect(controlRuntime).not.toHaveBeenCalled()
    fireEvent.change(screen.getByLabelText('本地控制令牌'), { target: { value: 'test-control-token' } })
    fireEvent.click(screen.getByRole('button', { name: /启.*动/ }))
    await waitFor(() => expect(controlRuntime).toHaveBeenCalledWith('start', 'test-control-token', 'manual web control'))
    expect(await screen.findByText('START 已执行：NORMAL')).toBeInTheDocument()
  })

  it('shows dated sellability and keeps market valuation unavailable', () => {
    cleanup()
    render(<PositionsPage data={data} />)
    expect(screen.getByText('2026-08-12')).toBeInTheDocument()
    expect(screen.getByText('1001')).toBeInTheDocument()
    expect(screen.getByText(/不会用历史收盘价冒充当前估值/)).toBeInTheDocument()
  })

  it('shows traceable PAPER orders and simulated fills', () => {
    cleanup()
    render(<OrdersPage data={data} />)
    expect(screen.getAllByText('trend@1.0.0').length).toBeGreaterThan(0)
    expect(screen.getByText('PAPER-TRADE-1')).toBeInTheDocument()
    expect(screen.getByText(/不是交易所或券商回报/)).toBeInTheDocument()
  })

  it('cancels an eligible local PAPER order with a page-stable idempotency key', async () => {
    cleanup()
    const openOrder = { ...data.orders[0], status: 'SUBMITTED', filled_quantity: 0 }
    vi.mocked(cancelPaperOrder).mockResolvedValue({ internal_order_id: 'ORDER-1', status: 'CANCELLED' })
    render(<OrdersPage data={{ ...data, orders: [openOrder] }} />)
    fireEvent.change(screen.getByLabelText('本地交易敏感令牌'), { target: { value: 'test-trade-token' } })
    fireEvent.click(screen.getByRole('button', { name: '撤单' }))
    expect(await screen.findByText('确认撤销这笔 PAPER 委托？')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: 'OK' }))
    await waitFor(() => expect(cancelPaperOrder).toHaveBeenCalledWith('ORDER-1', expect.stringMatching(/^web-cancel-ORDER-1-/), 'test-trade-token'))
    expect(await screen.findByText('ORDER-1：CANCELLED')).toBeInTheDocument()
  })

  it('labels recomputable cost-basis exposure and unavailable drawdown', () => {
    cleanup()
    render(<RiskPage data={data} />)
    expect(screen.getByText('成本口径总敞口')).toBeInTheDocument()
    expect(screen.getAllByText('1001')).toHaveLength(2)
    expect(screen.getByText(/不是实时市值风险/)).toBeInTheDocument()
    expect(screen.getByText('市价不可用')).toBeInTheDocument()
  })
})

describe('verified historical data pages', () => {
  it('shows verified history without claiming realtime readiness', async () => {
    render(<MarketPage data={data} />)
    expect(screen.getByText('正在加载已验证历史 K 线…')).toBeInTheDocument()
    expect(screen.queryByText('暂无校验通过的历史 K 线')).not.toBeInTheDocument()
    expect(screen.getByText('已验证：AKShare 1.18.84')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /历史日线 · 18960/ })).toBeInTheDocument()
    expect(screen.getByText(/没有 Bid\/Ask、授权实时价格或实盘权限/)).toBeInTheDocument()
    expect(screen.getByText('历史数据不等于实时行情')).toBeInTheDocument()
    expect(screen.getByText('510300')).toBeInTheDocument()
    expect(screen.getByText('200,000,000')).toBeInTheDocument()
    expect(screen.queryByText('Bid')).not.toBeInTheDocument()
    expect(await screen.findByText(/2\/2 个完整交易日/)).toBeInTheDocument()
    expect(screen.getByText(/2\/2 个完整交易日/)).toBeInTheDocument()
  })

  it('shows checksum-bound multi-asset minute history without calling it realtime', async () => {
    cleanup()
    render(<MarketPage data={data} />)
    fireEvent.click(screen.getByRole('tab', { name: /历史分时 · 115430/ }))
    expect(screen.getByText(/已验证 1 分钟数据：97 标的 × 5 日/)).toBeInTheDocument()
    expect(screen.getByText(/22 ETF \+ 75 行业股，覆盖 15 个行业/)).toBeInTheDocument()
    expect(screen.getByText(/不合成 14:58\/14:59/)).toBeInTheDocument()
    expect(await screen.findByText(/2\/2 根 1 分钟 Bar/)).toBeInTheDocument()
    expect(screen.getByText(/不是盘中实时跟踪/)).toBeInTheDocument()
    expect(screen.getByTestId('minute-history-chart')).toBeInTheDocument()
  })

  it('does not present a pending verified minute request as missing evidence', () => {
    cleanup()
    vi.mocked(loadMinuteBarSeries).mockReturnValueOnce(new Promise<never>(() => undefined))
    render(<MarketPage data={data} />)
    fireEvent.click(screen.getByRole('tab', { name: /历史分时 · 115430/ }))
    expect(screen.getByText('正在加载已验证历史分时线…')).toBeInTheDocument()
    expect(screen.queryByText('暂无校验通过的历史分时线')).not.toBeInTheDocument()
  })

  it('shows finalized same-day minute observations without claiming realtime', async () => {
    cleanup()
    render(<MarketPage data={data} />)
    fireEvent.click(screen.getByRole('tab', { name: /当日分钟观测 · 15\/15/ }))
    expect(screen.getByText('免费历史源当日观测：MORNING_SESSION')).toBeInTheDocument()
    expect(screen.getByText(/共同时间点 1 个/)).toBeInTheDocument()
    expect(screen.getByText(/额外等待 60 秒后才纳入/)).toBeInTheDocument()
    expect(screen.getByText(/不是授权实时行情，不进入 PAPER 或策略状态/)).toBeInTheDocument()
    expect(screen.getByText('盘中连续性 · 1 个签名检查点')).toBeInTheDocument()
    expect(screen.getByText('已验证单调推进')).toBeInTheDocument()
    expect(screen.getByText(/时间或共同分钟回退会被保留并标红/)).toBeInTheDocument()
    expect(screen.getByText('全股票宽度观测 · 75/75')).toBeInTheDocument()
    expect(screen.getByText('15 板块 × 每板块 5 只股票')).toBeInTheDocument()
    expect(screen.getByText(/跨股票共同已结束分钟 2 个/)).toBeInTheDocument()
    expect(screen.getByText(/后端从签名的逐股票时间集合重新计算共同覆盖/)).toBeInTheDocument()
    expect(screen.getByText('盘中横截面方向因子 · 仅观察')).toBeInTheDocument()
    expect(screen.getByText(/相对开盘上涨 45\/75、下跌 25/)).toBeInTheDocument()
    expect(screen.getByText(/等权开盘至今 0.12%/)).toBeInTheDocument()
    expect(screen.getByText(/不进入策略调参或 PAPER/)).toBeInTheDocument()
    expect(screen.getByText(/1 根同日已结束 Bar/)).toBeInTheDocument()
    expect(screen.getByTestId('minute-history-chart')).toBeInTheDocument()
  })

  it('shows checksum-bound native five-minute history without presenting a resample', async () => {
    cleanup()
    render(<MarketPage data={data} />)
    fireEvent.click(screen.getByRole('tab', { name: /原生 5 分钟 · 193920/ }))
    expect(screen.getByText(/已验证原生 5 分钟数据：101 标的 × 40 日/)).toBeInTheDocument()
    expect(screen.getByText(/26 ETF \+ 75 行业股，覆盖 15 个行业/)).toBeInTheDocument()
    expect(screen.getByText(/供应商原生 48 点/)).toBeInTheDocument()
    expect(await screen.findByText(/2\/2 根原生 5 分钟 Bar/)).toBeInTheDocument()
    expect(screen.getByText(/不是 1 分钟线降采样，也不是盘中实时跟踪/)).toBeInTheDocument()
    expect(screen.getByTestId('minute-history-chart')).toBeInTheDocument()
  })

  it('shows a checksum-bound long five-minute day without presenting it as realtime or PIT', async () => {
    cleanup()
    render(<MarketPage data={data} />)
    fireEvent.click(screen.getByRole('tab', { name: /长历史 5 分钟 · 1324800/ }))
    expect(screen.getByText(/已验证长历史原生 5 分钟数据：75 只股票 × 368 日/)).toBeInTheDocument()
    expect(screen.getByText(/当前前复权数据可能含未来复权知识、股票池非 PIT/)).toBeInTheDocument()
    expect(await screen.findByText(/2\/2 根原生 5 分钟 Bar/)).toBeInTheDocument()
    expect(loadLongFiveMinuteBarSeries).toHaveBeenCalledWith('600036', '2026-08-25')
    expect(screen.getByText(/每次只返回所选交易日 48 点；保留尾部不会进入策略调参/)).toBeInTheDocument()
    expect(screen.getByTestId('minute-history-chart')).toBeInTheDocument()
  })

  it('filters verified historical rows without inventing snapshots', () => {
    cleanup()
    render(<MarketPage data={data} />)
    fireEvent.change(screen.getByPlaceholderText('搜索代码、名称或板块'), { target: { value: '510500' } })
    expect(screen.queryByText('510300')).not.toBeInTheDocument()
    expect(screen.getByText(/暂无校验通过的历史日线/)).toBeInTheDocument()
  })

  it('removes the chart immediately when verified evidence disappears', async () => {
    cleanup()
    const view = render(<MarketPage data={data} />)
    expect(await screen.findByText(/2\/2 个完整交易日/)).toBeInTheDocument()
    view.rerender(<MarketPage data={{ ...data, market: { ...data.market, historical: null }, historicalBars: [] }} />)
    await waitFor(() => expect(screen.queryByText(/2\/2 个完整交易日/)).not.toBeInTheDocument())
    expect(screen.getByText('暂无校验通过的历史 K 线')).toBeInTheDocument()
  })

  it('keeps provider connectivity separate from execution gateway health', () => {
    render(<DataCenterPage data={data} />)
    expect(screen.getByText('DISCONNECTED')).toBeInTheDocument()
    expect(screen.queryByText('CONNECTED')).not.toBeInTheDocument()
    expect(screen.getByText('PASSED')).toBeInTheDocument()
    expect(screen.getAllByText('NO')).toHaveLength(2)
    expect(screen.getByText('paper-20260812')).toBeInTheDocument()
    expect(screen.getByText('SHA-256 已验证')).toBeInTheDocument()
    expect(screen.getByText('PAPER 日终自动报告：GENERATED')).toBeInTheDocument()
    expect(screen.getByText('公网分时可用性审计：90 次请求全部通过')).toBeInTheDocument()
    expect(screen.getByText(/强制未缓存中位 4.669 秒、P95 6.248 秒/)).toBeInTheDocument()
    expect(screen.getByText(/CDN 命中中位 0.179 秒、P95 0.274 秒/)).toBeInTheDocument()
    expect(screen.getByText(/公网写操作 7\/7 已阻断/)).toBeInTheDocument()
    expect(screen.getByText(/不是实时行情授权/)).toBeInTheDocument()
    expect(screen.getByText('15 行业代表股当日分钟点时观测')).toBeInTheDocument()
    expect(screen.getByText('75 股票全板块当日分钟宽度')).toBeInTheDocument()
    expect(screen.getByText('当日分钟多检查点连续性')).toBeInTheDocument()
    expect(screen.queryByText(/网页调用方不能提供这些身份/)).not.toBeInTheDocument()
  })
})

describe('verified backtest analytics', () => {
  it('runs an adjustable preview without presenting it as promotion evidence', async () => {
    window.localStorage.clear()
    const preview: BacktestPreview = {
      classification: 'AD_HOC_PREVIEW_CURRENT_UNIVERSE_NOT_PIT', promotion: 'NOT_ELIGIBLE',
      promotion_reasons: ['AD_HOC_PARAMETERS_NOT_PREDECLARED'], data_version: 'akshare-bars-v1',
      data_checksum: 'abc',
      parameters: {
        start: '2024-01-02', end: '2026-08-12', factor: 'momentum', horizons: [20, 60, 120], top_k: 3,
        rebalance_every: 20, reserve: '0.05', trend_window: 120, volatility_window: 20,
        target_annual_volatility: '0.15', initial_cash: '1000000',
        transaction_cost_rate: '0.0015', slippage_rate: '0.0005',
      },
      session_count: 632, universe_size: 30,
      metrics: { total_return: '0.03', annualized_return: '0.01', sharpe: '0.4', maximum_drawdown: '-0.09', average_exposure: '0.5', number_of_trades: '42', turnover: '2', transaction_costs: '100' },
      curve: [{ session: '2024-01-02', equity: '1000000', drawdown: '0' }],
      terminal_positions: [], execution_timing: 'close decision, next open execution',
      external_order_transmission: false,
      preview_hash: 'a'.repeat(64),
    }
    vi.mocked(runBacktestPreview).mockResolvedValueOnce(preview)
    render(<BacktestPage data={data} />)
    fireEvent.click(screen.getByRole('button', { name: '运行即时回测' }))
    expect(await screen.findByText('已完成 632 个会话、30 个标的 · 动量')).toBeInTheDocument()
    expect(screen.getByText(/即时预览，不是策略晋级证据/)).toBeInTheDocument()
    expect(screen.getAllByText(/结论固定为 NOT_ELIGIBLE/).length).toBeGreaterThan(0)
    expect(runBacktestPreview).toHaveBeenCalledWith(expect.objectContaining({
      factor: 'momentum', horizons: [20, 60, 120], top_k: 3, rebalance_every: 20,
    }))
    fireEvent.change(screen.getByLabelText('本地实验名称'), { target: { value: '动量方案 A' } })
    fireEvent.click(screen.getByRole('button', { name: '保存到本地实验' }))
    expect(screen.getByText(/已保存“动量方案 A”/)).toBeInTheDocument()
    expect(screen.getAllByText('动量方案 A').length).toBeGreaterThan(0)
    expect(screen.getByRole('button', { name: '载入参数' })).toBeInTheDocument()
    expect(screen.getByText(/记录严格绑定当前数据版本与 checksum/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /导出完整结果/ })).toBeInTheDocument()

    vi.mocked(runBacktestPreview).mockResolvedValueOnce({ ...preview, preview_hash: 'b'.repeat(64) })
    fireEvent.click(screen.getByRole('button', { name: '运行即时回测' }))
    await waitFor(() => expect(runBacktestPreview).toHaveBeenCalledTimes(2))
    fireEvent.change(screen.getByLabelText('本地实验名称'), { target: { value: '动量方案 B' } })
    fireEvent.click(screen.getByRole('button', { name: '保存到本地实验' }))
    expect(screen.getAllByRole('button', { name: '删除' })).toHaveLength(2)

    fireEvent.click(screen.getAllByRole('button', { name: '删除' })[0])
    fireEvent.click(await screen.findByRole('button', { name: 'OK' }))
    await waitFor(() => expect(screen.queryByText('动量方案 B')).not.toBeInTheDocument())
    fireEvent.click(screen.getByRole('button', { name: '删除' }))
    fireEvent.click(await screen.findByRole('button', { name: 'OK' }))
    await waitFor(() => expect(screen.queryByText('动量方案 A')).not.toBeInTheDocument())
    expect(screen.getByText('运行回测后可保存结果，用于反复调参与对比。')).toBeInTheDocument()
    window.localStorage.clear()
  })

  it('shows the verified leader without presenting it as Champion', async () => {
    cleanup()
    render(<BacktestPage data={data} />)
    expect(screen.getByText('探索性领先者，不是 Champion')).toBeInTheDocument()
    expect(screen.getByText(/分钟修正锁定测试：NOT_ELIGIBLE/)).toBeInTheDocument()
    expect(screen.getByText(/两者绝对收益均为负/)).toBeInTheDocument()
    expect(screen.getByText(/20 bps 优势消失/)).toBeInTheDocument()
    expect(screen.getAllByTestId('minute-backtest-chart')).toHaveLength(3)
    expect(screen.getByText('跨时间尺度否证：1/4 折绝对正收益')).toBeInTheDocument()
    expect(screen.getByText(/短窗口 1 分钟结果未跨时间尺度复现/)).toBeInTheDocument()
    expect(screen.getByText(/0 bps 起已跑输基准/)).toBeInTheDocument()
    expect(screen.getByText(/长历史复核：2\/6 折绝对正收益/)).toBeInTheDocument()
    expect(screen.getByText(/尾部 8 日未进入回测/)).toBeInTheDocument()
    expect(screen.getByText(/前复权，可能含未来复权知识/)).toBeInTheDocument()
    expect(screen.getAllByText('63.02%').length).toBeGreaterThan(0)
    expect(screen.getAllByText('80.03%').length).toBeGreaterThan(0)
    expect(screen.getByText('511.890')).toBeInTheDocument()
    expect(screen.getByText(/26.87% 初始权益单位/)).toBeInTheDocument()
    expect(screen.getByText(/分钟趋势弱 433,024/)).toBeInTheDocument()
    expect(screen.getByText(/同一标的-Bar 可以同时命中多个风险原因/)).toBeInTheDocument()
    expect(screen.getByText(/预注册尾部诊断被拒绝：仅通过 2\/6 个门槛/)).toBeInTheDocument()
    expect(screen.getByText(/失败后不二次调参/)).toBeInTheDocument()
    expect(screen.getAllByText(/82.51%/).length).toBeGreaterThan(0)
    expect(screen.getByText(/15 个整行业剔除中至少胜 12 个/)).toBeInTheDocument()
    expect(screen.getByText(/不是全盲独立测试/)).toBeInTheDocument()
    expect(screen.getByTestId('reserved-tail-chart')).toBeInTheDocument()
    expect(screen.getAllByText('样本组')).toHaveLength(2)
    expect(screen.getByText('整行业剔除')).toBeInTheDocument()
    expect(screen.getByText('基础化工')).toBeInTheDocument()
    expect(screen.getByText('92')).toBeInTheDocument()
    expect(screen.getByText(/Buy-and-Hold 仅在记录、资金曲线、回撤 checksum 全部通过/)).toBeInTheDocument()
    expect(screen.getByText(/测试窗指标在上方单列/)).toBeInTheDocument()
    expect(screen.getByText('45.00%')).toBeInTheDocument()
    expect(screen.getByText('2.010')).toBeInTheDocument()
    expect(screen.getByText('0.30%')).toBeInTheDocument()
    expect(screen.getByText('-2.89%')).toBeInTheDocument()
    expect(screen.getByText('研究回测模拟成交')).toBeInTheDocument()
    expect(screen.getByText(/不是市场真实成交/)).toBeInTheDocument()
    expect(screen.getByText('2024-01')).toBeInTheDocument()
    expect(screen.getByText('研究回测风险与运行契约')).toBeInTheDocument()
    expect(screen.getByText(/不是当前 PAPER 账户风险/)).toBeInTheDocument()
    expect(screen.getByText('三折独立样本外：0/3 折正收益')).toBeInTheDocument()
    expect(screen.getByText(/聚合收益 -20.44%/)).toBeInTheDocument()
    expect(screen.getByText(/不得因探索性领先者的正收益覆盖样本外败绩/)).toBeInTheDocument()
    expect(screen.getByText('momentum-trend')).toBeInTheDocument()
    expect(await screen.findByTestId('equity-chart')).toBeInTheDocument()
  })

  it('does not present missing walk-forward evidence as three failed folds', async () => {
    cleanup()
    const withoutWalkForward = {
      ...data,
      walkForward: { promotion: 'NOT_EVALUATED', promotion_reasons: [], result: { folds: [] } },
    } as unknown as PlatformSnapshot
    render(<BacktestPage data={withoutWalkForward} />)
    expect(screen.getByText('暂无已验证滚动样本外证据')).toBeInTheDocument()
    expect(screen.getByText(/不能据此声称策略通过或失败/)).toBeInTheDocument()
    expect(screen.queryByText(/0\/3 折正收益/)).not.toBeInTheDocument()
    expect(await screen.findByTestId('equity-chart')).toBeInTheDocument()
  })
})

describe('verified robustness evidence', () => {
  it('does not present experiment records as a registered lineage', () => {
    render(<ResearchPage data={data} />)
    expect(screen.getByRole('tab', { name: '实验候选' })).toBeInTheDocument()
    expect(screen.getByText(/spec_hash 未独立复算/)).toBeInTheDocument()
    expect(screen.getByText(/不存在可展示的 Champion、Challenger 或演化血缘/)).toBeInTheDocument()
  })

  it('labels reused observations as diagnostics rather than independent evidence', () => {
    cleanup()
    render(<ResearchPage data={data} />)
    fireEvent.click(screen.getByRole('tab', { name: '稳健性诊断' }))
    expect(screen.getByText(/复用样本的拒绝后诊断/)).toBeInTheDocument()
    expect(screen.getByText(/不是新的独立测试证据/)).toBeInTheDocument()
    expect(screen.getByText(/-1.1429/)).toBeInTheDocument()
    expect(screen.getAllByText(/结论：NOT_ELIGIBLE/).length).toBeGreaterThan(0)
  })

  it('shows the bounded factor search and its negative locked result', () => {
    cleanup()
    render(<ResearchPage data={data} />)
    fireEvent.click(screen.getByRole('tab', { name: '因子搜索' }))
    expect(screen.getAllByText(/reversal-20-top3-20/).length).toBeGreaterThan(0)
    expect(screen.getByText(/确认段收益 -12.39%/)).toBeInTheDocument()
    expect(screen.getByText(/主动超额：-7.77%/)).toBeInTheDocument()
    expect(screen.getByText(/不是全新盲测/)).toBeInTheDocument()
    expect(screen.getByText(/不能接入 PAPER/)).toBeInTheDocument()
  })

  it('shows the verified factor robustness gate without claiming promotion', () => {
    cleanup()
    render(<ResearchPage data={data} />)
    fireEvent.click(screen.getByRole('tab', { name: '因子鲁棒性' }))
    expect(screen.getByText(/冻结候选：36 个；季度窗口：8 个/)).toBeInTheDocument()
    expect(screen.getAllByText(/reversal-10-top3-20/).length).toBeGreaterThan(0)
    expect(screen.getByText(/双倍成本收益 30.99%/)).toBeInTheDocument()
    expect(screen.getByText(/Bootstrap 正收益概率 85.30%/)).toBeInTheDocument()
    expect(screen.getByText(/2026 污染审计：收益 -0.44%/)).toBeInTheDocument()
    expect(screen.getByText(/不是新的独立样本外确认/)).toBeInTheDocument()
    expect(screen.getByText(/仍为 NOT_ELIGIBLE，不能接入 PAPER/)).toBeInTheDocument()
  })

  it('shows multi-sector stock transfer evidence and its survivorship boundary', () => {
    cleanup()
    render(<ResearchPage data={data} />)
    fireEvent.click(screen.getByRole('tab', { name: '多板块个股迁移' }))
    expect(screen.getByText(/15 个板块 × 每板块 5 只/)).toBeInTheDocument()
    expect(screen.getByText(/共 75 只股票、149903 根日线/)).toBeInTheDocument()
    expect(screen.getByText(/正收益年份 7\/7，单板块 15\/15，留一板块 15\/15/)).toBeInTheDocument()
    expect(screen.getByText(/最大回撤 -34.20%/)).toBeInTheDocument()
    expect(screen.getByText(/Bootstrap 正收益概率 96.90%/)).toBeInTheDocument()
    expect(screen.getByText(/2026 当前成分股向历史回投/)).toBeInTheDocument()
    expect(screen.getByText(/结论固定为 NOT_ELIGIBLE，不能接入 PAPER/)).toBeInTheDocument()
  })

  it('shows the development-only minute policy lock without claiming a new test', () => {
    cleanup()
    render(<ResearchPage data={data} />)
    fireEvent.click(screen.getByRole('tab', { name: '开发区分钟策略' }))
    expect(screen.getByText(/1296000 根 5 分钟 Bar/)).toBeInTheDocument()
    expect(screen.getAllByText(/dev-floor-098-slow12x48-v1/).length).toBeGreaterThan(0)
    expect(screen.getByText(/此前尾部结果已经可见/)).toBeInTheDocument()
    expect(screen.getByText(/至少等待 20 个完整未来会话/)).toBeInTheDocument()
    expect(screen.getByText(/不证明 1 分钟实时就绪/)).toBeInTheDocument()
  })

  it('shows the locked complete-session observer without claiming a blind test', () => {
    cleanup()
    render(<ResearchPage data={data} />)
    fireEvent.click(screen.getByRole('tab', { name: '未来完整日观测' }))
    expect(screen.getByText(/观测进度：0\/20 个完整交易日/)).toBeInTheDocument()
    expect(screen.getByText(/15 板块、75 股、每股 48 根原生 5 分钟 Bar/)).toBeInTheDocument()
    expect(screen.getAllByText('等待样本')).toHaveLength(5)
    expect(screen.getByText(/不宣称完全盲预注册/)).toBeInTheDocument()
    expect(screen.getByText(/不会自动授权 PAPER、LIVE 或外部下单/)).toBeInTheDocument()
  })
})

describe('verified experiment candidates', () => {
  it('labels full-chain records as ineligible candidates rather than registered strategies', () => {
    render(<StrategyPage data={data} />)
    expect(screen.getByText('实验候选，不是已注册或已批准策略')).toBeInTheDocument()
    expect(screen.getByText('abc123def456')).toBeInTheDocument()
    expect(screen.getByText('NOT_ELIGIBLE')).toBeInTheDocument()
    expect(screen.getByText('已验证·不可晋级')).toBeInTheDocument()
    expect(screen.getByText(/也没有已注册谱系/)).toBeInTheDocument()
  })

  it('shows immutable model parameters and held-out split without claiming promotion', () => {
    cleanup()
    render(<ExperimentPage data={data} />)
    expect(screen.getByText('momentum-volatility')).toBeInTheDocument()
    expect(screen.getByText('lookback=120')).toBeInTheDocument()
    expect(screen.getByText('2026-01-01 至 2026-08-12')).toBeInTheDocument()
    expect(screen.getByText(/每行已验证 record\/result\/artifact 哈希/)).toBeInTheDocument()
    expect(screen.getByText(/当前结论仍为 NOT_ELIGIBLE/)).toBeInTheDocument()
  })
})
