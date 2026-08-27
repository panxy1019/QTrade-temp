import { describe, expect, it } from 'vitest'
import type { IntradayFactorBacktest } from '../types'
import { matchesIntradayFactorValidation } from './intradayFactorValidation'

const response = {
  selected_trading_days: ['2026-07-01', '2026-07-02'],
  symbol_count: 2,
  event_summary: {
    observation_count: 4,
    signal_count: 2,
    mean_signal_forward_return: '0.03',
    mean_universe_forward_return: '0.01',
    conditional_excess_return: '0.02',
  },
  factor_validation: {
    number_of_periods: 2,
    minimum_instruments: 2,
    ic: '0.2',
    rank_ic: '-0.1',
    icir: '0.5',
    quantile_returns: ['-0.01', '0.03'],
    factor_turnover: '0.75',
    long_short_spread: '0.04',
    passed: false,
  },
} as unknown as IntradayFactorBacktest

describe('intraday factor validation response', () => {
  it('accepts reproducible signal and non-signal return groups', () => {
    expect(matchesIntradayFactorValidation(response)).toBe(true)
  })

  it('rejects period and instrument scope drift', () => {
    for (const mutate of [
      (value: IntradayFactorBacktest) => { value.factor_validation.number_of_periods = 3 },
      (value: IntradayFactorBacktest) => { value.factor_validation.minimum_instruments = 75 },
    ]) {
      const drifted = structuredClone(response)
      mutate(drifted)
      expect(matchesIntradayFactorValidation(drifted)).toBe(false)
    }
  })

  it('rejects missing, extra and forged grouped returns or spread', () => {
    for (const quantile_returns of [
      ['-0.01'],
      ['-0.01', '0.03', '0.04'],
      ['0', '0.03'],
      ['-0.01', '0.02'],
    ]) {
      expect(matchesIntradayFactorValidation({
        ...response,
        factor_validation: { ...response.factor_validation, quantile_returns },
      })).toBe(false)
    }
    expect(matchesIntradayFactorValidation({
      ...response,
      factor_validation: { ...response.factor_validation, long_short_spread: '0.03' },
    })).toBe(false)
  })

  it('rejects event conditional-excess drift', () => {
    expect(matchesIntradayFactorValidation({
      ...response,
      event_summary: { ...response.event_summary, conditional_excess_return: '0.03' },
    })).toBe(false)
  })

  it('rejects non-finite or out-of-range IC and turnover diagnostics', () => {
    for (const factor_validation of [
      { ...response.factor_validation, ic: '1.01' },
      { ...response.factor_validation, rank_ic: '-1.01' },
      { ...response.factor_validation, icir: 'NaN' },
      { ...response.factor_validation, factor_turnover: '-0.01' },
      { ...response.factor_validation, factor_turnover: '1.01' },
    ]) {
      expect(matchesIntradayFactorValidation({ ...response, factor_validation })).toBe(false)
    }
  })

  it('derives a zero non-signal mean when every observation signals', () => {
    const allSignals = structuredClone(response)
    allSignals.event_summary.signal_count = 4
    allSignals.event_summary.mean_signal_forward_return = '0.01'
    allSignals.event_summary.conditional_excess_return = '0'
    allSignals.factor_validation.quantile_returns = ['0', '0.01']
    allSignals.factor_validation.long_short_spread = '0.01'
    expect(matchesIntradayFactorValidation(allSignals)).toBe(true)
  })
})
