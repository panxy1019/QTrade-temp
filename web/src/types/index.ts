export type RuntimeMode = 'BACKTEST' | 'PAPER' | 'SHADOW'
export type RiskState = 'NORMAL' | 'PAUSED' | 'REDUCE_ONLY' | 'HALTED'

export interface SystemStatus {
  mode: RuntimeMode
  running: boolean
  risk_state: RiskState
}

export interface HealthStatus {
  healthy: boolean
  runtime_mode: RuntimeMode
  risk_state: RiskState
  gateway_connected: boolean
  reconciliation_status: string
}

export interface OrderRow {
  internal_order_id: string
  broker_order_id: string | null
  strategy_id: string
  portfolio_id: string
  symbol: string
  side: 'BUY' | 'SELL'
  price: string
  status: string
  quantity: number
  filled_quantity: number
  rejection_reason: string | null
  created_at: string
  updated_at: string
}

export interface PositionRow {
  symbol: string
  quantity: number
  sellable_quantity: number | null
  as_of_date: string
  average_cost: string
  realized_pnl: string
  cost_basis_value: string
  market_price: string | null
  market_value: string | null
  unrealized_pnl: string | null
  valuation_status: 'MARKET_PRICE_UNAVAILABLE'
}

export interface PaperTradeRow {
  trade_id: string
  internal_order_id: string
  strategy_id: string | null
  portfolio_id: string | null
  symbol: string
  side: 'BUY' | 'SELL'
  price: string
  quantity: number
  fee: string
  timestamp: string
  execution_type: 'PAPER_SIMULATED_FILL'
}

export interface PaperRiskSummary {
  state: RiskState
  cash: string
  position_cost_basis: string
  open_buy_notional: string
  gross_exposure: string
  exposure_basis: 'COST_BASIS_PLUS_OPEN_BUY_LIMIT_PRICE'
  largest_position_concentration: string | null
  open_order_count: number
  realized_pnl: string
  drawdown: null
  valuation_status: 'MARKET_PRICE_UNAVAILABLE'
  gateway_connected: boolean
  reconciliation_status: string
}

export interface MetricsSnapshot {
  [key: string]: string | number | boolean
}

export interface DailyReportSummary {
  report_id: string
  trading_day: string
  generated_at: string
  data_version: string
  strategy_versions: string[]
  sha256: string
}

export interface ResearchStrategyRow {
  strategy_id: string
  name: string
  version: string
  family: string
  status: string
  role: string
  metrics: Record<string, string>
  scenario_type: string
  data_version: string
  git_commit: string
  evidence_status: string
  lineage_status: string
}

export interface ResearchExperiment {
  experiment_id: string
  spec_hash: string
  result_hash: string
  metrics: Record<string, string>
  scenario_type: string
  model?: string
  parameters?: Record<string, string>
  split?: { train_start: string; train_end: string; validation_start: string; validation_end: string; test_start: string; test_end: string }
  factor_set?: string[]
  transaction_cost?: string
  universe_size?: number
  git_commit?: string
  data_version?: string
  record_hash?: string
  evidence_status?: string
}

export interface ResearchReport {
  classification: string
  data_version?: string
  git_commit?: string
  promotion: string
  promotion_reasons: string[]
  experiments: ResearchExperiment[]
  lineage?: Array<{ parent: string; child: string }>
  lineage_status?: string
}

export interface WalkForwardFold {
  fold: number
  selected_candidate_id: string
  validation_sharpe: string
  split: {
    train_start: string
    train_end: string
    validation_start: string
    validation_end: string
    test_start: string
    test_end: string
  }
  test_metrics: Record<string, string | number>
}

export interface WalkForwardReport {
  classification: string
  data_version?: string
  git_commit?: string
  promotion: string
  promotion_reasons: string[]
  selection_contract?: {
    candidate_selection: string
    candidate_test_access_before_selection: boolean
    fold_ranges_disjoint: boolean
    execution_timing: string
  }
  result: {
    aggregate_metrics?: Record<string, string | number>
    fold_pass_ratio?: string
    folds: WalkForwardFold[]
    result_hash?: string
    source_result_hash?: string
  }
}

export interface RobustnessReport {
  classification: string
  promotion: string
  promotion_reasons: string[]
  result: {
    worst_sharpe?: string
    dispersion?: string
    any_negative_sharpe?: boolean
    scenario_metrics: Array<[string, Array<[string, string]>]>
    result_hash?: string
  }
}

