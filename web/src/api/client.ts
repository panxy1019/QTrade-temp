import type { BacktestPreview, BacktestPreviewRequest, HistoricalBarSeries, MinuteBarSeries, PlatformSnapshot } from '../types'

const API_BASE = import.meta.env.DEV || import.meta.env.MODE === 'vercel' ? '/api' : ''

export const prometheusMetricsUrl = `${API_BASE}/metrics/prometheus`

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`)
  if (!response.ok) throw new Error(`请求失败：${response.status}`)
  return response.json() as Promise<T>
}

async function postJson<T>(path: string, payload: object): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(body?.detail ?? `请求失败：${response.status}`)
  }
  return response.json() as Promise<T>
}

async function postAuthorized<T>(path: string, payload: object, token: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` },
    body: JSON.stringify(payload),
  })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: string } | null
    throw new Error(body?.detail ?? `请求失败：${response.status}`)
  }
  return response.json() as Promise<T>
}

interface OptionalResult<T> {
  value: T
  error: string | null
}

async function getOptional<T>(path: string, fallback: T): Promise<OptionalResult<T>> {
  try {
    return { value: await getJson<T>(path), error: null }
  } catch (reason) {
    return {
      value: fallback,
      error: reason instanceof Error ? reason.message : '读取失败',
    }
  }
}

export function loadHistoricalBarSeries(symbol: string): Promise<HistoricalBarSeries> {
  return getJson<HistoricalBarSeries>(`/historical-bars/${encodeURIComponent(symbol)}`)
}

export function loadMinuteBarSeries(symbol: string): Promise<MinuteBarSeries> {
  return getJson<MinuteBarSeries>(`/minute-bars/${encodeURIComponent(symbol)}`)
}

export function loadFiveMinuteBarSeries(symbol: string): Promise<MinuteBarSeries> {
  return getJson<MinuteBarSeries>(`/five-minute-bars/${encodeURIComponent(symbol)}`)
}

export function loadLongFiveMinuteBarSeries(symbol: string, tradingDay: string): Promise<MinuteBarSeries> {
  return getJson<MinuteBarSeries>(`/long-five-minute-bars/${encodeURIComponent(symbol)}?trading_day=${encodeURIComponent(tradingDay)}`)
}

export function runBacktestPreview(payload: BacktestPreviewRequest): Promise<BacktestPreview> {
  return postJson<BacktestPreview>('/backtest-preview', payload)
}

export type RuntimeControlAction = 'recover' | 'start' | 'pause' | 'resume' | 'stop' | 'kill'

export function controlRuntime(action: RuntimeControlAction, token: string, reason: string) {
  return postAuthorized<PlatformSnapshot['system']>(`/system/${action}`, { reason }, token)
}

export function cancelPaperOrder(internalOrderId: string, idempotencyKey: string, token: string) {
  return postAuthorized<{ internal_order_id: string; status: string }>('/orders/cancel', {
    internal_order_id: internalOrderId,
    idempotency_key: idempotencyKey,
  }, token)
}

