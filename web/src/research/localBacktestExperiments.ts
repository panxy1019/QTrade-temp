import type {
  BacktestPreview,
  BacktestPreviewRequest,
  CausalPreviewTemplateId,
  ExperimentRunSpecBacktest,
  HistoricalDataset,
} from '../types'

const STORAGE_KEY = 'qtrade.local-backtest-experiments.v1'
const MAX_EXPERIMENTS = 30

export interface LocalBacktestBinding {
  classification: 'BROWSER_LOCAL_REPLAY_BINDING_NOT_IMMUTABLE'
  template_id: CausalPreviewTemplateId
  spec_hash: string
  binding_hash: string
  creates_immutable_experiment: false
  registry_write: false
  paper_runtime_start: false
  live_runtime_start: false
}

export interface LocalBacktestExperiment {
  schema_version: 1
  name: string
  saved_at: string
  preview_hash: string
  data_version: string
  data_checksum: string
  session_count: number
  universe_size: number
  parameters: BacktestPreviewRequest
  metrics: Record<string, string>
  binding: LocalBacktestBinding | null
}

export interface LocalBacktestComparisonExport {
  schema_version: 1
  classification: 'BROWSER_LOCAL_BACKTEST_COMPARISON_EXPORT_NOT_IMMUTABLE'
  data_identity: {
    provider: string
    provider_version: string
    data_version: string
    data_checksum: string
    point_in_time: boolean
  }
  record_count: number
  records: LocalBacktestExperiment[]
  creates_immutable_experiment: false
  registry_write: false
  paper_runtime_start: false
  live_runtime_start: false
  state_mutation: false
  external_order_transmission: false
}

export function loadLocalBacktestExperiments(
  dataset: HistoricalDataset,
): LocalBacktestExperiment[] {
  return readAll().filter((record) => (
    record.data_version === dataset.data_version
    && record.data_checksum === dataset.checksum
  ))
}

export function buildLocalBacktestComparisonExport(
  dataset: HistoricalDataset,
): LocalBacktestComparisonExport {
  const records = loadLocalBacktestExperiments(dataset)
  return {
    schema_version: 1,
    classification: 'BROWSER_LOCAL_BACKTEST_COMPARISON_EXPORT_NOT_IMMUTABLE',
    data_identity: {
      provider: dataset.provider,
      provider_version: dataset.provider_version,
      data_version: dataset.data_version,
      data_checksum: dataset.checksum,
      point_in_time: dataset.universe.point_in_time,
    },
    record_count: records.length,
    records,
    creates_immutable_experiment: false,
    registry_write: false,
    paper_runtime_start: false,
    live_runtime_start: false,
    state_mutation: false,
    external_order_transmission: false,
  }
}

export function saveLocalBacktestExperiment(
  name: string,
  result: BacktestPreview,
  boundBacktest: ExperimentRunSpecBacktest | null = null,
): LocalBacktestExperiment {
  const normalizedName = name.trim()
  if (!normalizedName || normalizedName.length > 80) {
    throw new Error('实验名称需要 1-80 个字符')
  }
  const record: LocalBacktestExperiment = {
    schema_version: 1,
    name: normalizedName,
    saved_at: new Date().toISOString(),
    preview_hash: result.preview_hash,
    data_version: result.data_version,
    data_checksum: result.data_checksum,
    session_count: result.session_count,
    universe_size: result.universe_size,
    parameters: { ...result.parameters, horizons: [...result.parameters.horizons] },
    metrics: { ...result.metrics },
    binding: boundBacktest ? createBinding(result, boundBacktest) : null,
  }
  if (!isExperiment(record)) {
    throw new Error('回测结果不完整，未保存')
  }
  const next = [
    record,
    ...readAll().filter((item) => item.preview_hash !== record.preview_hash),
  ].slice(0, MAX_EXPERIMENTS)
  writeAll(next)
  return record
}

export function deleteLocalBacktestExperiment(previewHash: string): void {
  writeAll(readAll().filter((record) => record.preview_hash !== previewHash))
}

function readAll(): LocalBacktestExperiment[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed
      .map(parseExperiment)
      .filter((record): record is LocalBacktestExperiment => record !== null)
      .slice(0, MAX_EXPERIMENTS)
  } catch {
    return []
  }
}

function writeAll(records: LocalBacktestExperiment[]): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(records))
  } catch {
    throw new Error('浏览器本地存储不可用，实验未保存')
  }
}

function isExperiment(value: unknown): value is LocalBacktestExperiment {
  if (!isObject(value)) return false
  return value.schema_version === 1
    && isBoundedString(value.name, 80)
    && isIsoTimestamp(value.saved_at)
    && isHexHash(value.preview_hash)
    && isBoundedString(value.data_version, 128)
    && isBoundedString(value.data_checksum, 128)
    && isPositiveInteger(value.session_count)
    && isPositiveInteger(value.universe_size)
    && isRequest(value.parameters)
    && isMetrics(value.metrics)
    && (value.binding === null || isBinding(value.binding))
}

function parseExperiment(value: unknown): LocalBacktestExperiment | null {
  if (!isObject(value)) return null
  const normalized = { ...value, binding: value.binding === undefined ? null : value.binding }
  return isExperiment(normalized) ? normalized : null
}

