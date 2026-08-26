import type {
  FiveMinuteResearchReport,
  IntradayFactorBacktest,
  IntradayFactorBacktestRequest,
} from '../types'

const STORAGE_KEY = 'qtrade.local-intraday-factor-experiments.v1'
const MAX_EXPERIMENTS = 30

type Dataset = NonNullable<FiveMinuteResearchReport['minute_dataset']>

export interface LocalIntradayFactorExperiment {
  schema_version: 1
  name: string
  saved_at: string
  preview_hash: string
  data_version: string
  dataset_checksum: string
  selected_session_count: number
  symbol_count: number
  sector_count: number
  parameters: IntradayFactorBacktestRequest
  factor_definition: {
    factor_id: string
    name: string
    expression: string
  }
  observation_count: number
  signal_count: number
  metrics: {
    total_return: string
    annualized_return: string
    sharpe: string
    maximum_drawdown: string
    excess_return: string
    rank_ic: string
    maximum_absolute_correlation: string
    stitched_oos_total_return: string
    stitched_oos_excess_return: string
  }
  research_gate_passed: boolean
  robustness_gate_passed: boolean
  promotion: 'NOT_ELIGIBLE'
  state_mutation: false
  external_order_transmission: false
}

export function loadLocalIntradayFactorExperiments(
  dataset: Pick<Dataset, 'data_version' | 'checksum'>,
): LocalIntradayFactorExperiment[] {
  return readAll().filter((record) => (
    record.data_version === dataset.data_version
    && record.dataset_checksum === dataset.checksum
  ))
}

export function saveLocalIntradayFactorExperiment(
  name: string,
  result: IntradayFactorBacktest,
): LocalIntradayFactorExperiment {
  const normalizedName = name.trim()
  if (!normalizedName || normalizedName.length > 80) {
    throw new Error('实验名称需要 1-80 个字符')
  }
  const record: LocalIntradayFactorExperiment = {
    schema_version: 1,
    name: normalizedName,
    saved_at: new Date().toISOString(),
    preview_hash: result.preview_hash,
    data_version: result.data_version,
    dataset_checksum: result.dataset_checksum,
    selected_session_count: result.selected_session_count,
    symbol_count: result.symbol_count,
    sector_count: result.sector_count,
    parameters: {
      ...result.parameters,
      directions: [...result.parameters.directions],
    },
    factor_definition: {
      factor_id: result.factor_definition.factor_id,
      name: result.factor_definition.name,
      expression: result.factor_definition.expression,
    },
    observation_count: result.event_summary.observation_count,
    signal_count: result.event_summary.signal_count,
    metrics: {
      total_return: result.backtest.strategy_metrics.total_return,
      annualized_return: result.backtest.strategy_metrics.annualized_return,
      sharpe: result.backtest.strategy_metrics.sharpe,
      maximum_drawdown: result.backtest.strategy_metrics.maximum_drawdown,
      excess_return: result.backtest.strategy_metrics.excess_return,
      rank_ic: result.factor_validation.rank_ic,
      maximum_absolute_correlation: (
        result.correlation_validation.maximum_absolute_correlation
      ),
      stitched_oos_total_return: (
        result.robustness_validation.stitched_strategy_metrics.total_return
      ),
      stitched_oos_excess_return: (
        result.robustness_validation.stitched_strategy_metrics.excess_return
      ),
    },
    research_gate_passed: result.research_gate.passed,
    robustness_gate_passed: result.robustness_validation.passed,
    promotion: result.promotion,
    state_mutation: result.state_mutation,
    external_order_transmission: result.external_order_transmission,
  }
  if (!isExperiment(record)) {
    throw new Error('因子结果不完整或安全边界不匹配，未保存')
  }
  const next = [
    record,
    ...readAll().filter((item) => item.preview_hash !== record.preview_hash),
  ].slice(0, MAX_EXPERIMENTS)
  writeAll(next)
  return record
}

export function deleteLocalIntradayFactorExperiment(previewHash: string): void {
  writeAll(readAll().filter((record) => record.preview_hash !== previewHash))
}

