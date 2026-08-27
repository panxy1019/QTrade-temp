import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { runIntradayFactorBacktest, runIntradayFactorCorrelation, runIntradayFactorDiscovery } from '../api/client'
import { saveLocalIntradayFactorCandidate } from '../research/localIntradayFactorCandidates'
import type { LocalIntradayFactorCandidate } from '../research/localIntradayFactorCandidates'
import type { LocalIntradayFactorExperiment } from '../research/localIntradayFactorExperiments'
import { canonicalJson, sha256Hex } from '../research/strategyIdentity'
import type { FiveMinuteResearchReport, IntradayFactorBacktest, IntradayFactorCorrelation, IntradayFactorDiscovery } from '../types'
import { IntradayFactorResearchLab } from './IntradayFactorResearchLab'

const experiments = [
  {
    schema_version: 1,
    preview_hash: 'a'.repeat(64),
    name: '三连阳 A',
    saved_at: '2026-08-27T00:00:00+08:00',
    data_version: 'baostock-five-minute-long-v1',
    dataset_checksum: 'long-five-minute-checksum',
    selected_session_count: 20,
    symbol_count: 75,
    sector_count: 15,
    factor_definition: { factor_id: 'a', name: 'A', expression: 'a' },
    observation_count: 1500,
    signal_count: 100,
    clustered_signal_session_count: 20,
    metrics: { total_return: '0', annualized_return: '0', sharpe: '0', maximum_drawdown: '0', excess_return: '0', rank_ic: '0', maximum_absolute_correlation: '0', stitched_oos_total_return: '0', stitched_oos_excess_return: '0', newey_west_t_statistic: '0', confidence_interval_95_lower: '0', confidence_interval_95_upper: '0' },
    research_gate_passed: false,
    robustness_gate_passed: false,
    promotion: 'NOT_ELIGIBLE',
    state_mutation: false,
    external_order_transmission: false,
    parameters: {
      factor: 'opening_three_ten_minute_bullish',
      start: '2026-07-01',
      end: '2026-07-28',
      cost_bps: '5',
      minimum_body_return_bps: '0',
      directions: ['BULLISH', 'BULLISH', 'BULLISH'],
      combination: 'ALL',
    },
  },
  {
    schema_version: 1,
    preview_hash: 'b'.repeat(64),
    name: '混合方向 B',
    saved_at: '2026-08-27T00:01:00+08:00',
    data_version: 'baostock-five-minute-long-v1',
    dataset_checksum: 'long-five-minute-checksum',
    selected_session_count: 20,
    symbol_count: 75,
    sector_count: 15,
    factor_definition: { factor_id: 'b', name: 'B', expression: 'b' },
    observation_count: 1500,
    signal_count: 120,
    clustered_signal_session_count: 20,
    metrics: { total_return: '0', annualized_return: '0', sharpe: '0', maximum_drawdown: '0', excess_return: '0', rank_ic: '0', maximum_absolute_correlation: '0', stitched_oos_total_return: '0', stitched_oos_excess_return: '0', newey_west_t_statistic: '0', confidence_interval_95_lower: '0', confidence_interval_95_upper: '0' },
    research_gate_passed: false,
    robustness_gate_passed: false,
    promotion: 'NOT_ELIGIBLE',
    state_mutation: false,
    external_order_transmission: false,
    parameters: {
      factor: 'custom_opening_ten_minute_direction_rule',
      start: '2026-07-01',
      end: '2026-07-28',
      cost_bps: '5',
      minimum_body_return_bps: '10',
      directions: ['BULLISH', 'BEARISH', 'IGNORE'],
      combination: 'ANY',
      exit_rule: 'NEXT_SESSION_THREE_TEN_MINUTE_BEARISH_ELSE_14_55_TIME_STOP',
      exit_minimum_body_return_bps: '5',
    },
  },
] as unknown as LocalIntradayFactorExperiment[]

const factorCandidates = experiments.map((record, index) => ({
  schema_version: 1,
  classification: 'BROWSER_LOCAL_ENTRY_FACTOR_DRAFT_NOT_EVIDENCE',
  draft_key: `draft-${index + 1}`,
  name: record.name,
  saved_at: record.saved_at,
  data_version: record.data_version,
  dataset_checksum: record.dataset_checksum,
  directions: record.parameters.directions,
  combination: record.parameters.combination,
  minimum_body_return_bps: record.parameters.minimum_body_return_bps,
  expression: record.factor_definition.expression,
  validation_status: 'NOT_RUN',
  strategy_constructed: false,
  strategy_backtest_run: false,
  factor_registry_write: false,
  strategy_registry_write: false,
  promotion: 'NOT_ELIGIBLE',
  state_mutation: false,
  external_order_transmission: false,
})) as LocalIntradayFactorCandidate[]

vi.mock('../api/client', () => ({
  runIntradayFactorBacktest: vi.fn(),
  runIntradayFactorCorrelation: vi.fn(),
  runIntradayFactorDiscovery: vi.fn(),
}))

vi.mock('../research/localIntradayFactorExperiments', () => ({
  buildLocalIntradayFactorComparisonExport: vi.fn(() => ({
    classification: 'BROWSER_LOCAL_INTRADAY_FACTOR_COMPARISON_EXPORT_NOT_IMMUTABLE',
    records: experiments,
  })),
  loadLocalIntradayFactorExperiments: vi.fn(() => experiments),
  saveLocalIntradayFactorExperiment: vi.fn(),
  deleteLocalIntradayFactorExperiment: vi.fn(),
}))

vi.mock('../research/localIntradayFactorCandidates', () => ({
  loadLocalIntradayFactorCandidates: vi.fn(() => factorCandidates),
  saveLocalIntradayFactorCandidate: vi.fn(),
  deleteLocalIntradayFactorCandidate: vi.fn(),
}))

async function withPreviewHash<T extends { preview_hash: string }>(value: T): Promise<T> {
  const unsigned = structuredClone(value) as Record<string, unknown>
  delete unsigned.preview_hash
  const previewHash = await sha256Hex(canonicalJson(unsigned))
  if (previewHash === null) throw new Error('Web Crypto SHA-256 unavailable')
  return { ...value, preview_hash: previewHash }
}

const tradingDays = Array.from({ length: 22 }, (_, index) => (
  `2026-07-${String(index + 1).padStart(2, '0')}`
))
const dataset = {
  data_version: 'baostock-five-minute-long-v1',
  checksum: 'long-five-minute-checksum',
  symbol_count: 75,
  sector_count: 15,
  session_bar_count_per_symbol: 48,
  trading_days: tradingDays,
} as NonNullable<FiveMinuteResearchReport['minute_dataset']>

const discoveryTradingDays = Array.from({ length: 45 }, (_, index) => {
  const day = new Date(Date.UTC(2026, 5, 1 + index))
  return day.toISOString().slice(0, 10)
})
const discoveryDataset = {
  ...dataset,
  trading_days: discoveryTradingDays,
} as NonNullable<FiveMinuteResearchReport['minute_dataset']>

