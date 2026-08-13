import { render, screen } from '@testing-library/react'
import { waitFor } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import App from './App'

vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => ({
  ok: true,
  json: async () => String(input).includes('/paper-snapshot')
    ? { account: { cash: '100000', equity: null }, pnl: { realized: '0' }, positions: [], orders: [], trades: [], risk: { state: 'HALTED' } }
    : {},
})))
vi.mock('echarts-for-react', () => ({ default: () => <div data-testid="equity-chart" /> }))

describe('QTrade web shell', () => {
  it('renders Chinese navigation and makes the no-live boundary explicit', async () => {
    render(<App />)
    expect(screen.getAllByText('总览')).toHaveLength(2)
    expect(screen.getByText('策略研究')).toBeInTheDocument()
    expect(screen.getByText('实盘模式不可用')).toBeInTheDocument()
    expect(screen.queryByText('实盘交易')).not.toBeInTheDocument()
    await waitFor(() => expect(screen.getByText('PAPER 现金')).toBeInTheDocument())
    expect(screen.getByText(/不是当前 PAPER 权益/)).toBeInTheDocument()
  })
})
