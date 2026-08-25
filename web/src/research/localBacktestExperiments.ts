import type { BacktestPreview, BacktestPreviewRequest, HistoricalDataset } from '../types'

const STORAGE_KEY = 'qtrade.local-backtest-experiments.v1'
const MAX_EXPERIMENTS = 30

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
}

export function loadLocalBacktestExperiments(
  dataset: HistoricalDataset,
): LocalBacktestExperiment[] {
  return readAll().filter((record) => (
    record.data_version === dataset.data_version
    && record.data_checksum === dataset.checksum
  ))
}

export function saveLocalBacktestExperiment(
  name: string,
  result: BacktestPreview,
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
    return parsed.filter(isExperiment).slice(0, MAX_EXPERIMENTS)
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
