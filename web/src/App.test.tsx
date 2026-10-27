import { fireEvent, render, screen } from '@testing-library/react'
import { waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App from './App'

vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
  const path = String(input)
  if (path.endsWith('/system')) return { ok: true, status: 200, json: async () => ({ mode: 'PAPER', running: false, risk_state: 'HALTED' }) }
  if (path.endsWith('/health')) return { ok: true, status: 200, json: async () => ({ healthy: false, runtime_mode: 'PAPER', risk_state: 'HALTED', gateway_connected: false, reconciliation_status: 'UNKNOWN' }) }
  if (path.endsWith('/paper-snapshot')) return { ok: true, status: 200, json: async () => ({ account: { cash: '100000', equity: null }, pnl: { realized: '0' }, positions: [], orders: [], trades: [], risk: { state: 'HALTED' } }) }
  return { ok: false, status: 503, json: async () => ({ detail: 'optional partition unavailable' }) }
}))
vi.mock('./charts/ChartRenderer', () => ({ default: () => <div data-testid="equity-chart" /> }))

describe('QTrade web shell', () => {
  it('renders Chinese navigation and makes the no-live boundary explicit', async () => {
    render(<App />)
    await waitFor(() => expect(screen.getAllByText('总览')).toHaveLength(2))
    expect(screen.getByText('策略研究')).toBeInTheDocument()
    expect(screen.getByText('实盘模式不可用')).toBeInTheDocument()
    expect(screen.queryByText('实盘交易')).not.toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('1 · 统一目标池与日常采集')).toBeInTheDocument())
    expect(screen.queryByText('PAPER 现金')).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: '已完成历史回测对照' })).toHaveAttribute('href', '#/backtest')
    expect(screen.getByRole('link', { name: '查看冻结目标与观察明细' })).toHaveAttribute('href', '#/paper')
    expect(screen.getByText(/LIVE_TRADING_ENABLED=false/)).toBeInTheDocument()
    const requested = vi.mocked(fetch).mock.calls.map(([input]) => String(input))
    expect(requested).toContain('/api/unified/summary')
    expect(requested).not.toContain('/api/minute-research-snapshot')
    expect(requested).not.toContain('/api/current/datasets')
    expect(requested).not.toContain('/api/long-five-minute-research')
    expect(requested).not.toContain('/api/intraday-breadth-observation')
  })

  it('redirects an unknown hash route back to the overview', async () => {
    window.location.hash = '#/missing-route'
    render(<App />)
    await waitFor(() => expect(screen.getByRole('heading', { name: '总览' })).toBeInTheDocument())
    expect(window.location.hash).toBe('#/')
  })

  it('starts with the navigation collapsed at the phone breakpoint', async () => {
    const matchMedia = vi.spyOn(window, 'matchMedia').mockImplementation((query: string) => ({
      matches: query === '(max-width: 720px)', media: query, onchange: null,
      addEventListener: vi.fn(), removeEventListener: vi.fn(), addListener: vi.fn(), removeListener: vi.fn(), dispatchEvent: vi.fn(),
    }))
    window.location.hash = '#/'
    render(<App />)
    await waitFor(() => expect(screen.getByRole('heading', { name: '总览' })).toBeInTheDocument())
    expect(screen.queryByText('QTrade')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: '刷新业务状态' })).toBeInTheDocument()
    matchMedia.mockRestore()
  })

  it('defaults market to the unified pool and loads old scopes only after explicit selection', async () => {
    vi.mocked(fetch).mockClear()
    window.location.hash = '#/market'
    render(<App />)
    await screen.findByRole('heading', { name: '行情中心' })
    expect(screen.getByRole('tab', { name: '统一池当前行情' })).toHaveAttribute('aria-selected', 'true')
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([path]) => String(path).includes('/unified/securities?'))).toBe(true))
    expect(vi.mocked(fetch).mock.calls.some(([path]) => String(path).includes('/current/datasets'))).toBe(false)
    fireEvent.click(screen.getByRole('tab', { name: '旧范围参考（30 ETF / 75 股）' }))
    expect(await screen.findByText('旧范围参考：仅 30 ETF 日线 / 75 股分钟，不代表统一目标池')).toBeInTheDocument()
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([path]) => String(path).includes('/current/datasets'))).toBe(true))
  })

  it('opens historical candidates without requesting or displaying retired strategy templates', async () => {
    vi.mocked(fetch).mockClear()
    window.location.hash = '#/archive'
    render(<App />)
    expect(await screen.findByRole('tab', { name: '历史实验候选' })).toHaveAttribute('aria-selected', 'true')
    expect(screen.queryByRole('tab', { name: '原策略目录' })).not.toBeInTheDocument()
    expect(screen.queryByText(/已实现基线策略模板/)).not.toBeInTheDocument()
    await waitFor(() => expect(vi.mocked(fetch).mock.calls.some(([path]) => String(path).endsWith('/strategies'))).toBe(true))
    expect(vi.mocked(fetch).mock.calls.some(([path]) => String(path).endsWith('/strategy-templates'))).toBe(false)
  })
})
