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


