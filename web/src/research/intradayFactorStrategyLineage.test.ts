import { describe, expect, it } from 'vitest'
import type { LocalIntradayFactorCandidate } from './localIntradayFactorCandidates'
import type {
  FiveMinuteResearchReport,
  IntradayFactorBacktest,
  IntradayFactorBacktestRequest,
  IntradayFactorCorrelation,
} from '../types'
import { buildIntradayFactorStrategyLineage } from './intradayFactorStrategyLineage'

const dataset = {
  provider: 'BaoStock',
  provider_version: '0.9.3',
  data_version: 'baostock-five-minute-long-v1',
  checksum: 'dataset-checksum',
  evidence_sha256: '1'.repeat(64),
  symbol_count: 75,
  sector_count: 15,
} as NonNullable<FiveMinuteResearchReport['minute_dataset']>

const candidate = {
  draft_key: 'BULLISH|BEARISH|IGNORE:ANY:10',
  name: '开盘混合方向',
  data_version: dataset.data_version,
  dataset_checksum: dataset.checksum,
  directions: ['BULLISH', 'BEARISH', 'IGNORE'],
  combination: 'ANY',
  minimum_body_return_bps: '10',
  expression: 'body_return_0930_0940 >= 10 bps OR body_return_0940_0950 <= -10 bps',
} as LocalIntradayFactorCandidate

const request = {
  factor: 'custom_opening_ten_minute_direction_rule',
  start: '2026-07-01',
  end: '2026-07-28',
  cost_bps: '5',
  minimum_body_return_bps: '10',
  directions: candidate.directions,
  combination: candidate.combination,
  exit_rule: 'NEXT_SESSION_THREE_TEN_MINUTE_BEARISH_ELSE_14_55_TIME_STOP',
  exit_minimum_body_return_bps: '5',
} satisfies IntradayFactorBacktestRequest

const correlation = {
  classification: 'AD_HOC_INTRADAY_FACTOR_CORRELATION_CURRENT_NON_PIT',
  parameters: {
    start: request.start,
    end: request.end,
    rules: [candidate, {
      directions: ['BEARISH', 'BEARISH', 'BEARISH'],
      combination: 'ALL',
      minimum_body_return_bps: '0',
    }],
  },
  rules: [
    { ...candidate, rule_id: 'R01', factor_id: 'mixed-opening-v1' },
    { rule_id: 'R02', factor_id: 'three-bearish-v1', name: '三连阴', expression: 'three bearish', directions: ['BEARISH', 'BEARISH', 'BEARISH'], combination: 'ALL', minimum_body_return_bps: '0' },
  ],
  effectiveness: [
    {
      rule_id: 'R01', minimum_body_return_bps: '10', observation_count: 1500,
      signal_count: 88, clustered_signal_session_count: 20,
      mean_session_conditional_excess_return: '0.0012', newey_west_lag: 2,
      newey_west_standard_error: '0.0004', newey_west_t_statistic: '3',
      confidence_interval_95_lower: '0.0004', confidence_interval_95_upper: '0.002',
      raw_p_value: '0.003', holm_adjusted_p_value: '0.006', criteria: {}, passed: true,
    },
    {
      rule_id: 'R02', minimum_body_return_bps: '0', observation_count: 1500,
      signal_count: 42, clustered_signal_session_count: 16,
      mean_session_conditional_excess_return: '-0.0002', newey_west_lag: 2,
      newey_west_standard_error: '0.0004', newey_west_t_statistic: '-0.5',
      confidence_interval_95_lower: '-0.001', confidence_interval_95_upper: '0.0006',
      raw_p_value: '0.6', holm_adjusted_p_value: '0.6', criteria: {}, passed: false,
    },
  ],
  pairs: [{ left_rule_id: 'R01', right_rule_id: 'R02', correlation: '0.4', redundant: false }],
  data_version: dataset.data_version,
  dataset_checksum: dataset.checksum,
  data_interval: '5m',
  symbol_count: 75,
  sector_count: 15,
  point_in_time: false,
  future_adjustment_knowledge_possible: true,
  realtime_readiness: false,
  promotion: 'NOT_ELIGIBLE',
  paper_readiness: 'NOT_ELIGIBLE',
  live_readiness: 'DISABLED_REQUIRES_SEPARATE_AUTHORIZATION',
  state_mutation: false,
  external_order_transmission: false,
  preview_hash: 'a'.repeat(64),
} as unknown as IntradayFactorCorrelation

