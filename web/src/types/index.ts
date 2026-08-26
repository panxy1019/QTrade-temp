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

export interface DeploymentProvenance {
  classification: 'PUBLIC_BUILD_PROVENANCE'
  provider: 'VERCEL' | 'LOCAL'
  status: 'AVAILABLE' | 'UNAVAILABLE'
  target_environment: string | null
  git_commit_sha: string | null
  git_commit_ref: string | null
  external_order_transmission: false
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
    full_period_sharpe?: string
    full_period_turnover?: string
    full_period_transaction_costs?: string
    median_window_return?: string
    sharpe_dispersion?: string
    window_count?: number
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
  external_order_transmission: false
  preview_hash: string
}

export interface SingleStockFiveMinuteBacktestRequest {
  symbol: string
  start: string
  end: string
  cost_bps: string
  short_window: number
  long_window: number
  volatility_window: number
  maximum_minute_volatility: string
  maximum_intraday_drawdown: string
  reduction_multiplier: string
  cooldown_bars: number
}

export interface SingleStockFiveMinuteMetric {
  total_return: string
  maximum_drawdown: string
  realized_minute_volatility: string
  point_count: number
}

export interface SingleStockFiveMinuteExecution {
  symbol_count: number
  observation_count: number
  reduced_exposure_observation_count: number
  exposure_sum: string
  mean_exposure: string
  reduced_exposure_ratio: string
  turnover: string
  mean_turnover_per_symbol: string
  transaction_cost: string
  mean_transaction_cost_per_symbol: string
  reason_counts: Record<string, number>
}

export interface SingleStockFiveMinuteBacktest {
  classification: 'AD_HOC_SINGLE_STOCK_FIVE_MINUTE_BACKTEST_CURRENT_NON_PIT'
  promotion: 'NOT_ELIGIBLE'
  promotion_reasons: string[]
  symbol: string
  instrument: { name: string; sector_code: string; sector_name: string }
  data_version: string
  dataset_checksum: string
  data_interval: '5m'
  verified_universe_symbol_count: number
  available_session_count: number
  available_start: string
  available_end: string
  maximum_selectable_session_count: number
  selected_trading_days: string[]
  selected_session_count: number
  selected_bar_count: number
  warmup_session_count: number
  warmup_bar_count: number
  parameters: SingleStockFiveMinuteBacktestRequest
  policy_id: string
  policy_hash: string
  policy: Record<string, string>
  cost_bps: string
  metrics: {
    base: SingleStockFiveMinuteMetric
    overlay: SingleStockFiveMinuteMetric
    excess_return: string
  }
  execution: SingleStockFiveMinuteExecution
  daily: Array<{
    trading_day: string
    base: SingleStockFiveMinuteMetric
    overlay: SingleStockFiveMinuteMetric
    excess_return: string
    execution: SingleStockFiveMinuteExecution
  }>
  curve: Array<{
    session: string
    equity: string
    drawdown: string
    benchmark_equity: string
    benchmark_drawdown: string
    excess_return: string
  }>
  causal_contract: {
    signal_observed_at: 'FIVE_MINUTE_BAR_CLOSE_T'
    exposure_change_at: 'NEXT_FIVE_MINUTE_BAR_OPEN'
    same_bar_signal_execution: false
    reduction_only: true
    maximum_exposure: string
    prior_history_used_only_as_causal_warmup: true
  }
  point_in_time: false
  future_adjustment_knowledge_possible: true
  realtime_readiness: false
  real_data_paper_readiness: false
  state_mutation: false
  external_order_transmission: false
  preview_hash: string
}

export interface IntradayFactorBacktestRequest {
  factor: 'opening_three_ten_minute_bullish' | 'custom_opening_ten_minute_direction_rule'
  start: string
  end: string
  cost_bps: string
  minimum_body_return_bps: string
  directions: [IntradayOpeningDirection, IntradayOpeningDirection, IntradayOpeningDirection]
  combination: 'ALL' | 'ANY'
}

export type IntradayOpeningDirection = 'BULLISH' | 'BEARISH' | 'IGNORE'

export interface IntradayFactorCorrelationRule {
  directions: [IntradayOpeningDirection, IntradayOpeningDirection, IntradayOpeningDirection]
  combination: 'ALL' | 'ANY'
}

export interface IntradayFactorCorrelationRequest {
  start: string
  end: string
  rules: IntradayFactorCorrelationRule[]
}