function createBinding(
  result: BacktestPreview,
  boundBacktest: ExperimentRunSpecBacktest,
): LocalBacktestBinding {
  if (
    boundBacktest.classification !== 'READ_ONLY_HASH_BOUND_EXPERIMENT_PREVIEW_NOT_PERSISTED'
    || !isTemplateId(boundBacktest.template_id)
    || !isHexHash(boundBacktest.spec_hash)
    || !isHexHash(boundBacktest.binding_hash)
    || boundBacktest.preview_hash !== result.preview_hash
    || boundBacktest.result.preview_hash !== result.preview_hash
    || boundBacktest.data_checksum !== result.data_checksum
    || boundBacktest.result.data_checksum !== result.data_checksum
    || boundBacktest.result.data_version !== result.data_version
    || boundBacktest.spec.data_version !== result.data_version
    || boundBacktest.spec.strategy_id !== boundBacktest.template_id
    || boundBacktest.gates.single_request_spec_result_binding !== true
    || boundBacktest.gates.spec_hash_recomputed_before_run !== true
    || boundBacktest.gates.verified_dataset_reloaded_before_run !== true
    || boundBacktest.gates.preview_hash_bound_to_spec_hash !== true
    || boundBacktest.gates.immutable_artifact_persisted !== false
    || boundBacktest.gates.independent_leakage_review_passed !== false
    || boundBacktest.gates.fresh_unseen_test_evidence !== false
    || boundBacktest.creates_immutable_experiment !== false
    || boundBacktest.registry_write !== false
    || boundBacktest.paper_runtime_start !== false
    || boundBacktest.live_runtime_start !== false
    || boundBacktest.state_mutation !== false
    || boundBacktest.external_order_transmission !== false
    || boundBacktest.result.external_order_transmission !== false
  ) {
    throw new Error('规格绑定与当前回测结果不一致，未保存')
  }
  return {
    classification: 'BROWSER_LOCAL_REPLAY_BINDING_NOT_IMMUTABLE',
    template_id: boundBacktest.template_id,
    spec_hash: boundBacktest.spec_hash,
    binding_hash: boundBacktest.binding_hash,
    creates_immutable_experiment: false,
    registry_write: false,
    paper_runtime_start: false,
    live_runtime_start: false,
  }
}

function isBinding(value: unknown): value is LocalBacktestBinding {
  if (!isObject(value)) return false
  return value.classification === 'BROWSER_LOCAL_REPLAY_BINDING_NOT_IMMUTABLE'
    && isTemplateId(value.template_id)
    && isHexHash(value.spec_hash)
    && isHexHash(value.binding_hash)
    && value.creates_immutable_experiment === false
    && value.registry_write === false
    && value.paper_runtime_start === false
    && value.live_runtime_start === false
}

function isTemplateId(value: unknown): value is CausalPreviewTemplateId {
  return value === 'etf-momentum-rotation-v1'
    || value === 'etf-momentum-trend-volatility-v1'
    || value === 'cross-sectional-factor-ranking-v1'
}

function isRequest(value: unknown): value is BacktestPreviewRequest {
  if (!isObject(value)) return false
  return isDate(value.start)
    && isDate(value.end)
    && isFactor(value.factor)
    && Array.isArray(value.horizons)
    && value.horizons.length >= 1
    && value.horizons.length <= 3
    && value.horizons.every(isPositiveInteger)
    && isPositiveInteger(value.top_k)
    && isPositiveInteger(value.rebalance_every)
    && isDecimalString(value.reserve)
    && isNullablePositiveInteger(value.trend_window)
    && isNullablePositiveInteger(value.volatility_window)
    && isNullableDecimalString(value.target_annual_volatility)
    && isDecimalString(value.initial_cash)
    && isDecimalString(value.transaction_cost_rate)
    && isDecimalString(value.slippage_rate)
}

function isFactor(value: unknown): value is BacktestPreviewRequest['factor'] {
  return value === 'momentum' || value === 'reversal' || value === 'low_volatility'
}

function isMetrics(value: unknown): value is Record<string, string> {
  if (!isObject(value)) return false
  const entries = Object.entries(value)
  return entries.length > 0
    && entries.length <= 32
    && entries.every(([key, item]) => (
      isBoundedString(key, 64) && isBoundedString(item, 128)
    ))
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}

function isBoundedString(value: unknown, maximum: number): value is string {
  return typeof value === 'string' && value.length >= 1 && value.length <= maximum
}

function isHexHash(value: unknown): value is string {
  return typeof value === 'string' && /^[0-9a-f]{64}$/.test(value)
}

function isIsoTimestamp(value: unknown): value is string {
  return typeof value === 'string'
    && value.length <= 64
    && Number.isFinite(Date.parse(value))
}

function isDate(value: unknown): value is string {
  return typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/.test(value)
}

function isPositiveInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value > 0
}

function isNullablePositiveInteger(value: unknown): value is number | null {
  return value === null || isPositiveInteger(value)
}

function isDecimalString(value: unknown): value is string {
  return typeof value === 'string' && /^\d+(\.\d+)?$/.test(value)
}

function isNullableDecimalString(value: unknown): value is string | null {
  return value === null || isDecimalString(value)
}