const correlation = {
  classification: 'AD_HOC_INTRADAY_FACTOR_CORRELATION_CURRENT_NON_PIT',
  parameters: {
    start: '2026-07-02',
    end: '2026-07-21',
    rules: experiments.map((record) => ({
      directions: record.parameters.directions,
      combination: record.parameters.combination,
      minimum_body_return_bps: record.parameters.minimum_body_return_bps,
    })),
  },
  score_contract: {
    source_interval: 'NATIVE_5M_AGGREGATED_TO_10M',
    source_timestamp_convention: 'BAR_END_TIME',
    source_bars: '09:35,09:40,09:45,09:50,09:55,10:00',
    known_at: '10:00_BAR_CLOSE',
    score_definition: 'ALL_MIN_OR_ANY_MAX_OF_SIGNED_TEN_MINUTE_BODY_RETURNS',
    binary_signal_used: false,
    future_features_used: false,
    arbitrary_code_execution: false,
  },
  rules: [
    { rule_id: 'R01', factor_id: 'a', name: 'A', expression: 'a', directions: ['BULLISH', 'BULLISH', 'BULLISH'], combination: 'ALL', minimum_body_return_bps: '0' },
    { rule_id: 'R02', factor_id: 'b', name: 'B', expression: 'b', directions: ['BULLISH', 'BEARISH', 'IGNORE'], combination: 'ANY', minimum_body_return_bps: '10' },
  ],
  multiple_testing_contract: {
    method: 'HOLM_BONFERRONI',
    family_size: 2,
    alpha: '0.05',
    raw_p_value: 'TWO_SIDED_ASYMPTOTIC_NORMAL_FROM_NEWEY_WEST_T',
    effect_direction: 'POSITIVE_SESSION_CONDITIONAL_EXCESS',
    minimum_clustered_signal_sessions: 10,
    minimum_signal_count: 20,
    minimum_t_statistic: '1.96',
  },
  effectiveness: [
    {
      rule_id: 'R01', minimum_body_return_bps: '0', observation_count: 1500, signal_count: 100,
      clustered_signal_session_count: 20, mean_session_conditional_excess_return: '0.001', newey_west_lag: 2,
      newey_west_standard_error: '0.0004', newey_west_t_statistic: '2.5', confidence_interval_95_lower: '0.0002',
      confidence_interval_95_upper: '0.0018', raw_p_value: '0.012', holm_adjusted_p_value: '0.024',
      criteria: {
        minimum_observation_count: true,
        minimum_signal_count: true,
        minimum_ten_clustered_signal_sessions: true,
        positive_session_conditional_excess: true,
        newey_west_t_statistic_above_1_96: true,
        holm_adjusted_p_value_at_most_0_05: true,
      },
      passed: true,
    },
    {
      rule_id: 'R02', minimum_body_return_bps: '10', observation_count: 1500, signal_count: 40,
      clustered_signal_session_count: 15, mean_session_conditional_excess_return: '-0.0002', newey_west_lag: 2,
      newey_west_standard_error: '0.0004', newey_west_t_statistic: '-0.5', confidence_interval_95_lower: '-0.001',
      confidence_interval_95_upper: '0.0006', raw_p_value: '0.617', holm_adjusted_p_value: '0.617',
      criteria: {
        minimum_observation_count: true,
        minimum_signal_count: true,
        minimum_ten_clustered_signal_sessions: true,
        positive_session_conditional_excess: false,
        newey_west_t_statistic_above_1_96: false,
        holm_adjusted_p_value_at_most_0_05: false,
      },
      passed: false,
    },
  ],
  effective_rule_count: 1,
  observation_count: 1500,
  matrix: [
    { rule_id: 'R01', correlations: { R01: '1', R02: '0.95' } },
    { rule_id: 'R02', correlations: { R01: '0.95', R02: '1' } },
  ],
  pairs: [{ left_rule_id: 'R01', right_rule_id: 'R02', correlation: '0.95', redundant: true }],
  redundancy_threshold: '0.90',
  maximum_absolute_correlation: '0.95',
  passed: false,
  data_version: 'baostock-five-minute-long-v1',
  dataset_checksum: 'long-five-minute-checksum',
  data_interval: '5m',
  selected_trading_days: tradingDays.slice(1, 21),
  exit_completion_day: tradingDays[21],
  source_bar_count: 75_600,
  symbol_count: 75,
  sector_count: 15,
  promotion: 'NOT_ELIGIBLE',
  paper_readiness: 'NOT_ELIGIBLE',
  live_readiness: 'DISABLED_REQUIRES_SEPARATE_AUTHORIZATION',
  point_in_time: false,
  future_adjustment_knowledge_possible: true,
  realtime_readiness: false,
  state_mutation: false,
  external_order_transmission: false,
  preview_hash: 'c'.repeat(64),
} as unknown as IntradayFactorCorrelation

const admittedCorrelation = {
  ...correlation,
  effectiveness: correlation.effectiveness.map((row) => ({
    ...row,
    mean_session_conditional_excess_return: '0.001',
    newey_west_t_statistic: '2.5',
    raw_p_value: '0.012',
    holm_adjusted_p_value: '0.024',
    criteria: Object.fromEntries(Object.keys(row.criteria).map((key) => [key, true])),
    passed: true,
  })),
  effective_rule_count: 2,
  matrix: [
    { rule_id: 'R01', correlations: { R01: '1', R02: '0.50' } },
    { rule_id: 'R02', correlations: { R01: '0.50', R02: '1' } },
  ],
  pairs: [{ left_rule_id: 'R01', right_rule_id: 'R02', correlation: '0.50', redundant: false }],
  maximum_absolute_correlation: '0.50',
  passed: true,
  preview_hash: 'e'.repeat(64),
} as IntradayFactorCorrelation