export async function loadPlatformSnapshot(): Promise<PlatformSnapshot> {
  const [system, health, paper] = await Promise.all([
    getJson<PlatformSnapshot['system']>('/system'),
    getJson<PlatformSnapshot['health']>('/health'),
    getJson<Pick<PlatformSnapshot, 'account' | 'pnl' | 'positions' | 'orders' | 'trades' | 'risk'>>('/paper-snapshot'),
  ])
  const [metrics, reports, reporting, strategies, experiments, walkForward, robustness, factorSearch, robustFactorSearch, stockSectorTransfer, minuteOverlay, minuteResearch, fiveMinuteResearch, longFiveMinuteResearch, reservedTailDiagnostic, developmentPolicySearch, forwardObservation, publicMinuteAudit, intradayObservation, intradayBreadth, intradayContinuity, backtestAnalytics, market, historicalBars, audit] = await Promise.all([
    getOptional<PlatformSnapshot['metrics']>('/metrics', {}),
    getOptional<PlatformSnapshot['reports']>('/reports', []),
    getOptional<PlatformSnapshot['reporting']>('/reporting-status', {
      classification: 'TRUSTED_PAPER_EOD_REPORTING', readiness: 'UNCONFIGURED', automatic: false,
      state: 'STATUS_UNAVAILABLE', venue: null, timezone: 'Asia/Shanghai', cutoff: null,
      data_version: null, strategy_versions: [], last_attempt_day: null,
      last_generated_day: null, last_report_id: null, last_sha256: null,
      error_code: 'STATUS_REQUEST_FAILED', caller_supplied_identity: false,
      external_order_transmission: false,
    }),
    getOptional<PlatformSnapshot['strategies']>('/strategies', []),
    getOptional<PlatformSnapshot['experiments']>('/experiments', {
      classification: 'UNAVAILABLE', promotion: 'NOT_EVALUATED', promotion_reasons: ['REQUEST_FAILED'], experiments: [],
    }),
    getOptional<PlatformSnapshot['walkForward']>('/walk-forward', {
      classification: 'UNAVAILABLE', promotion: 'NOT_EVALUATED', promotion_reasons: ['REQUEST_FAILED'], result: { folds: [] },
    }),
    getOptional<PlatformSnapshot['robustness']>('/robustness', {
      classification: 'UNAVAILABLE', promotion: 'NOT_EVALUATED', promotion_reasons: ['REQUEST_FAILED'], result: { scenario_metrics: [] },
    }),
    getOptional<PlatformSnapshot['factorSearch']>('/factor-search', {
      classification: 'UNAVAILABLE', promotion: 'NOT_EVALUATED', promotion_reasons: ['REQUEST_FAILED'], factor_diagnostics: [], validation: { scores: [] }, test: {},
    }),
    getOptional<PlatformSnapshot['robustFactorSearch']>('/factor-robustness', {
      classification: 'UNAVAILABLE', promotion: 'NOT_EVALUATED', promotion_reasons: ['REQUEST_FAILED'], candidate_summaries: [], stress_tests: { evaluations: [] }, bootstrap: { evaluations: [] }, polluted_audit: {},
    }),
    getOptional<PlatformSnapshot['stockSectorTransfer']>('/stock-sector-transfer', {
      classification: 'UNAVAILABLE', promotion: 'NOT_EVALUATED', promotion_reasons: ['REQUEST_FAILED'], yearly: [], sector_only: [], leave_one_sector_out: [], cost_stress: {}, bootstrap: {}, polluted_audit: {},
    }),
    getOptional<PlatformSnapshot['minuteOverlay']>('/minute-overlay', {
      classification: 'CAUSAL_MINUTE_RISK_OVERLAY_PAPER_SHADOW_ONLY', readiness: 'STATUS_UNAVAILABLE', provider: null, provider_connected: false, bar_interval: '1m', symbol_count: 0, ready_symbol_count: 0, external_order_transmission: false, promotion: 'NOT_ELIGIBLE', reasons: ['STATUS_REQUEST_FAILED'], symbols: [],
    }),
    getOptional<PlatformSnapshot['minuteResearch']>('/minute-research', {
      classification: 'UNAVAILABLE', promotion: 'NOT_EVALUATED', promotion_reasons: ['REQUEST_FAILED'], minute_dataset: null,
      candidates: [], cost_stress: [], subgroups: [], leave_one_sector_out: [], daily: [], curve: [],
    }),
    getOptional<PlatformSnapshot['fiveMinuteResearch']>('/five-minute-research', {
      classification: 'UNAVAILABLE', promotion: 'NOT_EVALUATED', promotion_reasons: ['REQUEST_FAILED'], minute_dataset: null,
      candidates: [], folds: [], cost_stress: [], subgroups: [], leave_one_sector_out: [], daily: [], curve: [],
    }),
    getOptional<PlatformSnapshot['longFiveMinuteResearch']>('/long-five-minute-research', {
      classification: 'UNAVAILABLE', promotion: 'NOT_EVALUATED', promotion_reasons: ['REQUEST_FAILED'], minute_dataset: null,
      candidates: [], folds: [], cost_stress: [], subgroups: [], leave_one_sector_out: [], daily: [], curve: [],
    }),
    getOptional<PlatformSnapshot['reservedTailDiagnostic']>('/reserved-tail-diagnostic', {
      classification: 'UNAVAILABLE', promotion: 'NOT_EVALUATED', promotion_reasons: ['REQUEST_FAILED'], minute_dataset: null,
      gates: [], cost_stress: [], daily: [], leave_one_sector_out: [], curve: [],
    }),
    getOptional<NonNullable<PlatformSnapshot['developmentPolicySearch']>>('/development-policy-search', {
      classification: 'UNAVAILABLE', candidate_count: 0, candidates: [],
      promotion: 'NOT_EVALUATED', external_order_transmission: false,
    }),
    getOptional<PlatformSnapshot['forwardObservation']>('/forward-observation', {
      classification: 'UNAVAILABLE', observation_status: 'UNAVAILABLE',
      future_session_count: 0, minimum_complete_future_sessions: 20,
      gates: [], cost_stress: [], leave_one_sector_out: [],
      promotion: 'NOT_EVALUATED', realtime_readiness: false,
      real_data_paper_readiness: false, external_order_transmission: false,
    }),
    getOptional<PlatformSnapshot['publicMinuteAudit']>('/public-minute-audit', {
      classification: 'UNAVAILABLE', promotion: 'NOT_EVALUATED', first_round: null,
      repeat_round: null, all_valid: false, external_order_transmission: false,
    }),
    getOptional<PlatformSnapshot['intradayObservation']>('/intraday-observation', {
      classification: 'UNAVAILABLE', session_phase: 'UNAVAILABLE', symbol_count: 0,
      sector_count: 0, same_day_symbol_count: 0,
      synchronized_finalized_timestamp_count: 0,
      latest_synchronized_finalized_timestamp: null, observations: [],
      realtime_readiness: false, real_data_paper_readiness: false,
      promotion: 'NOT_EVALUATED', external_order_transmission: false,
    }),
    getOptional<PlatformSnapshot['intradayBreadth']>('/intraday-breadth-observation', {
      classification: 'UNAVAILABLE', coverage_scope: 'UNAVAILABLE',
      session_phase: 'UNAVAILABLE', symbol_count: 0, sector_count: 0,
      symbols_per_sector: 0, same_day_symbol_count: 0,
      synchronized_finalized_timestamp_count: 0,
      latest_synchronized_finalized_timestamp: null,
      observations: [], sector_summaries: [], realtime_readiness: false,
      real_data_paper_readiness: false, promotion: 'NOT_EVALUATED',
      external_order_transmission: false,
    }),
    getOptional<PlatformSnapshot['intradayContinuity']>('/intraday-continuity', {
      classification: 'UNAVAILABLE', trading_day: null, checkpoint_count: 0,
      all_checkpoints_monotonic: false, checkpoints: [], realtime_readiness: false,
      real_data_paper_readiness: false, promotion: 'NOT_EVALUATED',
      external_order_transmission: false,
    }),
    getOptional<PlatformSnapshot['backtestAnalytics']>('/backtest-analytics', {
      classification: 'UNAVAILABLE', promotion: 'NOT_EVALUATED', leader: null, curve: [],
    }),
    getOptional<PlatformSnapshot['market']>('/market', {
      provider_connected: false, execution_gateway_connected: health.gateway_connected, mode: system.mode, snapshot_readiness: false, historical: null,
    }),
    getOptional<PlatformSnapshot['historicalBars']>('/historical-bars/latest', []),
    getOptional<PlatformSnapshot['audit']>('/audit', null),
  ])
  const sections = { metrics, reports, reporting, strategies, experiments, walkForward, robustness, factorSearch, robustFactorSearch, stockSectorTransfer, minuteOverlay, minuteResearch, fiveMinuteResearch, longFiveMinuteResearch, reservedTailDiagnostic, developmentPolicySearch, forwardObservation, publicMinuteAudit, intradayObservation, intradayBreadth, intradayContinuity, backtestAnalytics, market, historicalBars, audit }
  const failures = Object.fromEntries(Object.entries(sections).flatMap(([name, result]) => result.error ? [[name, result.error]] : []))
  return {
    system, health, ...paper,
    metrics: metrics.value, reports: reports.value, reporting: reporting.value, strategies: strategies.value,
    experiments: experiments.value, walkForward: walkForward.value, robustness: robustness.value,
    factorSearch: factorSearch.value, robustFactorSearch: robustFactorSearch.value,
    stockSectorTransfer: stockSectorTransfer.value, minuteOverlay: minuteOverlay.value,
    minuteResearch: minuteResearch.value,
    fiveMinuteResearch: fiveMinuteResearch.value,
    longFiveMinuteResearch: longFiveMinuteResearch.value,
    reservedTailDiagnostic: reservedTailDiagnostic.value,
    developmentPolicySearch: developmentPolicySearch.value,
    forwardObservation: forwardObservation.value,
    publicMinuteAudit: publicMinuteAudit.value,
    intradayObservation: intradayObservation.value,
    intradayBreadth: intradayBreadth.value,
    intradayContinuity: intradayContinuity.value,
    backtestAnalytics: backtestAnalytics.value, market: market.value,
    historicalBars: historicalBars.value, audit: audit.value, availability: { failures },
  }
}
