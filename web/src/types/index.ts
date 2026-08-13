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
  backtestAnalytics: BacktestAnalytics
  market: MarketStatus
  historicalBars: HistoricalBarRow[]
}
