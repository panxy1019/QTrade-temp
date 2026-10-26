import { render, screen } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { EtfTrendPanel } from './EtfTrendPanel'

const get = vi.hoisted(() => vi.fn())
vi.mock('../research/qlibWorkflow', () => ({ workflowGet: get }))
vi.mock('./JsonExportButton', () => ({ JsonExportButton: () => <button>导出ETF诊断</button> }))
beforeEach(() => { get.mockReset() })
function result() {
  return { as_of: '2026-10-26', state: 'CLOSE_DIAGNOSTIC_READY', required_symbols: 30, qualified_close_symbols: 30,
    sessions: ['2026-09-21', '2026-10-26'], scores: [{ instrument: '510300', score: .1 }],
    close_histories: { '510300': [{ date: '2026-09-21', close: '2' }, { date: '2026-10-26', close: '2.2' }], '159915': [{ date: '2026-09-21', close: '3' }, { date: '2026-10-26', close: '2' }] },
    excluded: [], limitations: ['DISTRIBUTIONS_AND_SPLITS_NOT_RECONCILED', 'NOT_COMPARABLE_TO_ADJUSTED_STOCK_MODEL_OR_HISTORICAL_RETURN_SERIES'], receipt_sha256: 'a'.repeat(64), bindings: { master_sha256: 'b'.repeat(64), code_revision: 'c'.repeat(40) } }
}

it('shows ETF coverage, positive scores and qualified nonpositive ETF without invented scores', async () => {
  get.mockResolvedValue({ publication_available: true, current_input: true, diagnostic: result() })
  render(<EtfTrendPanel />)
  expect(await screen.findByText('30 / 30')).toBeInTheDocument()
  expect(screen.getByText('10.000%')).toBeInTheDocument()
  expect(screen.getByText('159915')).toBeInTheDocument()
  expect(screen.getByText('无正趋势')).toBeInTheDocument()
  expect(screen.getByText(/不能与复权股票模型/)).toBeInTheDocument()
  expect(screen.getByText(/没有委托、成交或前向收益/)).toBeInTheDocument()
  expect(get).toHaveBeenCalledWith('/online/etf-trend')
})

it('keeps an expired ETF archive visible with current-input warning', async () => {
  get.mockResolvedValue({ publication_available: true, current_input: false, diagnostic: result() })
  render(<EtfTrendPanel />)
  expect(await screen.findByText('ETF档案已过期，仅保留历史观察')).toBeInTheDocument()
  expect(screen.getByText('510300')).toBeInTheDocument()
  expect(screen.queryByText('符合当前日期要求')).not.toBeInTheDocument()
})

it('does not use a diagnostic when publication is unavailable', async () => {
  get.mockResolvedValue({ publication_available: false, current_input: false, diagnostic: result() })
  render(<EtfTrendPanel />)
  await screen.findByText('尚无通过校验的ETF收盘价诊断')
  expect(screen.queryByText('510300')).not.toBeInTheDocument()
})

it('clears data and reports a request failure', async () => {
  get.mockRejectedValue(new Error('ETF offline'))
  render(<EtfTrendPanel />)
  await screen.findByText('ETF诊断读取失败')
  expect(screen.getByText('Error: ETF offline')).toBeInTheDocument()
  expect(screen.queryByText('510300')).not.toBeInTheDocument()
})
