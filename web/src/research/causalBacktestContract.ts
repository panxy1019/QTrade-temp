import type { IntradayFactorBacktest, IntradayFactorBacktestRequest } from '../types'

type CausalContract = IntradayFactorBacktest['causal_contract']

export function matchesIntradayCausalContract(
  actual: unknown,
  request: IntradayFactorBacktestRequest,
): actual is CausalContract {
  const shared = {
    factor_known_at: 'THIRD_TEN_MINUTE_BAR_CLOSE_10_00',
    entry_at: 'NEXT_SOURCE_FIVE_MINUTE_BAR_OPEN_10_05',
    source_bar_timestamp_convention: 'BAR_END_TIME' as const,
    entry_bar_label: '10_05' as const,
    entry_interval_start: '10_00' as const,
    same_bar_execution: false as const,
    t_plus_one_compliant: true as const,
  }
  const expected: CausalContract = (request.exit_rule ?? 'NEXT_SESSION_OPEN') === 'NEXT_SESSION_OPEN'
    ? {
        ...shared,
        exit_at: 'NEXT_TRADING_SESSION_FIRST_FIVE_MINUTE_BAR_OPEN_T_PLUS_ONE',
        exit_bar_label: 'NEXT_SESSION_09_35',
        exit_interval_start: 'NEXT_SESSION_09_30',
      }
    : {
        ...shared,
        exit_condition_known_at: 'NEXT_SESSION_THIRD_TEN_MINUTE_BAR_CLOSE_10_00_T_PLUS_ONE',
        conditional_exit_at: 'NEXT_SOURCE_FIVE_MINUTE_BAR_OPEN_10_05_T_PLUS_ONE',
        fallback_exit_at: 'FINAL_SOURCE_FIVE_MINUTE_BAR_OPEN_14_55_T_PLUS_ONE',
        conditional_exit_bar_label: 'NEXT_SESSION_10_05',
        conditional_exit_interval_start: 'NEXT_SESSION_10_00',
        fallback_exit_bar_label: 'NEXT_SESSION_15_00',
        fallback_exit_interval_start: 'NEXT_SESSION_14_55',
      }
  return sameContract(actual, expected)
}

function sameContract(actual: unknown, expected: unknown): boolean {
  if (Array.isArray(expected)) {
    return Array.isArray(actual)
      && actual.length === expected.length
      && expected.every((item, index) => sameContract(actual[index], item))
  }
  if (expected !== null && typeof expected === 'object') {
    if (actual === null || typeof actual !== 'object' || Array.isArray(actual)) return false
    const actualRecord = actual as Record<string, unknown>
    const expectedRecord = expected as Record<string, unknown>
    const actualKeys = Object.keys(actualRecord)
    const expectedKeys = Object.keys(expectedRecord)
    return actualKeys.length === expectedKeys.length
      && expectedKeys.every((key) => Object.hasOwn(actualRecord, key)
        && sameContract(actualRecord[key], expectedRecord[key]))
  }
  return Object.is(actual, expected)
}
