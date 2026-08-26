import { render, screen } from '@testing-library/react'
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
    await waitFor(() => expect(screen.getByText('PAPER 现金')).toBeInTheDocument())
    expect(screen.getByText(/不是当前 PAPER 权益/)).toBeInTheDocument()
    expect(screen.getByText('27 个数据分区异常')).toBeInTheDocument()
    expect(screen.getByText(/已保留其余成功分区/)).toBeInTheDocument()
    const requested = vi.mocked(fetch).mock.calls.map(([input]) => String(input))
    expect(requested).toContain('/api/minute-research-snapshot')
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
    expect(screen.getByRole('button', { name: '刷新平台快照' })).toBeInTheDocument()
    matchMedia.mockRestore()
  })
})
