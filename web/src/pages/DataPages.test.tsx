import { fireEvent, render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { PlatformSnapshot } from '../types'
import { BacktestPage, DataCenterPage, MarketPage, ResearchPage } from './DataPages'

vi.mock('echarts-for-react', () => ({ default: () => <div data-testid="equity-chart" /> }))

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
  backtestAnalytics: {
    classification: 'exploratory_noncommercial_current_universe_not_pit',
    promotion: 'NOT_ELIGIBLE',
    leader: {
      experiment_id: 'momentum-vol-slow', spec_hash: 'a', result_hash: 'b', scenario_type: 'SENSITIVITY',
      metrics: { total_return: '0.0335', sharpe: '0.4412', maximum_drawdown: '-0.0909', test_exposure: '0.45', test_turnover: '2.01', test_transaction_cost: '0.003' },
    },
    split: { test_start: '2026-01-01', test_end: '2026-08-12' },
    curve: [
      { session: '2024-01-02', equity: '1000000', drawdown: '0' },
      { session: '2024-01-03', equity: '999000', drawdown: '-0.001' },
    ],
  },
} as unknown as PlatformSnapshot

describe('verified historical data pages', () => {
  it('shows verified history without claiming realtime readiness', () => {
    render(<MarketPage data={data} />)
    expect(screen.getByText('已验证历史数据：AKShare 1.18.84')).toBeInTheDocument()
    expect(screen.getByText(/18960 根日线/)).toBeInTheDocument()
    expect(screen.getByText(/实时 snapshot 仍不可用/)).toBeInTheDocument()
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
    expect(screen.getByText(/记录、资金曲线和回撤文件的哈希均已验证/)).toBeInTheDocument()
    expect(screen.getByText(/图为完整模拟期，测试窗指标在上方单列/)).toBeInTheDocument()
    expect(screen.getByText('45.00%')).toBeInTheDocument()
    expect(screen.getByText('2.010')).toBeInTheDocument()
    expect(screen.getByText('0.30%')).toBeInTheDocument()
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
    expect(screen.getByText(/结论：NOT_ELIGIBLE/)).toBeInTheDocument()
  })
})
