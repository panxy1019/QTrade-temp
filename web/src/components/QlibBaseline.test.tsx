import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { loadQlibBaseline } from '../api/client'
import { QlibBaseline } from './QlibBaseline'

vi.mock('../api/client', () => ({ loadQlibBaseline: vi.fn() }))
vi.mock('../charts/ChartRenderer', () => ({ default: () => <div>Qlib 净值图</div> }))
const load = vi.mocked(loadQlibBaseline)
beforeEach(() => { load.mockReset() })

it('shows actual preparation coverage without fabricated metrics', async () => {
  load.mockResolvedValue({ schema_version: 'qtrade.qlib-baseline.v1', state: 'PREPARING_DATA',
    live_trading_enabled: false, external_order_transmission: false,
    publication_status: 'CURRENT', published_at: '2026-10-25',
    progress: { completed: 345, target: 1000, failed: 4 }, run: null } as never)
  render(<QlibBaseline />)
  expect(await screen.findByText('正在适配数据')).toBeInTheDocument()
  expect(screen.getByText(/数据适配 345\/1000 股/)).toBeInTheDocument()
  expect(screen.queryByText('成本后累计收益')).not.toBeInTheDocument()
})

it('hides previous success when a refresh fails', async () => {
  load.mockResolvedValue({ schema_version: 'qtrade.qlib-baseline.v1', state: 'TRAINING_LIGHTGBM',
    live_trading_enabled: false, external_order_transmission: false,
    publication_status: 'CURRENT', published_at: '2026-10-25', progress: {}, run: null } as never)
  render(<QlibBaseline />)
  expect(await screen.findByText('正在训练 LightGBM')).toBeInTheDocument()
  load.mockRejectedValue(new Error('timeout'))
  fireEvent.click(screen.getByRole('button', { name: '刷新 Qlib 结果' }))
  expect(await screen.findByText('Qlib 发布不可用：timeout')).toBeInTheDocument()
  await waitFor(() => expect(screen.queryByText('正在训练 LightGBM')).not.toBeInTheDocument())
})

it('rejects a complete result without a persisted Alpha158 contract', async () => {
  load.mockResolvedValue({ schema_version: 'qtrade.qlib-baseline.v1', state: 'COMPLETE',
    live_trading_enabled: false, external_order_transmission: false, run: { persisted: false } } as never)
  render(<QlibBaseline />)
  expect(await screen.findByText('Qlib 发布不可用：Qlib 结果契约不匹配')).toBeInTheDocument()
})

it('renders persisted metrics and labels an older fixed result as historical', async () => {
  load.mockResolvedValue({ schema_version: 'qtrade.qlib-baseline.v1', state: 'COMPLETE',
    live_trading_enabled: false, external_order_transmission: false,
    publication_status: 'CURRENT', result_data_status: 'HISTORICAL', expected_complete_day: '2026-10-26',
    published_at: '2026-10-26', progress: { completed: 990, target: 1000, failed: 10 },
    artifact_manifest_sha256: 'a'.repeat(64), run: {
      persisted: true, feature_count: 158, run_id: 'baseline-v1', source_revision: 'b'.repeat(40),
      evidence_sha256: 'c'.repeat(64), symbols: 990, requested_symbols: 1000, data_rows: 2157805,
      data_end: '2026-10-23', segments: { train: ['2017-04-05', '2021-12-28'],
        valid: ['2022-01-04', '2023-12-26'], test: ['2024-01-02', '2026-10-23'] },
      signal_evaluation: { start: '2024-01-02', end: '2026-10-21' },
      cost_assumptions: { open_cost: 0.0008, close_cost: 0.0013, min_cost: 5 },
      metrics: { ic: 0.02, rank_ic: 0.03, total_return_net: -0.1,
        sharpe_net: -0.5, max_drawdown: -0.2, mean_daily_turnover: 0.15 }, curve: [],
    } } as never)
  render(<QlibBaseline />)
  expect(await screen.findByText('固定基线已归档')).toBeInTheDocument()
  expect(screen.getByText(/这是历史固定结果，尚未重跑到最新数据/)).toBeInTheDocument()
  expect(screen.getByText('-10.00%')).toBeInTheDocument()
  expect(screen.getByText('0.0200')).toBeInTheDocument()
  expect(screen.getByText('非 PIT · 存在幸存者偏差')).toBeInTheDocument()
})


