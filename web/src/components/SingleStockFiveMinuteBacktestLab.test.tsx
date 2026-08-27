import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { runSingleStockFiveMinuteBacktest } from '../api/client'
import type { FiveMinuteResearchReport, SingleStockFiveMinuteBacktest, SingleStockFiveMinuteBacktestRequest } from '../types'
import { SingleStockFiveMinuteBacktestLab } from './SingleStockFiveMinuteBacktestLab'

vi.mock('../api/client', () => ({ runSingleStockFiveMinuteBacktest: vi.fn() }))
vi.mock('../charts/EquityChart', () => ({ EquityChart: () => <div data-testid="equity-chart" /> }))

type Dataset = NonNullable<FiveMinuteResearchReport['minute_dataset']>

const dataset = {
  provider: 'BaoStock', provider_version: '0.9.3', data_version: 'signed-five-minute-v1',
  bar_interval: '5m', checksum: 'a'.repeat(64), evidence_sha256: 'b'.repeat(64),
  bar_count: 192, symbol_count: 4, etf_count: 0, stock_count: 4, sector_count: 3,
  session_count: 2, session_bar_count_per_symbol: 48,
  trading_days: ['2026-08-24', '2026-08-25'],
  observed_start: '2026-08-24T09:35:00+08:00', observed_end: '2026-08-25T15:00:00+08:00',
  eligible_symbols: ['600002', '600003', '600001', '600004'],
  instruments: [
    { symbol: '600002', instrument_type: 'STOCK', name: '乙二', sector_code: '801020', sector_name: '乙板块' },
    { symbol: '600003', instrument_type: 'STOCK', name: '甲一', sector_code: '801010', sector_name: '甲板块' },
    { symbol: '600001', instrument_type: 'STOCK', name: '乙一', sector_code: '801020', sector_name: '乙板块' },
    { symbol: '600004', instrument_type: 'STOCK', name: '丙一', sector_code: '801030', sector_name: '丙板块' },
  ],
  point_in_time: false, future_adjustment_knowledge_possible: true,
  realtime_readiness: false, real_data_paper_readiness: false, external_order_transmission: false,
} satisfies Dataset

function resultFor(
  payload: SingleStockFiveMinuteBacktestRequest,
  metrics: { totalReturn?: string; excessReturn?: string; maximumDrawdown?: string } = {},
): SingleStockFiveMinuteBacktest {
  const instrument = dataset.instruments.find((row) => row.symbol === payload.symbol)
  if (!instrument?.sector_code || !instrument.sector_name) throw new Error('missing fixture instrument')
  return {
    symbol: payload.symbol,
    instrument: { name: instrument.name, sector_code: instrument.sector_code, sector_name: instrument.sector_name },
    preview_hash: payload.symbol.repeat(11).slice(0, 64),
    parameters: payload,
    metrics: {
      overlay: {
        total_return: metrics.totalReturn ?? '0.01', annualized_return: '0.10', sharpe: '1.0',
        maximum_drawdown: metrics.maximumDrawdown ?? '-0.02',
      },
      excess_return: metrics.excessReturn ?? '0.001', information_ratio: '0.2',
    },
    execution: { mean_exposure: '0.90', turnover: '1.5' },
  } as unknown as SingleStockFiveMinuteBacktest
}

function requestFor(symbol: string): SingleStockFiveMinuteBacktestRequest {
  return {
    symbol, start: '2026-08-24', end: '2026-08-25', cost_bps: '5',
    short_window: 12, long_window: 48, volatility_window: 24,
    maximum_minute_volatility: '0.02', maximum_intraday_drawdown: '0.04',
    reduction_multiplier: '0.98', cooldown_bars: 6,
  }
}

