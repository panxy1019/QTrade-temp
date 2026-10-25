import type { BacktestPreview, BacktestPreviewRequest, ExperimentRunSpecBacktest, ExperimentRunSpecDraft, ExperimentRunSpecDraftRequest, HistoricalBarSeries, IntradayFactorBacktest, IntradayFactorBacktestRequest, IntradayFactorCorrelation, IntradayFactorCorrelationRequest, IntradayFactorDiscovery, IntradayFactorDiscoveryRequest, MinuteBarSeries, PlatformSnapshot, SingleStockFiveMinuteBacktest, SingleStockFiveMinuteBacktestRequest } from '../types'

const API_BASE = import.meta.env.DEV || import.meta.env.MODE === 'vercel' ? '/api' : ''
const MAX_CONCURRENT_GETS = 6
const GET_TIMEOUT_MS = 30_000
let activeGets = 0
const getWaiters: Array<() => void> = []

export const prometheusMetricsUrl = `${API_BASE}/metrics/prometheus`

export function loadPublicResearchObservations() {
  return getJson<import('../types').PublicResearchObservation>('/observations/latest')
}

async function acquireGetSlot(): Promise<void> {
  if (activeGets < MAX_CONCURRENT_GETS) {
    activeGets += 1
    return
  }
  await new Promise<void>((resolve) => {
    getWaiters.push(() => {
      activeGets += 1
      resolve()
    })
  })
}

function releaseGetSlot(): void {
  activeGets -= 1
  const next = getWaiters.shift()
  if (next) next()
}

async function getJson<T>(path: string): Promise<T> {
  await acquireGetSlot()
  const controller = new AbortController()
  const timeoutId = globalThis.setTimeout(() => controller.abort(), GET_TIMEOUT_MS)
  try {
    const response = await fetch(`${API_BASE}${path}`, { cache: 'no-store', signal: controller.signal })
    if (!response.ok) throw new Error(`请求失败：${response.status}`)
    return await response.json() as T
  } catch (reason) {
    if (controller.signal.aborted) throw new Error('请求超时：30 秒')
    throw reason
  } finally {
    globalThis.clearTimeout(timeoutId)
    releaseGetSlot()
  }
}

