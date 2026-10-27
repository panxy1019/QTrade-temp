import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { UnifiedMarket } from './UnifiedMarket'
import { loadUnifiedBars, loadUnifiedSecurities, loadUnifiedSummary } from '../api/client'
import { unifiedBars, unifiedSecurities, unifiedSummary } from '../test/unifiedFixtures'

vi.mock('../api/client', () => ({ loadUnifiedBars: vi.fn(), loadUnifiedSecurities: vi.fn(), loadUnifiedSummary: vi.fn() }))
vi.mock('../charts/MarketHistoryChart', () => ({ MarketHistoryChart: ({ series }: { series: { bars: { close: string }[] } }) => <div data-testid="unified-day-chart">{series.bars[0]?.close}</div> }))
vi.mock('../charts/MinuteHistoryChart', () => ({ MinuteHistoryChart: () => <div data-testid="unified-minute-chart" /> }))

describe('unified target market', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    vi.mocked(loadUnifiedSummary).mockResolvedValue(unifiedSummary())
    vi.mocked(loadUnifiedSecurities).mockResolvedValue(unifiedSecurities())
    vi.mocked(loadUnifiedBars).mockImplementation(async query => unifiedBars(query.frequency, query.symbol))
  })
  it('loads current target securities and discloses independent source clocks and prices', async () => {
    render(<UnifiedMarket />)
    expect(await screen.findByTestId('unified-day-chart')).toHaveTextContent('11')
    expect(screen.getByText(/当前目标 1500 \/ 1500/)).toBeInTheDocument()
    expect(screen.getByText('整个数据集实际截止（上海）')).toBeInTheDocument()
    expect(screen.getByText('所选范围最后一根（上海）')).toBeInTheDocument()
    expect(screen.getByText('新浪原生价格 · 复权未认证')).toBeInTheDocument()
    expect(loadUnifiedSecurities).toHaveBeenCalledWith(expect.objectContaining({ target_only: true, coverage: 'incremental' }), expect.objectContaining({ signal: expect.any(AbortSignal) }))
    expect(loadUnifiedBars).toHaveBeenCalledWith({ symbol: '000063', frequency: 'day', limit: 2000 }, expect.any(Object))
    fireEvent.change(screen.getByLabelText('行情开始日期'), { target: { value: '2026-10-20' } })
    fireEvent.change(screen.getByLabelText('行情结束日期'), { target: { value: '2026-10-26' } })
    fireEvent.click(screen.getByText('应用日期范围'))
    await waitFor(() => expect(loadUnifiedBars).toHaveBeenLastCalledWith(expect.objectContaining({ start: '2026-10-20', end: '2026-10-26' }), expect.any(Object)))
    fireEvent.click(screen.getByText('完整发布窗口'))
    fireEvent.mouseDown(screen.getByLabelText('行情频率'))
    fireEvent.click(await screen.findByText('五分钟'))
    expect(await screen.findByTestId('unified-minute-chart')).toBeInTheDocument()
  })
  it('searches the entire target catalog on the server, preserving no-data without legacy fallback', async () => {
    render(<UnifiedMarket />)
    await screen.findByTestId('unified-day-chart')
    vi.mocked(loadUnifiedSecurities).mockResolvedValue({ ...unifiedSecurities(), rows: [], total: 0 })
    const input = screen.getByLabelText('搜索行情证券')
    fireEvent.change(input, { target: { value: '没有这只' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })
    expect(await screen.findByText('当前目标池没有匹配证券；不会回退至旧数据池')).toBeInTheDocument()
    expect(screen.queryByTestId('unified-day-chart')).not.toBeInTheDocument()
    expect(loadUnifiedSecurities).toHaveBeenLastCalledWith(expect.objectContaining({ query: '没有这只' }), expect.any(Object))
  })
  it('clears old success on version drift or failed refresh', async () => {
    render(<UnifiedMarket />)
    await screen.findByTestId('unified-day-chart')
    vi.mocked(loadUnifiedBars).mockResolvedValue({ ...unifiedBars(), universe_version: 'f'.repeat(64) })
    fireEvent.click(screen.getByText('刷新统一行情'))
    expect(await screen.findByText(/统一行情不可用：统一行情身份/)).toBeInTheDocument()
    expect(screen.queryByTestId('unified-day-chart')).not.toBeInTheDocument()
    vi.mocked(loadUnifiedSummary).mockRejectedValue(new Error('503'))
    fireEvent.click(screen.getByText('刷新统一行情'))
    expect(await screen.findByText('统一证券池不可用：503')).toBeInTheDocument()
    expect(screen.queryByTestId('unified-day-chart')).not.toBeInTheDocument()
  })
  it('retains valid stale bars only with a prominent historical warning', async () => {
    vi.mocked(loadUnifiedBars).mockResolvedValue({ ...unifiedBars(), published_at: new Date(Date.now() - 240_000).toISOString(), freshness: 'STALE_PUBLICATION' })
    render(<UnifiedMarket />)
    expect(await screen.findByTestId('unified-day-chart')).toBeInTheDocument()
    expect(screen.getByText('行情发布已过期或时钟无效；以下仅为已验证的历史记录')).toBeInTheDocument()
  })
  it('does not draw future bars even when a reply claims current freshness', async () => {
    const value = unifiedBars()
    const tomorrow = new Date(Date.now() + 86_400_000).toISOString()
    value.checked_at = tomorrow; value.expected_end = tomorrow; value.actual_end = tomorrow
    value.available_end = tomorrow; value.bars[0].timestamp = tomorrow
    vi.mocked(loadUnifiedBars).mockResolvedValue(value)
    render(<UnifiedMarket />)
    expect(await screen.findByText(/统一行情不可用：统一行情身份/)).toBeInTheDocument()
    expect(screen.queryByTestId('unified-day-chart')).not.toBeInTheDocument()
    expect(screen.queryByText('整个数据集：时间齐备')).not.toBeInTheDocument()
  })
  it('cancels obsolete requests and ignores their late results', async () => {
    let finish: ((value: ReturnType<typeof unifiedBars>) => void) | undefined
    let firstSignal: AbortSignal | undefined
    vi.mocked(loadUnifiedBars).mockImplementationOnce((_, options) => {
      firstSignal = options?.signal
      return new Promise(resolve => { finish = resolve })
    })
    render(<UnifiedMarket />)
    await waitFor(() => expect(loadUnifiedBars).toHaveBeenCalled())
    fireEvent.mouseDown(screen.getByLabelText('行情频率'))
    fireEvent.click(await screen.findByText('五分钟'))
    await screen.findByTestId('unified-minute-chart')
    expect(firstSignal?.aborted).toBe(true)
    await act(async () => finish?.(unifiedBars()))
    expect(screen.queryByTestId('unified-day-chart')).not.toBeInTheDocument()
  })
})
