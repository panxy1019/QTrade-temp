import type { IntradayFactorBacktest, IntradayFactorBacktestRequest, IntradayOpeningDirection } from '../types'

const openingLabels = ['0930_0940', '0940_0950', '0950_1000'] as const
const allowedFeatures = [
  'TEN_MINUTE_BODY_0930_0940',
  'TEN_MINUTE_BODY_0940_0950',
  'TEN_MINUTE_BODY_0950_1000',
]

type Strategy = IntradayFactorBacktest['strategy']
type StrategyIdentity = IntradayFactorBacktest['strategy_identity']

interface ObservedExitCounts {
  signalCount: number
  exitTriggerCount?: number
}

export function matchesIntradayFactorDefinition(
  definition: unknown,
  request: IntradayFactorBacktestRequest,
): boolean {
  const factor = expectedOpeningFactor(request)
  if (!factor) return false
  return sameValue(definition, {
    factor_id: factor.id,
    name: factor.name,
    expression: factor.expression,
    source_interval: 'NATIVE_5M_AGGREGATED_TO_10M',
    source_timestamp_convention: 'BAR_END_TIME',
    source_bars: '09:35,09:40,09:45,09:50,09:55,10:00',
    known_at: '10:00_BAR_CLOSE',
    minimum_body_return_bps: request.minimum_body_return_bps,
    directions: request.directions,
    combination: request.combination,
    allowed_features: allowedFeatures,
    arbitrary_code_execution: false,
  })
}

export async function matchesIntradayStrategyIdentity(
  strategy: unknown,
  identity: unknown,
  request: IntradayFactorBacktestRequest,
  observed: ObservedExitCounts,
): Promise<boolean> {
  const strategySpec = expectedStrategySpec(request)
  const expectedStrategy = expectedStrategyWithObservedCounts(strategySpec, request, observed)
  if (!strategySpec || !expectedStrategy || !sameValue(strategy, expectedStrategy)) return false
  const specHash = await sha256Hex(canonicalJson(strategySpec))
  if (!specHash) return false
  return sameValue(identity, {
    classification: 'AD_HOC_READ_ONLY_STRATEGY_IDENTITY',
    strategy_id: `adhoc-intraday-${specHash.slice(0, 16)}`,
    spec_hash: specHash,
    spec_hash_scope: 'strategy_without_observed_exit_counts',
    registration_status: 'AD_HOC_UNREGISTERED',
    immutable_within_response: true,
    paper_eligible: false,
    promotion_authority: false,
    state_mutation: false,
    external_order_transmission: false,
  } satisfies StrategyIdentity)
}

function expectedStrategySpec(request: IntradayFactorBacktestRequest): Omit<Strategy, 'conditional_exit_count' | 'fallback_time_stop_count'> | null {
  const factor = expectedOpeningFactor(request)
  if (!factor) return null
  const base = {
    entry_factor_id: factor.id,
    entry_condition: factor.expression,
    entry_minimum_body_return_bps: request.minimum_body_return_bps,
    entry_directions: request.directions,
    entry_combination: request.combination,
    entry_rule: 'NEXT_5M_OPEN_10_05',
    entry_price_source: 'OPEN_OF_BAR_LABELLED_10_05_INTERVAL_START_10_00',
    position_rule: 'EQUAL_WEIGHT_TRIGGERED_STOCKS_ELSE_CASH',
    single_side_cost_bps: request.cost_bps,
    t_plus_one_compliant: true as const,
  }
  if ((request.exit_rule ?? 'NEXT_SESSION_OPEN') === 'NEXT_SESSION_OPEN') {
    return {
      ...base,
      exit_rule: 'NEXT_SESSION_OPEN_T_PLUS_ONE',
      exit_condition_type: 'TIME_EXIT',
      exit_price_source: 'OPEN_OF_NEXT_SESSION_BAR_LABELLED_09_35_INTERVAL_START_09_30',
      benchmark_rule: 'EQUAL_WEIGHT_ALL_ELIGIBLE_STOCKS_SAME_HORIZON',
    }
  }
  return {
    ...base,
    exit_rule: 'NEXT_SESSION_THREE_TEN_MINUTE_BEARISH_ELSE_14_55_TIME_STOP',
    exit_condition_type: 'FACTOR_THEN_TIME_STOP',
    exit_factor_id: 'next-session-three-ten-minute-bearish-v1',
    exit_condition: 'bearish_10m_0930_0940 AND bearish_10m_0940_0950 AND bearish_10m_0950_1000',
    exit_minimum_body_return_bps: request.exit_minimum_body_return_bps ?? '0',
    exit_condition_known_at: 'NEXT_SESSION_10_00_BAR_CLOSE_T_PLUS_ONE',
    conditional_exit_price_source: 'OPEN_OF_NEXT_SESSION_BAR_LABELLED_10_05_INTERVAL_START_10_00',
    fallback_exit_price_source: 'OPEN_OF_NEXT_SESSION_BAR_LABELLED_15_00_INTERVAL_START_14_55',
    benchmark_rule: 'EQUAL_WEIGHT_ALL_ELIGIBLE_STOCKS_SAME_PER_SYMBOL_EXIT_RULE',
  }
}

