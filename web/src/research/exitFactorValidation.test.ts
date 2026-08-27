import { describe, expect, it } from 'vitest'
import type { IntradayFactorBacktest } from '../types'
import { matchesIntradayExitFactorValidation } from './exitFactorValidation'

const response = {
  selected_trading_days: Array.from({ length: 20 }, (_, index) => `2026-07-${String(index + 1).padStart(2, '0')}`),
  event_summary: { signal_count: 30 },
  strategy: { conditional_exit_count: 20, fallback_time_stop_count: 10 },
  exit_factor_validation: {
    classification: 'TRIGGERED_EXIT_TIMING_ADVANTAGE_DIAGNOSTIC',
    advantage_definition: 'CONDITIONAL_EXIT_OPEN_DIVIDED_BY_TIME_STOP_OPEN_MINUS_ONE',
    entry_signal_count: 30,
    trigger_count: 20,
    trigger_rate: String(20 / 30),
    clustered_trigger_session_count: 10,
    mean_triggered_timing_advantage: '0.01',
    mean_session_triggered_timing_advantage: '0.02',
    trigger_hit_rate: '0.6',
    newey_west_lag: 2,
    newey_west_standard_error: '0.01',
    newey_west_t_statistic: '2',
    confidence_interval_95_lower: '0.0004',
    confidence_interval_95_upper: '0.0396',
    criteria: {
      minimum_twenty_exit_triggers: true,
      minimum_ten_clustered_exit_trigger_sessions: true,
      positive_mean_session_exit_timing_advantage: true,
      exit_timing_newey_west_t_statistic_above_1_96: true,
    },
    passed: true,
  },
  research_gate: { criteria: { exit_factor_effectiveness_passed: true } },
} as unknown as IntradayFactorBacktest

describe('intraday exit factor validation response', () => {
  it('accepts a reproducible triggered exit diagnostic', () => {
    expect(matchesIntradayExitFactorValidation(response)).toBe(true)
  })

  it('accepts the fixed T+1 exit branch without an exit diagnostic', () => {
    expect(matchesIntradayExitFactorValidation({} as IntradayFactorBacktest)).toBe(true)
  })

  it('rejects entry, trigger, fallback and clustered-session count drift', () => {
    for (const mutate of [
      (value: IntradayFactorBacktest) => { value.exit_factor_validation!.entry_signal_count = 29 },
      (value: IntradayFactorBacktest) => { value.exit_factor_validation!.trigger_count = 19 },
      (value: IntradayFactorBacktest) => { value.strategy.fallback_time_stop_count = 9 },
      (value: IntradayFactorBacktest) => { value.exit_factor_validation!.clustered_trigger_session_count = 21 },
    ]) {
      const drifted = structuredClone(response)
      mutate(drifted)
      expect(matchesIntradayExitFactorValidation(drifted)).toBe(false)
    }
  })

  it('rejects forged trigger rate and non-integral hit counts', () => {
    const rate = structuredClone(response)
    rate.exit_factor_validation!.trigger_rate = '0.5'
    expect(matchesIntradayExitFactorValidation(rate)).toBe(false)

    const hit = structuredClone(response)
    hit.exit_factor_validation!.trigger_hit_rate = '0.61'
    expect(matchesIntradayExitFactorValidation(hit)).toBe(false)
  })

  it('rejects Newey-West lag, t statistic and confidence interval drift', () => {
    for (const mutate of [
      (value: IntradayFactorBacktest) => { value.exit_factor_validation!.newey_west_lag = 1 },
      (value: IntradayFactorBacktest) => { value.exit_factor_validation!.newey_west_t_statistic = '1.9' },
      (value: IntradayFactorBacktest) => { value.exit_factor_validation!.confidence_interval_95_lower = '0' },
      (value: IntradayFactorBacktest) => { value.exit_factor_validation!.confidence_interval_95_upper = '0.04' },
    ]) {
      const drifted = structuredClone(response)
      mutate(drifted)
      expect(matchesIntradayExitFactorValidation(drifted)).toBe(false)
    }
  })

  it('rejects missing, reordered or forged criteria and pass bindings', () => {
    const missing = structuredClone(response)
    delete missing.exit_factor_validation!.criteria.minimum_twenty_exit_triggers
    expect(matchesIntradayExitFactorValidation(missing)).toBe(false)

    const reordered = structuredClone(response)
    reordered.exit_factor_validation!.criteria = Object.fromEntries(
      Object.entries(reordered.exit_factor_validation!.criteria).reverse(),
    )
    expect(matchesIntradayExitFactorValidation(reordered)).toBe(false)

    const criterion = structuredClone(response)
    criterion.exit_factor_validation!.criteria.exit_timing_newey_west_t_statistic_above_1_96 = false
    expect(matchesIntradayExitFactorValidation(criterion)).toBe(false)

    const gate = structuredClone(response)
    gate.research_gate.criteria.exit_factor_effectiveness_passed = false
    expect(matchesIntradayExitFactorValidation(gate)).toBe(false)
  })

  it('accepts a zero-trigger diagnostic only when all undefined statistics are zero', () => {
    const zero = structuredClone(response)
    zero.strategy.conditional_exit_count = 0
    zero.strategy.fallback_time_stop_count = 30
    Object.assign(zero.exit_factor_validation!, {
      trigger_count: 0,
      trigger_rate: '0',
      clustered_trigger_session_count: 0,
      mean_triggered_timing_advantage: '0',
      mean_session_triggered_timing_advantage: '0',
      trigger_hit_rate: '0',
      newey_west_lag: 0,
      newey_west_standard_error: '0',
      newey_west_t_statistic: '0',
      confidence_interval_95_lower: '0',
      confidence_interval_95_upper: '0',
      criteria: {
        minimum_twenty_exit_triggers: false,
        minimum_ten_clustered_exit_trigger_sessions: false,
        positive_mean_session_exit_timing_advantage: false,
        exit_timing_newey_west_t_statistic_above_1_96: false,
      },
      passed: false,
    })
    zero.research_gate.criteria.exit_factor_effectiveness_passed = false
    expect(matchesIntradayExitFactorValidation(zero)).toBe(true)
  })
})