export interface FactorSearchReport {
  classification: string
  promotion: string
  promotion_reasons: string[]
  factor_selection?: {
    passing_factor_ids: string[]
    discovery_start: string
    discovery_end: string
  }
  portfolio_selection?: {
    selected_candidate_id: string
    validation_start: string
    validation_end: string
    benchmark_excluded_from_active_selection: boolean
  }
  confirmatory_test?: {
    start: string
    end: string
    period_previously_viewed_by_project: boolean
  }
  factor_diagnostics: Array<{
    factor_id: string
    periods: number
    instruments_minimum: number
    metrics: Record<string, string | string[]>
  }>
  validation: {
    selected_candidate_id?: string
    candidate_count?: number
    scores: Array<[string, string]>
  }
  test: {
    selected_candidate_id?: string
    selected_metrics?: Record<string, string | number>
    buy_hold_metrics?: Record<string, string | number>
    terminal_excess_return?: string
  }
}

export interface RobustFactorSearchReport {
  classification: string
  promotion: string
  promotion_reasons: string[]
  selection?: {
    candidate_count: number
    window_ids: string[]
    best_observed_candidate_id: string
    window_gate_eligible_candidate_ids: string[]
    robust_candidate_id: string | null
    audited_candidate_id: string
  }
  robustness_gate?: {
    passed: boolean
    conclusion: string
    candidate_results: Array<{
      candidate_id: string
      passed: boolean
      criteria: Array<[string, boolean]>
    }>
  }
  candidate_summaries: Array<{
    candidate_id: string
    selection_rank: number
    positive_window_ratio: string
    median_window_sharpe: string
    worst_window_sharpe: string
    full_period_return: string
  }>
  stress_tests: {
    evaluations: Array<{
      candidate_id: string
      cost_stresses: Array<{
        scenario_id: string
        metrics: Record<string, string | number>
      }>
      parameter_neighbors: { positive_ratio: string }
      universe_subsets: { positive_ratio: string }
    }>
  }
  bootstrap: {
    evaluations: Array<{
      candidate_id: string
      probability_positive: string
      p05_total_return: string
      median_total_return: string
      p95_total_return: string
    }>
  }
  polluted_audit: {
    classification?: string
    audited_candidate_id?: string
    selected_metrics?: Record<string, string | number>
    buy_hold_metrics?: Record<string, string | number>
    terminal_excess_return?: string
  }
}

export interface StockSectorTransferReport {
  classification: string
  promotion: string
  promotion_reasons: string[]
  data?: {
    bar_count: number
    symbol_count: number
    sector_count: number
    stocks_per_sector: number
    adjustment: string
    point_in_time_universe: boolean
  }
  locked_strategy?: {
    candidate_id: string
    parameter_tuning_on_stock_results: boolean
  }
  formal_period?: {
    start: string
    end: string
    full_metrics: Record<string, string | number>
    positive_year_count: number
    positive_sector_only_count: number
    positive_leave_one_sector_out_count: number
    doubled_cost_metrics: Record<string, string | number>
  }
  transfer_gate?: {
    passed: boolean
    criteria: Record<string, boolean>
  }
  yearly: Array<{
    year: number
    metrics: Record<string, string | number>
  }>
  sector_only: Array<{
    sector_code: string
    sector_name: string
    symbols: string[]
    metrics: Record<string, string | number>
  }>
  leave_one_sector_out: Array<{
    excluded_sector_code: string
    excluded_sector_name: string
    metrics: Record<string, string | number>
  }>
  cost_stress: {
    base?: Record<string, string | number>
    double?: Record<string, string | number>
  }
  bootstrap: {
    probability_positive?: string
    p05_total_return?: string
    median_total_return?: string
    p95_total_return?: string
  }
  polluted_audit: {
    strategy_metrics?: Record<string, string | number>
    equal_weight_metrics?: Record<string, string | number>
    terminal_excess_return?: string
  }
}

export interface BacktestAnalytics {
  classification: string
  promotion: string
  data_version?: string
  git_commit?: string
  leader: ResearchExperiment | null
  benchmark?: ResearchExperiment | null
  split?: { test_start: string; test_end: string }
  record_hash?: string
  artifact_hashes?: { equity: string; drawdown: string; trades: string; positions: string; risk: string; logs: string }
  curve: Array<{ session: string; equity: string; drawdown: string; benchmark_equity?: string; benchmark_drawdown?: string; excess_return?: string }>
  monthly_returns?: Array<{ month: string; return: string }>
  trades?: Array<{ symbol: string; side: 'BUY' | 'SELL'; trading_day: string; quantity: number; price: string; cost: string; notional: string }>
  positions?: Array<{ symbol: string; quantity: number }>
  risk_summary?: { maximum_drawdown: string; transaction_costs: string; turnover: string }
  run_metadata?: { engine: string; timing: string; session_count: number }
}

