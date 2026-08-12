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

export interface PlatformSnapshot {
  system: SystemStatus
  health: HealthStatus
  account: { cash: string }
  pnl: { realized: string }
  positions: PositionRow[]
  orders: OrderRow[]
  metrics: MetricsSnapshot
  reports: DailyReportSummary[]
}
