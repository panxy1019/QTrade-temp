import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { PlatformSnapshot } from '../types'
import { cancelPaperOrder, controlRuntime, runBacktestPreview } from '../api/client'
import { BacktestPage, DataCenterPage, ExperimentPage, MarketPage, MonitorPage, OrdersPage, PaperPage, PositionsPage, ResearchPage, RiskPage, StrategyPage } from './DataPages'

vi.mock('../charts/ChartRenderer', () => ({ default: () => <div data-testid="equity-chart" /> }))
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
  runBacktestPreview: vi.fn(),
  controlRuntime: vi.fn(),
  cancelPaperOrder: vi.fn(),
}))

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
  reports: [{ report_id: 'paper-20260812', trading_day: '2026-08-12', generated_at: '2026-08-12T16:00:00+08:00', data_version: 'ticks-v1', strategy_versions: ['trend@1.0.0'], sha256: 'a'.repeat(64) }],
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
    expect(screen.getByText('已验证历史数据：AKShare 1.18.84')).toBeInTheDocument()
    expect(screen.getByText(/18960 根日线/)).toBeInTheDocument()
    expect(screen.getByText(/没有 Bid\/Ask、实时价格或实时延迟/)).toBeInTheDocument()
    expect(screen.getByText('以下是历史日线，不是实时行情')).toBeInTheDocument()
    expect(screen.getByText('510300')).toBeInTheDocument()
    expect(screen.getByText('200,000,000')).toBeInTheDocument()
    expect(screen.queryByText('Bid')).not.toBeInTheDocument()
    expect(await screen.findByText(/2\/2 个完整交易日/)).toBeInTheDocument()
    expect(screen.getByText(/图表来自同一 checksum 绑定的历史 Parquet/)).toBeInTheDocument()
  })

  it('filters verified historical rows without inventing snapshots', () => {
    cleanup()
    render(<MarketPage data={data} />)
    fireEvent.change(screen.getByPlaceholderText('搜索 ETF 代码'), { target: { value: '510500' } })
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
  })
})

describe('verified backtest analytics', () => {
  it('runs an adjustable preview without presenting it as promotion evidence', async () => {
    vi.mocked(runBacktestPreview).mockResolvedValueOnce({
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
      preview_hash: 'a'.repeat(64),
    })
    render(<BacktestPage data={data} />)
    fireEvent.click(screen.getByRole('button', { name: '运行即时回测' }))
    expect(await screen.findByText('已完成 632 个会话、30 个标的 · 动量')).toBeInTheDocument()
    expect(screen.getByText(/即时预览，不是策略晋级证据/)).toBeInTheDocument()
    expect(screen.getByText(/结论固定为 NOT_ELIGIBLE/)).toBeInTheDocument()
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
  })

  it('shows the verified leader without presenting it as Champion', async () => {
    cleanup()
    render(<BacktestPage data={data} />)
    expect(screen.getByText('探索性领先者，不是 Champion')).toBeInTheDocument()
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