function expectedStrategyWithObservedCounts(
  strategySpec: ReturnType<typeof expectedStrategySpec>,
  request: IntradayFactorBacktestRequest,
  observed: ObservedExitCounts,
): Strategy | null {
  if (!strategySpec || !Number.isSafeInteger(observed.signalCount) || observed.signalCount < 0) return null
  if ((request.exit_rule ?? 'NEXT_SESSION_OPEN') === 'NEXT_SESSION_OPEN') {
    return observed.exitTriggerCount === undefined ? strategySpec : null
  }
  const triggerCount = observed.exitTriggerCount
  if (!Number.isSafeInteger(triggerCount) || triggerCount === undefined || triggerCount < 0 || triggerCount > observed.signalCount) return null
  return {
    ...strategySpec,
    conditional_exit_count: triggerCount,
    fallback_time_stop_count: observed.signalCount - triggerCount,
  }
}

function expectedOpeningFactor(request: IntradayFactorBacktestRequest): { id: string; name: string; expression: string } | null {
  if (request.directions.length !== 3 || !request.directions.some((direction) => direction !== 'IGNORE')) return null
  if (request.factor === 'opening_three_ten_minute_bullish'
    && (!sameDirections(request.directions, ['BULLISH', 'BULLISH', 'BULLISH']) || request.combination !== 'ALL')) return null
  const fixedBullish = sameDirections(request.directions, ['BULLISH', 'BULLISH', 'BULLISH'])
    && request.combination === 'ALL'
  const operator = request.combination === 'ALL' ? 'AND' : 'OR'
  const expression = request.directions
    .map((direction, index) => direction === 'IGNORE' ? null : `${direction.toLowerCase()}_10m_${openingLabels[index]}`)
    .filter((clause): clause is string => clause !== null)
    .join(` ${operator} `)
  return {
    id: fixedBullish ? 'opening-three-ten-minute-bullish-v1' : 'opening-ten-minute-direction-rule-v1',
    name: fixedBullish ? '09:30 起三根十分钟阳线' : '自定义开盘十分钟方向规则',
    expression,
  }
}

function sameDirections(left: readonly IntradayOpeningDirection[], right: readonly IntradayOpeningDirection[]): boolean {
  return left.length === right.length && left.every((direction, index) => direction === right[index])
}

function sameValue(left: unknown, right: unknown): boolean {
  if (left === right) return true
  if (Array.isArray(left) || Array.isArray(right)) {
    return Array.isArray(left) && Array.isArray(right)
      && left.length === right.length
      && left.every((value, index) => sameValue(value, right[index]))
  }
  if (!isRecord(left) || !isRecord(right)) return false
  const leftKeys = Object.keys(left).sort()
  const rightKeys = Object.keys(right).sort()
  return sameValue(leftKeys, rightKeys)
    && leftKeys.every((key) => sameValue(left[key], right[key]))
}

export function canonicalJson(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`
  if (isRecord(value)) {
    return `{${Object.keys(value).sort().map((key) => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`
  }
  return JSON.stringify(value)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

export async function sha256Hex(value: string): Promise<string | null> {
  if (!globalThis.crypto?.subtle) return null
  const digest = await globalThis.crypto.subtle.digest('SHA-256', new TextEncoder().encode(value))
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('')
}
