import { describe, expect, it } from 'vitest'
import { matchesIntradayCausalContract } from './causalBacktestContract'
import type { IntradayFactorBacktest, IntradayFactorBacktestRequest } from '../types'

const request: IntradayFactorBacktestRequest = {
  factor: 'opening_three_ten_minute_bullish',
  start: '2026-07-28',
  end: '2026-08-24',
  cost_bps: '5',
  minimum_body_return_bps: '0',
  directions: ['BULLISH', 'BULLISH', 'BULLISH'],
  combination: 'ALL',
}

const timeExitContract: IntradayFactorBacktest['causal_contract'] = {
  factor_known_at: 'THIRD_TEN_MINUTE_BAR_CLOSE_10_00',
  entry_at: 'NEXT_SOURCE_FIVE_MINUTE_BAR_OPEN_10_05',
  exit_at: 'NEXT_TRADING_SESSION_FIRST_FIVE_MINUTE_BAR_OPEN_T_PLUS_ONE',
  source_bar_timestamp_convention: 'BAR_END_TIME',
  entry_bar_label: '10_05',
  entry_interval_start: '10_00',
  exit_bar_label: 'NEXT_SESSION_09_35',
  exit_interval_start: 'NEXT_SESSION_09_30',
  same_bar_execution: false,
  t_plus_one_compliant: true,
}

const conditionalRequest: IntradayFactorBacktestRequest = {
  ...request,
  exit_rule: 'NEXT_SESSION_THREE_TEN_MINUTE_BEARISH_ELSE_14_55_TIME_STOP',
  exit_minimum_body_return_bps: '5',
}

const conditionalContract: IntradayFactorBacktest['causal_contract'] = {
  factor_known_at: 'THIRD_TEN_MINUTE_BAR_CLOSE_10_00',
  entry_at: 'NEXT_SOURCE_FIVE_MINUTE_BAR_OPEN_10_05',
  exit_condition_known_at: 'NEXT_SESSION_THIRD_TEN_MINUTE_BAR_CLOSE_10_00_T_PLUS_ONE',
  conditional_exit_at: 'NEXT_SOURCE_FIVE_MINUTE_BAR_OPEN_10_05_T_PLUS_ONE',
  fallback_exit_at: 'FINAL_SOURCE_FIVE_MINUTE_BAR_OPEN_14_55_T_PLUS_ONE',
  source_bar_timestamp_convention: 'BAR_END_TIME',
  entry_bar_label: '10_05',
  entry_interval_start: '10_00',
  conditional_exit_bar_label: 'NEXT_SESSION_10_05',
  conditional_exit_interval_start: 'NEXT_SESSION_10_00',
  fallback_exit_bar_label: 'NEXT_SESSION_15_00',
  fallback_exit_interval_start: 'NEXT_SESSION_14_55',
  same_bar_execution: false,
  t_plus_one_compliant: true,
}

describe('intraday causal backtest contract', () => {
  it('accepts the exact fixed T+1 open-exit clock', () => {
    expect(matchesIntradayCausalContract(timeExitContract, request)).toBe(true)
  })

  it('accepts the exact conditional-exit and fallback clocks', () => {
    expect(matchesIntradayCausalContract(conditionalContract, conditionalRequest)).toBe(true)
  })

  it('rejects same-bar timing, label and extra-field drift', () => {
    expect(matchesIntradayCausalContract({ ...timeExitContract, entry_at: 'SAME_BAR_OPEN' }, request)).toBe(false)
    expect(matchesIntradayCausalContract({ ...timeExitContract, exit_interval_start: 'NEXT_SESSION_09_35' }, request)).toBe(false)
    expect(matchesIntradayCausalContract({ ...timeExitContract, forged: true }, request)).toBe(false)
  })

  it('rejects a causal clock from the other exit branch', () => {
    expect(matchesIntradayCausalContract(timeExitContract, conditionalRequest)).toBe(false)
    expect(matchesIntradayCausalContract(conditionalContract, request)).toBe(false)
  })
})