export interface IntradayFactorCorrelation {
  classification: 'AD_HOC_INTRADAY_FACTOR_CORRELATION_CURRENT_NON_PIT'
  parameters: IntradayFactorCorrelationRequest
  score_contract: {
    source_interval: 'NATIVE_5M_AGGREGATED_TO_10M'
    source_timestamp_convention: 'BAR_END_TIME'
    source_bars: string
    known_at: '10:00_BAR_CLOSE'
    score_definition: 'ALL_MIN_OR_ANY_MAX_OF_SIGNED_TEN_MINUTE_BODY_RETURNS'
    binary_signal_used: false
    future_features_used: false
    arbitrary_code_execution: false
  }
  rules: Array<IntradayFactorCorrelationRule & {
    rule_id: string
    factor_id: string
    name: string
    expression: string
  }>
  observation_count: number
  matrix: Array<{
    rule_id: string
    correlations: Record<string, string>
  }>
  pairs: Array<{
    left_rule_id: string
    right_rule_id: string
    correlation: string
    redundant: boolean
  }>
  redundancy_threshold: string
  maximum_absolute_correlation: string
  passed: boolean
  data_version: string
  dataset_checksum: string
  data_interval: '5m'
  selected_trading_days: string[]
  exit_completion_day: string
  source_bar_count: number
  symbol_count: number
  sector_count: number
  point_in_time: false
  future_adjustment_knowledge_possible: true
  realtime_readiness: false
  promotion: 'NOT_ELIGIBLE'
  promotion_reasons: string[]
  paper_readiness: 'NOT_ELIGIBLE'
  live_readiness: 'DISABLED_REQUIRES_SEPARATE_AUTHORIZATION'
  state_mutation: false
  external_order_transmission: false
  preview_hash: string
}

export interface IntradayPerformanceMetrics {
  total_return: string
  annualized_return: string
  annualized_volatility: string
  sharpe: string
  sortino: string
  maximum_drawdown: string
  calmar: string
  win_rate: string
  benchmark_return: string
  excess_return: string
  information_ratio: string
  number_of_periods: number
  turnover: string
  profit_loss_ratio: string
  exposure: string
  number_of_trades: number
  transaction_costs: string
}

export interface IntradayFactorGroup {
  group_id: string
  group_name: string
  observation_count: number
  signal_count: number
  mean_signal_forward_return: string
  signal_hit_rate: string
  strategy_metrics: IntradayPerformanceMetrics
  benchmark_metrics: IntradayPerformanceMetrics
}

