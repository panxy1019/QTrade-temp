import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { IntradayFactorBacktest, PlatformSnapshot, SingleStockFiveMinuteBacktest } from '../types'
import { cancelPaperOrder, controlRuntime, loadLongFiveMinuteBarSeries, loadMinuteBarSeries, runIntradayFactorBacktest, runSingleStockFiveMinuteBacktest } from '../api/client'
import { formatDirectionalDelta } from '../research/intradayDirectional'
import { canonicalJson, sha256Hex } from '../research/strategyIdentity'
import { HistoricalBacktestPage as BacktestPage, DataCenterPage, ExperimentPage, HistoricalMarketPage as MarketPage, MonitorPage, OrdersPage, PaperPage, PositionsPage, ResearchPage, RiskPage, StrategyPage } from './DataPages'

vi.mock('../charts/ChartRenderer', () => ({ default: () => <div data-testid="equity-chart" /> }))
vi.mock('../components/PublicResearchObservations', () => ({ PublicResearchObservations: () => null }))
vi.mock('../components/QlibBaseline', () => ({ QlibBaseline: () => null }))
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
  runIntradayFactorBacktest: vi.fn(),
  runSingleStockFiveMinuteBacktest: vi.fn(),
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

const longIntradayFullMetrics = {
  total_return: '-0.271715', annualized_return: '-0.474', annualized_volatility: '0.161',
  sharpe: '-3.11', sortino: '-4.18', maximum_drawdown: '-0.284', calmar: '-1.67',
  win_rate: '0.43', benchmark_return: '-0.212791', excess_return: '-0.058924',
  information_ratio: '-0.52', number_of_periods: 120, turnover: '200.2',
  profit_loss_ratio: '0.91', exposure: '1', number_of_trades: 1001,
  transaction_costs: '0.102',
}

const longIntradayStitchedMetrics = {
  ...longIntradayFullMetrics,
  total_return: '-0.283060', annualized_return: '-0.725', maximum_drawdown: '-0.287245',
  benchmark_return: '-0.158154', excess_return: '-0.124906', number_of_periods: 60,
  number_of_trades: 506,
}

const longIntradayFolds = Array.from({ length: 6 }, (_, index) => ({
  fold_id: `WF-0${index + 1}`,
  training_start: '2026-03-03', training_end: `2026-${String(5 + index).padStart(2, '0')}-29`,
  test_start: `2026-${String(6 + index).padStart(2, '0')}-01`, test_end: `2026-${String(6 + index).padStart(2, '0')}-12`,
  training_session_count: 60 + index * 10, test_session_count: 10,
  selected_minimum_body_return_bps: index < 2 ? '10' : '5',
  selection_known_at: `2026-${String(6 + index).padStart(2, '0')}-01T09:35:00+08:00`,
  training_signal_count: 345 + index * 70, test_signal_count: 74 - index,
  training_strategy_metrics: longIntradayFullMetrics,
  test_strategy_metrics: { ...longIntradayStitchedMetrics, total_return: index === 2 ? '0.01' : '-0.05', excess_return: index === 2 ? '0.005' : '-0.02', maximum_drawdown: '-0.08' },
  test_benchmark_metrics: { ...longIntradayStitchedMetrics, total_return: '-0.03', excess_return: '0' },
}))

const longIntradaySectorRows = Array.from({ length: 15 }, (_, index) => ({
  group_id: String(801000 + index), group_name: `板块${index + 1}`,
  observation_count: 600, signal_count: 60 + index,
  mean_signal_forward_return: index < 5 ? '0.001' : '-0.001', signal_hit_rate: '0.45',
  strategy_metrics: { ...longIntradayFullMetrics, total_return: index < 5 ? '0.05' : '-0.04', excess_return: index < 5 ? '0.06' : '-0.02' },
  benchmark_metrics: { ...longIntradayFullMetrics, total_return: '-0.01', excess_return: '0' },
}))

const longIntradaySymbolRows = Array.from({ length: 75 }, (_, index) => ({
  group_id: String(600000 + index), group_name: String(600000 + index),
  observation_count: 120, signal_count: 10 + index % 8,
  mean_signal_forward_return: index < 20 ? '0.001' : '-0.001', signal_hit_rate: '0.4',
  strategy_metrics: { ...longIntradayFullMetrics, total_return: index < 20 ? '0.03' : '-0.03', excess_return: index < 20 ? '0.04' : '-0.01' },
  benchmark_metrics: { ...longIntradayFullMetrics, total_return: '-0.01', excess_return: '0' },
}))

const intradayStabilityFixture: IntradayFactorBacktest['stability_validation'] = {
  classification: 'CONTIGUOUS_SESSION_BLOCK_FACTOR_STABILITY_DIAGNOSTIC',
  blocks: [
    { block_id: 'TS-01', start: '2026-03-03', end: '2026-04-14', session_count: 30, observation_count: 2250, signal_count: 249, signal_session_count: 30, mean_session_conditional_excess_return: '0.003492486' },
    { block_id: 'TS-02', start: '2026-04-15', end: '2026-05-29', session_count: 30, observation_count: 2250, signal_count: 221, signal_session_count: 30, mean_session_conditional_excess_return: '0.000094796' },
    { block_id: 'TS-03', start: '2026-06-01', end: '2026-07-13', session_count: 30, observation_count: 2250, signal_count: 239, signal_session_count: 30, mean_session_conditional_excess_return: '-0.003124842' },
    { block_id: 'TS-04', start: '2026-07-14', end: '2026-08-24', session_count: 30, observation_count: 2250, signal_count: 292, signal_session_count: 30, mean_session_conditional_excess_return: '-0.002757048' },
  ],
  block_count: 4,
  positive_block_count: 2,
  worst_block_mean_conditional_excess_return: '-0.003124842',
  maximum_absolute_block_contribution_share: '0.368827',
  contribution_share_threshold: '0.60',
  criteria: {
    minimum_four_contiguous_blocks: true,
    every_block_has_signal_session: true,
    positive_block_ratio_at_least_75pct: false,
    maximum_absolute_block_contribution_share_below_0_60: true,
  },
  passed: false,
}

