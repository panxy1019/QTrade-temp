import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { runIntradayFactorBacktest, runIntradayFactorCorrelation, runIntradayFactorDiscovery } from '../api/client'
import type { LocalIntradayFactorExperiment } from '../research/localIntradayFactorExperiments'
import type { FiveMinuteResearchReport, IntradayFactorCorrelation, IntradayFactorDiscovery } from '../types'
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

vi.mock('../api/client', () => ({
  runIntradayFactorBacktest: vi.fn(),
  runIntradayFactorCorrelation: vi.fn(),
  runIntradayFactorDiscovery: vi.fn(),
}))

vi.mock('../research/localIntradayFactorExperiments', () => ({
  loadLocalIntradayFactorExperiments: vi.fn(() => experiments),
  saveLocalIntradayFactorExperiment: vi.fn(),
  deleteLocalIntradayFactorExperiment: vi.fn(),
}))

const tradingDays = Array.from({ length: 22 }, (_, index) => (
  `2026-07-${String(index + 1).padStart(2, '0')}`
))
const dataset = {
  data_version: 'baostock-five-minute-long-v1',
  checksum: 'long-five-minute-checksum',
  symbol_count: 75,
  sector_count: 15,
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
    { rule_id: 'R01', minimum_body_return_bps: '0', observation_count: 1500, signal_count: 100, clustered_signal_session_count: 20, mean_session_conditional_excess_return: '0.001', newey_west_lag: 2, newey_west_standard_error: '0.0004', newey_west_t_statistic: '2.5', confidence_interval_95_lower: '0.0002', confidence_interval_95_upper: '0.0018', raw_p_value: '0.012', holm_adjusted_p_value: '0.024', criteria: {}, passed: true },
    { rule_id: 'R02', minimum_body_return_bps: '10', observation_count: 1500, signal_count: 40, clustered_signal_session_count: 15, mean_session_conditional_excess_return: '-0.0002', newey_west_lag: 2, newey_west_standard_error: '0.0004', newey_west_t_statistic: '-0.5', confidence_interval_95_lower: '-0.001', confidence_interval_95_upper: '0.0006', raw_p_value: '0.617', holm_adjusted_p_value: '0.617', criteria: {}, passed: false },
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
  dataset_checksum: 'long-five-minute-checksum',
  paper_readiness: 'NOT_ELIGIBLE',
  live_readiness: 'DISABLED_REQUIRES_SEPARATE_AUTHORIZATION',
  preview_hash: 'c'.repeat(64),
} as unknown as IntradayFactorCorrelation