export interface BacktestPreviewRequest {
  start: string
  end: string
  factor: 'momentum' | 'reversal' | 'low_volatility'
  horizons: number[]
  top_k: number
  rebalance_every: number
  reserve: string
  trend_window: number | null
  volatility_window: number | null
  target_annual_volatility: string | null
  initial_cash: string
  transaction_cost_rate: string
  slippage_rate: string
}

export interface BacktestPreview {
  classification: 'AD_HOC_PREVIEW_CURRENT_UNIVERSE_NOT_PIT'
  promotion: 'NOT_ELIGIBLE'
  promotion_reasons: string[]
  data_version: string
  data_checksum: string
  parameters: BacktestPreviewRequest
  session_count: number
  universe_size: number
  metrics: Record<string, string>
  curve: Array<{ session: string; equity: string; drawdown: string }>
  terminal_positions: Array<{ symbol: string; quantity: number }>
  execution_timing: 'close decision, next open execution'
  preview_hash: string
}

export interface HistoricalDataset {
  provider: string
  provider_version: string
  classification: string
  data_version: string
  quality_status: string
  checksum: string
  bar_count: number
  observed_start: string
  observed_end: string
  snapshot_readiness: boolean
  real_data_paper_readiness: boolean
  universe: { point_in_time: boolean; symbols: string[] }
}

export interface MarketStatus {
  provider_connected: boolean
  execution_gateway_connected: boolean
  mode: RuntimeMode
  snapshot_readiness: boolean
  historical: HistoricalDataset | null
}

export interface HistoricalBarRow {
  symbol: string
  exchange: string
  timestamp: string
  trading_day: string
  open: string
  high: string
  low: string
  close: string
  volume: string
  data_version: string
  observation_type: 'HISTORICAL_DAILY_CLOSE' | 'HISTORICAL_DAILY_BAR'
}

export interface HistoricalBarSeries {
  symbol: string
  data_version: string | null
  dataset_checksum: string | null
  observation_type: 'HISTORICAL_DAILY_BAR_SERIES'
  total_count: number
  truncated: boolean
  bars: HistoricalBarRow[]
}

export interface MinuteOverlaySymbolStatus {
  symbol: string
  ready: boolean
  trading_day?: string
  last_bar_timestamp: string
  base_target_quantity: number
  corrected_target_quantity: number
  multiplier: string
  reasons: string[]
  minute_volatility?: string | null
  intraday_drawdown?: string
  cooldown_bars_remaining?: number
  strategy_version?: string | null
}

export interface MinuteOverlayStatus {
  classification: 'CAUSAL_MINUTE_RISK_OVERLAY_PAPER_SHADOW_ONLY'
  readiness: 'UNCONFIGURED' | 'NOT_READY' | 'READY' | 'STATUS_UNAVAILABLE'
  provider: string | null
  provider_connected: boolean
  bar_interval: '1m'
  symbol_count: number
  ready_symbol_count: number
  external_order_transmission: false
  promotion: 'NOT_ELIGIBLE'
  reasons: string[]
  symbols: MinuteOverlaySymbolStatus[]
}

export interface PlatformSnapshot {
  system: SystemStatus
  health: HealthStatus
  account: { cash: string; equity: null; valuation_status: 'MARKET_PRICE_UNAVAILABLE' }
  pnl: { realized: string }
  positions: PositionRow[]
  orders: OrderRow[]
  trades: PaperTradeRow[]
  risk: PaperRiskSummary
  metrics: MetricsSnapshot
  reports: DailyReportSummary[]
  strategies: ResearchStrategyRow[]
  experiments: ResearchReport
  walkForward: WalkForwardReport
  robustness: RobustnessReport
  factorSearch: FactorSearchReport
  robustFactorSearch: RobustFactorSearchReport
  stockSectorTransfer: StockSectorTransferReport
  minuteOverlay: MinuteOverlayStatus
  backtestAnalytics: BacktestAnalytics
  market: MarketStatus
  historicalBars: HistoricalBarRow[]
  audit: Array<{
    timestamp: string
    event_type: string
    severity: 'INFO' | 'WARNING' | 'ERROR' | 'CRITICAL'
    message: string
    order_id: string | null
  }> | null
}