const data = {
  system: { mode: 'PAPER', running: true, risk_state: 'NORMAL' },
  health: { healthy: true, runtime_mode: 'PAPER', risk_state: 'NORMAL', gateway_connected: true, reconciliation_status: 'OK' },
  deployment: { classification: 'PUBLIC_BUILD_PROVENANCE', provider: 'VERCEL', status: 'AVAILABLE', target_environment: 'production', git_commit_sha: '81e357cb069a69d6be4d8ba7a9fb1acdf7167083', git_commit_ref: 'main', external_order_transmission: false },
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
  strategyTemplates: {
    classification: 'READ_ONLY_IMPLEMENTED_BASELINE_STRATEGY_CATALOG',
    template_count: 5,
    active_template_count: 4,
    benchmark_template_count: 1,
    causal_preview_template_count: 3,
    templates: [
      {
        template_id: 'equal-weight-buy-hold-benchmark-v1', name: '等权买入并持有', role: 'BENCHMARK_ONLY',
        family: 'BUY_AND_HOLD_BENCHMARK', implementation_ref: 'qtrade.research.multi_asset.EqualWeightBuyHold',
        description: '首个交易日等权建仓，之后保持仓位，作为主动策略比较基准。', data_interval: '1d', universe_scope: '多标的',
        entry_rule: '首个可交易会话按等权目标在下一会话开盘执行。', exit_rule: '基准期内不主动退出。',
        parameter_defaults: { reserve: '0' }, required_inputs: ['日线开收盘', '标的列表'],
        validation_status: 'IMPLEMENTED_NOT_PRODUCTION_VALIDATED', known_limitations: ['只作为基准，不是主动选股或择时策略。'], causal_preview_supported: false, causal_preview_blocker: 'COMPARISON_ONLY_NO_ACTIVE_TEMPLATE_PREVIEW',
      },
      {
        template_id: 'moving-average-trend-v1', name: '移动平均趋势', role: 'ACTIVE_BASELINE',
        family: 'SINGLE_ASSET_TREND', implementation_ref: 'qtrade.strategies.baselines.MovingAverageTrendStrategy',
        description: '价格位于移动平均线上方时持仓，跌破时清仓。', data_interval: '1d', universe_scope: '单标的',
        entry_rule: '收盘价大于等于窗口均线时目标持仓。', exit_rule: '收盘价低于窗口均线时目标仓位归零。',
        parameter_defaults: { window: '20', quantity: '100' }, required_inputs: ['日线收盘价'],
        validation_status: 'IMPLEMENTED_NOT_PRODUCTION_VALIDATED', known_limitations: ['震荡市可能频繁换手。'], causal_preview_supported: false, causal_preview_blocker: 'SINGLE_ASSET_DAILY_SELECTOR_NOT_CONNECTED',
      },
      {
        template_id: 'etf-momentum-rotation-v1', name: 'ETF 动量轮动', role: 'ACTIVE_BASELINE',
        family: 'ETF_MOMENTUM_ROTATION', implementation_ref: 'qtrade.research.multi_asset.MomentumAllocation',
        description: '按多周期动量排序并定期持有排名靠前的 ETF。', data_interval: '1d', universe_scope: '多 ETF',
        entry_rule: '只选择正动量且排名前 K 的 ETF。', exit_rule: '再平衡时跌出前 K 或动量非正则退出。',
        parameter_defaults: { horizons: '20,60,120', top_k: '3', rebalance_every: '20', reserve: '0.05' }, required_inputs: ['日线收盘价', 'ETF 标的池'],
        validation_status: 'IMPLEMENTED_NOT_PRODUCTION_VALIDATED', known_limitations: ['当前免费数据股票池不是 PIT。'], causal_preview_supported: true, causal_preview_blocker: null,
      },
      {
        template_id: 'etf-momentum-trend-volatility-v1', name: 'ETF 动量 + 趋势波动率约束', role: 'ACTIVE_BASELINE',
        family: 'ETF_MOMENTUM_TREND_VOLATILITY', implementation_ref: 'qtrade.research.multi_asset.MomentumAllocation',
        description: '在动量轮动上增加趋势过滤与目标年化波动率缩放。', data_interval: '1d', universe_scope: '多 ETF',
        entry_rule: '正动量、排名前 K 且价格不低于趋势均线时持有。', exit_rule: '再平衡时任一资格条件失效则退出。',
        parameter_defaults: { horizons: '20,60,120', top_k: '3', rebalance_every: '20', reserve: '0.05', trend_window: '120', volatility_window: '20', target_annual_volatility: '0.15' }, required_inputs: ['日线收盘价', 'ETF 标的池'],
        validation_status: 'IMPLEMENTED_NOT_PRODUCTION_VALIDATED', known_limitations: ['目标波动率只降低暴露，不使用杠杆。'], causal_preview_supported: true, causal_preview_blocker: null,
      },
      {
        template_id: 'cross-sectional-factor-ranking-v1', name: '横截面因子排序', role: 'ACTIVE_BASELINE',
        family: 'CROSS_SECTIONAL_FACTOR_RANKING', implementation_ref: 'qtrade.research.multi_asset.RankedFactorAllocation',
        description: '对预声明价格因子做横截面百分位排序并组合选股。', data_interval: '1d', universe_scope: '多股票或 ETF',
        entry_rule: '按加权因子得分选择排名前 K 的标的。', exit_rule: '再平衡时跌出前 K 或趋势资格失效则退出。',
        parameter_defaults: { factors: 'momentum_20', top_k: '3', rebalance_every: '20', reserve: '0.05' }, required_inputs: ['质量通过的日线', '冻结标的池', '预注册因子定义'],
        validation_status: 'IMPLEMENTED_NOT_PRODUCTION_VALIDATED', known_limitations: ['因子与参数必须在测试窗口之前冻结。'], causal_preview_supported: true, causal_preview_blocker: null,
      },
    ],
    selection_creates_experiment: false,
    selection_registers_strategy: false,
    paper_runtime_start: false,
    live_runtime_start: false,
    arbitrary_code_execution: false,
    external_order_transmission: false,
    next_step: 'FREEZE_EXPERIMENT_RUN_SPEC_THEN_CAUSAL_BACKTEST',
  },
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
  intradayFactorRobustness: {
    schema_version: 1,
    report_id: 'opening-triple-bullish-long-window-robustness-v1',
    classification: 'SIGNED_REUSED_LONG_WINDOW_INTRADAY_FACTOR_DIAGNOSTIC',
    generated_at: '2026-08-26T23:30:00+08:00',
    evaluation_contract: {
      selected_session_count: 120, selected_start: '2026-03-03', selected_end: '2026-08-24', exit_completion_day: '2026-08-25',
      candidate_thresholds_bps: ['0', '5', '10', '20'], initial_training_session_count: 60,
      fold_count: 6, test_sessions_per_fold: 10, test_returns_enter_same_fold_selection: false,
      reused_history_not_independent_confirmation: true,
    },
    result_preview_hash: '9'.repeat(64),
    summary: {
      observation_count: 9000, signal_count: 1001, rank_ic: '-0.0120601',
      full_window_strategy_metrics: longIntradayFullMetrics,
      fold_count: 6, positive_fold_count: 1, selected_thresholds_bps: ['10', '10', '5', '5', '5', '5'],
      stitched_strategy_metrics: longIntradayStitchedMetrics,
      stitched_excess_bootstrap: {
        method: 'PAIRED_STATIONARY_BOOTSTRAP', observation_count: 60,
        replications: 1000, expected_block_length: 10, seed: 20260827,
        observed_excess_total_return: '-0.1249055',
        p05_excess_total_return: '-0.2391069', median_excess_total_return: '-0.1183382',
        p95_excess_total_return: '0.0042813', probability_positive_excess: '0.057',
      },
      threshold_sensitivity: [
        { minimum_body_return_bps: '0', signal_count: 527, total_return: '-0.298072', excess_return: '-0.1401' },
        { minimum_body_return_bps: '5', signal_count: 506, total_return: '-0.271319', excess_return: '-0.1132' },
        { minimum_body_return_bps: '10', signal_count: 487, total_return: '-0.290270', excess_return: '-0.1321' },
        { minimum_body_return_bps: '20', signal_count: 450, total_return: '-0.317723', excess_return: '-0.1596' },
      ],
      robustness_passed: false, research_gate_passed: false,
    },
    result: {
      event_summary: {
        clustered_signal_session_count: 120,
        newey_west_t_statistic: '-0.4800296',
        confidence_interval_95_lower: '-0.0029159',
        confidence_interval_95_upper: '0.0017686',
      },
      stability_validation: intradayStabilityFixture,
      backtest: {
        curve: [
          { trading_day: '2026-03-03', signal_count: 3, eligible_symbol_count: 75, strategy_return: '0.01', benchmark_return: '0.005', strategy_equity: '1.01', benchmark_equity: '1.005', excess_equity: '0.005' },
          { trading_day: '2026-08-24', signal_count: 8, eligible_symbol_count: 75, strategy_return: '-0.02', benchmark_return: '-0.01', strategy_equity: '0.728285', benchmark_equity: '0.787209', excess_equity: '-0.058924' },
        ],
        sectors: longIntradaySectorRows, symbols: longIntradaySymbolRows,
      },
      robustness_validation: {
        folds: longIntradayFolds,
        criteria: {
          minimum_two_disjoint_test_folds: true, every_test_fold_has_signal: true,
          causal_selection_known_before_test_signal: true, stitched_oos_total_return_positive: false,
          stitched_oos_excess_return_positive: false, positive_fold_ratio_at_least_60pct: false,
          worst_threshold_total_return_non_negative: false,
        },
      },
      research_gate: {
        criteria: {
          minimum_observation_count: true, minimum_signal_count: true,
          positive_mean_signal_return: false, positive_conditional_excess_return: false,
          minimum_ten_clustered_signal_sessions: true,
          newey_west_t_statistic_above_1_96: false,
          temporal_stability_passed: false,
          positive_rank_ic: false, positive_sector_ratio_at_least_60pct: false,
          maximum_absolute_factor_correlation_below_0_90: true,
          base_cost_total_return_positive: false, base_cost_excess_return_positive: false,
          walk_forward_oos_total_return_positive: false, walk_forward_oos_excess_return_positive: false,
          walk_forward_positive_fold_ratio_at_least_60pct: false, threshold_sensitivity_majority_positive: false,
        },
      },
    } as unknown as IntradayFactorBacktest,
    promotion: 'NOT_ELIGIBLE',
    promotion_reasons: ['REUSED_HISTORICAL_WINDOW_NOT_INDEPENDENT_CONFIRMATION'],
    paper_readiness: 'NOT_ELIGIBLE', live_readiness: 'DISABLED_REQUIRES_SEPARATE_AUTHORIZATION',
    state_mutation: false, external_order_transmission: false, evidence_sha256: '8'.repeat(64),
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
    classification: 'LOCKED_FUTURE_COMPLETE_SESSION_OBSERVATION',
    preregistration_id: 'baostock-locked-forward-observation-v1',
    preregistration_sha256: '9'.repeat(64),
    created_at: '2026-08-26T11:22:38+08:00',
    status: 'LOCKED_AFTER_FIRST_SESSION_OPEN_BEFORE_TARGET_SESSION_FETCH',
    timing_disclosure: { development_policy_locked_before_first_session_open: true, evaluation_contract_fixed_after_first_session_open: true, target_session_data_fetched_before_this_lock: false, claim_as_blind_preregistration: false },
    policy_id: 'dev-floor-098-slow12x48-v1', policy_hash: '4'.repeat(64),
    parameters: { short_window: '12', long_window: '48' },
    first_eligible_trading_day: '2026-08-26', future_session_count: 1,
    minimum_complete_future_sessions: 20, required_symbol_count: 75,
    required_sector_count: 15, required_bars_per_symbol_session: 48,
    observation_status: 'INCOMPLETE', all_required_gates_passed: false,
    gates: [
      { gate_id: 'OVERLAY_DRAWDOWN_NOT_WORSE_AT_5_BPS', passed: true },
      { gate_id: 'RETURN_SHORTFALL_WITHIN_50_BPS_AT_5_BPS', passed: true },
      { gate_id: 'MEAN_EXPOSURE_AT_LEAST_90_PERCENT', passed: true },
      { gate_id: 'MEAN_TURNOVER_PER_SYMBOL_AT_MOST_FIVE', passed: true },
      { gate_id: 'LEAVE_ONE_SECTOR_OUT_STABILITY', passed: true },
    ],
    cost_stress: [], leave_one_sector_out: [], promotion: 'NOT_ELIGIBLE',
    promotion_reasons: ['FUTURE_TWENTY_SESSION_OBSERVATION_INCOMPLETE'],
    realtime_readiness: false, real_data_paper_readiness: false,
    external_order_transmission: false,
  },
  forwardSourceCoverage: {
    classification: 'SOURCE_COVERAGE_DIAGNOSTIC_NOT_FORWARD_EVIDENCE',
    report_id: 'baostock-forward-source-coverage-diagnostic-v1',
    generated_at: '2026-08-26T20:13:08+08:00',
    provider: 'BaoStock', provider_version: '0.9.3',
    anchor_day: '2026-08-25', target_day: '2026-08-26',
    preregistration_id: 'baostock-locked-forward-observation-v1',
    preregistration_sha256: '9'.repeat(64),
    warmup_data_version: 'baostock-five-minute-long-v1',
    warmup_checksum: '8'.repeat(64), warmup_evidence_sha256: '7'.repeat(64),
    required_symbol_count: 75, required_bars_per_symbol_session: 48,
    target_complete_symbol_count: 75, ready_symbol_count: 75,
    incomplete_symbol_count: 0,
    incomplete_symbols: [],
    target_incomplete_symbols: [],
    fetch_error_symbols: [],
    coverage: [
      { symbol: '600036', fetch_status: 'OK', frozen_anchor_bar_count: 48, anchor_bar_count: 48, target_bar_count: 48, frozen_anchor_grid_complete: true, anchor_grid_complete: true, target_grid_complete: true, anchor_exact_match: true, ready: true, reasons: [] },
      { symbol: '300750', fetch_status: 'OK', frozen_anchor_bar_count: 48, anchor_bar_count: 48, target_bar_count: 48, frozen_anchor_grid_complete: true, anchor_grid_complete: true, target_grid_complete: true, anchor_exact_match: true, ready: true, reasons: [] },
    ],
    status: 'READY_FOR_FORMAL_COLLECTION', ready_for_formal_collection: true,
    enters_forward_metrics: false, promotion: 'NOT_ELIGIBLE',
    realtime_readiness: false, real_data_paper_readiness: false,
    external_order_transmission: false,
  },
  forwardSourceCoverageHistory: {
    classification: 'SIGNED_SOURCE_COVERAGE_HISTORY_NOT_FORWARD_EVIDENCE',
    report_id: 'baostock-forward-source-coverage-history-v1',
    checkpoint_count: 6, all_checkpoints_monotonic: true,
    checkpoints: [
      { sequence: 1, generated_at: '2026-08-26T18:14:10+08:00', report_evidence_sha256: '1'.repeat(64), ready_symbol_count: 41, target_complete_symbol_count: 41, incomplete_symbol_count: 34, fetch_error_symbol_count: 0, status: 'SOURCE_INCOMPLETE', ready_symbol_count_delta: null, monotonic_vs_previous: true, previous_checkpoint_sha256: null, checkpoint_sha256: 'a'.repeat(64) },
      { sequence: 2, generated_at: '2026-08-26T18:31:17+08:00', report_evidence_sha256: '2'.repeat(64), ready_symbol_count: 47, target_complete_symbol_count: 47, incomplete_symbol_count: 28, fetch_error_symbol_count: 0, status: 'SOURCE_INCOMPLETE', ready_symbol_count_delta: 6, monotonic_vs_previous: true, previous_checkpoint_sha256: 'a'.repeat(64), checkpoint_sha256: 'b'.repeat(64) },
      { sequence: 3, generated_at: '2026-08-26T18:47:04+08:00', report_evidence_sha256: '3'.repeat(64), ready_symbol_count: 52, target_complete_symbol_count: 52, incomplete_symbol_count: 23, fetch_error_symbol_count: 0, status: 'SOURCE_INCOMPLETE', ready_symbol_count_delta: 5, monotonic_vs_previous: true, previous_checkpoint_sha256: 'b'.repeat(64), checkpoint_sha256: 'c'.repeat(64) },
      { sequence: 4, generated_at: '2026-08-26T19:20:50+08:00', report_evidence_sha256: '4'.repeat(64), ready_symbol_count: 69, target_complete_symbol_count: 69, incomplete_symbol_count: 6, fetch_error_symbol_count: 0, status: 'SOURCE_INCOMPLETE', ready_symbol_count_delta: 17, monotonic_vs_previous: true, previous_checkpoint_sha256: 'c'.repeat(64), checkpoint_sha256: 'd'.repeat(64) },
      { sequence: 5, generated_at: '2026-08-26T19:46:16+08:00', report_evidence_sha256: '5'.repeat(64), ready_symbol_count: 74, target_complete_symbol_count: 74, incomplete_symbol_count: 1, fetch_error_symbol_count: 0, status: 'SOURCE_INCOMPLETE', ready_symbol_count_delta: 5, monotonic_vs_previous: true, previous_checkpoint_sha256: 'd'.repeat(64), checkpoint_sha256: 'f'.repeat(64) },
      { sequence: 6, generated_at: '2026-08-26T20:13:08+08:00', report_evidence_sha256: '6'.repeat(64), ready_symbol_count: 75, target_complete_symbol_count: 75, incomplete_symbol_count: 0, fetch_error_symbol_count: 0, status: 'READY_FOR_FORMAL_COLLECTION', ready_symbol_count_delta: 1, monotonic_vs_previous: true, previous_checkpoint_sha256: 'f'.repeat(64), checkpoint_sha256: '0'.repeat(64) },
    ],
    evidence_sha256: 'e'.repeat(64), enters_forward_metrics: false,
    promotion: 'NOT_ELIGIBLE', realtime_readiness: false,
    real_data_paper_readiness: false, external_order_transmission: false,
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
    directional_breadth_first_sequence: 1,
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
        directional_breadth: {
          eligible_symbol_count: 75, above_open_count: 45,
          unchanged_from_open_count: 5, below_open_count: 25,
          latest_minute_eligible_symbol_count: 75, latest_minute_up_count: 40,
          latest_minute_unchanged_count: 10, latest_minute_down_count: 25,
          equal_weight_session_return: '0.0012',
          equal_weight_latest_minute_return: '0.0003',
        },
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
    expect(screen.getByText('81e357cb069a')).toBeInTheDocument()
    expect(screen.getByText('production')).toBeInTheDocument()
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
  it('formats directional checkpoint changes without hiding their sign', () => {
    const previous = data.intradayContinuity.checkpoints[0].breadth.directional_breadth
    expect(previous).toBeDefined()
    if (!previous) throw new Error('directional fixture is missing')
    const current = { ...previous, above_open_count: 42, equal_weight_session_return: '0.000001' }
    expect(formatDirectionalDelta(current, previous)).toBe('-3 股 · -11.99 bp')
    expect(formatDirectionalDelta(previous)).toBe('首次记录')
  })

  it('shows verified history without claiming realtime readiness', async () => {
    render(<MarketPage data={data} />)
    expect(screen.getByText('正在加载已验证历史 K 线…')).toBeInTheDocument()
    expect(screen.queryByText('暂无校验通过的历史 K 线')).not.toBeInTheDocument()
    expect(screen.getByText('已验证：AKShare 1.18.84')).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: /历史日线 · 18960/ })).toBeInTheDocument()
    expect(screen.getByText(/没有 Bid\/Ask、授权实时价格或实盘权限/)).toBeInTheDocument()
    expect(screen.getByText('冻结历史研究数据（不随观测快照更新）')).toBeInTheDocument()
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
    expect(screen.getByText(/方向序列自检查点 1 开始签名保留/)).toBeInTheDocument()
    expect(screen.getByText(/时间或共同分钟回退会被保留并标红/)).toBeInTheDocument()
    expect(screen.getByText('全股票宽度观测 · 75/75')).toBeInTheDocument()
    expect(screen.getByText('15 板块 × 每板块 5 只股票')).toBeInTheDocument()
    expect(screen.getByText(/跨股票共同已结束分钟 2 个/)).toBeInTheDocument()
    expect(screen.getByText(/后端从签名的逐股票时间集合重新计算共同覆盖/)).toBeInTheDocument()
    expect(screen.getByText('盘中横截面方向因子 · 仅观察')).toBeInTheDocument()
    expect(screen.getByText(/相对开盘上涨 45\/75、下跌 25/)).toBeInTheDocument()
    expect(screen.getByText(/等权开盘至今 0.12%/)).toBeInTheDocument()
    expect(screen.getByText(/等权最新分钟 \+3.00 bp/)).toBeInTheDocument()
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
    expect(screen.getByText('75 股票 BaoStock 前向来源覆盖')).toBeInTheDocument()
    expect(screen.getByText('120 日三连阳因子稳健性否证')).toBeInTheDocument()
    expect(screen.getByText(/75\/75 READY · READY_FOR_FORMAL_COLLECTION/)).toBeInTheDocument()
    expect(screen.getByText('BaoStock 来源覆盖发布序列')).toBeInTheDocument()
    expect(screen.getByText(/MONOTONIC · 41 → 47 → 52 → 69 → 74 → 75 READY/)).toBeInTheDocument()
    expect(screen.getByText('当日分钟多检查点连续性')).toBeInTheDocument()
    expect(screen.queryByText(/网页调用方不能提供这些身份/)).not.toBeInTheDocument()
  })
})

