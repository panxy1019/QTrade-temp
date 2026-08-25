import type { BacktestPreview, BacktestPreviewRequest, HistoricalBarSeries, PlatformSnapshot } from '../types'

const API_BASE = import.meta.env.DEV || import.meta.env.MODE === 'vercel' ? '/api' : ''

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
  const [metrics, reports, strategies, experiments, walkForward, robustness, factorSearch, robustFactorSearch, stockSectorTransfer, minuteOverlay, backtestAnalytics, market, historicalBars, audit] = await Promise.all([
    getOptional<PlatformSnapshot['metrics']>('/metrics', {}),
    getOptional<PlatformSnapshot['reports']>('/reports', []),
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
    getOptional<PlatformSnapshot['backtestAnalytics']>('/backtest-analytics', {
      classification: 'UNAVAILABLE', promotion: 'NOT_EVALUATED', leader: null, curve: [],
    }),
    getOptional<PlatformSnapshot['market']>('/market', {
      provider_connected: false, execution_gateway_connected: health.gateway_connected, mode: system.mode, snapshot_readiness: false, historical: null,
    }),
    getOptional<PlatformSnapshot['historicalBars']>('/historical-bars/latest', []),
    getOptional<PlatformSnapshot['audit']>('/audit', null),
  ])
  const sections = { metrics, reports, strategies, experiments, walkForward, robustness, factorSearch, robustFactorSearch, stockSectorTransfer, minuteOverlay, backtestAnalytics, market, historicalBars, audit }
  const failures = Object.fromEntries(Object.entries(sections).flatMap(([name, result]) => result.error ? [[name, result.error]] : []))
  return {
    system, health, ...paper,
    metrics: metrics.value, reports: reports.value, strategies: strategies.value,
    experiments: experiments.value, walkForward: walkForward.value, robustness: robustness.value,
    factorSearch: factorSearch.value, robustFactorSearch: robustFactorSearch.value,
    stockSectorTransfer: stockSectorTransfer.value, minuteOverlay: minuteOverlay.value,
    backtestAnalytics: backtestAnalytics.value, market: market.value,
    historicalBars: historicalBars.value, audit: audit.value, availability: { failures },
  }
}
