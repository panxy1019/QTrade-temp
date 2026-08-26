import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { runIntradayFactorCorrelation } from '../api/client'
import type { LocalIntradayFactorExperiment } from '../research/localIntradayFactorExperiments'
import type { FiveMinuteResearchReport, IntradayFactorCorrelation } from '../types'
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
      minimum_body_return_bps: '0',
      directions: ['BULLISH', 'BEARISH', 'IGNORE'],
      combination: 'ANY',
    },
  },
] as unknown as LocalIntradayFactorExperiment[]

vi.mock('../api/client', () => ({
  runIntradayFactorBacktest: vi.fn(),
  runIntradayFactorCorrelation: vi.fn(),
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

const correlation = {
  classification: 'AD_HOC_INTRADAY_FACTOR_CORRELATION_CURRENT_NON_PIT',
  parameters: {
    start: '2026-07-02',
    end: '2026-07-21',
    rules: experiments.map((record) => ({
      directions: record.parameters.directions,
      combination: record.parameters.combination,
    })),
  },
  rules: [
    { rule_id: 'R01', factor_id: 'a', name: 'A', expression: 'a', directions: ['BULLISH', 'BULLISH', 'BULLISH'], combination: 'ALL' },
    { rule_id: 'R02', factor_id: 'b', name: 'B', expression: 'b', directions: ['BULLISH', 'BEARISH', 'IGNORE'], combination: 'ANY' },
  ],
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

describe('intraday factor rule correlation', () => {
  beforeEach(() => {
    vi.clearAllMocks()
  })

  it('reruns two selected local rules on one bounded window and shows redundancy', async () => {
    vi.mocked(runIntradayFactorCorrelation).mockResolvedValueOnce(correlation)
    render(<IntradayFactorResearchLab dataset={dataset} />)

    const button = screen.getByRole('button', { name: '检验选中规则相关性（0/8）' })
    expect(button).toBeDisabled()
    const checkboxes = screen.getAllByRole('checkbox')
    fireEvent.click(checkboxes[1])
    fireEvent.click(checkboxes[2])
    fireEvent.click(screen.getByRole('button', { name: '检验选中规则相关性（2/8）' }))

    await waitFor(() => expect(runIntradayFactorCorrelation).toHaveBeenCalledWith({
      start: '2026-07-02',
      end: '2026-07-21',
      rules: [
        { directions: ['BULLISH', 'BULLISH', 'BULLISH'], combination: 'ALL' },
        { directions: ['BULLISH', 'BEARISH', 'IGNORE'], combination: 'ANY' },
      ],
    }))
    expect(await screen.findByText('发现 1 对高度相关规则')).toBeInTheDocument()
    expect(screen.getByText(/1,500 个同窗股票日观测/)).toBeInTheDocument()
    expect(screen.getByText('R01 · 三连阳 A')).toBeInTheDocument()
    expect(screen.getByText('R02 · 混合方向 B')).toBeInTheDocument()
    expect(screen.getByText('冗余')).toBeInTheDocument()
    expect(screen.getByText(/无状态写入、无外部订单/)).toBeInTheDocument()
  })
})
