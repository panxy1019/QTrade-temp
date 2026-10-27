import { render, screen } from '@testing-library/react'
import { beforeEach, expect, test, vi } from 'vitest'
import { loadCurrentDatasets, loadCurrentSeries } from '../api/client'
import { CurrentDataProvider, CurrentDataStatus, CurrentWorkspace } from './CurrentWorkspace'

vi.mock('../api/client', () => ({ loadCurrentDatasets: vi.fn(), loadCurrentSeries: vi.fn() }))
vi.mock('./PublicResearchObservations', () => ({ PublicResearchObservations: () => null }))
vi.mock('./QlibBaseline', () => ({ QlibBaseline: () => null }))
vi.mock('../charts/MarketHistoryChart', () => ({ MarketHistoryChart: ({ series }: { series: { data_version: string } | null }) => <div>{series?.data_version ?? '没有图表数据'}</div> }))
const load = vi.mocked(loadCurrentDatasets)
beforeEach(() => { vi.resetAllMocks() })

test('current workspace uses the current chart identity and latest date', async () => {
  const row = { data_version: 'current-october', checksum: 'a'.repeat(64), minimum_latest_day: '2026-10-23', expected_complete_day: '2026-10-23', freshness: 'FRESH', observed_start: '2022-09-06', observed_end: '2026-10-23', bar_count: 1000, universe: { symbols: ['510300'] }, instruments: [{ symbol: '510300', name: '沪深300' }], trading_days: ['2026-10-22', '2026-10-23'] }
  load.mockResolvedValue({ daily: row, minute: row } as never)
  vi.mocked(loadCurrentSeries).mockResolvedValue({ symbol: '510300', data_version: row.data_version, dataset_checksum: row.checksum, bars: [] } as never)
  render(<CurrentDataProvider><CurrentDataStatus /><CurrentWorkspace mode="market" platform={null} /></CurrentDataProvider>)
  expect(await screen.findByText('current-october')).toBeInTheDocument()
  expect(screen.getByText(/当前数据：日线 2026-10-23/)).toBeInTheDocument()
  expect(loadCurrentSeries).toHaveBeenCalledWith('daily', '510300', 'current-october', undefined)
})

test('a failed channel shows an error without an August fallback', async () => {
  load.mockRejectedValue(new Error('503'))
  render(<CurrentDataProvider><CurrentDataStatus /><CurrentWorkspace mode="market" platform={null} /></CurrentDataProvider>)
  expect(await screen.findByText('当前数据通道不可用：503')).toBeInTheDocument()
  expect(loadCurrentSeries).not.toHaveBeenCalled()
})

test('current backtest workspace retains minute research and only reads archived daily results', async () => {
  const row = { data_version: 'current-october', checksum: 'a'.repeat(64), minimum_latest_day: '2026-10-23', expected_complete_day: '2026-10-23', freshness: 'FRESH', observed_start: '2022-09-06', observed_end: '2026-10-23', bar_count: 1000, universe: { symbols: ['510300'] }, instruments: [{ symbol: '510300', name: '沪深300' }], trading_days: ['2026-10-22', '2026-10-23'] }
  load.mockResolvedValue({ daily: row, minute: row } as never)
  render(<CurrentDataProvider><CurrentWorkspace mode="backtest" platform={null} /></CurrentDataProvider>)
  expect(await screen.findByText('旧日线本地实验档案')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '运行当前五分钟回测' })).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '运行即时回测' })).not.toBeInTheDocument()
  expect(screen.queryByLabelText('基线策略模板')).not.toBeInTheDocument()
  expect(screen.queryByText('ExperimentRunSpec 哈希草案')).not.toBeInTheDocument()
})
