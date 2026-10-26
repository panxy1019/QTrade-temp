import { act, fireEvent, render, screen } from '@testing-library/react'
import { afterEach, beforeEach, expect, it, vi } from 'vitest'
import { EtfHistoryPanel } from './EtfHistoryPanel'
const get = vi.hoisted(() => vi.fn())
vi.mock('../research/qlibWorkflow', () => ({ workflowGet: get }))
vi.mock('../charts/ChartRenderer', () => ({ default: () => <div>ETF历史模拟图</div> }))
vi.mock('./JsonExportButton', () => ({ JsonExportButton: () => <button>导出完整模拟</button> }))
beforeEach(() => { get.mockReset() })
afterEach(() => { vi.useRealTimers() })
function publication() {
  return { report: { simulation_id: 'a'.repeat(64), generated_at: '2026-10-27', limitations: ['NO_PIT'],
    assumptions: { initial_cash: '1000000', lot_size: 100, transaction_cost_rate: '.001', slippage_rate: '.0005' },
    bindings: { cohort: ['510300'], warmup_sessions: ['2026-08-03'], evaluation_sessions: ['2026-09-01'], implementation_revision: 'b'.repeat(40) },
    strategies: { positive20_top3_daily: { total_return: '.02', maximum_drawdown: '-.01', number_of_trades: '3', total_assumed_fees: '40', terminal_net_asset_value: '1020000' } } },
    artifacts: { 'positive20_top3_daily/ledger.json': { records: [{ session: '2026-09-01', phase: 'EVALUATION', cash: '10000', net_asset_value: '1020000', drawdown: '-.01', assumed_fees: '40', assumed_slippage_cost: '20', turnover_previous_nav: '.3', holdings: [{ symbol: '510300', quantity: 100, market_value: '1010000' }] }] } } }
}
it('labels assumptions and displays simulation accounting separately from forward results', async () => {
  get.mockResolvedValue({ publication_available: true, comparison: publication() })
  render(<EtfHistoryPanel />)
  await screen.findByText('假设驱动的历史模拟，不构成 PAPER 准入')
  expect(screen.getByText('ETF历史模拟图')).toBeInTheDocument()
  expect(screen.getByText('导出完整模拟')).toBeInTheDocument()
  expect(screen.getByText('2.00%')).toBeInTheDocument()
  expect(screen.getByText(/最终收盘信号没有下一开盘数据时不成交/)).toBeInTheDocument()
  expect(get).toHaveBeenCalledWith('/online/etf-history', expect.objectContaining({ signal: expect.any(AbortSignal) }))
})
it('does not display supplied payload when publication fails validation', async () => {
  get.mockResolvedValue({ publication_available: false, comparison: publication() })
  render(<EtfHistoryPanel />)
  await screen.findByText('尚无通过校验的 ETF 历史组合模拟')
  expect(screen.queryByText('ETF历史模拟图')).not.toBeInTheDocument()
})
it('clears the prior report on failed refresh', async () => {
  get.mockResolvedValueOnce({ publication_available: true, comparison: publication() }).mockRejectedValueOnce(new Error('offline'))
  render(<EtfHistoryPanel />)
  await screen.findByText('ETF历史模拟图')
  fireEvent.click(screen.getByText('刷新 ETF 历史对照'))
  await screen.findByText('历史对照读取失败，请重试')
  expect(screen.queryByText('ETF历史模拟图')).not.toBeInTheDocument()
})
it('bounds a hanging fetch and ignores a late successful response', async () => {
  vi.useFakeTimers()
  let resolve: (value: unknown) => void = () => {}
  get.mockImplementation(() => new Promise(r => { resolve = r }))
  render(<EtfHistoryPanel />)
  await act(async () => { vi.advanceTimersByTime(15001) })
  expect(screen.getByText('历史对照读取超时，请重试')).toBeInTheDocument()
  await act(async () => { resolve({ publication_available: true, comparison: publication() }) })
  expect(screen.queryByText('ETF历史模拟图')).not.toBeInTheDocument()
})