describe('single-stock cross-sector batch diagnostic', () => {
  beforeEach(() => vi.mocked(runSingleStockFiveMinuteBacktest).mockReset())
  afterEach(cleanup)

  it('runs one deterministic representative per sector with identical parameters', async () => {
    vi.mocked(runSingleStockFiveMinuteBacktest)
      .mockResolvedValueOnce(resultFor(requestFor('600003'), { totalReturn: '0.03', excessReturn: '0.01', maximumDrawdown: '-0.04' }))
      .mockResolvedValueOnce(resultFor(requestFor('600001'), { totalReturn: '-0.01', excessReturn: '-0.02', maximumDrawdown: '-0.08' }))
      .mockResolvedValueOnce(resultFor(requestFor('600004'), { totalReturn: '0.02', excessReturn: '0.005', maximumDrawdown: '-0.03' }))
    render(<SingleStockFiveMinuteBacktestLab dataset={dataset} />)

    fireEvent.click(screen.getByRole('button', { name: '运行 3 板块同参诊断' }))

    expect(await screen.findByText('同参批量完成：3/3 个板块')).toBeInTheDocument()
    expect(vi.mocked(runSingleStockFiveMinuteBacktest).mock.calls.map(([payload]) => payload.symbol)).toEqual([
      '600003', '600001', '600004',
    ])
    const payloads = vi.mocked(runSingleStockFiveMinuteBacktest).mock.calls.map(([payload]) => ({ ...payload, symbol: 'SAME' }))
    expect(new Set(payloads.map((payload) => JSON.stringify(payload))).size).toBe(1)
    expect(screen.getByText('本次会话个股回测对比（3/15，覆盖 3 个板块）')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /导出本次会话对比/ })).toBeInTheDocument()
    const summary = screen.getByTestId('sector-batch-summary')
    expect(within(summary).getByText('同参批量横截面摘要（仅成功代表股）')).toBeInTheDocument()
    expect(within(summary).getAllByText('2/3')).toHaveLength(2)
    expect(within(summary).getByText('2.00%')).toBeInTheDocument()
    expect(within(summary).getByText('600001 · -1.00%')).toBeInTheDocument()
    expect(within(summary).getByText('600001 · -8.00%')).toBeInTheDocument()
  })

  it('reports partial coverage when one sector request fails', async () => {
    vi.mocked(runSingleStockFiveMinuteBacktest)
      .mockResolvedValueOnce(resultFor(requestFor('600003')))
      .mockRejectedValueOnce(new Error('provider timeout'))
      .mockResolvedValueOnce(resultFor(requestFor('600004')))
    render(<SingleStockFiveMinuteBacktestLab dataset={dataset} />)

    fireEvent.click(screen.getByRole('button', { name: '运行 3 板块同参诊断' }))

    expect(await screen.findByText('同参批量部分完成：2/3 个板块')).toBeInTheDocument()
    expect(screen.getByText(/1 个板块请求失败，失败结果未补造/)).toBeInTheDocument()
    expect(screen.getByText('本次会话个股回测对比（2/15，覆盖 2 个板块）')).toBeInTheDocument()
    expect(within(screen.getByTestId('sector-batch-summary')).getByText('指标有效代表股')).toBeInTheDocument()
    expect(within(screen.getByTestId('sector-batch-summary')).getAllByText('2/2')).toHaveLength(3)
    expect(within(screen.getByTestId('sector-batch-summary')).getByText(/只按 2 个成功代表股/)).toBeInTheDocument()
    await waitFor(() => expect(runSingleStockFiveMinuteBacktest).toHaveBeenCalledTimes(3))
  })

  it('excludes a successful response with non-finite metrics from the summary denominator', async () => {
    vi.mocked(runSingleStockFiveMinuteBacktest)
      .mockResolvedValueOnce(resultFor(requestFor('600003')))
      .mockResolvedValueOnce(resultFor(requestFor('600001'), { totalReturn: 'NaN' }))
      .mockRejectedValueOnce(new Error('provider timeout'))
    render(<SingleStockFiveMinuteBacktestLab dataset={dataset} />)

    fireEvent.click(screen.getByRole('button', { name: '运行 3 板块同参诊断' }))

    expect(await screen.findByText('同参批量部分完成：2/3 个板块')).toBeInTheDocument()
    const summary = screen.getByTestId('sector-batch-summary')
    expect(within(summary).getByText('1/2')).toBeInTheDocument()
    expect(within(summary).getAllByText('1/1')).toHaveLength(2)
    expect(within(summary).getByText(/失败请求和同板块其余股票均未进入分母/)).toBeInTheDocument()
  })

  it('does not create an aggregate summary when every representative request fails', async () => {
    vi.mocked(runSingleStockFiveMinuteBacktest)
      .mockRejectedValueOnce(new Error('provider timeout'))
      .mockRejectedValueOnce(new Error('provider timeout'))
      .mockRejectedValueOnce(new Error('provider timeout'))
    render(<SingleStockFiveMinuteBacktestLab dataset={dataset} />)

    fireEvent.click(screen.getByRole('button', { name: '运行 3 板块同参诊断' }))

    expect(await screen.findByText('同参批量部分完成：0/3 个板块')).toBeInTheDocument()
    expect(screen.queryByTestId('sector-batch-summary')).not.toBeInTheDocument()
    expect(screen.queryByText(/0\/0/)).not.toBeInTheDocument()
    await waitFor(() => expect(runSingleStockFiveMinuteBacktest).toHaveBeenCalledTimes(3))
  })
})
