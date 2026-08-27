import type { IntradayFactorBacktest } from '../types'

const TOLERANCE = 1e-9
const CRITERIA = [
  'minimum_twenty_exit_triggers',
  'minimum_ten_clustered_exit_trigger_sessions',
  'positive_mean_session_exit_timing_advantage',
  'exit_timing_newey_west_t_statistic_above_1_96',
] as const

export function matchesIntradayExitFactorValidation(response: IntradayFactorBacktest): boolean {
  const validation = response.exit_factor_validation
  if (validation === undefined) return true

  const triggerRate = finiteNumber(validation.trigger_rate)
  const meanTriggeredAdvantage = finiteNumber(validation.mean_triggered_timing_advantage)
  const meanSessionAdvantage = finiteNumber(validation.mean_session_triggered_timing_advantage)
  const hitRate = finiteNumber(validation.trigger_hit_rate)
  const standardError = finiteNumber(validation.newey_west_standard_error)
  const tStatistic = finiteNumber(validation.newey_west_t_statistic)
  const confidenceLower = finiteNumber(validation.confidence_interval_95_lower)
  const confidenceUpper = finiteNumber(validation.confidence_interval_95_upper)
  if ([
    triggerRate,
    meanTriggeredAdvantage,
    meanSessionAdvantage,
    hitRate,
    standardError,
    tStatistic,
    confidenceLower,
    confidenceUpper,
  ].some((value) => value === null)
    || validation.classification !== 'TRIGGERED_EXIT_TIMING_ADVANTAGE_DIAGNOSTIC'
    || validation.advantage_definition !== 'CONDITIONAL_EXIT_OPEN_DIVIDED_BY_TIME_STOP_OPEN_MINUS_ONE'
    || !Number.isInteger(validation.entry_signal_count)
    || validation.entry_signal_count !== response.event_summary.signal_count
    || !Number.isInteger(validation.trigger_count)
    || validation.trigger_count < 0
    || validation.trigger_count > validation.entry_signal_count
    || response.strategy.conditional_exit_count !== validation.trigger_count
    || response.strategy.fallback_time_stop_count
      !== validation.entry_signal_count - validation.trigger_count
    || !Number.isInteger(validation.clustered_trigger_session_count)
    || validation.clustered_trigger_session_count < 0
    || validation.clustered_trigger_session_count > validation.trigger_count
    || validation.clustered_trigger_session_count > response.selected_trading_days.length
    || !Number.isInteger(validation.newey_west_lag)
    || (standardError as number) < 0
    || (hitRate as number) < 0 || (hitRate as number) > 1
    || !approximatelyEqual(
      triggerRate as number,
      validation.entry_signal_count === 0
        ? 0
        : validation.trigger_count / validation.entry_signal_count,
    )
    || !approximatelyEqual(
      (hitRate as number) * validation.trigger_count,
      Math.round((hitRate as number) * validation.trigger_count),
    )) return false

  const expectedLag = validation.clustered_trigger_session_count === 0
    ? 0
    : Math.min(
      validation.clustered_trigger_session_count - 1,
      Math.floor(4 * (validation.clustered_trigger_session_count / 100) ** (2 / 9)),
    )
  const expectedT = (standardError as number) === 0
    ? 0
    : (meanSessionAdvantage as number) / (standardError as number)
  const margin = 1.96 * (standardError as number)
  if (validation.newey_west_lag !== expectedLag
    || !approximatelyEqual(tStatistic as number, expectedT)
    || !approximatelyEqual(confidenceLower as number, (meanSessionAdvantage as number) - margin)
    || !approximatelyEqual(confidenceUpper as number, (meanSessionAdvantage as number) + margin)
    || (validation.trigger_count === 0 && ![
      meanTriggeredAdvantage,
      meanSessionAdvantage,
      hitRate,
      standardError,
      tStatistic,
      confidenceLower,
      confidenceUpper,
    ].every((value) => approximatelyEqual(value as number, 0)))) return false

  const expectedCriteria: Record<string, boolean> = {
    minimum_twenty_exit_triggers: validation.trigger_count >= 20,
    minimum_ten_clustered_exit_trigger_sessions: validation.clustered_trigger_session_count >= 10,
    positive_mean_session_exit_timing_advantage: (meanSessionAdvantage as number) > 0,
    exit_timing_newey_west_t_statistic_above_1_96: (tStatistic as number) > 1.96,
  }
  const keys = Object.keys(validation.criteria)
  return sameStrings(keys, CRITERIA)
    && keys.every((key) => validation.criteria[key] === expectedCriteria[key])
    && validation.passed === Object.values(expectedCriteria).every(Boolean)
    && response.research_gate.criteria.exit_factor_effectiveness_passed === validation.passed
}

function finiteNumber(value: unknown): number | null {
  if (typeof value !== 'string' || value.trim() === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function approximatelyEqual(left: number, right: number): boolean {
  return Math.abs(left - right) <= TOLERANCE * Math.max(1, Math.abs(left), Math.abs(right))
}

function sameStrings(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index])
}
