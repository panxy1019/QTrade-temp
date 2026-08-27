import type { IntradayFactorBacktest } from '../types'

const BASE_CRITERIA = [
  'minimum_observation_count',
  'minimum_signal_count',
  'positive_mean_signal_return',
  'positive_conditional_excess_return',
  'minimum_ten_clustered_signal_sessions',
  'newey_west_t_statistic_above_1_96',
  'temporal_stability_passed',
  'positive_rank_ic',
  'positive_sector_ratio_at_least_60pct',
  'maximum_absolute_factor_correlation_below_0_90',
  'base_cost_total_return_positive',
  'base_cost_excess_return_positive',
  'walk_forward_oos_total_return_positive',
  'walk_forward_oos_excess_return_positive',
  'walk_forward_positive_fold_ratio_at_least_60pct',
  'threshold_sensitivity_majority_positive',
] as const

const CORRELATION_FEATURES = [
  'opening_30m_return',
  'opening_30m_range_position',
] as const

export function matchesIntradayResearchGate(response: IntradayFactorBacktest): boolean {
  const meanSignalReturn = finiteNumber(response.event_summary.mean_signal_forward_return)
  const conditionalExcessReturn = finiteNumber(response.event_summary.conditional_excess_return)
  const neweyWestT = finiteNumber(response.event_summary.newey_west_t_statistic)
  const rankIc = finiteNumber(response.factor_validation.rank_ic)
  const baseTotalReturn = finiteNumber(response.backtest.strategy_metrics.total_return)
  const baseExcessReturn = finiteNumber(response.backtest.strategy_metrics.excess_return)
  const stitchedTotalReturn = finiteNumber(response.robustness_validation.stitched_strategy_metrics.total_return)
  const stitchedExcessReturn = finiteNumber(response.robustness_validation.stitched_strategy_metrics.excess_return)
  const correlationThreshold = finiteNumber(response.correlation_validation.maximum_absolute_correlation)
  const correlationKeys = Object.keys(response.correlation_validation.correlations)
  const correlations = Object.values(response.correlation_validation.correlations).map(finiteNumber)
  if ([
    meanSignalReturn,
    conditionalExcessReturn,
    neweyWestT,
    rankIc,
    baseTotalReturn,
    baseExcessReturn,
    stitchedTotalReturn,
    stitchedExcessReturn,
    correlationThreshold,
    ...correlations,
  ].some((value) => value === null)
    || !sameStrings(correlationKeys, CORRELATION_FEATURES)
    || correlationThreshold !== 0.9
    || !matchesBooleanOutcome(
      response.stability_validation.criteria,
      response.stability_validation.passed,
    )
    || !matchesBooleanOutcome(
      response.robustness_validation.criteria,
      response.robustness_validation.passed,
    )
    || (response.exit_factor_validation !== undefined
      && !matchesBooleanOutcome(
        response.exit_factor_validation.criteria,
        response.exit_factor_validation.passed,
      ))) return false

  const maximumAbsoluteCorrelation = Math.max(...correlations.map((value) => Math.abs(value as number)))
  const positiveFoldCount = response.robustness_validation.folds.filter((fold) => (
    (finiteNumber(fold.test_strategy_metrics.total_return) ?? Number.NEGATIVE_INFINITY) > 0
  )).length
  const positiveThresholdCount = response.robustness_validation.threshold_sensitivity.filter((row) => (
    (finiteNumber(row.strategy_metrics.total_return) ?? Number.NEGATIVE_INFINITY) > 0
  )).length
  if (response.robustness_validation.folds.length === 0
    || response.robustness_validation.threshold_sensitivity.length === 0
    || response.robustness_validation.folds.some((fold) => finiteNumber(fold.test_strategy_metrics.total_return) === null)
    || response.robustness_validation.threshold_sensitivity.some((row) => finiteNumber(row.strategy_metrics.total_return) === null)) return false

  const positiveFoldRatio = positiveFoldCount * 5
    >= response.robustness_validation.folds.length * 3
  const positiveThresholdMajority = positiveThresholdCount * 2
    >= response.robustness_validation.threshold_sensitivity.length
  if (response.robustness_validation.criteria.positive_fold_ratio_at_least_60pct !== positiveFoldRatio
    || response.robustness_validation.criteria.threshold_sensitivity_majority_positive !== positiveThresholdMajority) return false

  const expected: Record<string, boolean> = {
    minimum_observation_count: response.event_summary.observation_count >= 250,
    minimum_signal_count: response.event_summary.signal_count >= 20,
    positive_mean_signal_return: (meanSignalReturn as number) > 0,
    positive_conditional_excess_return: (conditionalExcessReturn as number) > 0,
    minimum_ten_clustered_signal_sessions: response.event_summary.clustered_signal_session_count >= 10,
    newey_west_t_statistic_above_1_96: (neweyWestT as number) > 1.96,
    temporal_stability_passed: response.stability_validation.passed,
    positive_rank_ic: (rankIc as number) > 0,
    positive_sector_ratio_at_least_60pct: response.event_summary.positive_sector_count * 5
      >= response.event_summary.sector_count * 3,
    maximum_absolute_factor_correlation_below_0_90: maximumAbsoluteCorrelation < 0.9,
    base_cost_total_return_positive: (baseTotalReturn as number) > 0,
    base_cost_excess_return_positive: (baseExcessReturn as number) > 0,
    walk_forward_oos_total_return_positive: (stitchedTotalReturn as number) > 0,
    walk_forward_oos_excess_return_positive: (stitchedExcessReturn as number) > 0,
    walk_forward_positive_fold_ratio_at_least_60pct: positiveFoldRatio,
    threshold_sensitivity_majority_positive: positiveThresholdMajority,
  }
  if (response.exit_factor_validation !== undefined) {
    expected.exit_factor_effectiveness_passed = response.exit_factor_validation.passed
  }

  const expectedKeys = [
    ...BASE_CRITERIA,
    ...(response.exit_factor_validation === undefined ? [] : ['exit_factor_effectiveness_passed']),
  ]
  const actualKeys = Object.keys(response.research_gate.criteria)
  if (!sameStrings(actualKeys, expectedKeys)
    || actualKeys.some((key) => response.research_gate.criteria[key] !== expected[key])) return false

  const factorCriteria = BASE_CRITERIA.slice(0, 9).map((key) => expected[key])
  if (response.exit_factor_validation !== undefined) {
    factorCriteria.push(expected.exit_factor_effectiveness_passed)
  }
  return response.factor_validation.passed === factorCriteria.every(Boolean)
    && response.correlation_validation.passed
      === expected.maximum_absolute_factor_correlation_below_0_90
    && response.backtest.passed
      === (expected.base_cost_total_return_positive && expected.base_cost_excess_return_positive)
    && response.research_gate.passed === Object.values(expected).every(Boolean)
}

function matchesBooleanOutcome(criteria: Record<string, boolean>, passed: boolean): boolean {
  const values = Object.values(criteria)
  return values.length > 0
    && values.every((value) => typeof value === 'boolean')
    && typeof passed === 'boolean'
    && passed === values.every(Boolean)
}

function finiteNumber(value: unknown): number | null {
  if (typeof value !== 'string' || value.trim() === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function sameStrings(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index])
}