const discoveryMetrics = {
  total_return: '-0.061491', annualized_return: '-0.54', annualized_volatility: '0.12',
  sharpe: '-4.7951', sortino: '-5.2', maximum_drawdown: '-0.098972', calmar: '-5.455',
  win_rate: '0.4', benchmark_return: '-0.044', excess_return: '-0.018107', information_ratio: '-1.5',
  number_of_periods: 20, turnover: '0.3', profit_loss_ratio: '0.8', exposure: '0.2',
  number_of_trades: 326, transaction_costs: '0.001',
}
const discoveryCandidate = {
  candidate_id: 'DIR-XDU-T0000', directions: ['IGNORE', 'BEARISH', 'BULLISH'], combination: 'ALL',
  minimum_body_return_bps: '0', factor_expression: 'bearish_10m_0940_0950 AND bullish_10m_0950_1000',
  training_observation_count: 1500, training_signal_count: 348, training_signal_session_count: 20,
  training_mean_session_conditional_excess_return: '0.00493055', training_newey_west_t_statistic: '2.189668',
  training_strategy_metrics: { ...discoveryMetrics, total_return: '0.059943', excess_return: '0.096344' },
  training_benchmark_metrics: discoveryMetrics,
  training_gate_criteria: {
    minimum_250_training_observations: true,
    minimum_twenty_training_signals: true,
    minimum_ten_training_signal_sessions: true,
    positive_training_session_conditional_excess: true,
    training_newey_west_t_statistic_above_1_96: true,
  },
  training_gate_passed: true,
}
const discoveryDirections = ['BULLISH', 'BEARISH', 'IGNORE'] as const
const discoveryDirectionCodes = { BULLISH: 'U', BEARISH: 'D', IGNORE: 'X' } as const
const discoveryOpeningLabels = ['0930_0940', '0940_0950', '0950_1000'] as const
const discoveryThresholds = ['0', '5', '10', '20'] as const
const discoveryCandidates = discoveryDirections.flatMap((first) => (
  discoveryDirections.flatMap((second) => (
    discoveryDirections.flatMap((third) => {
      const directions = [first, second, third] as const
      if (directions.every((direction) => direction === 'IGNORE')) return []
      return discoveryThresholds.map((threshold) => {
        const candidateId = `DIR-${directions.map((direction) => discoveryDirectionCodes[direction]).join('')}-T${threshold.padStart(4, '0')}`
        if (candidateId === discoveryCandidate.candidate_id) return discoveryCandidate
        return {
          ...discoveryCandidate,
          candidate_id: candidateId,
          directions,
          minimum_body_return_bps: threshold,
          factor_expression: directions.flatMap((direction, index) => (
            direction === 'IGNORE' ? [] : [`${direction.toLowerCase()}_10m_${discoveryOpeningLabels[index]}`]
          )).join(' AND '),
          training_mean_session_conditional_excess_return: '-0.001',
          training_newey_west_t_statistic: '1',
          training_gate_criteria: {
            minimum_250_training_observations: true,
            minimum_twenty_training_signals: true,
            minimum_ten_training_signal_sessions: true,
            positive_training_session_conditional_excess: false,
            training_newey_west_t_statistic_above_1_96: false,
          },
          training_gate_passed: false,
        }
      })
    })
  ))
))
const discoverySelectedDays = discoveryTradingDays.slice(4, 44)
const discoveryBlockIds = Array.from({ length: 8 }, (_, index) => index + 1)
function numberCombinations(values: readonly number[], size: number): number[][] {
  const result: number[][] = []
  function visit(start: number, selected: number[]): void {
    if (selected.length === size) {
      result.push(selected)
      return
    }
    for (let index = start; index <= values.length - (size - selected.length); index += 1) {
      visit(index + 1, [...selected, values[index]])
    }
  }
  visit(0, [])
  return result
}
const discoveryAlternativeWinnerIds = discoveryCandidates
  .map((candidate) => candidate.candidate_id)
  .filter((candidateId) => candidateId !== 'DIR-DDD-T0020')
  .slice(0, 16)
