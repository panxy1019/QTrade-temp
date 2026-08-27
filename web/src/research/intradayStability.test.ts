import { describe, expect, it } from 'vitest'
import type { IntradayFactorBacktest } from '../types'
import { matchesIntradayStability } from './intradayStability'

const days = [
  '2026-07-01', '2026-07-02', '2026-07-03', '2026-07-06',
  '2026-07-07', '2026-07-08', '2026-07-09', '2026-07-10',
]
const signalCounts = [1, 0, 2, 1, 1, 1, 3, 1]
const means = [0.01, 0.02, 0.03, -0.01]
const signalSessions = [1, 2, 2, 2]
const eventMean = means.reduce(
  (total, mean, index) => total + mean * signalSessions[index],
  0,
) / signalSessions.reduce((total, value) => total + value, 0)

const response = {
  selected_trading_days: days,
  symbol_count: 10,
  event_summary: {
    observation_count: 80,
    signal_count: 10,
    clustered_signal_session_count: 7,
    mean_session_conditional_excess_return: String(eventMean),
  },
  backtest: {
    curve: days.map((tradingDay, index) => ({
      trading_day: tradingDay,
      signal_count: signalCounts[index],
    })),
  },
  stability_validation: {
    classification: 'CONTIGUOUS_SESSION_BLOCK_FACTOR_STABILITY_DIAGNOSTIC',
    blocks: Array.from({ length: 4 }, (_, index) => ({
      block_id: `TS-${String(index + 1).padStart(2, '0')}`,
      start: days[index * 2],
      end: days[index * 2 + 1],
      session_count: 2,
      observation_count: 20,
      signal_count: signalCounts[index * 2] + signalCounts[index * 2 + 1],
      signal_session_count: signalSessions[index],
      mean_session_conditional_excess_return: String(means[index]),
    })),
    block_count: 4,
    positive_block_count: 3,
    worst_block_mean_conditional_excess_return: '-0.01',
    maximum_absolute_block_contribution_share: String(0.03 / 0.07),
    contribution_share_threshold: '0.60',
    criteria: {
      minimum_four_contiguous_blocks: true,
      every_block_has_signal_session: true,
      positive_block_ratio_at_least_75pct: true,
      maximum_absolute_block_contribution_share_below_0_60: true,
    },
    passed: true,
  },
} as unknown as IntradayFactorBacktest

describe('intraday temporal stability response', () => {
  it('accepts four reproducible contiguous blocks', () => {
    expect(matchesIntradayStability(response)).toBe(true)
  })

  it('rejects block identity, boundary and size drift', () => {
    for (const mutate of [
      (value: IntradayFactorBacktest) => { value.stability_validation.blocks[0].block_id = 'TS-02' },
      (value: IntradayFactorBacktest) => { value.stability_validation.blocks[0].end = days[2] },
      (value: IntradayFactorBacktest) => { value.stability_validation.blocks[0].session_count = 3 },
      (value: IntradayFactorBacktest) => { value.stability_validation.block_count = 3 },
    ]) {
      const drifted = structuredClone(response)
      mutate(drifted)
      expect(matchesIntradayStability(drifted)).toBe(false)
    }
  })

  it('rejects observation, signal and signal-session decomposition drift', () => {
    for (const mutate of [
      (value: IntradayFactorBacktest) => { value.stability_validation.blocks[1].observation_count = 19 },
      (value: IntradayFactorBacktest) => { value.stability_validation.blocks[1].signal_count = 4 },
      (value: IntradayFactorBacktest) => { value.stability_validation.blocks[1].signal_session_count = 1 },
      (value: IntradayFactorBacktest) => { value.event_summary.clustered_signal_session_count = 6 },
    ]) {
      const drifted = structuredClone(response)
      mutate(drifted)
      expect(matchesIntradayStability(drifted)).toBe(false)
    }
  })

  it('rejects forged weighted mean, positive count, worst block and contribution share', () => {
    for (const mutate of [
      (value: IntradayFactorBacktest) => { value.event_summary.mean_session_conditional_excess_return = '0' },
      (value: IntradayFactorBacktest) => { value.stability_validation.positive_block_count = 2 },
      (value: IntradayFactorBacktest) => { value.stability_validation.worst_block_mean_conditional_excess_return = '-0.02' },
      (value: IntradayFactorBacktest) => { value.stability_validation.maximum_absolute_block_contribution_share = '0.2' },
      (value: IntradayFactorBacktest) => { value.stability_validation.contribution_share_threshold = '0.61' },
    ]) {
      const drifted = structuredClone(response)
      mutate(drifted)
      expect(matchesIntradayStability(drifted)).toBe(false)
    }
  })

  it('rejects missing, reordered or forged stability criteria and pass', () => {
    const missing = structuredClone(response)
    delete missing.stability_validation.criteria.every_block_has_signal_session
    expect(matchesIntradayStability(missing)).toBe(false)

    const reordered = structuredClone(response)
    reordered.stability_validation.criteria = Object.fromEntries(
      Object.entries(reordered.stability_validation.criteria).reverse(),
    )
    expect(matchesIntradayStability(reordered)).toBe(false)

    const forged = structuredClone(response)
    forged.stability_validation.criteria.positive_block_ratio_at_least_75pct = false
    expect(matchesIntradayStability(forged)).toBe(false)

    const pass = structuredClone(response)
    pass.stability_validation.passed = false
    expect(matchesIntradayStability(pass)).toBe(false)
  })
})
