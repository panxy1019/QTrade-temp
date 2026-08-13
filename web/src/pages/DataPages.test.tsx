import { cleanup, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { PlatformSnapshot } from '../types'
import { BacktestPage, DataCenterPage, MarketPage, ResearchPage } from './DataPages'

vi.mock('echarts-for-react', () => ({ default: () => <div data-testid="equity-chart" /> }))
vi.mock('../api/client', () => ({
  loadHistoricalBarSeries: vi.fn(async (symbol: string) => ({
    symbol, data_version: 'akshare-bars-v1', dataset_checksum: 'abc', observation_type: 'HISTORICAL_DAILY_BAR_SERIES',
    total_count: 2, truncated: false,
    bars: [
      { symbol, exchange: 'SSE', timestamp: '2026-08-11T15:00:00+08:00', trading_day: '2026-08-11', open: '4', high: '4.2', low: '3.9', close: '4.1', volume: '100', data_version: 'akshare-bars-v1', observation_type: 'HISTORICAL_DAILY_BAR' },
      { symbol, exchange: 'SSE', timestamp: '2026-08-12T15:00:00+08:00', trading_day: '2026-08-12', open: '4.1', high: '4.3', low: '4', close: '4.2', volume: '200', data_version: 'akshare-bars-v1', observation_type: 'HISTORICAL_DAILY_BAR' },
    ],
  })),
}))

const data = {
  market: {
    provider_connected: false,
    execution_gateway_connected: true,
    mode: 'PAPER',
    snapshot_readiness: false,
    historical: {
      provider: 'AKShare',
      provider_version: '1.18.84',
      classification: 'noncommercial_research_historical_only',
      data_version: 'akshare-bars-v1',
      quality_status: 'PASSED',
      checksum: 'abc',
      bar_count: 18960,
      observed_start: '2024-01-02T15:00:00+08:00',
      observed_end: '2026-08-12T15:00:00+08:00',
      snapshot_readiness: false,
      real_data_paper_readiness: false,
      universe: { point_in_time: false, symbols: ['510300'] },
    },
  },
  reports: [],
  historicalBars: [
    {
      symbol: '510300', exchange: 'SSE', timestamp: '2026-08-12T07:00:00+00:00',
      trading_day: '2026-08-12', open: '4.1', high: '4.3', low: '4.0', close: '4.2',
      volume: '200000000', data_version: 'akshare-bars-v1',
      observation_type: 'HISTORICAL_DAILY_CLOSE',
    },
  ],
  robustness: {
    classification: 'post_rejection_diagnostic_reuses_observations_not_new_heldout',
    promotion: 'NOT_ELIGIBLE',
    promotion_reasons: ['NEGATIVE_HELD_OUT_WALK_FORWARD'],
    result: {
      worst_sharpe: '-1.1429',
      dispersion: '1.6704',
      any_negative_sharpe: true,
      scenario_metrics: [['base', [['sharpe', '-0.0964']]]],
    },
  },
  walkForward: {
    classification: 'held_out_walk_forward',
    promotion: 'NOT_ELIGIBLE',
    promotion_reasons: ['NEGATIVE_HELD_OUT_WALK_FORWARD'],
    selection_contract: {
      candidate_selection: 'validation_sharpe_only',
      candidate_test_access_before_selection: false,
      fold_ranges_disjoint: true,
      execution_timing: 'close_decision_next_observed_open',
    },
    result: {
      fold_pass_ratio: '0',
      aggregate_metrics: { total_return: '-0.2044', sharpe: '-1.1005', maximum_drawdown: '-0.2862' },
      folds: [
        { fold: 1, selected_candidate_id: 'buy-hold', validation_sharpe: '2.78', split: { train_start: '2024-01-02', train_end: '2024-06-28', validation_start: '2024-07-01', validation_end: '2024-09-30', test_start: '2024-10-01', test_end: '2024-12-31' }, test_metrics: { total_return: '-0.0210', sharpe: '-0.1518', maximum_drawdown: '-0.1152', number_of_trades: 0 } },
        { fold: 2, selected_candidate_id: 'buy-hold', validation_sharpe: '3.47', split: { train_start: '2025-01-01', train_end: '2025-06-30', validation_start: '2025-07-01', validation_end: '2025-09-30', test_start: '2025-10-01', test_end: '2025-12-31' }, test_metrics: { total_return: '-0.0110', sharpe: '-0.2406', maximum_drawdown: '-0.0692', number_of_trades: 0 } },
        { fold: 3, selected_candidate_id: 'momentum-trend', validation_sharpe: '1.80', split: { train_start: '2026-01-01', train_end: '2026-03-31', validation_start: '2026-04-01', validation_end: '2026-05-29', test_start: '2026-06-01', test_end: '2026-08-12' }, test_metrics: { total_return: '-0.1783', sharpe: '-2.6533', maximum_drawdown: '-0.2173', number_of_trades: 14 } },
      ],
    },
  },
  backtestAnalytics: {
    classification: 'exploratory_noncommercial_current_universe_not_pit',
    promotion: 'NOT_ELIGIBLE',
    leader: {
      experiment_id: 'momentum-vol-slow', spec_hash: 'a', result_hash: 'b', scenario_type: 'SENSITIVITY',
      metrics: { total_return: '0.0335', sharpe: '0.4412', maximum_drawdown: '-0.0909', test_exposure: '0.45', test_turnover: '2.01', test_transaction_cost: '0.003' },
    },
    benchmark: {
      experiment_id: 'buy-hold', spec_hash: 'c', result_hash: 'd', scenario_type: 'PRIMARY',
      metrics: { total_return: '-0.0289', sharpe: '-0.1559', maximum_drawdown: '-0.1499' },
    },
    split: { test_start: '2026-01-01', test_end: '2026-08-12' },
    curve: [
      { session: '2024-01-02', equity: '1000000', drawdown: '0', benchmark_equity: '1000000', benchmark_drawdown: '0', excess_return: '0' },
      { session: '2024-01-03', equity: '999000', drawdown: '-0.001', benchmark_equity: '998000', benchmark_drawdown: '-0.002', excess_return: '0.001' },
    ],
  },
} as unknown as PlatformSnapshot

describe('verified historical data pages', () => {
  it('shows verified history without claiming realtime readiness', async () => {
    render(<MarketPage data={data} />)
    expect(screen.getByText('已验证历史数据：AKShare 1.18.84')).toBeInTheDocument()
    expect(screen.getByText(/18960 根日线/)).toBeInTheDocument()
    expect(screen.getByText(/没有 Bid\/Ask、实时价格或实时延迟/)).toBeInTheDocument()
    expect(screen.getByText('以下是历史日线，不是实时行情')).toBeInTheDocument()
    expect(screen.getByText('510300')).toBeInTheDocument()
    expect(screen.getByText('200,000,000')).toBeInTheDocument()
    expect(screen.queryByText('Bid')).not.toBeInTheDocument()
    expect(await screen.findByText(/2\/2 个完整交易日/)).toBeInTheDocument()
    expect(screen.getByText(/图表来自同一 checksum 绑定的历史 Parquet/)).toBeInTheDocument()
  })

  it('filters verified historical rows without inventing snapshots', () => {
    cleanup()
    render(<MarketPage data={data} />)
    fireEvent.change(screen.getByPlaceholderText('搜索 ETF 代码'), { target: { value: '510500' } })
    expect(screen.queryByText('510300')).not.toBeInTheDocument()
    expect(screen.getByText(/暂无校验通过的历史日线/)).toBeInTheDocument()
  })

  it('removes the chart immediately when verified evidence disappears', async () => {
    cleanup()
    const view = render(<MarketPage data={data} />)
    expect(await screen.findByText(/2\/2 个完整交易日/)).toBeInTheDocument()
    view.rerender(<MarketPage data={{ ...data, market: { ...data.market, historical: null }, historicalBars: [] }} />)
    await waitFor(() => expect(screen.queryByText(/2\/2 个完整交易日/)).not.toBeInTheDocument())
    expect(screen.getByText('暂无校验通过的历史 K 线')).toBeInTheDocument()
  })

  it('keeps provider connectivity separate from execution gateway health', () => {
    render(<DataCenterPage data={data} />)
    expect(screen.getByText('DISCONNECTED')).toBeInTheDocument()
    expect(screen.queryByText('CONNECTED')).not.toBeInTheDocument()
    expect(screen.getByText('PASSED')).toBeInTheDocument()
    expect(screen.getAllByText('NO')).toHaveLength(2)
  })
})

describe('verified backtest analytics', () => {
  it('shows the verified leader without presenting it as Champion', async () => {
    render(<BacktestPage data={data} />)
    expect(screen.getByText('探索性领先者，不是 Champion')).toBeInTheDocument()
    expect(screen.getByText(/Buy-and-Hold 仅在记录、资金曲线、回撤 checksum 全部通过/)).toBeInTheDocument()
    expect(screen.getByText(/测试窗指标在上方单列/)).toBeInTheDocument()
    expect(screen.getByText('45.00%')).toBeInTheDocument()
    expect(screen.getByText('2.010')).toBeInTheDocument()
    expect(screen.getByText('0.30%')).toBeInTheDocument()
    expect(screen.getByText('-2.89%')).toBeInTheDocument()
    expect(screen.getByText('三折独立样本外：0/3 折正收益')).toBeInTheDocument()
    expect(screen.getByText(/聚合收益 -20.44%/)).toBeInTheDocument()
    expect(screen.getByText(/不得因探索性领先者的正收益覆盖样本外败绩/)).toBeInTheDocument()
    expect(screen.getByText('momentum-trend')).toBeInTheDocument()
    expect(await screen.findByTestId('equity-chart')).toBeInTheDocument()
  })

  it('does not present missing walk-forward evidence as three failed folds', async () => {
    cleanup()
    const withoutWalkForward = {
      ...data,
      walkForward: { promotion: 'NOT_EVALUATED', promotion_reasons: [], result: { folds: [] } },
    } as unknown as PlatformSnapshot
    render(<BacktestPage data={withoutWalkForward} />)
    expect(screen.getByText('暂无已验证滚动样本外证据')).toBeInTheDocument()
    expect(screen.getByText(/不能据此声称策略通过或失败/)).toBeInTheDocument()
    expect(screen.queryByText(/0\/3 折正收益/)).not.toBeInTheDocument()
    expect(await screen.findByTestId('equity-chart')).toBeInTheDocument()
  })
})

describe('verified robustness evidence', () => {
  it('labels reused observations as diagnostics rather than independent evidence', () => {
    render(<ResearchPage data={data} />)
    fireEvent.click(screen.getByRole('tab', { name: '稳健性诊断' }))
    expect(screen.getByText(/复用样本的拒绝后诊断/)).toBeInTheDocument()
    expect(screen.getByText(/不是新的独立测试证据/)).toBeInTheDocument()
    expect(screen.getByText(/-1.1429/)).toBeInTheDocument()
    expect(screen.getAllByText(/结论：NOT_ELIGIBLE/).length).toBeGreaterThan(0)
  })
})
