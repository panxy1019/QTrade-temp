import { describe, expect, it } from 'vitest'
import { matchesIntradayFactorDefinition, matchesIntradayStrategyIdentity } from './strategyIdentity'
import type { IntradayFactorBacktest, IntradayFactorBacktestRequest } from '../types'

const request: IntradayFactorBacktestRequest = {
  factor: 'custom_opening_ten_minute_direction_rule',
  start: '2026-07-28',
  end: '2026-08-24',
  cost_bps: '5',
  minimum_body_return_bps: '0',
  directions: ['BULLISH', 'BULLISH', 'BULLISH'],
  combination: 'ALL',
  exit_rule: 'NEXT_SESSION_THREE_TEN_MINUTE_BEARISH_ELSE_14_55_TIME_STOP',
  exit_minimum_body_return_bps: '5',
}

const strategy: IntradayFactorBacktest['strategy'] = {
  entry_factor_id: 'opening-three-ten-minute-bullish-v1',
  entry_condition: 'bullish_10m_0930_0940 AND bullish_10m_0940_0950 AND bullish_10m_0950_1000',
  entry_minimum_body_return_bps: '0',
  entry_directions: ['BULLISH', 'BULLISH', 'BULLISH'],
  entry_combination: 'ALL',
  entry_rule: 'NEXT_5M_OPEN_10_05',
  exit_rule: 'NEXT_SESSION_THREE_TEN_MINUTE_BEARISH_ELSE_14_55_TIME_STOP',
  exit_condition_type: 'FACTOR_THEN_TIME_STOP',
  exit_factor_id: 'next-session-three-ten-minute-bearish-v1',
  exit_condition: 'bearish_10m_0930_0940 AND bearish_10m_0940_0950 AND bearish_10m_0950_1000',
  exit_minimum_body_return_bps: '5',
  exit_condition_known_at: 'NEXT_SESSION_10_00_BAR_CLOSE_T_PLUS_ONE',
  entry_price_source: 'OPEN_OF_BAR_LABELLED_10_05_INTERVAL_START_10_00',
  conditional_exit_price_source: 'OPEN_OF_NEXT_SESSION_BAR_LABELLED_10_05_INTERVAL_START_10_00',
  fallback_exit_price_source: 'OPEN_OF_NEXT_SESSION_BAR_LABELLED_15_00_INTERVAL_START_14_55',
  conditional_exit_count: 28,
  fallback_time_stop_count: 164,
  position_rule: 'EQUAL_WEIGHT_TRIGGERED_STOCKS_ELSE_CASH',
  benchmark_rule: 'EQUAL_WEIGHT_ALL_ELIGIBLE_STOCKS_SAME_PER_SYMBOL_EXIT_RULE',
  single_side_cost_bps: '5',
  t_plus_one_compliant: true,
}

const specHash = 'ae3e57ed25bd3fe909cdf1e46e360c3e0da53ac9f52f32d4eeb104cbd06d2d8c'
const identity: IntradayFactorBacktest['strategy_identity'] = {
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
}

describe('intraday strategy identity', () => {
  it('recomputes the exact conditional-exit behavior hash', async () => {
    await expect(matchesIntradayStrategyIdentity(strategy, identity, request, {
      signalCount: 192,
      exitTriggerCount: 28,
    })).resolves.toBe(true)
  })

  it('recomputes the fixed T+1 open-exit behavior hash', async () => {
    const timeRequest = { ...request, exit_rule: undefined, exit_minimum_body_return_bps: undefined }
    const timeStrategy: IntradayFactorBacktest['strategy'] = {
      entry_factor_id: strategy.entry_factor_id,
      entry_condition: strategy.entry_condition,
      entry_minimum_body_return_bps: '0',
      entry_directions: ['BULLISH', 'BULLISH', 'BULLISH'],
      entry_combination: 'ALL',
      entry_rule: 'NEXT_5M_OPEN_10_05',
      exit_rule: 'NEXT_SESSION_OPEN_T_PLUS_ONE',
      exit_condition_type: 'TIME_EXIT',
      entry_price_source: 'OPEN_OF_BAR_LABELLED_10_05_INTERVAL_START_10_00',
      exit_price_source: 'OPEN_OF_NEXT_SESSION_BAR_LABELLED_09_35_INTERVAL_START_09_30',
      position_rule: 'EQUAL_WEIGHT_TRIGGERED_STOCKS_ELSE_CASH',
      benchmark_rule: 'EQUAL_WEIGHT_ALL_ELIGIBLE_STOCKS_SAME_HORIZON',
      single_side_cost_bps: '5',
      t_plus_one_compliant: true,
    }
    const hash = 'd6b31e9ee2e5b3459ec4fdd0e431c007d602a7499f539052229408ec12b20be3'
    await expect(matchesIntradayStrategyIdentity(timeStrategy, {
      ...identity,
      spec_hash: hash,
      strategy_id: `adhoc-intraday-${hash.slice(0, 16)}`,
    }, timeRequest, { signalCount: 192 })).resolves.toBe(true)
  })

  it('rejects behavior, hash-scope and digest drift', async () => {
    await expect(matchesIntradayStrategyIdentity(
      { ...strategy, position_rule: 'FORGED' }, identity, request,
      { signalCount: 192, exitTriggerCount: 28 },
    )).resolves.toBe(false)
    await expect(matchesIntradayStrategyIdentity(strategy, {
      ...identity,
      spec_hash_scope: 'forged' as IntradayFactorBacktest['strategy_identity']['spec_hash_scope'],
    }, request, { signalCount: 192, exitTriggerCount: 28 })).resolves.toBe(false)
    await expect(matchesIntradayStrategyIdentity(strategy, {
      ...identity,
      spec_hash: 'f'.repeat(64),
      strategy_id: `adhoc-intraday-${'f'.repeat(16)}`,
    }, request, { signalCount: 192, exitTriggerCount: 28 })).resolves.toBe(false)
  })

  it('binds observed exit counts without including them in the behavior digest', async () => {
    await expect(matchesIntradayStrategyIdentity(
      { ...strategy, conditional_exit_count: 27, fallback_time_stop_count: 165 },
      identity,
      request,
      { signalCount: 192, exitTriggerCount: 28 },
    )).resolves.toBe(false)
  })

  it('requires the exact factor contract derived from the request', () => {
    const definition = {
      factor_id: 'opening-three-ten-minute-bullish-v1',
      name: '09:30 起三根十分钟阳线',
      expression: 'bullish_10m_0930_0940 AND bullish_10m_0940_0950 AND bullish_10m_0950_1000',
      source_interval: 'NATIVE_5M_AGGREGATED_TO_10M',
      source_timestamp_convention: 'BAR_END_TIME' as const,
      source_bars: '09:35,09:40,09:45,09:50,09:55,10:00',
      known_at: '10:00_BAR_CLOSE',
      minimum_body_return_bps: '0',
      directions: ['BULLISH', 'BULLISH', 'BULLISH'] as const,
      combination: 'ALL' as const,
      allowed_features: ['TEN_MINUTE_BODY_0930_0940', 'TEN_MINUTE_BODY_0940_0950', 'TEN_MINUTE_BODY_0950_1000'],
      arbitrary_code_execution: false as const,
    }
    expect(matchesIntradayFactorDefinition(definition, request)).toBe(true)
    expect(matchesIntradayFactorDefinition({ ...definition, factor_id: 'forged' }, request)).toBe(false)
  })
})