const backtest = {
  parameters: request,
  data_version: dataset.data_version,
  dataset_checksum: dataset.checksum,
  strategy_identity: {
    strategy_id: `adhoc-intraday-${'b'.repeat(16)}`,
    spec_hash: 'b'.repeat(64),
    registration_status: 'AD_HOC_UNREGISTERED',
  },
  preview_hash: 'c'.repeat(64),
  promotion: 'NOT_ELIGIBLE',
  paper_readiness: 'NOT_ELIGIBLE',
  live_readiness: 'DISABLED_REQUIRES_SEPARATE_AUTHORIZATION',
  state_mutation: false,
  external_order_transmission: false,
} as unknown as IntradayFactorBacktest

describe('intraday factor-to-strategy lineage', () => {
  it('binds one manually admitted factor to the exact later strategy preview', () => {
    const lineage = buildIntradayFactorStrategyLineage({
      dataset,
      candidate,
      ruleId: 'R01',
      correlation,
      request,
      backtest,
    })

    expect(lineage).toMatchObject({
      schema_version: 1,
      classification: 'BROWSER_SESSION_FACTOR_TO_STRATEGY_LINEAGE_NOT_IMMUTABLE',
      factor_evidence: {
        candidate_name: '开盘混合方向',
        rule_id: 'R01',
        correlation_preview_hash: 'a'.repeat(64),
        effectiveness_passed: true,
        redundant: false,
      },
      strategy_preview: {
        request,
        preview_hash: 'c'.repeat(64),
        strategy_id: `adhoc-intraday-${'b'.repeat(16)}`,
        behavior_spec_hash: 'b'.repeat(64),
      },
      interpretation: {
        same_historical_window: true,
        entry_factor_identity_match: true,
        exit_rule_validated_by_factor_correlation: false,
        strategy_returns_validated_by_factor_correlation: false,
        independent_evidence: false,
      },
      immutable_persistence: false,
      factor_registry_write: false,
      strategy_registry_write: false,
      paper_runtime_start: false,
      live_runtime_start: false,
      state_mutation: false,
      external_order_transmission: false,
    })
  })

  it('fails closed when the strategy window or entry factor drifts after handoff', () => {
    expect(buildIntradayFactorStrategyLineage({
      dataset,
      candidate,
      ruleId: 'R01',
      correlation,
      request: { ...request, end: '2026-07-29' },
      backtest,
    })).toBeNull()

    expect(buildIntradayFactorStrategyLineage({
      dataset,
      candidate,
      ruleId: 'R01',
      correlation,
      request: { ...request, minimum_body_return_bps: '11' },
      backtest: { ...backtest, parameters: { ...request, minimum_body_return_bps: '11' } },
    })).toBeNull()
  })

  it('fails closed when either evidence surface relaxes the no-authority boundary', () => {
    expect(buildIntradayFactorStrategyLineage({
      dataset,
      candidate,
      ruleId: 'R01',
      correlation: { ...correlation, state_mutation: true } as unknown as IntradayFactorCorrelation,
      request,
      backtest,
    })).toBeNull()

    expect(buildIntradayFactorStrategyLineage({
      dataset,
      candidate,
      ruleId: 'R01',
      correlation,
      request,
      backtest: { ...backtest, external_order_transmission: true } as unknown as IntradayFactorBacktest,
    })).toBeNull()
  })
})
