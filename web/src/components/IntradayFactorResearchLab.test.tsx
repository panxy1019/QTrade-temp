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
    expect(screen.getAllByText(/DIR-XDU-T0000/).length).toBeGreaterThan(0)
    expect(screen.getByText('-0.09%')).toBeInTheDocument()
    expect(screen.getByText('-6.15%')).toBeInTheDocument()
    expect(screen.getByText(/选择时刻.*早于首个测试信号/)).toBeInTheDocument()
    expect(screen.getByText(/无状态写入、无外部订单/)).toBeInTheDocument()
  })
})
