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
  symbol: string
  status: string
  quantity: number
  filled_quantity: number
}

export interface PositionRow {
  symbol: string
  quantity: number
  average_cost: string
  realized_pnl: string
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
  family: string
  status: string
  role: string
  metrics: Record<string, string>
  scenario_type: string
  data_version: string
}

export interface ResearchExperiment {
  experiment_id: string
  spec_hash: string
  result_hash: string
  metrics: Record<string, string>
  scenario_type: string
}

export interface ResearchReport {
  classification: string
  data_version?: string
  git_commit?: string
  promotion: string
  promotion_reasons: string[]
  experiments: ResearchExperiment[]
}

export interface WalkForwardFold {
  fold: number
  selected_candidate_id: string
  validation_sharpe: string
  test_metrics: Record<string, string | number>
}

export interface WalkForwardReport {
  classification: string
  data_version?: string
  git_commit?: string
  promotion: string
  promotion_reasons: string[]
  result: {
    aggregate_metrics?: Record<string, string | number>
    fold_pass_ratio?: string
    folds: WalkForwardFold[]
    result_hash?: string
  }
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

export interface PlatformSnapshot {
  system: SystemStatus
  health: HealthStatus
  account: { cash: string }
  pnl: { realized: string }
  positions: PositionRow[]
  orders: OrderRow[]
  metrics: MetricsSnapshot
  reports: DailyReportSummary[]
  strategies: ResearchStrategyRow[]
  experiments: ResearchReport
  walkForward: WalkForwardReport
  market: MarketStatus
}