export interface IntradayFactorBacktest {
  classification: 'AD_HOC_INTRADAY_FACTOR_RESEARCH_CURRENT_NON_PIT'
  factor_definition: {
    factor_id: string
    name: string
    expression: string
    source_interval: string
    source_timestamp_convention: 'BAR_END_TIME'
    source_bars: string
    known_at: string
    minimum_body_return_bps: string
    directions: [IntradayOpeningDirection, IntradayOpeningDirection, IntradayOpeningDirection]
    combination: 'ALL' | 'ANY'
    allowed_features: string[]
    arbitrary_code_execution: false
  }
  parameters: IntradayFactorBacktestRequest
  workflow: Array<{ id: string; status: string; detail: string }>
  factor_validation: {
    number_of_periods: number
    minimum_instruments: number
    ic: string
    rank_ic: string
    icir: string
    quantile_returns: string[]
    factor_turnover: string
    long_short_spread: string
    passed: boolean
  }
  event_summary: {
    observation_count: number
    signal_count: number
    signal_rate: string
    mean_signal_forward_return: string
    mean_universe_forward_return: string
    conditional_excess_return: string
    signal_hit_rate: string
    positive_sector_count: number
    sector_count: number
    clustered_signal_session_count: number
    mean_session_conditional_excess_return: string
    newey_west_lag: number
    newey_west_standard_error: string
    newey_west_t_statistic: string
    confidence_interval_95_lower: string
    confidence_interval_95_upper: string
  }
  correlation_validation: {
    correlations: Record<string, string>
    maximum_absolute_correlation: string
    passed: boolean
  }
  robustness_validation: {
    classification: 'REUSED_CHRONOLOGICAL_WALK_FORWARD_DIAGNOSTIC_NOT_INDEPENDENT'
    candidate_thresholds_bps: string[]
    initial_training_session_count: number
    stitched_test_session_count: number
    folds: Array<{
      fold_id: string
      training_start: string
      training_end: string
      test_start: string
      test_end: string
      training_session_count: number
      test_session_count: number
      selected_minimum_body_return_bps: string
      selection_known_at: string
      training_signal_count: number
      test_signal_count: number
      training_strategy_metrics: IntradayPerformanceMetrics
      test_strategy_metrics: IntradayPerformanceMetrics
      test_benchmark_metrics: IntradayPerformanceMetrics
    }>
    stitched_strategy_metrics: IntradayPerformanceMetrics
    stitched_benchmark_metrics: IntradayPerformanceMetrics
    stitched_curve: Array<{
      trading_day: string
      signal_count: number
      eligible_symbol_count: number
      strategy_return: string
      benchmark_return: string
      strategy_equity: string
      benchmark_equity: string
      excess_equity: string
    }>
    threshold_sensitivity: Array<{
      minimum_body_return_bps: string
      signal_count: number
      signal_rate: string
      strategy_metrics: IntradayPerformanceMetrics
      benchmark_metrics: IntradayPerformanceMetrics
    }>
    criteria: Record<string, boolean>
    passed: boolean
  }
  strategy: {
    entry_rule: string
    exit_rule: string
    entry_price_source: string
    exit_price_source: string
    position_rule: string
    benchmark_rule: string
    single_side_cost_bps: string
    t_plus_one_compliant: true
  }
  backtest: {
    strategy_metrics: IntradayPerformanceMetrics
    benchmark_metrics: IntradayPerformanceMetrics
    cost_stress: Record<string, {
      strategy_metrics: IntradayPerformanceMetrics
      benchmark_metrics: IntradayPerformanceMetrics
    }>
    curve: Array<{
      trading_day: string
      signal_count: number
      eligible_symbol_count: number
      strategy_return: string
      benchmark_return: string
      strategy_equity: string
      benchmark_equity: string
      excess_equity: string
    }>
    sectors: IntradayFactorGroup[]
    symbols: IntradayFactorGroup[]
    passed: boolean
  }
  research_gate: { passed: boolean; criteria: Record<string, boolean> }
  causal_contract: {
    factor_known_at: string
    entry_at: string
    exit_at: string
    source_bar_timestamp_convention: 'BAR_END_TIME'
    entry_bar_label: '10_05'
    entry_interval_start: '10_00'
    exit_bar_label: 'NEXT_SESSION_09_35'
    exit_interval_start: 'NEXT_SESSION_09_30'
    same_bar_execution: false
    t_plus_one_compliant: true
  }
  l2_factor_readiness: {
    factor_id: string
    formula: string
    required_data: string
    readiness: 'UNCONFIGURED'
    reason: string
    derived_from_ohlcv: false
  }
  data_version: string
  dataset_checksum: string
  data_interval: '5m'
  selected_trading_days: string[]
  exit_completion_day: string
  selected_session_count: number
  source_bar_count: number
  symbol_count: number
  sector_count: number
  point_in_time: false
  future_adjustment_knowledge_possible: true
  realtime_readiness: false
  promotion: 'NOT_ELIGIBLE'
  promotion_reasons: string[]
  paper_readiness: 'NOT_ELIGIBLE'
  live_readiness: 'DISABLED_REQUIRES_SEPARATE_AUTHORIZATION'
  state_mutation: false
  external_order_transmission: false
  preview_hash: string
}

