import { describe, expect, it } from 'vitest'
import type { IntradayFactorBacktest } from '../types'
import { matchesIntradayEventSummary } from './intradayEventSummary'

function fixture(): IntradayFactorBacktest {
  const meanSession = 0.0012
  const standardError = 0.0005
  return {
    selected_trading_days: ['2026-08-01', '2026-08-02', '2026-08-03', '2026-08-04'],
    symbol_count: 10,
    sector_count: 2,
    event_summary: {
      observation_count: 40,
      signal_count: 7,
      signal_rate: String(7 / 40),
      mean_signal_forward_return: '0.002',
      mean_universe_forward_return: '0.0005',
      conditional_excess_return: '0.0015',
      signal_hit_rate: String(4 / 7),
      positive_sector_count: 1,
      sector_count: 2,
      clustered_signal_session_count: 3,
      mean_session_conditional_excess_return: String(meanSession),
      newey_west_lag: 1,
      newey_west_standard_error: String(standardError),
      newey_west_t_statistic: String(meanSession / standardError),
      confidence_interval_95_lower: String(meanSession - 1.96 * standardError),
      confidence_interval_95_upper: String(meanSession + 1.96 * standardError),
    },
    backtest: {
      curve: [
        { signal_count: 2 },
        { signal_count: 0 },
        { signal_count: 3 },
        { signal_count: 2 },
      ],
    },
  } as unknown as IntradayFactorBacktest
}

describe('intraday event summary response', () => {
  it('accepts exact counts, means and clustered Newey-West arithmetic', () => {
    expect(matchesIntradayEventSummary(fixture())).toBe(true)
  })

  it('rejects forged counts, rates, means and hit counts', () => {
    for (const mutate of [
      (value: IntradayFactorBacktest) => { value.event_summary.observation_count = 39 },
      (value: IntradayFactorBacktest) => { value.event_summary.signal_rate = '0.5' },
      (value: IntradayFactorBacktest) => { value.event_summary.conditional_excess_return = '0.5' },
      (value: IntradayFactorBacktest) => { value.event_summary.signal_hit_rate = '0.5' },
      (value: IntradayFactorBacktest) => { value.event_summary.clustered_signal_session_count = 2 },
    ]) {
      const drifted = fixture()
      mutate(drifted)
      expect(matchesIntradayEventSummary(drifted)).toBe(false)
    }
  })

  it('rejects forged Newey-West lag, t statistic and confidence interval', () => {
    for (const mutate of [
      (value: IntradayFactorBacktest) => { value.event_summary.newey_west_lag = 2 },
      (value: IntradayFactorBacktest) => { value.event_summary.newey_west_t_statistic = '1.96' },
      (value: IntradayFactorBacktest) => { value.event_summary.confidence_interval_95_lower = '0' },
      (value: IntradayFactorBacktest) => { value.event_summary.newey_west_standard_error = '-0.1' },
    ]) {
      const drifted = fixture()
      mutate(drifted)
      expect(matchesIntradayEventSummary(drifted)).toBe(false)
    }
  })

  it('accepts a conservative all-zero no-signal diagnostic', () => {
    const empty = fixture()
    empty.event_summary.signal_count = 0
    empty.event_summary.signal_rate = '0'
    empty.event_summary.mean_signal_forward_return = '0'
    empty.event_summary.mean_universe_forward_return = '0.001'
    empty.event_summary.conditional_excess_return = '-0.001'
    empty.event_summary.signal_hit_rate = '0'
    empty.event_summary.clustered_signal_session_count = 0
    empty.event_summary.mean_session_conditional_excess_return = '0'
    empty.event_summary.newey_west_lag = 0
    empty.event_summary.newey_west_standard_error = '0'
    empty.event_summary.newey_west_t_statistic = '0'
    empty.event_summary.confidence_interval_95_lower = '0'
    empty.event_summary.confidence_interval_95_upper = '0'
    empty.backtest.curve.forEach((point) => { point.signal_count = 0 })
    expect(matchesIntradayEventSummary(empty)).toBe(true)
  })
})