const discoveryCscvSplits = numberCombinations(discoveryBlockIds, 4).map((trainingBlocks, index) => {
  const belowMedian = index < 29
  return {
    split_id: `CSCV-${String(index + 1).padStart(3, '0')}`,
    training_block_ids: trainingBlocks,
    test_block_ids: discoveryBlockIds.filter((blockId) => !trainingBlocks.includes(blockId)),
    selected_candidate_id: index < 24
      ? 'DIR-DDD-T0020'
      : discoveryAlternativeWinnerIds[(index - 24) % discoveryAlternativeWinnerIds.length],
    training_excess_return: '0.08',
    test_excess_return: index < 35 ? '-0.004' : '0.0004235066',
    test_average_rank: belowMedian ? '26.5' : '78.5',
    test_rank_percentile: belowMedian ? '0.25' : '0.75',
    test_rank_logit: belowMedian ? '-1.0986122886681098' : '1.0986122886681098',
    below_median: belowMedian,
  }
})
const discovery = {
  classification: 'BOUNDED_INTRADAY_FACTOR_DISCOVERY_REUSED_HISTORY_NOT_INDEPENDENT',
  parameters: { start: '2026-06-05', end: '2026-07-14', cost_bps: '5' },
  candidate_contract: {
    direction_values: ['BULLISH', 'BEARISH', 'IGNORE'], active_slot_requirement: 'AT_LEAST_ONE',
    combination: 'ALL', thresholds_bps: ['0', '5', '10', '20'], candidate_count: 104,
    arbitrary_code_execution: false, future_features_used: false,
  },
  split: {
    training_start: '2026-06-05', training_end: '2026-06-24', training_session_count: 20,
    test_start: '2026-06-25', test_end: '2026-07-14', test_session_count: 20,
    selection_known_at: '2026-06-25T09:35:00+08:00', first_test_signal_known_at: '2026-06-25T10:00:00+08:00',
    selection_uses_test_returns: false, chronological_disjoint: true,
  },
  candidates: discoveryCandidates,
  selected: {
    candidate_id: 'DIR-XDU-T0000', directions: ['IGNORE', 'BEARISH', 'BULLISH'], combination: 'ALL',
    minimum_body_return_bps: '0', factor_expression: 'bearish_10m_0940_0950 AND bullish_10m_0950_1000',
    training_gate_passed: true, training_signal_count: 348, training_signal_session_count: 20,
    training_mean_session_conditional_excess_return: '0.00493055', training_newey_west_t_statistic: '2.189668',
    training_strategy_metrics: discoveryCandidate.training_strategy_metrics,
    selection_rule: 'TRAINING_GATE_THEN_NEWEY_WEST_T_THEN_EFFECT_THEN_LOWER_THRESHOLD_THEN_ID',
  },
  overfitting_audit: {
    method: 'COMBINATORIALLY_SYMMETRIC_CROSS_VALIDATION', block_count: 8, split_count: 70,
    selection_metric: 'TRAINING_GATE_THEN_NEWEY_WEST_T_THEN_SESSION_CONDITIONAL_EXCESS',
    rank_tie_method: 'AVERAGE_RANK', block_partition: 'EIGHT_CONTIGUOUS_BALANCED_BLOCKS_OLDEST_TO_NEWEST',
    rank_components: ['GATE_PASSED', 'NEWEY_WEST_T_STATISTIC', 'SESSION_CONDITIONAL_EXCESS', 'LOWER_BODY_THRESHOLD'],
    training_blocks_per_split: 4, test_blocks_per_split: 4, reuses_original_locked_test_blocks: true,
    candidate_selection_uses_only_each_cscv_training_partition: true, changes_original_locked_candidate: false,
    independent_confirmation: false, promotion_authority: false,
    blocks: discoveryBlockIds.map((blockId, index) => ({
      block_id: blockId,
      start: discoverySelectedDays[index * 5],
      end: discoverySelectedDays[index * 5 + 4],
      session_count: 5,
    })),
    splits: discoveryCscvSplits,
    below_median_split_count: 29, probability_of_backtest_overfitting: '0.4142857143',
    selected_oos_positive_excess_split_count: 35, selected_oos_positive_excess_ratio: '0.5',
    median_selected_oos_excess_return: '-0.0017882467', unique_selected_candidate_count: 17,
    most_selected_candidate_id: 'DIR-DDD-T0020', most_selected_candidate_count: 24,
    criteria: {
      probability_of_backtest_overfitting_below_0_25: false,
      selected_oos_positive_excess_ratio_at_least_0_60: false,
      median_selected_oos_excess_return_positive: false,
    },
    passed: false,
  },
  locked_test_cost_stress: {
    costs_bps: ['0', '5', '10', '20'], selected_candidate_id: 'DIR-XDU-T0000',
    test_start: '2026-06-25', test_end: '2026-07-14', signal_count: 326,
    candidate_reselected: false, cost_rows_used_for_selection: false,
    rows: {
      '0': { strategy_metrics: { ...discoveryMetrics, total_return: '-0.042527', excess_return: '-0.018473', sharpe: '-3.2561', maximum_drawdown: '-0.089914', transaction_costs: '0' }, benchmark_metrics: discoveryMetrics },
      '5': { strategy_metrics: discoveryMetrics, benchmark_metrics: discoveryMetrics },
      '10': { strategy_metrics: { ...discoveryMetrics, total_return: '-0.080089', excess_return: '-0.017749', sharpe: '-6.3364', maximum_drawdown: '-0.108719', transaction_costs: '0.039895' }, benchmark_metrics: discoveryMetrics },
      '20': { strategy_metrics: { ...discoveryMetrics, total_return: '-0.116212', excess_return: '-0.017052', sharpe: '-9.4260', maximum_drawdown: '-0.130167', transaction_costs: '0.079751' }, benchmark_metrics: discoveryMetrics },
    },
    criteria: {
      zero_cost_locked_test_total_return_positive: false,
      all_fixed_cost_locked_test_total_returns_positive: false,
      twenty_bps_locked_test_excess_return_positive: false,
      locked_test_total_return_non_increasing_with_cost: true,
    },
    passed: false,
  },
  locked_test_sector_stress: {
    selected_candidate_id: 'DIR-XDU-T0000', test_start: '2026-06-25', test_end: '2026-07-14',
    candidate_reselected: false, sector_rows_used_for_selection: false,
    sector_only: Array.from({ length: 15 }, (_, index) => ({
      sector_code: `S${String(index + 1).padStart(2, '0')}`, sector_name: `行业${index + 1}`,
      observation_count: 100, signal_count: index === 14 ? 18 : 22,
      mean_signal_forward_return: '0', signal_hit_rate: '0.5',
      strategy_metrics: { ...discoveryMetrics, total_return: index < 4 ? '0.02' : '-0.05' },
      benchmark_metrics: discoveryMetrics,
    })),
    leave_one_sector_out: Array.from({ length: 15 }, (_, index) => ({
      excluded_sector_code: `S${String(index + 1).padStart(2, '0')}`, excluded_sector_name: `行业${index + 1}`,
      observation_count: 1400, signal_count: index === 14 ? 308 : 304,
      strategy_metrics: { ...discoveryMetrics, total_return: '-0.06' }, benchmark_metrics: discoveryMetrics,
    })),
    positive_sector_count: 4, positive_sector_ratio: '0.2666666667',
    positive_exclusion_count: 0, positive_exclusion_ratio: '0',
    criteria: {
      every_locked_test_sector_has_signal: true,
      positive_locked_test_sector_ratio_at_least_0_60: false,
      positive_leave_one_sector_out_ratio_at_least_0_80: false,
      worst_leave_one_sector_out_total_return_non_negative: false,
    },
    passed: false,
  },
  locked_test_symbol_stress: {
    selected_candidate_id: 'DIR-XDU-T0000', test_start: '2026-06-25', test_end: '2026-07-14',
    candidate_reselected: false, symbol_rows_used_for_selection: false,
    symbols: Array.from({ length: 75 }, (_, index) => ({
      symbol: `${index < 30 ? '00' : index < 55 ? '60' : '30'}${String(index + 1).padStart(4, '0')}`,
      sector_code: `S${String((index % 15) + 1).padStart(2, '0')}`,
      sector_name: `行业${(index % 15) + 1}`, observation_count: 20,
      signal_count: index === 74 ? 0 : index < 30 ? 5 : 4,
      strategy_metrics: {
        ...discoveryMetrics,
        total_return: index < 5 ? '0.06310879872037346' : index < 22 ? '0.02' : '-0.0116444',
      },
      benchmark_metrics: discoveryMetrics,
    })),
    positive_symbol_count: 22, positive_symbol_ratio: '0.2933333333',
    median_symbol_total_return: '-0.0116444', top_five_absolute_return_share: '0.2479332831',
    top_five_absolute_return_symbols: ['000001', '000002', '000003', '000004', '000005'],
    criteria: {
      every_locked_test_symbol_has_signal: false,
      positive_locked_test_symbol_ratio_at_least_0_60: false,
      median_locked_test_symbol_total_return_positive: false,
      top_five_absolute_symbol_return_share_below_0_30: true,
    },
    passed: false,
  },
  locked_test_regime_stress: {
    selected_candidate_id: 'DIR-XDU-T0000', test_start: '2026-06-25', test_end: '2026-07-14',
    candidate_reselected: false, regime_rows_used_for_selection: false,
    regime_definition: 'PREVIOUS_SESSION_EQUAL_WEIGHT_UNIVERSE_GROSS_RETURN_SIGN',
    regime_result_known_at: 'CURRENT_SESSION_09_35', current_signal_known_at: 'CURRENT_SESSION_10_00',
    regimes: [
      {
        regime_id: 'PREVIOUS_SESSION_NEGATIVE', regime_name: '上一交易日等权收益为负',
        session_count: 12, observation_count: 900, signal_count: 188,
        previous_session_mean_universe_return_minimum: '-0.010247', previous_session_mean_universe_return_maximum: '-0.0000107',
        known_before_signal: true,
        strategy_metrics: { ...discoveryMetrics, number_of_periods: 12, total_return: '-0.007402', excess_return: '0.014887', sharpe: '-0.8998', maximum_drawdown: '-0.042835' },
        benchmark_metrics: { ...discoveryMetrics, number_of_periods: 12 },
      },
      {
        regime_id: 'PREVIOUS_SESSION_NON_NEGATIVE', regime_name: '上一交易日等权收益非负',
        session_count: 8, observation_count: 600, signal_count: 138,
        previous_session_mean_universe_return_minimum: '0.0002869', previous_session_mean_universe_return_maximum: '0.012327',
        known_before_signal: true,
        strategy_metrics: { ...discoveryMetrics, number_of_periods: 8, total_return: '-0.054493', excess_return: '-0.032917', sharpe: '-11.5724', maximum_drawdown: '-0.059405' },
        benchmark_metrics: { ...discoveryMetrics, number_of_periods: 8 },
      },
    ],
    criteria: {
      both_previous_session_direction_regimes_present: true,
      minimum_five_sessions_per_previous_session_regime: true,
      every_previous_session_regime_known_before_signal: true,
      every_previous_session_regime_has_signal: true,
      all_previous_session_regime_total_returns_positive: false,
      all_previous_session_regime_excess_returns_positive: false,
    },
    passed: false,
  },
  locked_test_placebo_audit: {
    selected_candidate_id: 'DIR-XDU-T0000', test_start: '2026-06-25', test_end: '2026-07-14',
    candidate_reselected: false, placebo_rows_used_for_selection: false,
    method: 'SESSION_PRESERVING_CROSS_SECTIONAL_PERMUTATION', replications: 499, seed: 20260827,
    preserves_daily_signal_counts: true,
    observed_mean_session_conditional_excess_return: '-0.0009189368',
    placebo_mean_session_conditional_excess_return: '-0.0000033146',
    placebo_median_session_conditional_excess_return: '0.0000264164',
    placebo_p95_session_conditional_excess_return: '0.0017929205',
    placebo_at_least_observed_count: 387, empirical_one_sided_p_value: '0.776',
    criteria: {
      minimum_ten_locked_test_signal_sessions_for_placebo: true,
      placebo_preserves_every_session_signal_count: true,
      observed_conditional_excess_positive: false,
      empirical_one_sided_placebo_p_value_at_most_0_05: false,
      observed_conditional_excess_above_placebo_p95: false,
    },
    passed: false,
  },
  locked_test_execution_delay_stress: {
    selected_candidate_id: 'DIR-XDU-T0000', test_start: '2026-06-25', test_end: '2026-07-14',
    cost_bps: '5', candidate_reselected: false, delay_rows_used_for_selection: false,
    signal_known_at: 'CURRENT_SESSION_10_00', source_bar_timestamp_convention: 'BAR_END_TIME',
    rows: [
      { delay_bars_after_signal: 1, entry_source_bar_time: '10:05', signal_count: 326, signal_session_count: 20, mean_session_conditional_excess_return: '-0.0009189368', strategy_metrics: discoveryMetrics, benchmark_metrics: discoveryMetrics },
      { delay_bars_after_signal: 2, entry_source_bar_time: '10:10', signal_count: 326, signal_session_count: 20, mean_session_conditional_excess_return: '-0.0004561347', strategy_metrics: { ...discoveryMetrics, total_return: '-0.0500131', excess_return: '-0.0093541', sharpe: '-3.9658', maximum_drawdown: '-0.0892412' }, benchmark_metrics: discoveryMetrics },
      { delay_bars_after_signal: 3, entry_source_bar_time: '10:15', signal_count: 326, signal_session_count: 20, mean_session_conditional_excess_return: '-0.0003367495', strategy_metrics: { ...discoveryMetrics, total_return: '-0.0467354', excess_return: '-0.0070270', sharpe: '-3.7751', maximum_drawdown: '-0.0803629' }, benchmark_metrics: discoveryMetrics },
    ],
    criteria: {
      three_predeclared_execution_delays_present: true,
      every_execution_delay_preserves_locked_signal_identity: true,
      all_execution_delay_total_returns_positive: false,
      all_execution_delay_excess_returns_positive: false,
    },
    passed: false,
  },
  locked_test_liquidity_capacity_stress: {
    selected_candidate_id: 'DIR-XDU-T0000', test_start: '2026-06-25', test_end: '2026-07-14',
    candidate_reselected: false, capacity_rows_used_for_selection: false,
    volume_source_bar_time: '10:00', volume_source_interval: '09:55-10:00',
    volume_source_bar_completed_before_entry: true, volume_unit: 'SHARES',
    order_lot_size_shares: 100, source_amount_available: false,
    qfq_price_volume_used_as_actual_notional: false,
    rmb_notional_capacity_status: 'UNAVAILABLE_QFQ_OHLCV_WITHOUT_PROVIDER_AMOUNT',
    rows: [
      { participation_rate: '0.01', signal_count: 326, signal_session_count: 20, minimum_round_lot_share_capacity: 100, lower_median_round_lot_share_capacity: 3200, maximum_round_lot_share_capacity: 25800, zero_capacity_signal_count: 0, one_lot_eligible_signal_count: 326, one_lot_eligible_signal_ratio: '1' },
      { participation_rate: '0.05', signal_count: 326, signal_session_count: 20, minimum_round_lot_share_capacity: 500, lower_median_round_lot_share_capacity: 16400, maximum_round_lot_share_capacity: 129400, zero_capacity_signal_count: 0, one_lot_eligible_signal_count: 326, one_lot_eligible_signal_ratio: '1' },
      { participation_rate: '0.10', signal_count: 326, signal_session_count: 20, minimum_round_lot_share_capacity: 1100, lower_median_round_lot_share_capacity: 32900, maximum_round_lot_share_capacity: 258900, zero_capacity_signal_count: 0, one_lot_eligible_signal_count: 326, one_lot_eligible_signal_ratio: '1' },
    ],
    criteria: {
      three_predeclared_participation_rates_present: true,
      all_capacity_uses_completed_signal_bar_volume: true,
      every_participation_row_preserves_locked_signal_identity: true,
      every_locked_signal_supports_one_round_lot_at_1pct: true,
      exact_provider_rmb_amount_available: false,
    },
    passed: false,
  },
  discovery_gate: {
    criteria: {
      chronological_locked_test_passed: false,
      selection_bias_audit_passed: false,
      locked_test_cost_stress_passed: false,
      locked_test_sector_stress_passed: false,
      locked_test_symbol_stress_passed: false,
      locked_test_regime_stress_passed: false,
      locked_test_placebo_audit_passed: false,
      locked_test_execution_delay_stress_passed: false,
      locked_test_liquidity_capacity_stress_passed: false,
    },
    passed: false,
    promotion_authority: false,
  },
  test_validation: {
    observation_count: 1500, signal_count: 326, signal_session_count: 20,
    mean_session_conditional_excess_return: '-0.0009189368', newey_west_lag: 2,
    newey_west_standard_error: '0.001682', newey_west_t_statistic: '-0.546233',
    confidence_interval_95_lower: '-0.00421628', confidence_interval_95_upper: '0.0023784',
    strategy_metrics: discoveryMetrics, benchmark_metrics: discoveryMetrics,
    criteria: {
      minimum_250_test_observations: true,
      minimum_twenty_test_signals: true,
      minimum_ten_test_signal_sessions: true,
      positive_test_session_conditional_excess: false,
      test_newey_west_t_statistic_above_1_96: false,
      test_strategy_total_return_positive: false,
      test_strategy_excess_return_positive: false,
    },
    passed: false,
  },
  data_version: 'baostock-five-minute-long-v1', dataset_checksum: 'long-five-minute-checksum',
  data_interval: '5m', selected_trading_days: discoveryTradingDays.slice(4, 44),
  exit_completion_day: discoveryTradingDays[44], source_bar_count: 147_600,
  symbol_count: 75, sector_count: 15, point_in_time: false,
  future_adjustment_knowledge_possible: true, realtime_readiness: false,
  independent_confirmation: false, promotion: 'NOT_ELIGIBLE',
  promotion_reasons: ['DISCOVERY_AND_TEST_WINDOW_ALREADY_VIEWED'],
  paper_readiness: 'NOT_ELIGIBLE', live_readiness: 'DISABLED_REQUIRES_SEPARATE_AUTHORIZATION',
  state_mutation: false, external_order_transmission: false,
  preview_hash: 'd'.repeat(64),
} as unknown as IntradayFactorDiscovery