describe('verified backtest analytics', () => {
  it('runs a selectable stock five-minute backtest against the verified long dataset', async () => {
    const execution = {
      symbol_count: 1, observation_count: 96, reduced_exposure_observation_count: 48,
      exposure_sum: '72', mean_exposure: '0.75', reduced_exposure_ratio: '0.5',
      turnover: '1.5', mean_turnover_per_symbol: '1.5', transaction_cost: '0.00075',
      mean_transaction_cost_per_symbol: '0.00075', reason_counts: { MINUTE_TREND_WEAK: 12 },
    }
    const metric = {
      total_return: '0.01', annualized_return: '0.30', annualized_volatility: '0.14', sharpe: '2.100',
      maximum_drawdown: '-0.02', realized_minute_volatility: '0.001', point_count: 96,
    }
    const result: SingleStockFiveMinuteBacktest = {
      classification: 'AD_HOC_SINGLE_STOCK_FIVE_MINUTE_BACKTEST_CURRENT_NON_PIT',
      promotion: 'NOT_ELIGIBLE', promotion_reasons: ['AD_HOC_PARAMETERS_NOT_PREREGISTERED'],
      symbol: '600036', instrument: { name: '招商银行', sector_code: '801780', sector_name: '银行' },
      data_version: 'baostock-five-minute-long-v1', dataset_checksum: 'long-five-minute-checksum', data_interval: '5m',
      verified_universe_symbol_count: 75, available_session_count: 368,
      available_start: '2025-01-02', available_end: '2026-08-25', maximum_selectable_session_count: 120,
      selected_trading_days: ['2025-01-02', '2026-08-25'], selected_session_count: 2,
      selected_bar_count: 96, warmup_session_count: 0, warmup_bar_count: 0,
      parameters: {
        symbol: '600036', start: '2025-01-02', end: '2026-08-25', cost_bps: '5',
        short_window: 12, long_window: 48, volatility_window: 24,
        maximum_minute_volatility: '0.02', maximum_intraday_drawdown: '0.04',
        reduction_multiplier: '0.98', cooldown_bars: 6,
      },
      policy_id: 'adhoc-single-stock-five-minute-v1', policy_hash: 'b'.repeat(64),
      policy: {}, cost_bps: '5',
      metrics: {
        base: metric,
        overlay: { ...metric, total_return: '0.008', annualized_return: '0.25', annualized_volatility: '0.12', sharpe: '1.234' },
        excess_return: '-0.002', information_ratio: '-0.456',
        annualization: { observation_interval: '5m', bars_per_session: 48, sessions_per_year: 252, periods_per_year: 12096, risk_free_rate: '0' },
      },
      execution, daily: [],
      curve: [{ session: '2025-01-02T09:35:00+08:00', equity: '1.008', drawdown: '-0.001', benchmark_equity: '1.01', benchmark_drawdown: '-0.002', excess_return: '-0.002' }],
      causal_contract: {
        signal_observed_at: 'FIVE_MINUTE_BAR_CLOSE_T', exposure_change_at: 'NEXT_FIVE_MINUTE_BAR_OPEN',
        same_bar_signal_execution: false, reduction_only: true, maximum_exposure: '1',
        prior_history_used_only_as_causal_warmup: true,
      },
      point_in_time: false, future_adjustment_knowledge_possible: true,
      realtime_readiness: false, real_data_paper_readiness: false,
      state_mutation: false, external_order_transmission: false, preview_hash: 'c'.repeat(64),
    }
    vi.mocked(runSingleStockFiveMinuteBacktest).mockResolvedValueOnce(result)
    render(<BacktestPage data={data} />)
    expect(screen.getByRole('combobox', { name: '个股' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '开始交易日' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '结束交易日' })).toBeInTheDocument()
    expect(screen.getByText('当前分钟修正规则草案（未运行）')).toBeInTheDocument()
    expect(screen.getByText('TREND: SMA_CLOSE_12 < SMA_CLOSE_48 → EXPOSURE ≤ 0.98')).toBeInTheDocument()
    expect(screen.getByText('VOL: STDEV(RET_CLOSE, 24) ≥ 2.00% → EXPOSURE ≤ 0.98')).toBeInTheDocument()
    expect(screen.getByText('DRAWDOWN: CLOSE / SESSION_PEAK - 1 ≤ -4.00% → EXPOSURE = 0')).toBeInTheDocument()
    expect(screen.getByText('CLOCK: KNOWN_AT_5M_CLOSE_T → APPLY_NEXT_5M_OPEN; WARMUP ≥ 48 Bars; COST 5 bps / exposure change')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '运行个股 5 分钟回测' }))
    expect(await screen.findByText('已完成 600036 招商银行 · 2 日 / 96 Bar')).toBeInTheDocument()
    expect(runSingleStockFiveMinuteBacktest).toHaveBeenCalledWith({
      symbol: '600036', start: '2025-01-02', end: '2026-08-25', cost_bps: '5',
      short_window: 12, long_window: 48, volatility_window: 24,
      maximum_minute_volatility: '0.02', maximum_intraday_drawdown: '0.04',
      reduction_multiplier: '0.98', cooldown_bars: 6,
    })
    expect(screen.getByText('修正后年化收益')).toBeInTheDocument()
    expect(screen.getAllByText('25.00%').length).toBeGreaterThan(0)
    expect(screen.getByText('修正后年化波动')).toBeInTheDocument()
    expect(screen.getByText('12.00%')).toBeInTheDocument()
    expect(screen.getByText('修正后 Sharpe')).toBeInTheDocument()
    expect(screen.getAllByText('1.234').length).toBeGreaterThan(0)
    expect(screen.getAllByText('信息比率').length).toBeGreaterThan(0)
    expect(screen.getAllByText('-0.456').length).toBeGreaterThan(0)
    expect(screen.getByText(/按每年 252 个交易日、每日 48 根 5 分钟 Bar 年化/)).toBeInTheDocument()
    expect(screen.getByText(/实线为分钟修正策略，虚线为该个股 Buy-and-Hold/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /导出个股回测/ })).toBeInTheDocument()
    expect(screen.getByText('本次会话个股回测对比（1/30，覆盖 1 个板块）')).toBeInTheDocument()
    const minuteComparison = screen.getByText('本次会话个股回测对比（1/30，覆盖 1 个板块）').closest('.ant-card')
    expect(minuteComparison).not.toBeNull()
    const minuteComparisonTable = within(minuteComparison as HTMLElement)
    expect(minuteComparisonTable.getAllByText('年化收益').length).toBeGreaterThan(0)
    expect(minuteComparisonTable.getAllByText('信息比率').length).toBeGreaterThan(0)
    expect(minuteComparisonTable.getByText('银行')).toBeInTheDocument()
    expect(minuteComparisonTable.getByText(`${'c'.repeat(12)}…`)).toBeInTheDocument()
    expect(minuteComparisonTable.getByRole('button', { name: /导出该次/ })).toBeInTheDocument()
    const individualBacktestCard = screen.getByText('个股 5 分钟因果回测').closest('.ant-card')
    expect(individualBacktestCard).not.toBeNull()
    fireEvent.change(within(individualBacktestCard as HTMLElement).getByRole('spinbutton', { name: '单边成本（bps）' }), { target: { value: '6' } })
    expect(screen.getByText('CLOCK: KNOWN_AT_5M_CLOSE_T → APPLY_NEXT_5M_OPEN; WARMUP ≥ 48 Bars; COST 6 bps / exposure change')).toBeInTheDocument()
    expect(screen.queryByText('已完成 600036 招商银行 · 2 日 / 96 Bar')).not.toBeInTheDocument()
    expect(screen.getByText('本次会话个股回测对比（1/30，覆盖 1 个板块）')).toBeInTheDocument()
    fireEvent.click(minuteComparisonTable.getByRole('button', { name: '载入该次参数' }))
    expect(screen.queryByText('已完成 600036 招商银行 · 2 日 / 96 Bar')).not.toBeInTheDocument()
    expect(screen.getByText('本次会话个股回测对比（1/30，覆盖 1 个板块）')).toBeInTheDocument()
  })

  it('keeps frozen results but ignores retired template deep links', () => {
    window.location.hash = '#/backtest?template=etf-momentum-rotation-v1'
    render(<BacktestPage data={data} />)
    expect(screen.queryByLabelText('基线策略模板')).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '运行即时回测' })).not.toBeInTheDocument()
    expect(screen.queryByText('ExperimentRunSpec 哈希草案')).not.toBeInTheDocument()
    expect(screen.getByText('旧日线本地实验档案')).toBeInTheDocument()
    expect(screen.getByText('探索性领先者，不是 Champion')).toBeInTheDocument()
    expect(screen.getByText('个股 5 分钟因果回测')).toBeInTheDocument()
    window.location.hash = ''
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
  it('shows the signed 120-day intraday factor veto without claiming promotion', () => {
    render(<ResearchPage data={data} />)
    expect(screen.getByText('因子定义与数据契约目录 · 4')).toBeInTheDocument()
    expect(screen.getByText('只读证据索引，不是 FactorRegistry')).toBeInTheDocument()
    expect(screen.getByText('momentum-252-skip-20')).toBeInTheDocument()
    expect(screen.getByText('opening-three-ten-minute-bullish-v1')).toBeInTheDocument()
    expect(screen.getByText('120 日固定因子稳健性证据')).toBeInTheDocument()
    expect(screen.getByText('长期复核未通过：1/6 个测试折为正')).toBeInTheDocument()
    expect(screen.getByText('全窗口收益').nextSibling).toHaveTextContent('-27.17%')
    expect(screen.getAllByText('聚类信号日')[0].nextSibling).toHaveTextContent('120')
    expect(screen.getAllByText('Newey-West t')[0].nextSibling).toHaveTextContent('-0.480')
    expect(screen.getByText('聚类超额 95% 区间').nextSibling).toHaveTextContent('-0.29% ～ 0.18%')
    expect(screen.getAllByText('样本外收益')[0].nextSibling).toHaveTextContent('-28.31%')
    expect(screen.getByText('Bootstrap 超额中位').nextSibling).toHaveTextContent('-11.83%')
    expect(screen.getByText('Bootstrap 超额 90% 区间').nextSibling).toHaveTextContent('-23.91% ～ 0.43%')
    expect(screen.getByText('Bootstrap 超额为正概率').nextSibling).toHaveTextContent('5.70%')
    expect(screen.getByText(/10 → 10 → 5 → 5 → 5 → 5 bps/)).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /导出长期证据/ })).toBeInTheDocument()
    expect(screen.getByText(/PAPER NOT_ELIGIBLE/)).toBeInTheDocument()
    expect(screen.getByText(/外部订单 DISABLED/)).toBeInTheDocument()
    expect(screen.getByRole('tab', { name: '长期收益曲线' })).toBeInTheDocument()
    expect(screen.getByTestId('equity-chart')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: '时间稳定性 · 2/4' }))
    expect(screen.getByText('连续时间块 2/4 正向')).toBeInTheDocument()
    expect(screen.getByText(/四个块按日期固定且互不重叠/)).toBeInTheDocument()
    expect(screen.getByText('至少 75% 时间块条件超额为正')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: '滚动测试折 · 6' }))
    expect(screen.getByText('WF-01')).toBeInTheDocument()
    expect(screen.getAllByText('-5.00%').length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('tab', { name: '板块拆分 · 15' }))
    expect(screen.getByText('正收益板块 5/15')).toBeInTheDocument()
    expect(screen.getByText('板块15')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: '个股拆分 · 75' }))
    expect(screen.getByText('正收益个股 20/75')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('搜索长期因子个股'), { target: { value: '600074' } })
    expect(screen.getByText('600074')).toBeInTheDocument()
    expect(screen.queryByText('600000')).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: '研究硬门' }))
    expect(screen.getByText('Rank IC 为正')).toBeInTheDocument()
    expect(screen.getByText('最差固定阈值收益不为负')).toBeInTheDocument()
  })

  it('runs the full intraday factor gate without promoting a losing rule', async () => {
    const intradaySymbols = [
      '000001',
      ...Array.from({ length: 74 }, (_, index) => String(600001 + index)),
    ]
    const intradayInstruments = intradaySymbols.map((symbol, index) => ({
      symbol,
      instrument_type: 'STOCK' as const,
      name: `测试股票${index + 1}`,
      sector_code: String(801001 + Math.floor(index / 5)),
      sector_name: `板块${Math.floor(index / 5) + 1}`,
    }))
    const intradayData: PlatformSnapshot = {
      ...data,
      longFiveMinuteResearch: {
        ...data.longFiveMinuteResearch,
        minute_dataset: {
          ...data.longFiveMinuteResearch.minute_dataset!,
          trading_days: [
            '2026-07-28', '2026-07-29', '2026-07-30', '2026-07-31',
            '2026-08-03', '2026-08-04', '2026-08-05', '2026-08-06', '2026-08-07',
            '2026-08-10', '2026-08-11', '2026-08-12', '2026-08-13', '2026-08-14',
            '2026-08-17', '2026-08-18', '2026-08-19', '2026-08-20', '2026-08-21',
            '2026-08-24', '2026-08-25',
          ],
          eligible_symbols: intradaySymbols,
          instruments: intradayInstruments,
        },
      },
    }
    window.localStorage.clear()
    const selectedTradingDays = intradayData.longFiveMinuteResearch.minute_dataset!.trading_days.slice(0, 20)
    const strategyFinalEquity = 0.5913 ** (20 / 252)
    const benchmarkFinalEquity = strategyFinalEquity - 0.0025
    const strategyReturns = [
      -0.051,
      strategyFinalEquity / (0.949 * 1.001 ** 8) - 1,
      ...Array.from({ length: 8 }, () => 0.001),
      ...Array.from({ length: 10 }, () => 0),
    ]
    const benchmarkReturns = [
      -0.051,
      benchmarkFinalEquity / (0.949 * 1.0005 ** 8) - 1,
      ...Array.from({ length: 8 }, () => 0.0005),
      ...Array.from({ length: 10 }, () => 0),
    ]
    let strategyEquity = 1
    let benchmarkEquity = 1
    const curve = selectedTradingDays.map((tradingDay, index) => {
      strategyEquity *= 1 + strategyReturns[index]
      benchmarkEquity *= 1 + benchmarkReturns[index]
      return {
        trading_day: tradingDay,
        signal_count: index < 12 ? 10 : 9,
        eligible_symbol_count: 75,
        strategy_return: String(strategyReturns[index]),
        benchmark_return: String(benchmarkReturns[index]),
        strategy_equity: String(strategyEquity),
        benchmark_equity: String(benchmarkEquity),
        excess_equity: String(strategyEquity - benchmarkEquity),
      }
    })
    const currentBlockMeans = [0.0001, 0.0001, -0.0005, -0.0005]
    const currentIntradayStability: IntradayFactorBacktest['stability_validation'] = {
      classification: 'CONTIGUOUS_SESSION_BLOCK_FACTOR_STABILITY_DIAGNOSTIC',
      blocks: Array.from({ length: 4 }, (_, index) => {
        const blockCurve = curve.slice(index * 5, index * 5 + 5)
        return {
          block_id: `TS-${String(index + 1).padStart(2, '0')}`,
          start: selectedTradingDays[index * 5],
          end: selectedTradingDays[index * 5 + 4],
          session_count: 5,
          observation_count: 375,
          signal_count: blockCurve.reduce((total, point) => total + point.signal_count, 0),
          signal_session_count: 5,
          mean_session_conditional_excess_return: String(currentBlockMeans[index]),
        }
      }),
      block_count: 4,
      positive_block_count: 2,
      worst_block_mean_conditional_excess_return: '-0.0005',
      maximum_absolute_block_contribution_share: String(5 / 12),
      contribution_share_threshold: '0.60',
      criteria: {
        minimum_four_contiguous_blocks: true,
        every_block_has_signal_session: true,
        positive_block_ratio_at_least_75pct: false,
        maximum_absolute_block_contribution_share_below_0_60: true,
      },
      passed: false,
    }
    const average = (values: number[]) => values.reduce((total, value) => total + value, 0) / values.length
    const populationStandardDeviation = (values: number[]) => {
      const mean = average(values)
      return Math.sqrt(average(values.map((value) => (value - mean) ** 2)))
    }
    const baseCostBps = 5
    const removeCost = (value: number) => (1 + value) / (1 - baseCostBps / 10_000) ** 2 - 1
    const applyCost = (value: number, costBps: number) => (1 + value) * (1 - costBps / 10_000) ** 2 - 1
    const transactionCosts = (gross: number[], net: number[]) => (
      gross.reduce((total, value, index) => total + value - net[index], 0)
    )
    const performanceMetrics = (
      returns: number[],
      benchmarks: number[],
      turnover: number,
      exposure: number,
      trades: number,
      costs: number,
    ) => {
      let finalEquity = 1
      let finalBenchmarkEquity = 1
      let peak = 1
      let maximumDrawdown = 0
      for (const [index, value] of returns.entries()) {
        finalEquity *= 1 + value
        finalBenchmarkEquity *= 1 + benchmarks[index]
        peak = Math.max(peak, finalEquity)
        maximumDrawdown = Math.min(maximumDrawdown, finalEquity / peak - 1)
      }
      const annualizedReturn = finalEquity ** (252 / returns.length) - 1
      const annualizedVolatility = populationStandardDeviation(returns) * Math.sqrt(252)
      const annualizedAverageReturn = average(returns) * 252
      const downsideDeviation = Math.sqrt(average(returns.map((value) => Math.min(0, value) ** 2))) * Math.sqrt(252)
      const activeReturns = returns.map((value, index) => value - benchmarks[index])
      const trackingError = populationStandardDeviation(activeReturns) * Math.sqrt(252)
      return {
        total_return: String(finalEquity - 1),
        annualized_return: String(annualizedReturn),
        annualized_volatility: String(annualizedVolatility),
        sharpe: String(annualizedVolatility === 0 ? 0 : annualizedAverageReturn / annualizedVolatility),
        sortino: String(downsideDeviation === 0 ? 0 : annualizedAverageReturn / downsideDeviation),
        maximum_drawdown: String(maximumDrawdown),
        calmar: String(maximumDrawdown === 0 ? 0 : annualizedReturn / Math.abs(maximumDrawdown)),
        win_rate: String(returns.filter((value) => value > 0).length / returns.length),
        benchmark_return: String(finalBenchmarkEquity - 1),
        excess_return: String(finalEquity - finalBenchmarkEquity),
        information_ratio: String(trackingError === 0 ? 0 : average(activeReturns) * 252 / trackingError),
        number_of_periods: returns.length, turnover: String(turnover),
        profit_loss_ratio: '1.145', exposure: String(exposure), number_of_trades: trades,
        transaction_costs: String(costs),
      }
    }
    const grossStrategyReturns = strategyReturns.map(removeCost)
    const grossBenchmarkReturns = benchmarkReturns.map(removeCost)
    const metrics = performanceMetrics(
      strategyReturns,
      benchmarkReturns,
      40,
      1,
      192,
      transactionCosts(grossStrategyReturns, strategyReturns),
    )
    const benchmarkMetrics = performanceMetrics(
      benchmarkReturns,
      benchmarkReturns,
      40,
      1,
      1500,
      transactionCosts(grossBenchmarkReturns, benchmarkReturns),
    )
    const costStress = Object.fromEntries([0, 5, 10, 20].map((costBps) => {
      const stressedStrategy = grossStrategyReturns.map((value) => applyCost(value, costBps))
      const stressedBenchmark = grossBenchmarkReturns.map((value) => applyCost(value, costBps))
      return [String(costBps), {
        strategy_metrics: performanceMetrics(
          stressedStrategy,
          stressedBenchmark,
          40,
          1,
          192,
          transactionCosts(grossStrategyReturns, stressedStrategy),
        ),
        benchmark_metrics: performanceMetrics(
          stressedBenchmark,
          stressedBenchmark,
          40,
          1,
          1500,
          transactionCosts(grossBenchmarkReturns, stressedBenchmark),
        ),
      }]
    }))
    const positiveGroupMean = 0.001
    const negativeGroupMean = (-0.0013 * 192 - positiveGroupMean * 120) / 72
    const makeGroupMetrics = (signalCount: number, observationCount: number, positive: boolean) => {
      const activeDays = Math.min(signalCount, selectedTradingDays.length)
      const returns = selectedTradingDays.map((_, index) => (
        index < activeDays ? (positive ? 0.001 : -0.001) : 0
      ))
      const grossReturns = returns.map(removeCost)
      return {
        strategy_metrics: performanceMetrics(
          returns,
          benchmarkReturns,
          activeDays * 2,
          activeDays / selectedTradingDays.length,
          signalCount,
          transactionCosts(grossReturns, returns),
        ),
        benchmark_metrics: performanceMetrics(
          benchmarkReturns,
          benchmarkReturns,
          selectedTradingDays.length * 2,
          1,
          observationCount,
          transactionCosts(grossBenchmarkReturns, benchmarkReturns),
        ),
      }
    }
    const symbolGroups = intradayInstruments.map((instrument, index) => {
      const signalCount = index < 42 ? 3 : 2
      const hitCount = index < 70 ? 1 : 2
      const positive = Math.floor(index / 5) < 8
      return {
        group_id: instrument.symbol,
        group_name: instrument.symbol,
        observation_count: selectedTradingDays.length,
        signal_count: signalCount,
        mean_signal_forward_return: String(positive ? positiveGroupMean : negativeGroupMean),
        signal_hit_rate: String(hitCount / signalCount),
        ...makeGroupMetrics(signalCount, selectedTradingDays.length, positive),
      }
    })
    const sectorGroups = Array.from({ length: 15 }, (_, sectorIndex) => {
      const members = symbolGroups.slice(sectorIndex * 5, sectorIndex * 5 + 5)
      const signalCount = members.reduce((total, row) => total + row.signal_count, 0)
      const hitCount = members.reduce(
        (total, row) => total + Number(row.signal_hit_rate) * row.signal_count,
        0,
      )
      const positive = sectorIndex < 8
      return {
        group_id: String(801001 + sectorIndex),
        group_name: `板块${sectorIndex + 1}`,
        observation_count: selectedTradingDays.length * members.length,
        signal_count: signalCount,
        mean_signal_forward_return: String(positive ? positiveGroupMean : negativeGroupMean),
        signal_hit_rate: String(hitCount / signalCount),
        ...makeGroupMetrics(signalCount, selectedTradingDays.length * members.length, positive),
      }
    })
    const totalGroupHitCount = symbolGroups.reduce(
      (total, row) => total + Number(row.signal_hit_rate) * row.signal_count,
      0,
    )
    const totalGroupMean = symbolGroups.reduce(
      (total, row) => total + Number(row.mean_signal_forward_return) * row.signal_count,
      0,
    ) / 192
    const universeMean = -0.0012
    const nonSignalMean = (
      universeMean * 1500 - totalGroupMean * 192
    ) / (1500 - 192)
    const stitchedCurve = curve.slice(10).map((point) => ({
      ...point,
      strategy_equity: '1',
      benchmark_equity: '1',
      excess_equity: '0',
    }))
    const stitchedReturns = stitchedCurve.map(() => 0)
    const stitchedSignals = stitchedCurve.map((point) => point.signal_count)
    const stitchedSignalCount = stitchedSignals.reduce((total, value) => total + value, 0)
    const stitchedGrossReturns = stitchedReturns.map(removeCost)
    const stitchedStrategyMetrics = performanceMetrics(
      stitchedReturns,
      stitchedReturns,
      20,
      1,
      stitchedSignalCount,
      transactionCosts(stitchedGrossReturns, stitchedReturns),
    )
    const stitchedBenchmarkMetrics = performanceMetrics(
      stitchedReturns,
      stitchedReturns,
      20,
      1,
      750,
      transactionCosts(stitchedGrossReturns, stitchedReturns),
    )
    const robustnessFolds = [0, 1].map((foldIndex) => {
      const offset = foldIndex * 5
      const foldSignals = stitchedSignals.slice(offset, offset + 5)
      const foldSignalCount = foldSignals.reduce((total, value) => total + value, 0)
      const foldReturns = stitchedReturns.slice(offset, offset + 5)
      const foldGrossReturns = foldReturns.map(removeCost)
      const trainingCount = 10 + offset
      return {
        fold_id: `WF-${String(foldIndex + 1).padStart(2, '0')}`,
        training_start: selectedTradingDays[0],
        training_end: selectedTradingDays[trainingCount - 1],
        test_start: selectedTradingDays[10 + offset],
        test_end: selectedTradingDays[14 + offset],
        training_session_count: trainingCount,
        test_session_count: 5,
        selected_minimum_body_return_bps: '5',
        selection_known_at: `${selectedTradingDays[10 + offset]}T09:35:00+08:00`,
        training_signal_count: foldIndex === 0 ? 96 : 144,
        test_signal_count: foldSignalCount,
        training_strategy_metrics: performanceMetrics(
          Array.from({ length: trainingCount }, () => 0),
          Array.from({ length: trainingCount }, () => 0),
          trainingCount * 2,
          1,
          foldIndex === 0 ? 96 : 144,
          0,
        ),
        test_strategy_metrics: performanceMetrics(
          foldReturns,
          foldReturns,
          10,
          1,
          foldSignalCount,
          transactionCosts(foldGrossReturns, foldReturns),
        ),
        test_benchmark_metrics: performanceMetrics(
          foldReturns,
          foldReturns,
          10,
          1,
          375,
          transactionCosts(foldGrossReturns, foldReturns),
        ),
      }
    })
    const thresholdSensitivity = [
      { threshold: '0', signalCount: 100, loss: -0.001 },
      { threshold: '5', signalCount: 92, loss: -0.002 },
      { threshold: '10', signalCount: 80, loss: -0.003 },
      { threshold: '20', signalCount: 60, loss: -0.004 },
    ].map(({ threshold, signalCount, loss }) => {
      const returns = [loss, ...Array.from({ length: 9 }, () => 0)]
      const grossReturns = returns.map(removeCost)
      return {
        minimum_body_return_bps: threshold,
        signal_count: signalCount,
        signal_rate: String(signalCount / 750),
        strategy_metrics: performanceMetrics(
          returns,
          stitchedReturns,
          20,
          1,
          signalCount,
          transactionCosts(grossReturns, returns),
        ),
        benchmark_metrics: structuredClone(stitchedBenchmarkMetrics),
      }
    })
    const robustnessValidation: IntradayFactorBacktest['robustness_validation'] = {
      classification: 'REUSED_CHRONOLOGICAL_WALK_FORWARD_DIAGNOSTIC_NOT_INDEPENDENT',
      candidate_thresholds_bps: ['0', '5', '10', '20'],
      initial_training_session_count: 10,
      stitched_test_session_count: 10,
      folds: robustnessFolds,
      stitched_strategy_metrics: stitchedStrategyMetrics,
      stitched_benchmark_metrics: stitchedBenchmarkMetrics,
      stitched_curve: stitchedCurve,
      threshold_sensitivity: thresholdSensitivity,
      criteria: {
        minimum_two_disjoint_test_folds: true,
        every_test_fold_has_signal: true,
        causal_selection_known_before_test_signal: true,
        stitched_oos_total_return_positive: false,
        stitched_oos_excess_return_positive: false,
        positive_fold_ratio_at_least_60pct: false,
        threshold_sensitivity_majority_positive: false,
        worst_threshold_total_return_non_negative: false,
      },
      passed: false,
    }
    const strategySpecHash = 'ae3e57ed25bd3fe909cdf1e46e360c3e0da53ac9f52f32d4eeb104cbd06d2d8c'
    const strategyId = `adhoc-intraday-${strategySpecHash.slice(0, 16)}`
    const result = {
      classification: 'AD_HOC_INTRADAY_FACTOR_RESEARCH_CURRENT_NON_PIT',
      factor_definition: {
        factor_id: 'opening-three-ten-minute-bullish-v1', name: '09:30 起三根十分钟阳线',
        expression: 'bullish_10m_0930_0940 AND bullish_10m_0940_0950 AND bullish_10m_0950_1000',
        source_interval: 'NATIVE_5M_AGGREGATED_TO_10M', source_timestamp_convention: 'BAR_END_TIME', source_bars: '09:35,09:40,09:45,09:50,09:55,10:00',
        known_at: '10:00_BAR_CLOSE', minimum_body_return_bps: '0',
        directions: ['BULLISH', 'BULLISH', 'BULLISH'], combination: 'ALL',
        allowed_features: ['TEN_MINUTE_BODY_0930_0940', 'TEN_MINUTE_BODY_0940_0950', 'TEN_MINUTE_BODY_0950_1000'],
        arbitrary_code_execution: false,
      },
      parameters: {
        factor: 'custom_opening_ten_minute_direction_rule', start: '2026-07-28', end: '2026-08-24',
        cost_bps: '5', minimum_body_return_bps: '0',
        directions: ['BULLISH', 'BULLISH', 'BULLISH'], combination: 'ALL',
        exit_rule: 'NEXT_SESSION_THREE_TEN_MINUTE_BEARISH_ELSE_14_55_TIME_STOP',
        exit_minimum_body_return_bps: '5',
      },
      workflow: [
        { id: 'FACTOR_DEFINITION', status: 'AVAILABLE', detail: 'Exact expression and data contract are fixed for this run.' },
        { id: 'FACTOR_VALIDATION', status: 'FAILED', detail: 'Entry-factor tests and triggered-exit timing-advantage Newey-West tests completed.' },
        { id: 'CORRELATION_VALIDATION', status: 'PASSED', detail: 'Redundancy checks against related opening features completed.' },
        { id: 'ROBUSTNESS_VALIDATION', status: 'FAILED', detail: 'Expanding-window threshold selection and disjoint test folds completed without using future test returns.' },
        { id: 'STRATEGY_CONSTRUCTION', status: 'DIAGNOSTIC_ONLY', detail: 'Entry, exit, sizing, cost and benchmark rules are explicit, but strategy construction is not admitted because at least one prerequisite research stage failed.' },
        { id: 'CAUSAL_BACKTEST', status: 'FAILED', detail: '75-stock and 15-sector causal replay completed.' },
        { id: 'PAPER', status: 'NOT_ELIGIBLE', detail: 'No authorized forward realtime observation is attached.' },
        { id: 'LIVE', status: 'DISABLED', detail: 'Separate explicit user authorization is required.' },
      ],
      sequential_advancement: {
        classification: 'READ_ONLY_SEQUENTIAL_FACTOR_TO_STRATEGY_ADVANCEMENT',
        required_research_stages: [
          { stage_id: 'FACTOR_VALIDATION', passed: false },
          { stage_id: 'CORRELATION_VALIDATION', passed: true },
          { stage_id: 'ROBUSTNESS_VALIDATION', passed: false },
        ],
        passed_research_stage_count: 1, required_research_stage_count: 3,
        all_research_stages_passed: false, first_blocking_stage: 'FACTOR_VALIDATION',
        strategy_construction_status: 'DIAGNOSTIC_PREVIEW_ONLY', strategy_preview_generated: true,
        causal_backtest_diagnostic_completed: true, causal_backtest_passed: false,
        strategy_registration_allowed: false, historical_result_can_authorize_paper: false,
        viewed_window_reselection_allowed: false,
        next_allowed_action: 'PREDECLARE_NEW_HYPOTHESIS_BEFORE_NEW_EVIDENCE',
        state_mutation: false, external_order_transmission: false,
      },
      next_hypothesis_preregistration: {
        classification: 'READ_ONLY_NEXT_HYPOTHESIS_PREREGISTRATION_PREFLIGHT',
        status: 'REQUIRED_NOT_CREATED',
        current_result_binding: {
          strategy_id: strategyId,
          strategy_spec_hash: strategySpecHash,
          data_version: 'baostock-five-minute-long-v1',
          dataset_checksum: 'long-five-minute-checksum',
          viewed_evidence_period: { start: '2026-07-28', end: '2026-08-24' },
          first_blocking_stage: 'FACTOR_VALIDATION',
          next_allowed_action: 'PREDECLARE_NEW_HYPOTHESIS_BEFORE_NEW_EVIDENCE',
        },
        current_result_may_seed_motivation: true,
        current_result_may_seed_new_parameters: false,
        required_hypothesis_fields: ['hypothesis_id', 'motivation', 'economic_intuition', 'expected_regimes', 'expected_failure_modes', 'required_data', 'evaluation_criteria'],
        required_experiment_lock_fields: ['experiment_id', 'strategy_id', 'git_commit', 'data_version', 'universe', 'factor_set', 'label', 'split', 'model', 'parameters', 'cost_model_id', 'transaction_cost', 'seed'],
        required_governance_gates: [
          { gate_id: 'new_hypothesis_identity', required: true, requirement: 'Use a hypothesis_id not evaluated before.' },
          { gate_id: 'new_evidence_period_disjoint_from_viewed_and_prior_test_periods', required: true, requirement: 'Use a new evidence period disjoint from this viewed window and prior held-out test periods.' },
          { gate_id: 'independent_leakage_review_pass', required: true, requirement: 'Attach an independent leakage review PASS.' },
          { gate_id: 'fixed_candidate_budget', required: true, requirement: 'Fix the candidate budget before data access.' },
          { gate_id: 'fixed_robustness_scenarios', required: true, requirement: 'Fix bounded robustness scenarios before data access.' },
          { gate_id: 'fixed_cost_model', required: true, requirement: 'Fix the cost model before data access.' },
          { gate_id: 'immutable_spec_and_hash_before_data_access', required: true, requirement: 'Freeze the experiment specification and hash before new evidence is read.' },
          { gate_id: 'contamination_disclosure', required: true, requirement: 'Disclose all prior evidence used to motivate the new hypothesis.' },
        ],
        evidence_window_contract: {
          must_be_new_and_disjoint: true,
          current_viewed_period_excluded_from_new_confirmation: true,
          prior_test_period_reuse_for_candidate_selection_allowed: false,
        },
        strategy_registry_bridge: {
          preregistration_is_registry_insertion: false,
          registry_write_performed: false,
          earliest_status_after_completed_preregistration_and_leakage_review: 'IDEA',
          legal_lifecycle: ['IDEA', 'EXPERIMENT', 'CANDIDATE', 'VALIDATED', 'PAPER', 'SHADOW', 'APPROVED', 'CHAMPION'],
          human_authorization_required_for: ['APPROVED', 'CHAMPION'],
          current_lifecycle_status: 'NOT_REGISTERED',
        },
        preregistration_created: false,
        state_mutation: false,
        registry_write: false,
        paper_runtime_start: false,
        live_runtime_start: false,
        external_order_transmission: false,
      },
      strategy_registration_preflight: {
        classification: 'READ_ONLY_STRATEGY_REGISTRY_ADMISSION_PREFLIGHT',
        decision: 'BLOCKED',
        current_candidate: {
          strategy_id: strategyId,
          behavior_spec_hash: strategySpecHash,
          behavior_spec_hash_is_complete_registry_spec_hash: false,
          registration_status: 'AD_HOC_UNREGISTERED',
          registry_spec_hash_status: 'NOT_COMPUTABLE_REQUIRED_FIELDS_MISSING',
        },
        required_strategy_spec_fields: ['strategy_id', 'name', 'family', 'version', 'parent_strategy_id', 'git_commit', 'data_version', 'universe', 'factor_set', 'model', 'portfolio_rule', 'parameters', 'split', 'created_at', 'updated_at'],
        current_field_readiness: {
          preview_source_available_fields: ['strategy_id', 'data_version', 'universe', 'factor_set', 'portfolio_rule', 'parameters'],
          missing_registration_fields: ['name', 'family', 'version', 'parent_strategy_id', 'git_commit', 'model', 'split', 'created_at', 'updated_at'],
          available_field_count: 6, required_field_count: 15, complete: false,
        },
        required_gates: [
          { gate_id: 'all_ordered_research_stages_passed', passed: false, evidence: '1/3' },
          { gate_id: 'causal_backtest_passed', passed: false, evidence: 'backtest.passed=false' },
          { gate_id: 'signed_hypothesis_preregistration_attached', passed: false, evidence: 'NOT_ATTACHED' },
          { gate_id: 'independent_leakage_review_passed', passed: false, evidence: 'NOT_ATTACHED' },
          { gate_id: 'behavior_spec_hash_bound', passed: true, evidence: strategySpecHash },
          { gate_id: 'complete_registry_spec_and_hash_bound', passed: false, evidence: 'NOT_COMPUTABLE_REQUIRED_FIELDS_MISSING' },
          { gate_id: 'dataset_identity_bound', passed: true, evidence: 'baostock-five-minute-long-v1 · long-five-minute-checksum' },
          { gate_id: 'disjoint_time_split_frozen', passed: false, evidence: 'NOT_FROZEN' },
          { gate_id: 'lineage_root_or_parent_binding_declared', passed: false, evidence: 'NOT_DECLARED' },
        ],
        passed_gate_count: 2, required_gate_count: 9, all_required_gates_passed: false,
        earliest_registry_status: 'IDEA',
        idea_to_experiment_transition: {
          from: 'IDEA', to: 'EXPERIMENT', allowed: false,
          required_evidence: ['COMPLETED_EXPERIMENT_RUN_SPEC', 'QUALITY_PASSED_DATA_MANIFEST', 'INDEPENDENT_LEAKAGE_REVIEW_PASS'],
        },
        registration_operation_exposed: false, registry_write: false, state_mutation: false,
        paper_runtime_start: false, live_runtime_start: false, external_order_transmission: false,
      },
      factor_validation: {
        number_of_periods: 20, minimum_instruments: 75, ic: '0.0117', rank_ic: '-0.0030',
        icir: '0.0711', quantile_returns: [String(nonSignalMean), String(totalGroupMean)],
        factor_turnover: '0.589', long_short_spread: String(totalGroupMean - nonSignalMean),
        passed: false,
      },
      event_summary: {
        observation_count: 1500, signal_count: 192, signal_rate: '0.128',
        mean_signal_forward_return: String(totalGroupMean), mean_universe_forward_return: String(universeMean),
        conditional_excess_return: String(totalGroupMean - universeMean), signal_hit_rate: String(totalGroupHitCount / 192),
        positive_sector_count: 8, sector_count: 15,
        clustered_signal_session_count: 20,
        mean_session_conditional_excess_return: '-0.0002',
        newey_west_lag: 2, newey_west_standard_error: '0.0001',
        newey_west_t_statistic: '-2.0', confidence_interval_95_lower: '-0.000396',
        confidence_interval_95_upper: '-0.000004',
      },
      exit_factor_validation: {
        classification: 'TRIGGERED_EXIT_TIMING_ADVANTAGE_DIAGNOSTIC',
        advantage_definition: 'CONDITIONAL_EXIT_OPEN_DIVIDED_BY_TIME_STOP_OPEN_MINUS_ONE',
        entry_signal_count: 192, trigger_count: 28, trigger_rate: String(28 / 192),
        clustered_trigger_session_count: 10,
        mean_triggered_timing_advantage: '0.0019661',
        mean_session_triggered_timing_advantage: '0.0048978', trigger_hit_rate: String(18 / 28),
        newey_west_lag: 2, newey_west_standard_error: '0.0026726',
        newey_west_t_statistic: String(0.0048978 / 0.0026726),
        confidence_interval_95_lower: String(0.0048978 - 1.96 * 0.0026726),
        confidence_interval_95_upper: String(0.0048978 + 1.96 * 0.0026726),
        criteria: {
          minimum_twenty_exit_triggers: true,
          minimum_ten_clustered_exit_trigger_sessions: true,
          positive_mean_session_exit_timing_advantage: true,
          exit_timing_newey_west_t_statistic_above_1_96: false,
        },
        passed: false,
      },
      stability_validation: currentIntradayStability,
      correlation_validation: {
        correlations: { opening_30m_return: '0.4668', opening_30m_range_position: '0.4712' },
        maximum_absolute_correlation: '0.90', passed: true,
      },
      robustness_validation: robustnessValidation,
      strategy: {
        entry_factor_id: 'opening-three-ten-minute-bullish-v1',
        entry_condition: 'bullish_10m_0930_0940 AND bullish_10m_0940_0950 AND bullish_10m_0950_1000',
        entry_minimum_body_return_bps: '0', entry_directions: ['BULLISH', 'BULLISH', 'BULLISH'], entry_combination: 'ALL',
        entry_rule: 'NEXT_5M_OPEN_10_05', exit_rule: 'NEXT_SESSION_THREE_TEN_MINUTE_BEARISH_ELSE_14_55_TIME_STOP',
        exit_condition_type: 'FACTOR_THEN_TIME_STOP', exit_factor_id: 'next-session-three-ten-minute-bearish-v1',
        entry_price_source: 'OPEN_OF_BAR_LABELLED_10_05_INTERVAL_START_10_00',
        exit_condition: 'bearish_10m_0930_0940 AND bearish_10m_0940_0950 AND bearish_10m_0950_1000',
        exit_minimum_body_return_bps: '5', exit_condition_known_at: 'NEXT_SESSION_10_00_BAR_CLOSE_T_PLUS_ONE',
        conditional_exit_price_source: 'OPEN_OF_NEXT_SESSION_BAR_LABELLED_10_05_INTERVAL_START_10_00',
        fallback_exit_price_source: 'OPEN_OF_NEXT_SESSION_BAR_LABELLED_15_00_INTERVAL_START_14_55',
        conditional_exit_count: 28, fallback_time_stop_count: 164,
        position_rule: 'EQUAL_WEIGHT_TRIGGERED_STOCKS_ELSE_CASH',
        benchmark_rule: 'EQUAL_WEIGHT_ALL_ELIGIBLE_STOCKS_SAME_PER_SYMBOL_EXIT_RULE',
        single_side_cost_bps: '5', t_plus_one_compliant: true,
      },
      strategy_identity: {
        classification: 'AD_HOC_READ_ONLY_STRATEGY_IDENTITY',
        strategy_id: strategyId, spec_hash: strategySpecHash, spec_hash_scope: 'strategy_without_observed_exit_counts',
        registration_status: 'AD_HOC_UNREGISTERED', immutable_within_response: true,
        paper_eligible: false, promotion_authority: false, state_mutation: false, external_order_transmission: false,
      },
      backtest: {
        strategy_metrics: metrics, benchmark_metrics: benchmarkMetrics,
        cost_stress: costStress,
        curve,
        sectors: sectorGroups, symbols: symbolGroups, passed: false,
      },
      research_gate: {
        passed: false,
        criteria: {
          minimum_observation_count: true, minimum_signal_count: true,
          positive_mean_signal_return: false, positive_conditional_excess_return: false,
          minimum_ten_clustered_signal_sessions: true,
          newey_west_t_statistic_above_1_96: false,
          temporal_stability_passed: false,
          positive_rank_ic: false, positive_sector_ratio_at_least_60pct: false,
          maximum_absolute_factor_correlation_below_0_90: true,
          base_cost_total_return_positive: false, base_cost_excess_return_positive: true,
          walk_forward_oos_total_return_positive: false,
          walk_forward_oos_excess_return_positive: false,
          walk_forward_positive_fold_ratio_at_least_60pct: false,
          threshold_sensitivity_majority_positive: false,
          exit_factor_effectiveness_passed: false,
        },
      },
      causal_contract: {
        factor_known_at: 'THIRD_TEN_MINUTE_BAR_CLOSE_10_00', entry_at: 'NEXT_SOURCE_FIVE_MINUTE_BAR_OPEN_10_05',
        exit_condition_known_at: 'NEXT_SESSION_THIRD_TEN_MINUTE_BAR_CLOSE_10_00_T_PLUS_ONE',
        conditional_exit_at: 'NEXT_SOURCE_FIVE_MINUTE_BAR_OPEN_10_05_T_PLUS_ONE',
        fallback_exit_at: 'FINAL_SOURCE_FIVE_MINUTE_BAR_OPEN_14_55_T_PLUS_ONE', same_bar_execution: false,
        source_bar_timestamp_convention: 'BAR_END_TIME', entry_bar_label: '10_05', entry_interval_start: '10_00',
        conditional_exit_bar_label: 'NEXT_SESSION_10_05', conditional_exit_interval_start: 'NEXT_SESSION_10_00',
        fallback_exit_bar_label: 'NEXT_SESSION_15_00', fallback_exit_interval_start: 'NEXT_SESSION_14_55',
        t_plus_one_compliant: true,
      },
      paper_admission: {
        classification: 'READ_ONLY_INTRADAY_FACTOR_PAPER_ADMISSION', decision: 'BLOCKED',
        required_gates: [
          { gate_id: 'verified_historical_dataset_identity_bound', passed: true, evidence: 'baostock-five-minute-long-v1 · long-five-minute-checksum' },
          { gate_id: 'research_gate_passed', passed: false, evidence: 'research_gate.passed=false' },
          { gate_id: 'causal_backtest_passed', passed: false, evidence: 'backtest.passed=false' },
          { gate_id: 'registered_immutable_strategy_version_available', passed: false, evidence: `AD_HOC_UNREGISTERED · ${strategyId} · ${strategySpecHash}` },
          { gate_id: 'unseen_forward_shadow_evidence_attached', passed: false, evidence: 'NO_AUTHORIZED_FORWARD_PAPER_EVIDENCE' },
          { gate_id: 'authorized_realtime_provider_ready', passed: false, evidence: 'NO_AUTHORIZED_REALTIME_PROVIDER' },
          { gate_id: 'fresh_runtime_readiness_attestation_available', passed: false, evidence: 'NO_RUNTIME_ATTESTATION' },
          { gate_id: 'reconciliation_ready', passed: false, evidence: 'NO_BOUND_PAPER_RUNTIME' },
        ],
        passed_gate_count: 1, required_gate_count: 8, all_required_gates_passed: false,
        next_allowed_stage: 'PREDECLARE_NEW_HYPOTHESIS_BEFORE_NEW_EVIDENCE',
        automatic_start_allowed: false, runtime_mode_change: false,
        state_mutation: false, external_order_transmission: false,
      },
      l2_factor_readiness: {
        classification: 'READ_ONLY_L2_FACTOR_DATA_CONTRACT',
        factor_id: 'order-book-buying-intensity-v1',
        formula: '(bid_size_1+...+bid_size_5)/(ask_size_1+...+ask_size_5)',
        buying_hypothesis: {
          condition: 'buying_intensity > 1',
          interpretation: 'BUY_SIDE_QUANTITY_EXCEEDS_SELL_SIDE_QUANTITY',
          validation_status: 'UNTESTED_NO_AUTHORIZED_L2_DATA',
        },
        sell_condition_status: 'NOT_DEFINED_REQUIRES_SEPARATE_RESEARCH',
        required_data: 'AUTHORIZED_L2_FIVE_LEVEL_ORDER_BOOK', readiness: 'UNCONFIGURED',
        normalized_input_contract: {
          level_count: 5,
          bid_price_fields: ['bid_price_1', 'bid_price_2', 'bid_price_3', 'bid_price_4', 'bid_price_5'],
          bid_size_fields: ['bid_size_1', 'bid_size_2', 'bid_size_3', 'bid_size_4', 'bid_size_5'],
          ask_price_fields: ['ask_price_1', 'ask_price_2', 'ask_price_3', 'ask_price_4', 'ask_price_5'],
          ask_size_fields: ['ask_size_1', 'ask_size_2', 'ask_size_3', 'ask_size_4', 'ask_size_5'],
          size_unit: 'SHARES',
          required_identity_fields: ['symbol', 'exchange', 'provider_event_at', 'received_at', 'sequence'],
          allowed_session: 'CONTINUOUS_AUCTION',
        },
        invalid_snapshot_policy: {
          missing_or_non_finite_value: 'REJECT', negative_size: 'REJECT',
          zero_total_ask_size: 'REJECT_FACTOR_UNDEFINED', non_monotonic_or_crossed_book: 'REJECT',
          stale_snapshot: 'REJECT', future_snapshot: 'REJECT',
        },
        causal_contract: {
          decision_clock: 'FINALIZED_MINUTE_CORRECTION', snapshot_must_be_known_before_decision: true,
          same_symbol_snapshot: true, same_provider_snapshot: true, future_book_used: false,
          freshness_limit: 'REQUIRES_PROVIDER_CERTIFICATION',
        },
        required_gates: [
          { gate_id: 'authorized_l2_provider_selected', passed: false, reason: 'NO_AUTHORIZED_L2_PROVIDER' },
          { gate_id: 'five_level_schema_certified', passed: false, reason: 'PROVIDER_SCHEMA_NOT_CERTIFIED' },
          { gate_id: 'size_unit_normalized_to_shares', passed: false, reason: 'PROVIDER_SIZE_UNIT_NOT_CERTIFIED' },
          { gate_id: 'event_time_and_sequence_available', passed: false, reason: 'PROVIDER_CLOCK_NOT_CERTIFIED' },
          { gate_id: 'freshness_threshold_certified', passed: false, reason: 'PROVIDER_FRESHNESS_NOT_CERTIFIED' },
          { gate_id: 'recorded_replay_reconciliation_passed', passed: false, reason: 'NO_AUTHORIZED_RECORDED_L2_SESSION' },
        ],
        passed_gate_count: 0, required_gate_count: 6,
        reason: 'NO_AUTHORIZED_L2_PROVIDER', factor_effectiveness_tested: false,
        correlation_tested: false, strategy_backtested: false, derived_from_ohlcv: false,
        state_mutation: false, external_order_transmission: false,
      },
      data_version: 'baostock-five-minute-long-v1', dataset_checksum: 'long-five-minute-checksum',
      data_interval: '5m', selected_trading_days: selectedTradingDays, exit_completion_day: '2026-08-25',
      selected_session_count: 20, source_bar_count: 75600, symbol_count: 75, sector_count: 15,
      point_in_time: false, future_adjustment_knowledge_possible: true, realtime_readiness: false,
      promotion: 'NOT_ELIGIBLE', promotion_reasons: ['CURRENT_UNIVERSE_NON_PIT'],
      paper_readiness: 'NOT_ELIGIBLE', live_readiness: 'DISABLED_REQUIRES_SEPARATE_AUTHORIZATION',
      state_mutation: false, external_order_transmission: false, preview_hash: 'd'.repeat(64),
    } as unknown as IntradayFactorBacktest
    const unsignedResult = { ...result } as Record<string, unknown>
    delete unsignedResult.preview_hash
    const resultPreviewHash = await sha256Hex(canonicalJson(unsignedResult))
    if (resultPreviewHash === null) throw new Error('Web Crypto SHA-256 unavailable')
    result.preview_hash = resultPreviewHash
    vi.mocked(runIntradayFactorBacktest).mockResolvedValueOnce(result)
    render(<ResearchPage data={intradayData} />)
    fireEvent.mouseDown(screen.getByRole('combobox', { name: '因子' }))
    fireEvent.click(await screen.findByText('自定义开盘十分钟方向规则'))
    expect(screen.getByRole('combobox', { name: '第一段 09:30–09:40' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '第二段 09:40–09:50' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '第三段 09:50–10:00' })).toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '条件组合' })).toBeInTheDocument()
    expect(screen.getByText('受限规则，不执行任意表达式')).toBeInTheDocument()
    fireEvent.mouseDown(screen.getByRole('combobox', { name: '平仓规则' }))
    fireEvent.click(await screen.findByText('T+1 三根 10 分钟阴线退出，否则 14:55 时间退出'))
    fireEvent.change(screen.getByRole('spinbutton', { name: '每根退出阴线实体下限（bps）' }), { target: { value: '5' } })
    fireEvent.click(screen.getByRole('button', { name: '运行 75 股因子检验与回测' }))
    expect(await screen.findByText('策略回测响应身份已绑定')).toBeInTheDocument()
    expect(screen.getByText('本次回测没有因子检验手工交接血缘')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /导出因子到策略血缘/ })).not.toBeInTheDocument()
    expect(await screen.findByText('回测已完成，研究硬门未通过')).toBeInTheDocument()
    expect(screen.getByText('顺序晋级停在：有效性检验')).toBeInTheDocument()
    expect(screen.getByText('前置研究门 1/3')).toBeInTheDocument()
    expect(screen.getByText(/策略构造与因果回测只是诊断预览/)).toBeInTheDocument()
    expect(screen.getByText(/禁止在已查看窗口重选参数/)).toBeInTheDocument()
    expect(runIntradayFactorBacktest).toHaveBeenCalledWith(expect.objectContaining({
      factor: 'custom_opening_ten_minute_direction_rule', cost_bps: '5', minimum_body_return_bps: '0',
      directions: ['BULLISH', 'BULLISH', 'BULLISH'], combination: 'ALL',
      exit_rule: 'NEXT_SESSION_THREE_TEN_MINUTE_BEARISH_ELSE_14_55_TIME_STOP',
      exit_minimum_body_return_bps: '5',
    }))
    expect(screen.getByText(/实际触发 192\/1500 次/)).toBeInTheDocument()
    expect(screen.getByText('PAPER 模拟盘准入：BLOCKED')).toBeInTheDocument()
    expect(screen.getByText('已通过 1/8 项')).toBeInTheDocument()
    expect(screen.getByText(/下一允许动作：在接触新证据前预注册新假设/)).toBeInTheDocument()
    expect(screen.queryByText(/下一允许动作：注册策略/)).not.toBeInTheDocument()
    expect(screen.getByText('下一假设预注册：尚未创建')).toBeInTheDocument()
    expect(screen.getByText(/当前失败结果可以解释研究动机，但不能直接用于重选新参数/)).toBeInTheDocument()
    expect(screen.getByText('ResearchHypothesis 必填字段 7 个')).toBeInTheDocument()
    expect(screen.getByText('ExperimentRunSpec 冻结字段 13 个')).toBeInTheDocument()
    expect(screen.getByText('治理门槛 8 项')).toBeInTheDocument()
    expect(screen.getByText('当前注册表状态 NOT_REGISTERED')).toBeInTheDocument()
    expect(screen.getByText(/完成预注册与独立泄漏审查后，最早只能注册为 IDEA/)).toBeInTheDocument()
    expect(screen.getByText(/APPROVED、CHAMPION 仍需人工授权/)).toBeInTheDocument()
    expect(screen.getByText('策略注册前审查：BLOCKED')).toBeInTheDocument()
    expect(screen.getByText('注册门 2/9')).toBeInTheDocument()
    expect(screen.getByText('StrategySpec 字段 6/15')).toBeInTheDocument()
    expect(screen.getByText(/当前行为规格哈希不是完整 StrategySpec 哈希/)).toBeInTheDocument()
    expect(screen.getByText(/当前不能写入 IDEA，也不能迁移到 EXPERIMENT/)).toBeInTheDocument()
    expect(screen.getByText('完整注册规格与哈希已绑定')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /注册策略/ })).not.toBeInTheDocument()
    expect(screen.getByText('历史数据身份已验证并绑定')).toBeInTheDocument()
    expect(screen.getByText('不可变策略版本已注册')).toBeInTheDocument()
    expect(screen.getByText('AD_HOC_UNREGISTERED')).toBeInTheDocument()
    expect(screen.getByText('策略规格已冻结为只读预览，但未注册')).toBeInTheDocument()
    expect(screen.getByText(strategyId)).toBeInTheDocument()
    expect(screen.getByText('策略规格 SHA-256 已绑定')).toBeInTheDocument()
    expect(screen.getByText(/开仓因子、平仓规则、仓位、基准与成本已进入同一哈希/)).toBeInTheDocument()
    expect(screen.getAllByText('聚类信号日')[1].nextSibling).toHaveTextContent('20')
    expect(screen.getAllByText('Newey-West t')[1].nextSibling).toHaveTextContent('-2.000')
    expect(screen.getByText('95% 置信区间').nextSibling).toHaveTextContent('-0.04% ～ -0.00%')
    expect(screen.getAllByText('累计收益')[0].nextSibling).toHaveTextContent('-4.08%')
    fireEvent.click(screen.getByRole('tab', { name: '有效性与相关性' }))
    expect(screen.getByText('平仓因子有效性门未通过')).toBeInTheDocument()
    expect(screen.getByText(/触发 28 次、覆盖 10 个交易日/)).toBeInTheDocument()
    expect(screen.getByText(/五档买卖盘强度：UNCONFIGURED/)).toBeInTheDocument()
    expect(screen.getByText(/不会从 OHLCV 伪造买一至买五/)).toBeInTheDocument()
    expect(screen.getByText('买入假设 > 1 · 未检验')).toBeInTheDocument()
    expect(screen.getByText('卖出条件未定义')).toBeInTheDocument()
    expect(screen.getByText('规范深度字段 20 个')).toBeInTheDocument()
    expect(screen.getByText('零卖方总量：因子无定义')).toBeInTheDocument()
    expect(screen.getByText('L2 数据门 0/6')).toBeInTheDocument()
    expect(screen.getByText('获授权 L2 供应商已选择')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /导出因子研究证据/ })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: '滚动样本外与阈值' }))
    expect(screen.getByText('复用历史样本的因果诊断，不是新的独立证据')).toBeInTheDocument()
    expect(screen.getByText(/固定候选阈值 0\/5\/10\/20 bps/)).toBeInTheDocument()
    expect(screen.getByText('滚动样本外净值曲线')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('tab', { name: '成本压力' }))
    expect(screen.getByText('交易成本')).toBeInTheDocument()
    fireEvent.click(screen.getAllByRole('tab', { name: '时间稳定性 · 2/4' })[1])
    expect(screen.getAllByText('连续时间块 2/4 正向').length).toBeGreaterThan(0)
    expect(screen.getAllByText('单块绝对贡献占比低于 60%').length).toBeGreaterThan(0)
    fireEvent.click(screen.getByRole('tab', { name: '75 个股' }))
    expect(screen.getByText('当前结果个股 75/75')).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('搜索本次因子个股'), { target: { value: '000001' } })
    expect(screen.getByText('当前结果个股 1/75')).toBeInTheDocument()
    expect(screen.getByText('000001')).toBeInTheDocument()
    expect(screen.queryByText('600036')).not.toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('本地因子实验名称'), { target: { value: '三时段方向方案 A' } })
    fireEvent.click(screen.getByRole('button', { name: '保存到本地因子实验' }))
    expect(screen.getByText('已保存“三时段方向方案 A”；相同结果哈希会更新原记录。')).toBeInTheDocument()
    expect(screen.getByText('本地因子实验对比（1/30）')).toBeInTheDocument()
    expect(screen.getByText('三时段方向方案 A')).toBeInTheDocument()
    const localFactorComparison = screen.getByText('本地因子实验对比（1/30）').closest('.ant-card')
    expect(localFactorComparison).not.toBeNull()
    const factorComparison = within(localFactorComparison as HTMLElement)
    expect(factorComparison.getAllByText('年化收益').length).toBeGreaterThan(0)
    expect(factorComparison.getAllByText('主动超额').length).toBeGreaterThan(0)
    expect(factorComparison.getAllByText('Rank IC').length).toBeGreaterThan(0)
    expect(factorComparison.getAllByText('最大 |ρ|').length).toBeGreaterThan(0)
    expect(factorComparison.getAllByText('样本外超额').length).toBeGreaterThan(0)
    expect(factorComparison.getAllByText('NW 95% 区间').length).toBeGreaterThan(0)
    expect(factorComparison.getAllByText('策略身份').length).toBeGreaterThan(0)
    expect(factorComparison.getAllByText(strategyId).length).toBeGreaterThan(0)
    expect(factorComparison.getAllByText(`规格 ${strategySpecHash.slice(0, 12)}… · 未注册`).length).toBeGreaterThan(0)
    expect(factorComparison.getAllByText('-40.87%').length).toBeGreaterThan(0)
    expect(factorComparison.getAllByText('0.25%').length).toBeGreaterThan(0)
    expect(factorComparison.getAllByText('-0.003').length).toBeGreaterThan(0)
    expect(factorComparison.getAllByText('0.900').length).toBeGreaterThan(0)
    expect(factorComparison.getByRole('button', { name: /导出因子对比/ })).toBeEnabled()
    expect(factorComparison.getByText(/可导出确定性只读 JSON 留档或跨设备核对/)).toBeInTheDocument()
    expect(factorComparison.getByText(/策略身份仍是 AD_HOC_UNREGISTERED/)).toBeInTheDocument()
    fireEvent.change(screen.getByRole('spinbutton', { name: '每根 10 分钟实体下限（bps）' }), { target: { value: '1' } })
    expect(screen.queryByText('回测已完成，研究硬门未通过')).not.toBeInTheDocument()
    expect(screen.getByText('本地因子实验对比（1/30）')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '载入因子参数' }))
    expect(screen.getByText('已载入“三时段方向方案 A”的参数；请重新运行以验证当前结果。')).toBeInTheDocument()
    expect(screen.queryByText('回测已完成，研究硬门未通过')).not.toBeInTheDocument()
    expect(screen.getByRole('combobox', { name: '第一段 09:30–09:40' })).toBeInTheDocument()
    vi.mocked(runIntradayFactorBacktest).mockResolvedValueOnce({
      ...result,
      workflow: result.workflow.map((stage) => stage.id === 'FACTOR_VALIDATION'
        ? { ...stage, status: 'PASSED' }
        : stage),
    } as unknown as IntradayFactorBacktest)
    fireEvent.click(screen.getByRole('button', { name: '运行 75 股因子检验与回测' }))
    expect(await screen.findByText('策略回测响应身份或安全边界与请求不一致，结果已拒绝')).toBeInTheDocument()
    expect(screen.queryByText('回测已完成，研究硬门未通过')).not.toBeInTheDocument()
    const clearResponseError = () => {
      const threshold = screen.getByRole('spinbutton', { name: '每根 10 分钟实体下限（bps）' })
      fireEvent.change(threshold, { target: { value: '1' } })
      fireEvent.change(threshold, { target: { value: '0' } })
      expect(screen.queryByText('策略回测响应身份或安全边界与请求不一致，结果已拒绝')).not.toBeInTheDocument()
    }
    clearResponseError()
    vi.mocked(runIntradayFactorBacktest).mockResolvedValueOnce({
      ...result,
      causal_contract: {
        ...result.causal_contract,
        entry_at: 'SAME_BAR_OPEN',
      },
    } as unknown as IntradayFactorBacktest)
    fireEvent.click(screen.getByRole('button', { name: '运行 75 股因子检验与回测' }))
    expect(await screen.findByText('策略回测响应身份或安全边界与请求不一致，结果已拒绝')).toBeInTheDocument()
    expect(screen.queryByText('回测已完成，研究硬门未通过')).not.toBeInTheDocument()
    clearResponseError()
    vi.mocked(runIntradayFactorBacktest).mockResolvedValueOnce({
      ...result,
      sequential_advancement: {
        ...result.sequential_advancement,
        passed_research_stage_count: 2,
      },
    } as unknown as IntradayFactorBacktest)
    fireEvent.click(screen.getByRole('button', { name: '运行 75 股因子检验与回测' }))
    expect(await screen.findByText('策略回测响应身份或安全边界与请求不一致，结果已拒绝')).toBeInTheDocument()
    expect(screen.queryByText('回测已完成，研究硬门未通过')).not.toBeInTheDocument()
    clearResponseError()
    vi.mocked(runIntradayFactorBacktest).mockResolvedValueOnce({
      ...result,
      next_hypothesis_preregistration: {
        ...result.next_hypothesis_preregistration,
        required_governance_gates: result.next_hypothesis_preregistration.required_governance_gates.slice(0, 7),
      },
    } as unknown as IntradayFactorBacktest)
    fireEvent.click(screen.getByRole('button', { name: '运行 75 股因子检验与回测' }))
    expect(await screen.findByText('策略回测响应身份或安全边界与请求不一致，结果已拒绝')).toBeInTheDocument()
    expect(screen.queryByText('回测已完成，研究硬门未通过')).not.toBeInTheDocument()
    clearResponseError()
    vi.mocked(runIntradayFactorBacktest).mockResolvedValueOnce({
      ...result,
      l2_factor_readiness: {
        ...result.l2_factor_readiness,
        required_gates: result.l2_factor_readiness.required_gates.slice(0, 5),
        required_gate_count: 5,
      },
    } as unknown as IntradayFactorBacktest)
    fireEvent.click(screen.getByRole('button', { name: '运行 75 股因子检验与回测' }))
    expect(await screen.findByText('策略回测响应身份或安全边界与请求不一致，结果已拒绝')).toBeInTheDocument()
    expect(screen.queryByText('回测已完成，研究硬门未通过')).not.toBeInTheDocument()
    clearResponseError()
    vi.mocked(runIntradayFactorBacktest).mockResolvedValueOnce({
      ...result,
      paper_admission: {
        ...result.paper_admission,
        passed_gate_count: 7,
      },
    } as unknown as IntradayFactorBacktest)
    fireEvent.click(screen.getByRole('button', { name: '运行 75 股因子检验与回测' }))
    expect(await screen.findByText('策略回测响应身份或安全边界与请求不一致，结果已拒绝')).toBeInTheDocument()
    expect(screen.queryByText('回测已完成，研究硬门未通过')).not.toBeInTheDocument()
    clearResponseError()
    vi.mocked(runIntradayFactorBacktest).mockResolvedValueOnce({
      ...result,
      strategy_registration_preflight: {
        ...result.strategy_registration_preflight,
        passed_gate_count: 8,
      },
    } as unknown as IntradayFactorBacktest)
    fireEvent.click(screen.getByRole('button', { name: '运行 75 股因子检验与回测' }))
    expect(await screen.findByText('策略回测响应身份或安全边界与请求不一致，结果已拒绝')).toBeInTheDocument()
    expect(screen.queryByText('回测已完成，研究硬门未通过')).not.toBeInTheDocument()
  }, 20_000)

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
    expect(screen.getByText(/观测进度：1\/20 个完整交易日/)).toBeInTheDocument()
    expect(screen.getByText(/BaoStock 来源覆盖：75\/75 可正式采集/)).toBeInTheDocument()
    expect(screen.getByText(/0 只仍未形成完整目标日网格/)).toBeInTheDocument()
    expect(screen.getByText(/来源发布序列：6 个检查点/)).toBeInTheDocument()
    expect(screen.getByText(/41 → 47 → 52 → 69 → 74 → 75 READY/)).toBeInTheDocument()
    expect(screen.getByText(/不进入 0\/20/)).toBeInTheDocument()
    expect(screen.getByText('75 只股票均已形成完整来源网格')).toBeInTheDocument()
    expect(screen.getByText(/15 板块、75 股、每股 48 根原生 5 分钟 Bar/)).toBeInTheDocument()
    expect(screen.getAllByText('通过')).toHaveLength(5)
    expect(screen.getByText(/不宣称完全盲预注册/)).toBeInTheDocument()
    expect(screen.getByText(/不会自动授权 PAPER、LIVE 或外部下单/)).toBeInTheDocument()
  })
})

describe('verified experiment candidates', () => {
  it('retains historical candidates without showing the retired template catalog', () => {
    render(<StrategyPage data={data} />)
    expect(screen.getByRole('heading', { name: '历史实验候选' })).toBeInTheDocument()
    expect(screen.getByText('abc123def456')).toBeInTheDocument()
    expect(screen.queryByText(/已实现基线策略模板/)).not.toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '载入因果回测' })).not.toBeInTheDocument()
    for (const template of data.strategyTemplates.templates) {
      expect(screen.queryByText(template.name)).not.toBeInTheDocument()
    }
  })

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