const discoveryMetrics = {
  total_return: '-0.061491', annualized_return: '-0.54', sharpe: '-4.7951', maximum_drawdown: '-0.098972',
  excess_return: '-0.018107', information_ratio: '-1.5', number_of_periods: 20, turnover: '0.3',
  profit_loss_ratio: '0.8', exposure: '0.2', number_of_trades: 326, transaction_costs: '0.001',
}
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
  candidates: [{
    candidate_id: 'DIR-XDU-T0000', directions: ['IGNORE', 'BEARISH', 'BULLISH'], combination: 'ALL',
    minimum_body_return_bps: '0', factor_expression: 'bearish_10m_0940_0950 AND bullish_10m_0950_1000',
    training_observation_count: 1500, training_signal_count: 348, training_signal_session_count: 20,
    training_mean_session_conditional_excess_return: '0.00493055', training_newey_west_t_statistic: '2.189668',
    training_strategy_metrics: { ...discoveryMetrics, total_return: '0.059943', excess_return: '0.096344' },
    training_benchmark_metrics: discoveryMetrics, training_gate_criteria: {}, training_gate_passed: true,
  }],
  selected: {
    candidate_id: 'DIR-XDU-T0000', directions: ['IGNORE', 'BEARISH', 'BULLISH'], combination: 'ALL',
    minimum_body_return_bps: '0', factor_expression: 'bearish_10m_0940_0950 AND bullish_10m_0950_1000',
    training_gate_passed: true, training_signal_count: 348, training_signal_session_count: 20,
    training_mean_session_conditional_excess_return: '0.00493055', training_newey_west_t_statistic: '2.189668',
    training_strategy_metrics: discoveryMetrics, selection_rule: 'TRAINING_ONLY',
  },
  overfitting_audit: {
    method: 'COMBINATORIALLY_SYMMETRIC_CROSS_VALIDATION', block_count: 8, split_count: 70,
    selection_metric: 'TRAINING_GATE_THEN_NEWEY_WEST_T_THEN_SESSION_CONDITIONAL_EXCESS',
    rank_tie_method: 'AVERAGE_RANK', block_partition: 'EIGHT_CONTIGUOUS_BALANCED_BLOCKS_OLDEST_TO_NEWEST',
    rank_components: ['GATE_PASSED', 'NEWEY_WEST_T_STATISTIC', 'SESSION_CONDITIONAL_EXCESS', 'LOWER_BODY_THRESHOLD'],
    training_blocks_per_split: 4, test_blocks_per_split: 4, reuses_original_locked_test_blocks: true,
    candidate_selection_uses_only_each_cscv_training_partition: true, changes_original_locked_candidate: false,
    independent_confirmation: false, promotion_authority: false,
    blocks: Array.from({ length: 8 }, (_, index) => ({
      block_id: index + 1, start: `2026-06-${String(5 + index * 2).padStart(2, '0')}`,
      end: `2026-06-${String(6 + index * 2).padStart(2, '0')}`, session_count: 5,
    })),
    splits: [{
      split_id: 'CSCV-001', training_block_ids: [1, 2, 3, 4], test_block_ids: [5, 6, 7, 8],
      selected_candidate_id: 'DIR-DDD-T0020', training_excess_return: '0.08', test_excess_return: '0.03',
      test_average_rank: '80', test_rank_percentile: '0.7644', test_rank_logit: '1.177', below_median: false,
    }],
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
      observation_count: 100, signal_count: 10 + index, mean_signal_forward_return: '0', signal_hit_rate: '0.5',
      strategy_metrics: { ...discoveryMetrics, total_return: index < 4 ? '0.02' : '-0.05' },
      benchmark_metrics: discoveryMetrics,
    })),
    leave_one_sector_out: Array.from({ length: 15 }, (_, index) => ({
      excluded_sector_code: `S${String(index + 1).padStart(2, '0')}`, excluded_sector_name: `行业${index + 1}`,
      observation_count: 1400, signal_count: 300,
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
      signal_count: index === 74 ? 0 : 2 + (index % 9),
      strategy_metrics: { ...discoveryMetrics, total_return: index < 22 ? '0.02' : '-0.0116444' },
      benchmark_metrics: discoveryMetrics,
    })),
    positive_symbol_count: 22, positive_symbol_ratio: '0.2933333333',
    median_symbol_total_return: '-0.0116444', top_five_absolute_return_share: '0.2479332831',
    top_five_absolute_return_symbols: ['002558', '002353', '002028', '000408', '603993'],
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
        strategy_metrics: { ...discoveryMetrics, total_return: '-0.007402', excess_return: '0.014887', sharpe: '-0.8998', maximum_drawdown: '-0.042835' },
        benchmark_metrics: discoveryMetrics,
      },
      {
        regime_id: 'PREVIOUS_SESSION_NON_NEGATIVE', regime_name: '上一交易日等权收益非负',
        session_count: 8, observation_count: 600, signal_count: 138,
        previous_session_mean_universe_return_minimum: '0.0002869', previous_session_mean_universe_return_maximum: '0.012327',
        known_before_signal: true,
        strategy_metrics: { ...discoveryMetrics, total_return: '-0.054493', excess_return: '-0.032917', sharpe: '-11.5724', maximum_drawdown: '-0.059405' },
        benchmark_metrics: discoveryMetrics,
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
  discovery_gate: {
    criteria: {
      chronological_locked_test_passed: false,
      selection_bias_audit_passed: false,
      locked_test_cost_stress_passed: false,
      locked_test_sector_stress_passed: false,
      locked_test_symbol_stress_passed: false,
      locked_test_regime_stress_passed: false,
      locked_test_placebo_audit_passed: false,
    },
    passed: false,
    promotion_authority: false,
  },
  test_validation: {
    observation_count: 1500, signal_count: 326, signal_session_count: 20,
    mean_session_conditional_excess_return: '-0.00091894', newey_west_lag: 2,
    newey_west_standard_error: '0.001682', newey_west_t_statistic: '-0.546233',
    confidence_interval_95_lower: '-0.00421628', confidence_interval_95_upper: '0.0023784',
    strategy_metrics: discoveryMetrics, benchmark_metrics: discoveryMetrics,
    criteria: { positive_test_session_conditional_excess: false, test_strategy_total_return_positive: false },
    passed: false,
  },
  promotion: 'NOT_ELIGIBLE', state_mutation: false, external_order_transmission: false,
  preview_hash: 'd'.repeat(64),
} as unknown as IntradayFactorDiscovery

describe('intraday factor rule correlation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('reruns two selected local rules on one bounded window and shows redundancy', async () => {
    vi.mocked(runIntradayFactorCorrelation).mockResolvedValueOnce(correlation)
    render(<IntradayFactorResearchLab dataset={dataset} />)

    const button = screen.getByRole('button', { name: '检验选中规则有效性与相关性（0/8）' })
    expect(button).toBeDisabled()
    const checkboxes = screen.getAllByRole('checkbox')
    fireEvent.click(checkboxes[1])
    fireEvent.click(checkboxes[2])
    fireEvent.click(screen.getByRole('button', { name: '检验选中规则有效性与相关性（2/8）' }))

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
    expect(screen.getByText(/无状态写入、无外部订单/)).toBeInTheDocument()
  })

  it('loads and submits the bounded bearish exit identity', async () => {
    vi.mocked(runIntradayFactorBacktest).mockResolvedValueOnce(
      null as unknown as Awaited<ReturnType<typeof runIntradayFactorBacktest>>,
    )
    render(<IntradayFactorResearchLab dataset={dataset} />)

    fireEvent.click(screen.getAllByRole('button', { name: '载入因子参数' })[1])
    expect(screen.getByText('平仓因子严格遵守 T+1 与下一 Bar 执行')).toBeInTheDocument()
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
    vi.mocked(runIntradayFactorDiscovery).mockResolvedValueOnce(discovery)
    render(<IntradayFactorResearchLab dataset={discoveryDataset} />)

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
    expect(screen.getByText('41.43%')).toBeInTheDocument()
    expect(screen.getByText('DIR-DDD-T0020 · 24/70')).toBeInTheDocument()
    expect(screen.getAllByText(/DIR-XDU-T0000/).length).toBeGreaterThan(0)
    expect(screen.getByText('-0.09%')).toBeInTheDocument()
    expect(screen.getAllByText('-6.15%').length).toBeGreaterThan(0)
    expect(screen.getByText(/选择时刻.*早于首个测试信号/)).toBeInTheDocument()
    expect(screen.getByText(/无状态写入、无外部订单/)).toBeInTheDocument()
  })
})