describe('intraday factor rule correlation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('reruns two selected local rules on one bounded window and shows redundancy', async () => {
    vi.mocked(runIntradayFactorCorrelation).mockResolvedValueOnce(await withPreviewHash(correlation))
    render(<IntradayFactorResearchLab dataset={dataset} />)

    expect(screen.getByText('当前开平仓条件草案（未运行）')).toBeInTheDocument()
    expect(screen.getByText('body_return_0930_0940 >= 0 bps AND body_return_0940_0950 >= 0 bps AND body_return_0950_1000 >= 0 bps')).toBeInTheDocument()
    expect(screen.getByText('KNOWN_AT_10_00_CLOSE → NEXT_5M_OPEN_10_05')).toBeInTheDocument()
    expect(screen.getByText('T+1_OPEN_09_30')).toBeInTheDocument()
    expect(screen.getByText(/不是服务端规范、规格哈希、有效性结论或晋级证据/)).toBeInTheDocument()
    expect(screen.getByText('本地开仓因子候选（2/30）')).toBeInTheDocument()
    expect(screen.getByText(/不包含平仓条件、策略指标或回测结论/)).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /导出有效性与相关性证据/ })).not.toBeInTheDocument()
    const button = screen.getByRole('button', { name: '检验选中规则有效性与相关性（0/8）' })
    expect(button).toBeDisabled()
    const checkboxes = screen.getAllByRole('checkbox')
    fireEvent.click(checkboxes[1])
    fireEvent.click(checkboxes[2])
    fireEvent.click(screen.getByRole('button', { name: '检验选中规则有效性与相关性（2/8）' }))

    expect(runIntradayFactorBacktest).not.toHaveBeenCalled()

    await waitFor(() => expect(runIntradayFactorCorrelation).toHaveBeenCalledWith({
      start: '2026-07-02',
      end: '2026-07-21',
      rules: [
        { directions: ['BULLISH', 'BULLISH', 'BULLISH'], combination: 'ALL', minimum_body_return_bps: '0' },
        { directions: ['BULLISH', 'BEARISH', 'IGNORE'], combination: 'ANY', minimum_body_return_bps: '10' },
      ],
    }))
    expect(await screen.findByText('1/2 条规则通过同窗有效性硬门')).toBeInTheDocument()
    expect(screen.getAllByText('Holm p').length).toBeGreaterThan(0)
    expect(screen.getAllByText('未通过').length).toBeGreaterThan(0)
    expect(screen.getByText('发现 1 对高度相关规则')).toBeInTheDocument()
    expect(screen.getByText(/1,500 个同窗股票日观测/)).toBeInTheDocument()
    expect(screen.getAllByText('R01 · 三连阳 A').length).toBeGreaterThan(0)
    expect(screen.getAllByText('R02 · 混合方向 B').length).toBeGreaterThan(0)
    expect(screen.getByText('冗余')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /导出有效性与相关性证据/ })).toBeEnabled()
    expect(screen.getByText(/无状态写入、无外部订单/)).toBeInTheDocument()
  })

  it('saves the current entry factor before any strategy backtest', async () => {
    vi.mocked(saveLocalIntradayFactorCandidate).mockReturnValueOnce(factorCandidates[0])
    render(<IntradayFactorResearchLab dataset={dataset} />)

    fireEvent.change(screen.getByRole('textbox', { name: '开仓因子候选名称' }), {
      target: { value: '三连阳开仓草案' },
    })
    fireEvent.click(screen.getByRole('button', { name: '保存当前开仓因子候选（不回测）' }))

    await waitFor(() => expect(saveLocalIntradayFactorCandidate).toHaveBeenCalledWith(
      '三连阳开仓草案',
      dataset,
      {
        directions: ['BULLISH', 'BULLISH', 'BULLISH'],
        combination: 'ALL',
        minimum_body_return_bps: '0',
      },
    ))
    expect(screen.getByText(/尚未运行有效性、相关性或策略回测/)).toBeInTheDocument()
    expect(runIntradayFactorBacktest).not.toHaveBeenCalled()
    expect(runIntradayFactorCorrelation).not.toHaveBeenCalled()
  })

  it('hands an effective non-redundant entry factor to strategy construction without running it', async () => {
    vi.mocked(runIntradayFactorCorrelation).mockResolvedValueOnce(await withPreviewHash(admittedCorrelation))
    render(<IntradayFactorResearchLab dataset={dataset} />)

    expect(screen.queryByRole('button', { name: '载入“混合方向 B”进入策略构造' })).not.toBeInTheDocument()
    const checkboxes = screen.getAllByRole('checkbox')
    fireEvent.click(checkboxes[1])
    fireEvent.click(checkboxes[2])
    fireEvent.click(screen.getByRole('button', { name: '检验选中规则有效性与相关性（2/8）' }))

    const handoff = await screen.findByRole('button', { name: '载入“混合方向 B”进入策略构造' })
    fireEvent.click(handoff)
    expect(await screen.findByText('body_return_0930_0940 >= 10 bps OR body_return_0940_0950 <= -10 bps')).toBeInTheDocument()
    expect(screen.getByText(/只复制已绑定的开仓因子；日期、成本和平仓条件保持不变/)).toBeInTheDocument()
    expect(screen.getByText('T+1_OPEN_09_30')).toBeInTheDocument()
    expect(runIntradayFactorBacktest).not.toHaveBeenCalled()

    fireEvent.change(screen.getByRole('spinbutton', { name: '每根 10 分钟实体下限（bps）' }), {
      target: { value: '11' },
    })
    expect(screen.queryByText(/只复制已绑定的开仓因子；日期、成本和平仓条件保持不变/)).not.toBeInTheDocument()
  })

  it('fails closed when the factor-correlation response drifts from the requested dataset', async () => {
    vi.mocked(runIntradayFactorCorrelation).mockResolvedValueOnce(await withPreviewHash({
      ...admittedCorrelation,
      dataset_checksum: 'drifted-checksum',
    }))
    render(<IntradayFactorResearchLab dataset={dataset} />)

    const checkboxes = screen.getAllByRole('checkbox')
    fireEvent.click(checkboxes[1])
    fireEvent.click(checkboxes[2])
    fireEvent.click(screen.getByRole('button', { name: '检验选中规则有效性与相关性（2/8）' }))

    expect(await screen.findByText('相关性响应身份或安全边界与请求不一致，结果已拒绝')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /进入策略构造/ })).not.toBeInTheDocument()
    expect(runIntradayFactorBacktest).not.toHaveBeenCalled()
  })

  it('fails closed when the factor-correlation response drifts from the requested 75-stock scope', async () => {
    vi.mocked(runIntradayFactorCorrelation).mockResolvedValueOnce({
      ...admittedCorrelation,
      symbol_count: 1,
      observation_count: 20,
    })
    render(<IntradayFactorResearchLab dataset={dataset} />)

    const checkboxes = screen.getAllByRole('checkbox')
    fireEvent.click(checkboxes[1])
    fireEvent.click(checkboxes[2])
    fireEvent.click(screen.getByRole('button', { name: '检验选中规则有效性与相关性（2/8）' }))

    expect(await screen.findByText('相关性响应身份或安全边界与请求不一致，结果已拒绝')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /进入策略构造/ })).not.toBeInTheDocument()
  })

  it('fails closed when the factor-correlation matrix is asymmetric', async () => {
    vi.mocked(runIntradayFactorCorrelation).mockResolvedValueOnce({
      ...admittedCorrelation,
      matrix: [
        { rule_id: 'R01', correlations: { R01: '1', R02: '0.50' } },
        { rule_id: 'R02', correlations: { R01: '0.40', R02: '1' } },
      ],
    })
    render(<IntradayFactorResearchLab dataset={dataset} />)

    const checkboxes = screen.getAllByRole('checkbox')
    fireEvent.click(checkboxes[1])
    fireEvent.click(checkboxes[2])
    fireEvent.click(screen.getByRole('button', { name: '检验选中规则有效性与相关性（2/8）' }))

    expect(await screen.findByText('相关性响应身份或安全边界与请求不一致，结果已拒绝')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /进入策略构造/ })).not.toBeInTheDocument()
  })

  it('fails closed when the reported Holm adjustment does not match the submitted family', async () => {
    vi.mocked(runIntradayFactorCorrelation).mockResolvedValueOnce({
      ...admittedCorrelation,
      effectiveness: admittedCorrelation.effectiveness.map((row, index) => (
        index === 0 ? { ...row, holm_adjusted_p_value: row.raw_p_value } : row
      )),
    })
    render(<IntradayFactorResearchLab dataset={dataset} />)

    const checkboxes = screen.getAllByRole('checkbox')
    fireEvent.click(checkboxes[1])
    fireEvent.click(checkboxes[2])
    fireEvent.click(screen.getByRole('button', { name: '检验选中规则有效性与相关性（2/8）' }))

    expect(await screen.findByText('相关性响应身份或安全边界与请求不一致，结果已拒绝')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: /进入策略构造/ })).not.toBeInTheDocument()
  })

  it('fails closed before displaying a strategy backtest with drifted data identity', async () => {
    vi.mocked(runIntradayFactorBacktest).mockResolvedValueOnce({
      classification: 'AD_HOC_INTRADAY_FACTOR_RESEARCH_CURRENT_NON_PIT',
      parameters: {
        factor: 'opening_three_ten_minute_bullish',
        start: '2026-07-02',
        end: '2026-07-21',
        cost_bps: '5',
        minimum_body_return_bps: '0',
        directions: ['BULLISH', 'BULLISH', 'BULLISH'],
        combination: 'ALL',
      },
      data_version: dataset.data_version,
      dataset_checksum: 'drifted-checksum',
    } as unknown as IntradayFactorBacktest)
    render(<IntradayFactorResearchLab dataset={dataset} />)

    fireEvent.click(screen.getByRole('button', { name: '运行 75 股因子检验与回测' }))

    expect(await screen.findByText('策略回测响应身份或安全边界与请求不一致，结果已拒绝')).toBeInTheDocument()
    expect(screen.queryByText(/回测已完成/)).not.toBeInTheDocument()
  })

  it('loads and submits the bounded bearish exit identity', async () => {
    vi.mocked(runIntradayFactorBacktest).mockResolvedValueOnce(
      null as unknown as Awaited<ReturnType<typeof runIntradayFactorBacktest>>,
    )
    render(<IntradayFactorResearchLab dataset={dataset} />)

    fireEvent.click(screen.getAllByRole('button', { name: '载入因子参数' })[1])
    expect(screen.getByText('平仓因子严格遵守 T+1 与下一 Bar 执行')).toBeInTheDocument()
    expect(await screen.findByText('body_return_0930_0940 >= 10 bps OR body_return_0940_0950 <= -10 bps')).toBeInTheDocument()
    expect(await screen.findByText('IF T+1 (body_return_0930_0940 <= -5 bps AND body_return_0940_0950 <= -5 bps AND body_return_0950_1000 <= -5 bps) THEN NEXT_5M_OPEN_10_05 ELSE TIME_STOP_OPEN_14_55')).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '运行 75 股因子检验与回测' }))

    await waitFor(() => expect(runIntradayFactorBacktest).toHaveBeenCalledWith({
      factor: 'custom_opening_ten_minute_direction_rule',
      start: '2026-07-01',
      end: '2026-07-28',
      cost_bps: '5',
      minimum_body_return_bps: '10',
      directions: ['BULLISH', 'BEARISH', 'IGNORE'],
      combination: 'ANY',
      exit_rule: 'NEXT_SESSION_THREE_TEN_MINUTE_BEARISH_ELSE_14_55_TIME_STOP',
      exit_minimum_body_return_bps: '5',
    }))
  })

  it('locks one training rule before the disjoint discovery test window', async () => {
    vi.mocked(runIntradayFactorDiscovery).mockResolvedValueOnce(await withPreviewHash(discovery))
    render(<IntradayFactorResearchLab dataset={discoveryDataset} />)

    expect(screen.queryByRole('button', { name: '载入训练锁定开仓因子' })).not.toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '挖掘最近 40 日的 104 个有界因子' }))

    await waitFor(() => expect(runIntradayFactorDiscovery).toHaveBeenCalledWith({
      start: '2026-06-05',
      end: '2026-07-14',
      cost_bps: '5',
    }))
    expect(await screen.findByText('训练最优规则未通过互斥测试段')).toBeInTheDocument()
    expect(screen.getByText('CSCV 检出较高选择偏差风险')).toBeInTheDocument()
    expect(screen.getByText('锁定规则未通过固定成本压力门')).toBeInTheDocument()
    expect(screen.getByText('完整因子发现研究门未通过')).toBeInTheDocument()
    expect(screen.getByText('锁定规则未通过多行业鲁棒性门')).toBeInTheDocument()
    expect(screen.getByText('锁定规则未通过逐股票鲁棒性门')).toBeInTheDocument()
    expect(screen.getByText('锁定规则未通过信号前市场状态压力门')).toBeInTheDocument()
    expect(screen.getByText('锁定规则未通过会话内置换安慰剂门')).toBeInTheDocument()
    expect(screen.getByText('锁定规则未通过执行延迟压力门')).toBeInTheDocument()
    expect(screen.getByText('锁定规则股数容量可复算，但人民币容量不可用')).toBeInTheDocument()
    expect(screen.getByText('人民币容量不可用')).toBeInTheDocument()
    expect(screen.getByText('09:55-10:00')).toBeInTheDocument()
    expect(screen.getByText('3,200 股')).toBeInTheDocument()
    expect(screen.getByText('4/15 · 26.67%')).toBeInTheDocument()
    expect(screen.getByText('0/15 · 0.00%')).toBeInTheDocument()
    expect(screen.getByText('22/75 · 29.33%')).toBeInTheDocument()
    expect(screen.getByText('-1.16%')).toBeInTheDocument()
    expect(screen.getByText('24.79%')).toBeInTheDocument()
    expect(screen.getByText('12 / 900 / 188')).toBeInTheDocument()
    expect(screen.getByText('8 / 600 / 138')).toBeInTheDocument()
    expect(screen.getByText('-5.45%')).toBeInTheDocument()
    expect(screen.getByText('-11.62%')).toBeInTheDocument()
    expect(screen.getByText('+0.2642 bp')).toBeInTheDocument()
    expect(screen.getByText('77.60%')).toBeInTheDocument()
    expect(screen.getByText('387/499')).toBeInTheDocument()
    expect(screen.getByText('10:15')).toBeInTheDocument()
    expect(screen.getByText('-4.67%')).toBeInTheDocument()
    expect(screen.getByText('-0.70%')).toBeInTheDocument()
    expect(screen.getByText('41.43%')).toBeInTheDocument()
    expect(screen.getByText('DIR-DDD-T0020 · 24/70')).toBeInTheDocument()
    expect(screen.getAllByText(/DIR-XDU-T0000/).length).toBeGreaterThan(0)
    expect(screen.getByText('-0.09%')).toBeInTheDocument()
    expect(screen.getAllByText('-6.15%').length).toBeGreaterThan(0)
    expect(screen.getByText(/选择时刻.*早于首个测试信号/)).toBeInTheDocument()
    expect(screen.getByText(/无状态写入、无外部订单/)).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '载入训练锁定开仓因子' }))
    expect(await screen.findByText('body_return_0940_0950 <= -0 bps AND body_return_0950_1000 >= 0 bps')).toBeInTheDocument()
    expect(screen.getByText(/只回填开仓方向、ALL 组合和实体阈值；日期、成本与平仓条件保持不变/)).toBeInTheDocument()
    expect(runIntradayFactorBacktest).not.toHaveBeenCalled()
    fireEvent.change(screen.getByRole('spinbutton', { name: '单边成本（bps）' }), { target: { value: '6' } })
    expect(screen.queryByText('训练最优规则未通过互斥测试段')).not.toBeInTheDocument()
  })

  it('fails closed when a signed discovery response drifts from the requested dataset', async () => {
    vi.mocked(runIntradayFactorDiscovery).mockResolvedValueOnce(await withPreviewHash({
      ...discovery,
      dataset_checksum: 'drifted-checksum',
    } as unknown as IntradayFactorDiscovery))
    render(<IntradayFactorResearchLab dataset={discoveryDataset} />)

    fireEvent.click(screen.getByRole('button', { name: '挖掘最近 40 日的 104 个有界因子' }))

    expect(await screen.findByText('因子挖掘响应身份或安全边界与请求不一致，结果已拒绝')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '载入训练锁定开仓因子' })).not.toBeInTheDocument()
    expect(runIntradayFactorBacktest).not.toHaveBeenCalled()
  })

  it('fails closed when a signed discovery response substitutes a candidate identity', async () => {
    vi.mocked(runIntradayFactorDiscovery).mockResolvedValueOnce(await withPreviewHash({
      ...discovery,
      candidates: discovery.candidates.map((candidate, index) => (
        index === 1 ? { ...candidate, candidate_id: 'DIR-SUBSTITUTED-T0000' } : candidate
      )),
    } as unknown as IntradayFactorDiscovery))
    render(<IntradayFactorResearchLab dataset={discoveryDataset} />)

    fireEvent.click(screen.getByRole('button', { name: '挖掘最近 40 日的 104 个有界因子' }))

    expect(await screen.findByText('因子挖掘响应身份或安全边界与请求不一致，结果已拒绝')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '载入训练锁定开仓因子' })).not.toBeInTheDocument()
  })

  it('fails closed when a signed discovery response misstates its CSCV aggregate', async () => {
    vi.mocked(runIntradayFactorDiscovery).mockResolvedValueOnce(await withPreviewHash({
      ...discovery,
      overfitting_audit: {
        ...discovery.overfitting_audit,
        below_median_split_count: 28,
      },
    } as unknown as IntradayFactorDiscovery))
    render(<IntradayFactorResearchLab dataset={discoveryDataset} />)

    fireEvent.click(screen.getByRole('button', { name: '挖掘最近 40 日的 104 个有界因子' }))

    expect(await screen.findByText('因子挖掘响应身份或安全边界与请求不一致，结果已拒绝')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '载入训练锁定开仓因子' })).not.toBeInTheDocument()
  })

  it('fails closed when a signed discovery response misstates its cross-sector breadth', async () => {
    vi.mocked(runIntradayFactorDiscovery).mockResolvedValueOnce(await withPreviewHash({
      ...discovery,
      locked_test_sector_stress: {
        ...discovery.locked_test_sector_stress,
        positive_sector_count: 5,
      },
    } as unknown as IntradayFactorDiscovery))
    render(<IntradayFactorResearchLab dataset={discoveryDataset} />)

    fireEvent.click(screen.getByRole('button', { name: '挖掘最近 40 日的 104 个有界因子' }))

    expect(await screen.findByText('因子挖掘响应身份或安全边界与请求不一致，结果已拒绝')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '载入训练锁定开仓因子' })).not.toBeInTheDocument()
  })

  it('fails closed when a signed discovery response misstates its placebo probability', async () => {
    vi.mocked(runIntradayFactorDiscovery).mockResolvedValueOnce(await withPreviewHash({
      ...discovery,
      locked_test_placebo_audit: {
        ...discovery.locked_test_placebo_audit,
        placebo_at_least_observed_count: 386,
      },
    } as unknown as IntradayFactorDiscovery))
    render(<IntradayFactorResearchLab dataset={discoveryDataset} />)

    fireEvent.click(screen.getByRole('button', { name: '挖掘最近 40 日的 104 个有界因子' }))

    expect(await screen.findByText('因子挖掘响应身份或安全边界与请求不一致，结果已拒绝')).toBeInTheDocument()
    expect(screen.queryByRole('button', { name: '载入训练锁定开仓因子' })).not.toBeInTheDocument()
  })
})