async function postJson<T>(path: string, payload: object, currentVersion?: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json', ...(currentVersion ? { 'X-QTrade-Data-Version': currentVersion } : {}) },
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

interface MinuteResearchSnapshot {
  classification: string
  data_version: string | null
  dataset_checksum: string | null
  longFiveMinuteResearch: PlatformSnapshot['longFiveMinuteResearch'] | null
  intradayFactorRobustness: PlatformSnapshot['intradayFactorRobustness'] | null
  reservedTailDiagnostic: PlatformSnapshot['reservedTailDiagnostic'] | null
  developmentPolicySearch: NonNullable<PlatformSnapshot['developmentPolicySearch']> | null
  forwardObservation: PlatformSnapshot['forwardObservation'] | null
  forwardSourceCoverage: PlatformSnapshot['forwardSourceCoverage'] | null
  forwardSourceCoverageHistory: PlatformSnapshot['forwardSourceCoverageHistory'] | null
  publicMinuteAudit: PlatformSnapshot['publicMinuteAudit'] | null
  intradayObservation: PlatformSnapshot['intradayObservation'] | null
  intradayBreadth: PlatformSnapshot['intradayBreadth'] | null
  intradayContinuity: PlatformSnapshot['intradayContinuity'] | null
  unavailable_sections: string[]
  realtime_readiness: false
  real_data_paper_readiness: false
  external_order_transmission: false
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

function getBundledSection<T>(
  name: string,
  value: T | null,
  fallback: T,
  bundleError: string | null,
  unavailableSections: ReadonlySet<string>,
): OptionalResult<T> {
  const unavailable = value === null || value === undefined || unavailableSections.has(name)
  return {
    value: unavailable ? fallback : value,
    error: bundleError ?? (unavailable ? '聚合快照分区不可用' : null),
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

export function runBacktestPreview(payload: BacktestPreviewRequest, currentVersion?: string): Promise<BacktestPreview> {
  return postJson<BacktestPreview>(`${currentVersion ? '/current' : ''}/backtest-preview`, payload, currentVersion)
}

export function createExperimentRunSpecDraft(payload: ExperimentRunSpecDraftRequest, currentVersion?: string): Promise<ExperimentRunSpecDraft> {
  return postJson<ExperimentRunSpecDraft>(`${currentVersion ? '/current' : ''}/experiment-run-spec-draft`, payload, currentVersion)
}

export function runExperimentRunSpecBacktest(payload: ExperimentRunSpecDraftRequest, currentVersion?: string): Promise<ExperimentRunSpecBacktest> {
  return postJson<ExperimentRunSpecBacktest>(`${currentVersion ? '/current' : ''}/experiment-run-spec-backtest`, payload, currentVersion)
}

export function runSingleStockFiveMinuteBacktest(payload: SingleStockFiveMinuteBacktestRequest, currentVersion?: string): Promise<SingleStockFiveMinuteBacktest> {
  return postJson<SingleStockFiveMinuteBacktest>(`${currentVersion ? '/current' : ''}/single-stock-five-minute-backtest`, payload, currentVersion)
}

export const loadCurrentDatasets = () => getJson<import('../types').CurrentDatasets>('/current/datasets')
export const loadCurrentSeries = (kind: 'daily' | 'minute', symbol: string, version: string, day?: string) =>
  getJson<HistoricalBarSeries | MinuteBarSeries>(`/current/bars/${kind}/${encodeURIComponent(symbol)}?version=${encodeURIComponent(version)}${day ? `&trading_day=${encodeURIComponent(day)}` : ''}`)

export async function runIntradayFactorBacktest(payload: IntradayFactorBacktestRequest): Promise<IntradayFactorBacktest> {
  try {
    return await postJson<IntradayFactorBacktest>('/intraday-factor-backtest', payload)
  } catch (reason) {
    if (!(reason instanceof TypeError)) throw reason
    return postJson<IntradayFactorBacktest>('/intraday-factor-backtest', payload)
  }
}

export async function runIntradayFactorCorrelation(payload: IntradayFactorCorrelationRequest): Promise<IntradayFactorCorrelation> {
  try {
    return await postJson<IntradayFactorCorrelation>('/intraday-factor-correlation', payload)
  } catch (reason) {
    if (!(reason instanceof TypeError)) throw reason
    return postJson<IntradayFactorCorrelation>('/intraday-factor-correlation', payload)
  }
}

export async function runIntradayFactorDiscovery(payload: IntradayFactorDiscoveryRequest): Promise<IntradayFactorDiscovery> {
  try {
    return await postJson<IntradayFactorDiscovery>('/intraday-factor-discovery', payload)
  } catch (reason) {
    if (!(reason instanceof TypeError)) throw reason
    return postJson<IntradayFactorDiscovery>('/intraday-factor-discovery', payload)
  }
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
  const longFiveMinuteFallback: PlatformSnapshot['longFiveMinuteResearch'] = {
    classification: 'UNAVAILABLE', promotion: 'NOT_EVALUATED', promotion_reasons: ['REQUEST_FAILED'], minute_dataset: null,
    candidates: [], folds: [], cost_stress: [], subgroups: [], leave_one_sector_out: [], daily: [], curve: [],
  }
  const reservedTailFallback: PlatformSnapshot['reservedTailDiagnostic'] = {
    classification: 'UNAVAILABLE', promotion: 'NOT_EVALUATED', promotion_reasons: ['REQUEST_FAILED'], minute_dataset: null,
    gates: [], cost_stress: [], daily: [], leave_one_sector_out: [], curve: [],
  }
  const intradayFactorRobustnessFallback: PlatformSnapshot['intradayFactorRobustness'] = {
    schema_version: 1,
    report_id: null,
    classification: 'UNAVAILABLE',
    summary: null,
    result: null,
    promotion: 'NOT_EVALUATED',
    promotion_reasons: ['REQUEST_FAILED'],
    paper_readiness: 'NOT_ELIGIBLE',
    live_readiness: 'DISABLED_REQUIRES_SEPARATE_AUTHORIZATION',
    state_mutation: false,
    external_order_transmission: false,
  }
  const developmentFallback: NonNullable<PlatformSnapshot['developmentPolicySearch']> = {
    classification: 'UNAVAILABLE', candidate_count: 0, candidates: [],
    promotion: 'NOT_EVALUATED', external_order_transmission: false,
  }
  const forwardFallback: PlatformSnapshot['forwardObservation'] = {
    classification: 'UNAVAILABLE', observation_status: 'UNAVAILABLE',
    future_session_count: 0, minimum_complete_future_sessions: 20,
    gates: [], cost_stress: [], leave_one_sector_out: [],
    promotion: 'NOT_EVALUATED', realtime_readiness: false,
    real_data_paper_readiness: false, external_order_transmission: false,
  }
  const forwardSourceCoverageFallback: PlatformSnapshot['forwardSourceCoverage'] = {
    classification: 'UNAVAILABLE', status: 'UNAVAILABLE',
    required_symbol_count: 75, required_bars_per_symbol_session: 48,
    target_complete_symbol_count: 0, ready_symbol_count: 0,
    incomplete_symbol_count: 75, incomplete_symbols: [],
    target_incomplete_symbols: [], fetch_error_symbols: [], coverage: [],
    ready_for_formal_collection: false, enters_forward_metrics: false,
    promotion: 'NOT_EVALUATED', realtime_readiness: false,
    real_data_paper_readiness: false, external_order_transmission: false,
  }
  const forwardSourceCoverageHistoryFallback: PlatformSnapshot['forwardSourceCoverageHistory'] = {
    classification: 'UNAVAILABLE', checkpoint_count: 0,
    all_checkpoints_monotonic: false, checkpoints: [],
    enters_forward_metrics: false, promotion: 'NOT_EVALUATED',
    realtime_readiness: false, real_data_paper_readiness: false,
    external_order_transmission: false,
  }
  const publicAuditFallback: PlatformSnapshot['publicMinuteAudit'] = {
    classification: 'UNAVAILABLE', promotion: 'NOT_EVALUATED', first_round: null,
    repeat_round: null, all_valid: false, external_order_transmission: false,
  }
  const intradayFallback: PlatformSnapshot['intradayObservation'] = {
    classification: 'UNAVAILABLE', session_phase: 'UNAVAILABLE', symbol_count: 0,
    sector_count: 0, same_day_symbol_count: 0,
    synchronized_finalized_timestamp_count: 0,
    latest_synchronized_finalized_timestamp: null, observations: [],
    realtime_readiness: false, real_data_paper_readiness: false,
    promotion: 'NOT_EVALUATED', external_order_transmission: false,
  }
  const breadthFallback: PlatformSnapshot['intradayBreadth'] = {
    classification: 'UNAVAILABLE', coverage_scope: 'UNAVAILABLE',
    session_phase: 'UNAVAILABLE', symbol_count: 0, sector_count: 0,
    symbols_per_sector: 0, same_day_symbol_count: 0,
    synchronized_finalized_timestamp_count: 0,
    latest_synchronized_finalized_timestamp: null,
    directional_breadth: {
      eligible_symbol_count: 0, above_open_count: 0,
      unchanged_from_open_count: 0, below_open_count: 0,
      latest_minute_eligible_symbol_count: 0, latest_minute_up_count: 0,
      latest_minute_unchanged_count: 0, latest_minute_down_count: 0,
      equal_weight_session_return: null, equal_weight_latest_minute_return: null,
    },
    observations: [], sector_summaries: [], realtime_readiness: false,
    real_data_paper_readiness: false, promotion: 'NOT_EVALUATED',
    external_order_transmission: false,
  }
  const continuityFallback: PlatformSnapshot['intradayContinuity'] = {
    classification: 'UNAVAILABLE', trading_day: null, checkpoint_count: 0,
    directional_breadth_first_sequence: null,
    all_checkpoints_monotonic: false, checkpoints: [], realtime_readiness: false,
    real_data_paper_readiness: false, promotion: 'NOT_EVALUATED',
    external_order_transmission: false,
  }
  const deploymentFallback: PlatformSnapshot['deployment'] = {
    classification: 'PUBLIC_BUILD_PROVENANCE', provider: 'LOCAL', status: 'UNAVAILABLE',
    target_environment: null, git_commit_sha: null, git_commit_ref: null,
    external_order_transmission: false,
  }
  const minuteSnapshotFallback: MinuteResearchSnapshot = {
    classification: 'UNAVAILABLE', data_version: null, dataset_checksum: null,
    longFiveMinuteResearch: null, intradayFactorRobustness: null,
    reservedTailDiagnostic: null,
    developmentPolicySearch: null, forwardObservation: null,
    forwardSourceCoverage: null, forwardSourceCoverageHistory: null,
    publicMinuteAudit: null, intradayObservation: null, intradayBreadth: null,
    intradayContinuity: null, unavailable_sections: [], realtime_readiness: false,
    real_data_paper_readiness: false, external_order_transmission: false,
  }
  const [deployment, metrics, reports, reporting, strategies, strategyTemplates, experiments, walkForward, robustness, factorSearch, robustFactorSearch, stockSectorTransfer, minuteOverlay, minuteResearch, fiveMinuteResearch, minuteEvidence, backtestAnalytics, market, historicalBars, audit] = await Promise.all([
    getOptional<PlatformSnapshot['deployment']>('/deployment', deploymentFallback),
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
    getOptional<PlatformSnapshot['strategyTemplates']>('/strategy-templates', {
      classification: 'UNAVAILABLE', template_count: 0, active_template_count: 0, benchmark_template_count: 0, causal_preview_template_count: 0,
      templates: [], selection_creates_experiment: false, selection_registers_strategy: false,
      paper_runtime_start: false, live_runtime_start: false, arbitrary_code_execution: false,
      external_order_transmission: false, next_step: 'FREEZE_EXPERIMENT_RUN_SPEC_THEN_CAUSAL_BACKTEST',
    }),
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
    getOptional<MinuteResearchSnapshot>('/minute-research-snapshot', minuteSnapshotFallback),
    getOptional<PlatformSnapshot['backtestAnalytics']>('/backtest-analytics', {
      classification: 'UNAVAILABLE', promotion: 'NOT_EVALUATED', leader: null, curve: [],
    }),
    getOptional<PlatformSnapshot['market']>('/market', {
      provider_connected: false, execution_gateway_connected: health.gateway_connected, mode: system.mode, snapshot_readiness: false, historical: null,
    }),
    getOptional<PlatformSnapshot['historicalBars']>('/historical-bars/latest', []),
    getOptional<PlatformSnapshot['audit']>('/audit', null),
  ])
  const bundledError = minuteEvidence.error
  const bundledUnavailable = new Set(minuteEvidence.value.unavailable_sections)
  const longFiveMinuteResearch = getBundledSection('longFiveMinuteResearch', minuteEvidence.value.longFiveMinuteResearch, longFiveMinuteFallback, bundledError, bundledUnavailable)
  const intradayFactorRobustness = getBundledSection('intradayFactorRobustness', minuteEvidence.value.intradayFactorRobustness, intradayFactorRobustnessFallback, bundledError, bundledUnavailable)
  const reservedTailDiagnostic = getBundledSection('reservedTailDiagnostic', minuteEvidence.value.reservedTailDiagnostic, reservedTailFallback, bundledError, bundledUnavailable)
  const developmentPolicySearch = getBundledSection('developmentPolicySearch', minuteEvidence.value.developmentPolicySearch, developmentFallback, bundledError, bundledUnavailable)
  const forwardObservation = getBundledSection('forwardObservation', minuteEvidence.value.forwardObservation, forwardFallback, bundledError, bundledUnavailable)
  const forwardSourceCoverage = getBundledSection('forwardSourceCoverage', minuteEvidence.value.forwardSourceCoverage, forwardSourceCoverageFallback, bundledError, bundledUnavailable)
  const forwardSourceCoverageHistory = getBundledSection('forwardSourceCoverageHistory', minuteEvidence.value.forwardSourceCoverageHistory, forwardSourceCoverageHistoryFallback, bundledError, bundledUnavailable)
  const publicMinuteAudit = getBundledSection('publicMinuteAudit', minuteEvidence.value.publicMinuteAudit, publicAuditFallback, bundledError, bundledUnavailable)
  const intradayObservation = getBundledSection('intradayObservation', minuteEvidence.value.intradayObservation, intradayFallback, bundledError, bundledUnavailable)
  const intradayBreadth = getBundledSection('intradayBreadth', minuteEvidence.value.intradayBreadth, breadthFallback, bundledError, bundledUnavailable)
  const intradayContinuity = getBundledSection('intradayContinuity', minuteEvidence.value.intradayContinuity, continuityFallback, bundledError, bundledUnavailable)
  const sections = { deployment, metrics, reports, reporting, strategies, strategyTemplates, experiments, walkForward, robustness, factorSearch, robustFactorSearch, stockSectorTransfer, minuteOverlay, minuteResearch, fiveMinuteResearch, longFiveMinuteResearch, intradayFactorRobustness, reservedTailDiagnostic, developmentPolicySearch, forwardObservation, forwardSourceCoverage, forwardSourceCoverageHistory, publicMinuteAudit, intradayObservation, intradayBreadth, intradayContinuity, backtestAnalytics, market, historicalBars, audit }
  const failures = Object.fromEntries(Object.entries(sections).flatMap(([name, result]) => result.error ? [[name, result.error]] : []))
  return {
    system, health, deployment: deployment.value, ...paper,
    metrics: metrics.value, reports: reports.value, reporting: reporting.value, strategies: strategies.value,
    strategyTemplates: strategyTemplates.value,
    experiments: experiments.value, walkForward: walkForward.value, robustness: robustness.value,
    factorSearch: factorSearch.value, robustFactorSearch: robustFactorSearch.value,
    stockSectorTransfer: stockSectorTransfer.value, minuteOverlay: minuteOverlay.value,
    minuteResearch: minuteResearch.value,
    fiveMinuteResearch: fiveMinuteResearch.value,
    longFiveMinuteResearch: longFiveMinuteResearch.value,
    intradayFactorRobustness: intradayFactorRobustness.value,
    reservedTailDiagnostic: reservedTailDiagnostic.value,
    developmentPolicySearch: developmentPolicySearch.value,
    forwardObservation: forwardObservation.value,
    forwardSourceCoverage: forwardSourceCoverage.value,
    forwardSourceCoverageHistory: forwardSourceCoverageHistory.value,
    publicMinuteAudit: publicMinuteAudit.value,
    intradayObservation: intradayObservation.value,
    intradayBreadth: intradayBreadth.value,
    intradayContinuity: intradayContinuity.value,
    backtestAnalytics: backtestAnalytics.value, market: market.value,
    historicalBars: historicalBars.value, audit: audit.value, availability: { failures },
  }
}
