import { describe, expect, it } from 'vitest'
import type { IntradayFactorBacktest } from '../types'
import { matchesIntradayResearchGate } from './researchGate'

const criteria = {
  minimum_observation_count: true,
  minimum_signal_count: true,
  positive_mean_signal_return: true,
  positive_conditional_excess_return: true,
  minimum_ten_clustered_signal_sessions: true,
  newey_west_t_statistic_above_1_96: true,
  temporal_stability_passed: true,
  positive_rank_ic: true,
  positive_sector_ratio_at_least_60pct: true,
  maximum_absolute_factor_correlation_below_0_90: true,
  base_cost_total_return_positive: true,
  base_cost_excess_return_positive: true,
  walk_forward_oos_total_return_positive: true,
  walk_forward_oos_excess_return_positive: true,
  walk_forward_positive_fold_ratio_at_least_60pct: true,
  threshold_sensitivity_majority_positive: true,
}

const response = {
  factor_validation: { rank_ic: '0.02', passed: true },
  event_summary: {
    observation_count: 300,
    signal_count: 30,
    mean_signal_forward_return: '0.01',
    conditional_excess_return: '0.005',
    clustered_signal_session_count: 12,
    newey_west_t_statistic: '2.1',
    positive_sector_count: 9,
    sector_count: 15,
  },
  stability_validation: {
    criteria: {
      minimum_four_contiguous_blocks: true,
      every_block_has_signal_session: true,
      positive_block_ratio_at_least_75pct: true,
      maximum_absolute_block_contribution_share_below_0_60: true,
    },
    passed: true,
  },
  correlation_validation: {
    correlations: { opening_30m_return: '0.2', opening_30m_range_position: '-0.3' },
    maximum_absolute_correlation: '0.90',
    passed: true,
  },
  robustness_validation: {
    folds: [
      { test_strategy_metrics: { total_return: '0.01' } },
      { test_strategy_metrics: { total_return: '0.02' } },
    ],
    stitched_strategy_metrics: { total_return: '0.05', excess_return: '0.02' },
    threshold_sensitivity: [
      { strategy_metrics: { total_return: '0.01' } },
      { strategy_metrics: { total_return: '0.02' } },
      { strategy_metrics: { total_return: '-0.01' } },
      { strategy_metrics: { total_return: '-0.02' } },
    ],
    criteria: {
      minimum_two_disjoint_test_folds: true,
      every_test_fold_has_signal: true,
      causal_selection_known_before_test_signal: true,
      stitched_oos_total_return_positive: true,
      stitched_oos_excess_return_positive: true,
      positive_fold_ratio_at_least_60pct: true,
      threshold_sensitivity_majority_positive: true,
      worst_threshold_total_return_non_negative: true,
    },
    passed: true,
  },
  backtest: {
    strategy_metrics: { total_return: '0.05', excess_return: '0.02' },
    passed: true,
  },
  research_gate: { criteria, passed: true },
} as unknown as IntradayFactorBacktest

describe('intraday research gate response', () => {
  it('accepts an exactly derived base gate', () => {
    expect(matchesIntradayResearchGate(response)).toBe(true)
  })

  it('accepts a conditional exit gate only when its failed result is appended', () => {
    const conditional = structuredClone(response)
    conditional.exit_factor_validation = {
      criteria: {
        minimum_twenty_exit_triggers: true,
        minimum_ten_clustered_exit_trigger_sessions: true,
        positive_mean_session_exit_timing_advantage: true,
        exit_timing_newey_west_t_statistic_above_1_96: false,
      },
      passed: false,
    } as unknown as IntradayFactorBacktest['exit_factor_validation']
    conditional.research_gate.criteria = {
      ...conditional.research_gate.criteria,
      exit_factor_effectiveness_passed: false,
    }
    conditional.research_gate.passed = false
    conditional.factor_validation.passed = false
    expect(matchesIntradayResearchGate(conditional)).toBe(true)
  })

  it('rejects missing, extra or reordered criteria', () => {
    const missing = structuredClone(response)
    delete missing.research_gate.criteria.minimum_signal_count
    expect(matchesIntradayResearchGate(missing)).toBe(false)

    const extra = structuredClone(response)
    extra.research_gate.criteria.unregistered_gate = true
    expect(matchesIntradayResearchGate(extra)).toBe(false)

    const reordered = structuredClone(response)
    reordered.research_gate.criteria = Object.fromEntries(
      Object.entries(reordered.research_gate.criteria).reverse(),
    )
    expect(matchesIntradayResearchGate(reordered)).toBe(false)
  })

  it('rejects criteria forged against event, factor, correlation and base-cost facts', () => {
    for (const mutate of [
      (value: IntradayFactorBacktest) => { value.event_summary.observation_count = 249 },
      (value: IntradayFactorBacktest) => { value.event_summary.newey_west_t_statistic = '1.96' },
      (value: IntradayFactorBacktest) => { value.factor_validation.rank_ic = '-0.01' },
      (value: IntradayFactorBacktest) => { value.correlation_validation.correlations.opening_30m_return = '0.90' },
      (value: IntradayFactorBacktest) => { value.backtest.strategy_metrics.total_return = '-0.01' },
    ]) {
      const drifted = structuredClone(response)
      mutate(drifted)
      expect(matchesIntradayResearchGate(drifted)).toBe(false)
    }
  })

  it('rejects walk-forward ratios forged against fold and threshold metrics', () => {
    const folds = structuredClone(response)
    folds.robustness_validation.folds[1].test_strategy_metrics.total_return = '-0.01'
    expect(matchesIntradayResearchGate(folds)).toBe(false)

    const thresholds = structuredClone(response)
    thresholds.robustness_validation.threshold_sensitivity[1].strategy_metrics.total_return = '-0.01'
    expect(matchesIntradayResearchGate(thresholds)).toBe(false)
  })

  it('rejects inconsistent sub-gate and aggregate pass flags', () => {
    for (const mutate of [
      (value: IntradayFactorBacktest) => { value.stability_validation.passed = false },
      (value: IntradayFactorBacktest) => { value.robustness_validation.passed = false },
      (value: IntradayFactorBacktest) => { value.factor_validation.passed = false },
      (value: IntradayFactorBacktest) => { value.correlation_validation.passed = false },
      (value: IntradayFactorBacktest) => { value.backtest.passed = false },
      (value: IntradayFactorBacktest) => { value.research_gate.passed = false },
    ]) {
      const drifted = structuredClone(response)
      mutate(drifted)
      expect(matchesIntradayResearchGate(drifted)).toBe(false)
    }
  })
})
