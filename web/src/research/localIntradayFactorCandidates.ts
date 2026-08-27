import type {
  FiveMinuteResearchReport,
  IntradayFactorCorrelationRule,
  IntradayOpeningDirection,
} from '../types'

const STORAGE_KEY = 'qtrade.local-intraday-factor-candidates.v1'
const MAX_CANDIDATES = 30
const DIRECTION_VALUES = ['BULLISH', 'BEARISH', 'IGNORE'] as const
const SOURCE_WINDOWS = ['0930_0940', '0940_0950', '0950_1000'] as const

type Dataset = Pick<
  NonNullable<FiveMinuteResearchReport['minute_dataset']>,
  'data_version' | 'checksum'
>

export interface LocalIntradayFactorCandidate extends IntradayFactorCorrelationRule {
  schema_version: 1
  classification: 'BROWSER_LOCAL_ENTRY_FACTOR_DRAFT_NOT_EVIDENCE'
  draft_key: string
  name: string
  saved_at: string
  data_version: string
  dataset_checksum: string
  expression: string
  validation_status: 'NOT_RUN'
  strategy_constructed: false
  strategy_backtest_run: false
  factor_registry_write: false
  strategy_registry_write: false
  promotion: 'NOT_ELIGIBLE'
  state_mutation: false
  external_order_transmission: false
}

export function buildLocalIntradayFactorExpression(
  directions: readonly IntradayOpeningDirection[],
  combination: 'ALL' | 'ANY',
  minimumBodyReturnBps: string,
): string {
  const threshold = normalizeThreshold(minimumBodyReturnBps)
  const parts = directions.flatMap((direction, index) => {
    if (direction === 'IGNORE') return []
    const operator = direction === 'BULLISH' ? '>=' : '<='
    return [`body_return_${SOURCE_WINDOWS[index]} ${operator} ${direction === 'BULLISH' ? threshold : `-${threshold}`} bps`]
  })
  if (parts.length === 0) {
    throw new Error('至少选择一个有效的十分钟方向条件')
  }
  return parts.join(combination === 'ALL' ? ' AND ' : ' OR ')
}

export function loadLocalIntradayFactorCandidates(
  dataset: Dataset,
): LocalIntradayFactorCandidate[] {
  return readAll().filter((candidate) => (
    candidate.data_version === dataset.data_version
    && candidate.dataset_checksum === dataset.checksum
  ))
}

export function saveLocalIntradayFactorCandidate(
  name: string,
  dataset: Dataset,
  rule: IntradayFactorCorrelationRule,
): LocalIntradayFactorCandidate {
  const normalizedName = name.trim()
  if (!normalizedName || normalizedName.length > 80) {
    throw new Error('候选名称需要 1-80 个字符')
  }
  const directions = normalizeDirections(rule.directions)
  const combination = normalizeCombination(rule.combination)
  const minimumBodyReturnBps = normalizeThreshold(rule.minimum_body_return_bps)
  const draftKey = buildDraftKey(directions, combination, minimumBodyReturnBps)
  const candidate: LocalIntradayFactorCandidate = {
    schema_version: 1,
    classification: 'BROWSER_LOCAL_ENTRY_FACTOR_DRAFT_NOT_EVIDENCE',
    draft_key: draftKey,
    name: normalizedName,
    saved_at: new Date().toISOString(),
    data_version: dataset.data_version,
    dataset_checksum: dataset.checksum,
    directions,
    combination,
    minimum_body_return_bps: minimumBodyReturnBps,
    expression: buildLocalIntradayFactorExpression(
      directions,
      combination,
      minimumBodyReturnBps,
    ),
    validation_status: 'NOT_RUN',
    strategy_constructed: false,
    strategy_backtest_run: false,
    factor_registry_write: false,
    strategy_registry_write: false,
    promotion: 'NOT_ELIGIBLE',
    state_mutation: false,
    external_order_transmission: false,
  }
  const next = [
    candidate,
    ...readAll().filter((item) => item.draft_key !== draftKey),
  ].slice(0, MAX_CANDIDATES)
  writeAll(next)
  return candidate
}

export function deleteLocalIntradayFactorCandidate(draftKey: string): void {
  writeAll(readAll().filter((candidate) => candidate.draft_key !== draftKey))
}

function buildDraftKey(
  directions: readonly IntradayOpeningDirection[],
  combination: 'ALL' | 'ANY',
  threshold: string,
): string {
  return `${directions.join('|')}:${combination}:${threshold}`
}

function normalizeDirections(
  value: readonly IntradayOpeningDirection[],
): [IntradayOpeningDirection, IntradayOpeningDirection, IntradayOpeningDirection] {
  if (value.length !== 3 || value.some((direction) => !DIRECTION_VALUES.includes(direction))) {
    throw new Error('开仓方向必须是三段受限十分钟条件')
  }
  if (value.every((direction) => direction === 'IGNORE')) {
    throw new Error('至少选择一个有效的十分钟方向条件')
  }
  return [value[0], value[1], value[2]]
}

function normalizeCombination(value: unknown): 'ALL' | 'ANY' {
  if (value !== 'ALL' && value !== 'ANY') {
    throw new Error('条件组合只能是 ALL 或 ANY')
  }
  return value
}

function normalizeThreshold(value: string): string {
  const normalized = value.trim()
  if (!/^\d+(?:\.\d+)?$/.test(normalized)) {
    throw new Error('实体阈值需要在 0-1000 bps 之间')
  }
  const numeric = Number(normalized)
  if (!Number.isFinite(numeric) || numeric < 0 || numeric > 1000) {
    throw new Error('实体阈值需要在 0-1000 bps 之间')
  }
  return String(numeric)
}

function readAll(): LocalIntradayFactorCandidate[] {
  try {
    const raw = window.localStorage.getItem(STORAGE_KEY)
    if (!raw) return []
    const parsed: unknown = JSON.parse(raw)
    if (!Array.isArray(parsed)) return []
    return parsed
      .filter(isCandidate)
      .slice(0, MAX_CANDIDATES)
  } catch {
    return []
  }
}

function writeAll(candidates: LocalIntradayFactorCandidate[]): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(candidates))
  } catch {
    throw new Error('浏览器本地存储不可用，因子候选未保存')
  }
}

function isCandidate(value: unknown): value is LocalIntradayFactorCandidate {
  if (!isObject(value)) return false
  try {
    const directions = normalizeDirections(value.directions as IntradayOpeningDirection[])
    const combination = normalizeCombination(value.combination)
    const threshold = normalizeThreshold(String(value.minimum_body_return_bps))
    return value.schema_version === 1
      && value.classification === 'BROWSER_LOCAL_ENTRY_FACTOR_DRAFT_NOT_EVIDENCE'
      && typeof value.name === 'string'
      && value.name.trim().length > 0
      && value.name.length <= 80
      && typeof value.saved_at === 'string'
      && typeof value.data_version === 'string'
      && typeof value.dataset_checksum === 'string'
      && value.draft_key === buildDraftKey(directions, combination, threshold)
      && value.expression === buildLocalIntradayFactorExpression(directions, combination, threshold)
      && value.validation_status === 'NOT_RUN'
      && value.strategy_constructed === false
      && value.strategy_backtest_run === false
      && value.factor_registry_write === false
      && value.strategy_registry_write === false
      && value.promotion === 'NOT_ELIGIBLE'
      && value.state_mutation === false
      && value.external_order_transmission === false
  } catch {
    return false
  }
}

function isObject(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