function readAll(): LocalIntradayFactorExperiment[] {
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

function writeAll(records: LocalIntradayFactorExperiment[]): void {
  try {
    window.localStorage.setItem(STORAGE_KEY, JSON.stringify(records))
  } catch {
    throw new Error('浏览器本地存储不可用，实验未保存')
  }
}

function isExperiment(value: unknown): value is LocalIntradayFactorExperiment {
  if (!isObject(value)) return false
  return value.schema_version === 1
    && isBoundedString(value.name, 80)
    && isIsoTimestamp(value.saved_at)
    && isHexHash(value.preview_hash)
    && isBoundedString(value.data_version, 128)
    && isBoundedString(value.dataset_checksum, 128)
    && isPositiveInteger(value.selected_session_count)
    && isPositiveInteger(value.symbol_count)
    && isPositiveInteger(value.sector_count)
    && isRequest(value.parameters)
    && isFactorDefinition(value.factor_definition)
    && matchesFactorDefinition(value.parameters, value.factor_definition)
    && isPositiveInteger(value.observation_count)
    && isNonNegativeInteger(value.signal_count)
    && isMetrics(value.metrics)
    && typeof value.research_gate_passed === 'boolean'
    && typeof value.robustness_gate_passed === 'boolean'
    && value.promotion === 'NOT_ELIGIBLE'
    && value.state_mutation === false
    && value.external_order_transmission === false
}

function matchesFactorDefinition(
  parameters: IntradayFactorBacktestRequest,
  definition: LocalIntradayFactorExperiment['factor_definition'],
): boolean {
  const fixed = parameters.directions.every((direction) => direction === 'BULLISH')
    && parameters.combination === 'ALL'
  const labels = ['0930_0940', '0940_0950', '0950_1000']
  const clauses = parameters.directions.flatMap((direction, index) => (
    direction === 'IGNORE'
      ? []
      : [`${direction.toLowerCase()}_10m_${labels[index]}`]
  ))
  return definition.factor_id === (
    fixed
      ? 'opening-three-ten-minute-bullish-v1'
      : 'opening-ten-minute-direction-rule-v1'
  )
    && definition.name === (
      fixed ? '09:30 起三根十分钟阳线' : '自定义开盘十分钟方向规则'
    )
    && definition.expression === clauses.join(
      parameters.combination === 'ALL' ? ' AND ' : ' OR ',
    )
}

function isRequest(value: unknown): value is IntradayFactorBacktestRequest {
  if (!isObject(value)) return false
  if (!isDate(value.start) || !isDate(value.end) || value.start > value.end) return false
  if (!isDecimalString(value.cost_bps) || !isDecimalString(value.minimum_body_return_bps)) {
    return false
  }
  if (value.factor !== 'opening_three_ten_minute_bullish'
    && value.factor !== 'custom_opening_ten_minute_direction_rule') return false
  if (!Array.isArray(value.directions) || value.directions.length !== 3
    || !value.directions.every(isDirection)
    || value.directions.every((direction) => direction === 'IGNORE')) return false
  if (value.combination !== 'ALL' && value.combination !== 'ANY') return false
  return value.factor !== 'opening_three_ten_minute_bullish'
    || (value.combination === 'ALL'
      && value.directions.every((direction) => direction === 'BULLISH'))
}

function isFactorDefinition(value: unknown): value is LocalIntradayFactorExperiment['factor_definition'] {
  return isObject(value)
    && isBoundedString(value.factor_id, 128)
    && isBoundedString(value.name, 128)
    && isBoundedString(value.expression, 512)
}

function isMetrics(value: unknown): value is LocalIntradayFactorExperiment['metrics'] {
  if (!isObject(value)) return false
  return [
    value.total_return,
    value.annualized_return,
    value.sharpe,
    value.maximum_drawdown,
    value.excess_return,
    value.rank_ic,
    value.maximum_absolute_correlation,
    value.stitched_oos_total_return,
    value.stitched_oos_excess_return,
  ].every(isSignedDecimalString)
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

function isNonNegativeInteger(value: unknown): value is number {
  return typeof value === 'number' && Number.isInteger(value) && value >= 0
}

function isDirection(value: unknown): boolean {
  return value === 'BULLISH' || value === 'BEARISH' || value === 'IGNORE'
}

function isDecimalString(value: unknown): value is string {
  return typeof value === 'string' && /^\d+(\.\d+)?$/.test(value)
}

function isSignedDecimalString(value: unknown): value is string {
  return typeof value === 'string' && /^-?\d+(\.\d+)?$/.test(value)
}