export interface IntradayFactorLongWindowReport {
  schema_version: 1
  report_id: 'opening-triple-bullish-long-window-robustness-v1' | null
  classification: string
  generated_at?: string
  dataset?: {
    data_version: string
    checksum: string
    evidence_sha256: string
    bar_interval: '5m'
    session_count: number
    symbol_count: number
    sector_count: number
    point_in_time: false
    future_adjustment_knowledge_possible: true
  }
  evaluation_contract?: {
    selected_session_count: 120
    selected_start: string
    selected_end: string
    exit_completion_day: string
    candidate_thresholds_bps: string[]
    initial_training_session_count: 60
    fold_count: 6
    test_sessions_per_fold: 10
    test_returns_enter_same_fold_selection: false
    reused_history_not_independent_confirmation: true
  }
  parameters?: IntradayFactorBacktestRequest
  result_preview_hash?: string
  summary: {
    observation_count: number
    signal_count: number
    rank_ic: string
    full_window_strategy_metrics: IntradayPerformanceMetrics
    fold_count: number
    positive_fold_count: number
    selected_thresholds_bps: string[]
    stitched_strategy_metrics: IntradayPerformanceMetrics
    threshold_sensitivity: Array<{
      minimum_body_return_bps: string
      signal_count: number
      total_return: string
      excess_return: string
    }>
    robustness_passed: boolean
    research_gate_passed: boolean
  } | null
  result: IntradayFactorBacktest | null
  promotion: 'NOT_ELIGIBLE' | 'NOT_EVALUATED'
  promotion_reasons: string[]
  paper_readiness: 'NOT_ELIGIBLE'
  live_readiness: 'DISABLED_REQUIRES_SEPARATE_AUTHORIZATION'
  state_mutation: false
  external_order_transmission: false
  evidence_sha256?: string
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

export interface MinuteBarRow {
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
  observation_type: 'HISTORICAL_MINUTE_BAR' | 'HISTORICAL_FIVE_MINUTE_BAR'
}

export interface MinuteBarSeries {
  symbol: string
  data_version: string | null
  dataset_checksum: string | null
  observation_type: 'HISTORICAL_MINUTE_BAR_SERIES' | 'HISTORICAL_FIVE_MINUTE_BAR_SERIES'
  interval: '1m' | '5m'
  selected_trading_day?: string | null
  total_count: number
  truncated: boolean
  bars: MinuteBarRow[]
}

export interface MinuteCurveMetrics {
  total_return: number
  maximum_drawdown: number
  realized_minute_volatility: number
  point_count: number
}

export interface MinuteExecutionDiagnostics {
  symbol_count: number
  observation_count: number
  reduced_exposure_observation_count: number
  exposure_sum: number
  mean_exposure: number
  reduced_exposure_ratio: number
  turnover: number
  mean_turnover_per_symbol: number
  transaction_cost: number
  mean_transaction_cost_per_symbol: number
  reason_counts: Record<string, number>
}

export interface MinuteResearchReport {
  classification?: string
  promotion: 'NOT_ELIGIBLE' | 'NOT_EVALUATED'
  promotion_reasons: string[]
  minute_dataset: null | {
    provider: string
    data_version: string
    bar_interval: '1m' | '5m'
    checksum: string
    evidence_sha256: string
    bar_count: number
    symbol_count: number
    etf_count: number
    stock_count: number
    sector_count: number
    session_count: number
    session_bar_count_per_symbol: number
    trading_days: string[]
    observed_start: string
    observed_end: string
    eligible_symbols: string[]
    instruments: Array<{
      symbol: string
      instrument_type: 'ETF' | 'STOCK'
      name: string
      sector_code: string | null
      sector_name: string | null
    }>
    point_in_time: false
    provider_version?: string | null
    future_adjustment_knowledge_possible?: boolean
    realtime_readiness: false
    real_data_paper_readiness: false
    external_order_transmission: false
  }
  selected?: {
    policy_id: string
    policy_hash: string
    policy: Record<string, string>
    cost_bps: number
    test_base: MinuteCurveMetrics
    test_overlay: MinuteCurveMetrics
    test_excess_return: number
    mean_turnover: number
    mean_transaction_cost: number
    reason_counts: Record<string, number>
  }
  split?: { train_days: string[]; test_days: string[]; candidate_count: number }
  causal_contract?: {
    signal_observed_at: 'BAR_CLOSE_T'
    exposure_change_at: 'BAR_OPEN_T_PLUS_1'
    same_bar_signal_execution: false
    maximum_exposure: number
    external_order_transmission: false
  }
  candidates: Array<{
    policy_id: string
    selection_score: number
    train_base: MinuteCurveMetrics
    train_overlay: MinuteCurveMetrics
  }>
  cost_stress: Array<{
    cost_bps: number
    base: MinuteCurveMetrics
    overlay: MinuteCurveMetrics
    mean_turnover: number
    mean_transaction_cost: number
  }>
  subgroups: Array<{
    group: 'ETF' | 'STOCK'
    symbol_count: number
    base: MinuteCurveMetrics
    overlay: MinuteCurveMetrics
  }>
  leave_one_sector_out: Array<{
    excluded_sector_code: string
    excluded_sector_name: string
    excluded_symbols: string[]
    excluded_symbol_count: number
    retained_symbol_count: number
    overlay: MinuteCurveMetrics
    return_delta_vs_all: number
  }>
  daily: Array<{
    trading_day: string
    partition: 'TRAIN' | 'TEST'
    base: MinuteCurveMetrics
    overlay: MinuteCurveMetrics
  }>
  curve: Array<{ timestamp: string; base_equity: number; overlay_equity: number }>
}

export interface FiveMinuteResearchReport {
  classification?: string
  promotion: 'NOT_ELIGIBLE' | 'NOT_EVALUATED'
  promotion_reasons: string[]
  minute_dataset: MinuteResearchReport['minute_dataset']
  causal_contract?: {
    signal_observed_at: 'FIVE_MINUTE_BAR_CLOSE_T'
    exposure_change_at: 'NEXT_FIVE_MINUTE_BAR_OPEN'
    same_bar_signal_execution: false
    candidate_state: 'ALL_CANDIDATES_CAUSALLY_SHADOW_WARMED'
    maximum_exposure: number
    external_order_transmission: false
  }
  walk_forward?: {
    initial_train_sessions: number
    test_sessions_per_fold: number
    fold_count: number
    candidate_count: number
    test_windows_disjoint: boolean
    selection_objective: string
    selected_policy_sequence: string[]
    used_session_count?: number
    unused_trailing_sessions?: number
    processed_session_count?: number
    processed_bar_count?: number
    trailing_sessions_entered_backtest?: false
  }
  aggregate?: {
    cost_bps: number
    base: MinuteCurveMetrics
    overlay: MinuteCurveMetrics
    excess_return: number
    positive_test_fold_count: number
    execution?: MinuteExecutionDiagnostics
  }
  candidates: Array<{
    fold: number
    policy_id: string
    policy_hash: string
    rank: number
    selection_score: number
    train_base: MinuteCurveMetrics
    train_overlay: MinuteCurveMetrics
  }>
  folds: Array<{
    fold: number
    train_days: string[]
    test_days: string[]
    selected_policy_id: string
    selected_policy_hash: string
    test_base: MinuteCurveMetrics
    test_overlay: MinuteCurveMetrics
    test_excess_return: number
    execution?: MinuteExecutionDiagnostics
  }>
  cost_stress: Array<{
    cost_bps: number
    base: MinuteCurveMetrics
    overlay: MinuteCurveMetrics
    excess_return: number
  }>
  subgroups: Array<{
    group: 'ETF' | 'STOCK'
    symbol_count: number
    base: MinuteCurveMetrics
    overlay: MinuteCurveMetrics
  }>
  leave_one_sector_out: Array<{
    excluded_sector_code: string
    excluded_sector_name: string
    excluded_symbols: string[]
    excluded_symbol_count: number
    retained_symbol_count: number
    overlay: MinuteCurveMetrics
    return_delta_vs_all: number
  }>
  daily: Array<{
    fold: number
    trading_day: string
    selected_policy_id: string
    base: MinuteCurveMetrics
    overlay: MinuteCurveMetrics
  }>
  curve: Array<{ timestamp: string; base_equity: number; overlay_equity: number }>
}

export interface ReservedTailDiagnosticReport {
  classification?: string
  promotion: 'NOT_ELIGIBLE' | 'NOT_EVALUATED'
  promotion_reasons: string[]
  minute_dataset: MinuteResearchReport['minute_dataset']
  candidate?: { policy_id: string; policy_hash: string }
  comparator?: { policy_id: string; policy_hash: string }
  warmup_session_count?: number
  test_session_count?: number
  test_days?: string[]
  all_required_gates_passed?: false
  sector_win_count?: number
  five_bps?: ReservedTailCostStressRow
  gates: Array<{ gate_id: string; passed: boolean }>
  cost_stress: ReservedTailCostStressRow[]
  daily: Array<{
    trading_day: string
    base: MinuteCurveMetrics
    candidate: MinuteCurveMetrics
    comparator: MinuteCurveMetrics
  }>
  leave_one_sector_out: Array<{
    excluded_sector_code: string
    excluded_sector_name: string
    excluded_symbol_count: number
    retained_symbol_count: number
    candidate: MinuteCurveMetrics
    comparator: MinuteCurveMetrics
    candidate_beats_comparator: boolean
  }>
  curve: Array<{
    timestamp: string
    base_equity: number
    candidate_equity: number
    comparator_equity: number
  }>
  external_order_transmission?: false
}

export interface ReservedTailCostStressRow {
  cost_bps: number
  base: MinuteCurveMetrics
  candidate: MinuteCurveMetrics
  comparator: MinuteCurveMetrics
  candidate_execution: MinuteExecutionDiagnostics
  comparator_execution: MinuteExecutionDiagnostics
}

export interface PublicMinuteAuditRound {
  request_count: number
  all_valid: boolean
  latency_seconds: {
    minimum: number
    median: number
    p95: number
    maximum: number
  }
  vercel_cache_counts: Record<string, number>
  failures: unknown[]
}

export interface PublicMinuteAuditReport {
  classification?: string
  generated_at?: string
  base_url?: string
  data_version?: string
  dataset_checksum?: string
  sector_count?: number
  sampled_trading_days?: string[]
  first_round: PublicMinuteAuditRound | null
  repeat_round: PublicMinuteAuditRound | null
  public_write_boundary?: {
    request_count: number
    all_blocked: boolean
    checks: Array<{ path: string; status: number; blocked: boolean }>
  }
  all_valid: boolean
  promotion: 'NOT_ELIGIBLE' | 'NOT_EVALUATED'
  external_order_transmission: false
}

export interface IntradayObservationReport {
  classification?: string
  provider?: string
  bar_interval?: '1m'
  started_at?: string
  completed_at?: string
  session_phase: 'PRE_OPEN' | 'MORNING_SESSION' | 'MIDDAY_BREAK' | 'AFTERNOON_SESSION' | 'POST_CLOSE' | 'UNAVAILABLE'
  data_version?: string
  dataset_checksum?: string
  evidence_sha256?: string
  symbol_count: number
  sector_count: number
  same_day_symbol_count: number
  synchronized_finalized_timestamp_count: number
  latest_synchronized_finalized_timestamp: string | null
  source_request_window_seconds?: number
  all_symbols_same_day_coverage?: boolean
  maximum_same_day_latest_age_seconds?: number | null
  finalization_guard_seconds?: number
  observations: Array<{
    symbol: string
    exchange?: string
    name: string
    sector_code: string
    sector_name: string
    observed_at: string
    session_phase: string
    source_row_count: number
    latest_observed_timestamp: string
    latest_finalized_timestamp: string
    same_day_finalized_bar_count: number
    latest_same_day_finalized_age_seconds: number | null
    forming_or_uncertain_bar_excluded: boolean
    bars: Array<{
      symbol: string
      exchange: string
      timestamp: string
      trading_day: string
      open: string
      high: string
      low: string
      close: string
      volume: string
    }>
  }>
  realtime_readiness: false
  real_data_paper_readiness: false
  promotion: 'NOT_ELIGIBLE' | 'NOT_EVALUATED'
  external_order_transmission: false
}

export interface IntradayBreadthObservationReport {
  classification?: string
  coverage_scope?: 'ALL_FROZEN_STOCKS_15_SECTORS_X5' | 'UNAVAILABLE'
  provider?: string
  bar_interval?: '1m'
  started_at?: string
  completed_at?: string
  session_phase: IntradayObservationReport['session_phase']
  data_version?: string
  dataset_checksum?: string
  evidence_sha256?: string
  symbol_count: number
  sector_count: number
  symbols_per_sector: number
  same_day_symbol_count: number
  synchronized_finalized_timestamp_count: number
  latest_synchronized_finalized_timestamp: string | null
  source_request_window_seconds?: number
  all_symbols_same_day_coverage?: boolean
  maximum_same_day_latest_age_seconds?: number | null
  finalization_guard_seconds?: number
  directional_breadth?: IntradayDirectionalBreadth
  observations: Array<{
    symbol: string
    name: string
    sector_code: string
    sector_name: string
    observed_at: string
    session_phase: string
    source_row_count: number
    latest_observed_timestamp: string
    latest_finalized_timestamp: string | null
    same_day_finalized_bar_count: number
    latest_same_day_finalized_timestamp: string | null
    latest_same_day_finalized_age_seconds: number | null
    forming_or_uncertain_bar_excluded: boolean
    first_same_day_open: string | null
    previous_same_day_close: string | null
    latest_same_day_close: string | null
    session_return: string | null
    latest_minute_return: string | null
  }>
  sector_summaries: Array<{
    sector_code: string
    sector_name: string
    symbol_count: number
    same_day_symbol_count: number
    synchronized_finalized_timestamp_count: number
    latest_synchronized_finalized_timestamp: string | null
    maximum_same_day_latest_age_seconds: number | null
    directional_breadth: IntradayDirectionalBreadth
  }>
  realtime_readiness: false
  real_data_paper_readiness: false
  promotion: 'NOT_ELIGIBLE' | 'NOT_EVALUATED'
  external_order_transmission: false
}

export interface IntradayDirectionalBreadth {
  eligible_symbol_count: number
  above_open_count: number
  unchanged_from_open_count: number
  below_open_count: number
  latest_minute_eligible_symbol_count: number
  latest_minute_up_count: number
  latest_minute_unchanged_count: number
  latest_minute_down_count: number
  equal_weight_session_return: string | null
  equal_weight_latest_minute_return: string | null
}

export interface IntradayContinuityReport {
  classification?: string
  trading_day: string | null
  checkpoint_count: number
  directional_breadth_first_sequence?: number | null
  all_checkpoints_monotonic: boolean
  checkpoints: Array<{
    sequence: number
    captured_at: string
    trading_day: string
    detail: {
      evidence_sha256: string
      completed_at: string
      session_phase: string
      symbol_count: number
      sector_count: number
      same_day_symbol_count: number
      synchronized_finalized_timestamp_count: number
      latest_synchronized_finalized_timestamp: string | null
      source_request_window_seconds: number
      maximum_same_day_latest_age_seconds: number | null
      all_symbols_same_day_coverage: boolean
    }
    breadth: {
      evidence_sha256: string
      completed_at: string
      session_phase: string
      symbol_count: number
      sector_count: number
      same_day_symbol_count: number
      synchronized_finalized_timestamp_count: number
      latest_synchronized_finalized_timestamp: string | null
      source_request_window_seconds: number
      maximum_same_day_latest_age_seconds: number | null
      all_symbols_same_day_coverage: boolean
      directional_breadth?: IntradayDirectionalBreadth
    }
    monotonic_vs_previous: boolean
    previous_checkpoint_sha256: string | null
    checkpoint_sha256: string
  }>
  evidence_sha256?: string
  realtime_readiness: false
  real_data_paper_readiness: false
  promotion: 'NOT_ELIGIBLE' | 'NOT_EVALUATED'
  external_order_transmission: false
}

export interface DevelopmentPolicySearchReport {
  classification?: string
  generated_at?: string
  candidate_count: number
  development_contract?: {
    session_count: number
    observed_start: string
    observed_end: string
    processed_bar_count: number
    cost_bps: number
  }
  reserved_tail?: {
    session_count: number
    trading_days: string[]
    entered_strategy_engine: false
    entered_candidate_metrics: false
  }
  candidates: Array<{
    policy_id: string
    policy_hash: string
    policy: Record<string, string>
    base: Record<string, number>
    overlay: Record<string, number>
    return_delta_vs_base: number
    drawdown_improvement: number
    execution: {
      mean_exposure: number
      mean_turnover_per_symbol: number
    }
  }>
  observed_leader?: {
    policy_id: string
    selection_timing: 'POST_HOC_DEVELOPMENT_ONLY'
  }
  future_observation_lock?: {
    locked_at: string
    policy_id: string
    first_eligible_trading_day: string
    minimum_complete_future_sessions: number
    partial_same_day_observations_enter_metrics: false
    parameter_changes_before_completion: false
  }
  promotion: 'NOT_ELIGIBLE' | 'NOT_EVALUATED'
  external_order_transmission: false
}

export interface ForwardObservationReport {
  classification?: string
  preregistration_id?: string
  preregistration_sha256?: string
  created_at?: string
  status?: string
  timing_disclosure?: {
    development_policy_locked_before_first_session_open: true
    evaluation_contract_fixed_after_first_session_open: true
    target_session_data_fetched_before_this_lock: false
    claim_as_blind_preregistration: false
  }
  policy_id?: string
  policy_hash?: string
  parameters?: Record<string, string>
  first_eligible_trading_day?: string
  future_session_count: number
  minimum_complete_future_sessions: number
  required_symbol_count?: number
  required_sector_count?: number
  required_bars_per_symbol_session?: number
  observation_status: 'INCOMPLETE' | 'COMPLETE' | 'UNAVAILABLE'
  all_required_gates_passed?: boolean
  gates: Array<{ gate_id: string; passed: boolean | null }>
  cost_stress: Array<{
    cost_bps: number
    base: MinuteCurveMetrics
    overlay: MinuteCurveMetrics
    excess_return: number
    execution: MinuteExecutionDiagnostics
  }>
  leave_one_sector_out: Array<{
    excluded_sector_code: string
    excluded_sector_name: string
    base: MinuteCurveMetrics
    overlay: MinuteCurveMetrics
    excess_return: number
  }>
  promotion: 'NOT_ELIGIBLE' | 'NOT_EVALUATED'
  promotion_reasons?: string[]
  realtime_readiness: false
  real_data_paper_readiness: false
  external_order_transmission: false
}

export interface ForwardSourceCoverageReport {
  classification?: string
  report_id?: string
  generated_at?: string
  provider?: string
  provider_version?: string
  anchor_day?: string
  target_day?: string
  preregistration_id?: string
  preregistration_sha256?: string
  warmup_data_version?: string
  warmup_checksum?: string
  warmup_evidence_sha256?: string
  required_symbol_count: number
  required_bars_per_symbol_session: number
  target_complete_symbol_count: number
  ready_symbol_count: number
  incomplete_symbol_count: number
  incomplete_symbols: string[]
  target_incomplete_symbols: string[]
  fetch_error_symbols: string[]
  coverage: Array<{
    symbol: string
    fetch_status: 'OK' | 'ERROR'
    frozen_anchor_bar_count: number
    anchor_bar_count: number
    target_bar_count: number
    frozen_anchor_grid_complete: boolean
    anchor_grid_complete: boolean
    target_grid_complete: boolean
    anchor_exact_match: boolean
    ready: boolean
    reasons: string[]
  }>
  status: 'READY_FOR_FORMAL_COLLECTION' | 'SOURCE_INCOMPLETE' | 'UNAVAILABLE'
  ready_for_formal_collection: boolean
  enters_forward_metrics: false
  promotion: 'NOT_ELIGIBLE' | 'NOT_EVALUATED'
  realtime_readiness: false
  real_data_paper_readiness: false
  external_order_transmission: false
}

export interface ForwardSourceCoverageHistoryReport {
  classification: string
  report_id?: string
  checkpoint_count: number
  all_checkpoints_monotonic: boolean
  checkpoints: Array<{
    sequence: number
    generated_at: string
    report_evidence_sha256: string
    ready_symbol_count: number
    target_complete_symbol_count: number
    incomplete_symbol_count: number
    fetch_error_symbol_count: number
    status: 'READY_FOR_FORMAL_COLLECTION' | 'SOURCE_INCOMPLETE'
    ready_symbol_count_delta: number | null
    monotonic_vs_previous: boolean
    previous_checkpoint_sha256: string | null
    checkpoint_sha256: string
  }>
  evidence_sha256?: string
  enters_forward_metrics: false
  promotion: 'NOT_ELIGIBLE' | 'NOT_EVALUATED'
  realtime_readiness: false
  real_data_paper_readiness: false
  external_order_transmission: false
}

export interface DailyReportAutomationStatus {
  classification: 'TRUSTED_PAPER_EOD_REPORTING'
  readiness: 'CONFIGURED' | 'UNCONFIGURED'
  automatic: boolean
  state: 'UNCONFIGURED' | 'STATUS_UNAVAILABLE' | 'WAITING_FOR_CUTOFF' | 'CALENDAR_SESSION_UNAVAILABLE' | 'NON_TRADING_DAY' | 'GENERATED' | 'ALREADY_GENERATED' | 'FAILED'
  venue: string | null
  timezone: 'Asia/Shanghai'
  cutoff: string | null
  data_version: string | null
  strategy_versions: string[]
  last_attempt_day: string | null
  last_generated_day: string | null
  last_report_id: string | null
  last_sha256: string | null
  error_code: string | null
  caller_supplied_identity: false
  external_order_transmission: false
}

export interface PlatformSnapshot {
  system: SystemStatus
  health: HealthStatus
  deployment: DeploymentProvenance
  account: { cash: string; equity: null; valuation_status: 'MARKET_PRICE_UNAVAILABLE' }
  pnl: { realized: string }
  positions: PositionRow[]
  orders: OrderRow[]
  trades: PaperTradeRow[]
  risk: PaperRiskSummary
  metrics: MetricsSnapshot
  reports: DailyReportSummary[]
  reporting: DailyReportAutomationStatus
  strategies: ResearchStrategyRow[]
  experiments: ResearchReport
  walkForward: WalkForwardReport
  robustness: RobustnessReport
  factorSearch: FactorSearchReport
  robustFactorSearch: RobustFactorSearchReport
  stockSectorTransfer: StockSectorTransferReport
  minuteOverlay: MinuteOverlayStatus
  minuteResearch: MinuteResearchReport
  fiveMinuteResearch: FiveMinuteResearchReport
  longFiveMinuteResearch: FiveMinuteResearchReport
  intradayFactorRobustness: IntradayFactorLongWindowReport
  reservedTailDiagnostic: ReservedTailDiagnosticReport
  developmentPolicySearch?: DevelopmentPolicySearchReport
  forwardObservation: ForwardObservationReport
  forwardSourceCoverage: ForwardSourceCoverageReport
  forwardSourceCoverageHistory: ForwardSourceCoverageHistoryReport
  publicMinuteAudit: PublicMinuteAuditReport
  intradayObservation: IntradayObservationReport
  intradayBreadth: IntradayBreadthObservationReport
  intradayContinuity: IntradayContinuityReport
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
  availability: {
    failures: Record<string, string>
  }
}
