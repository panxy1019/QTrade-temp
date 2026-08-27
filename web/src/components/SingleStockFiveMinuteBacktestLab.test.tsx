import { cleanup, fireEvent, render, screen, waitFor, within } from '@testing-library/react'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { runSingleStockFiveMinuteBacktest } from '../api/client'
import type { FiveMinuteResearchReport, SingleStockFiveMinuteBacktest, SingleStockFiveMinuteBacktestRequest } from '../types'
import { SingleStockFiveMinuteBacktestLab } from './SingleStockFiveMinuteBacktestLab'

vi.mock('../api/client', () => ({ runSingleStockFiveMinuteBacktest: vi.fn() }))
vi.mock('../charts/EquityChart', () => ({ EquityChart: () => <div data-testid="equity-chart" /> }))
vi.mock('./JsonExportButton', () => ({
  JsonExportButton: ({ value, label = '导出 JSON' }: { value: unknown; label?: string }) => (
    <button data-export={JSON.stringify(value)}>{label}</button>
  ),
}))

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
  metrics: {
    totalReturn?: string
    annualizedReturn?: string
    sharpe?: string
    excessReturn?: string
    informationRatio?: string
    maximumDrawdown?: string
  } = {},
): SingleStockFiveMinuteBacktest {
  const instrument = dataset.instruments.find((row) => row.symbol === payload.symbol)
  if (!instrument?.sector_code || !instrument.sector_name) throw new Error('missing fixture instrument')
  return {
    classification: 'AD_HOC_SINGLE_STOCK_FIVE_MINUTE_BACKTEST_CURRENT_NON_PIT',
    promotion: 'NOT_ELIGIBLE',
    symbol: payload.symbol,
    instrument: { name: instrument.name, sector_code: instrument.sector_code, sector_name: instrument.sector_name },
    data_version: dataset.data_version,
    dataset_checksum: dataset.checksum,
    preview_hash: payload.symbol.repeat(11).slice(0, 64),
    parameters: payload,
    metrics: {
      overlay: {
        total_return: metrics.totalReturn ?? '0.01', annualized_return: metrics.annualizedReturn ?? '0.10', sharpe: metrics.sharpe ?? '1.0',
        maximum_drawdown: metrics.maximumDrawdown ?? '-0.02',
      },
      excess_return: metrics.excessReturn ?? '0.001', information_ratio: metrics.informationRatio ?? '0.2',
    },
    execution: { mean_exposure: '0.90', turnover: '1.5' },
    realtime_readiness: false,
    real_data_paper_readiness: false,
    state_mutation: false,
    external_order_transmission: false,
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
      .mockResolvedValueOnce(resultFor(requestFor('600003'), { totalReturn: '0.03', annualizedReturn: '0.30', sharpe: '1.5', excessReturn: '0.01', informationRatio: '0.4', maximumDrawdown: '-0.04' }))
      .mockResolvedValueOnce(resultFor(requestFor('600001'), { totalReturn: '-0.01', annualizedReturn: '-0.10', sharpe: '-0.5', excessReturn: '-0.02', informationRatio: '-0.3', maximumDrawdown: '-0.08' }))
      .mockResolvedValueOnce(resultFor(requestFor('600004'), { totalReturn: '0.02', annualizedReturn: '0.20', sharpe: '0.8', excessReturn: '0.005', informationRatio: '0.1', maximumDrawdown: '-0.03' }))
    render(<SingleStockFiveMinuteBacktestLab dataset={dataset} />)

    fireEvent.click(screen.getByRole('button', { name: '运行 3 股 / 3 板块同参诊断' }))

    expect(await screen.findByText('同参批量完成：3/3 只代表股，覆盖 3/3 个板块')).toBeInTheDocument()
    expect(vi.mocked(runSingleStockFiveMinuteBacktest).mock.calls.map(([payload]) => payload.symbol)).toEqual([
      '600003', '600001', '600004',
    ])
    const payloads = vi.mocked(runSingleStockFiveMinuteBacktest).mock.calls.map(([payload]) => ({ ...payload, symbol: 'SAME' }))
    expect(new Set(payloads.map((payload) => JSON.stringify(payload))).size).toBe(1)
    expect(screen.getByText('本次会话个股回测对比（3/30，覆盖 3 个板块）')).toBeInTheDocument()
    expect(screen.getByRole('button', { name: /导出本次会话对比/ })).toBeInTheDocument()
    expect(within(screen.getByTestId('sector-batch-attempts')).getAllByText('已接纳')).toHaveLength(3)
    const summary = screen.getByTestId('sector-batch-summary')
    expect(within(summary).getByText('同参批量横截面摘要（仅成功代表股）')).toBeInTheDocument()
    expect(within(summary).getAllByText('2/3')).toHaveLength(2)
    expect(within(summary).getByText('2.00%')).toBeInTheDocument()
    expect(within(summary).getByText('年化收益中位数')).toBeInTheDocument()
    expect(within(summary).getByText('20.00%')).toBeInTheDocument()
    expect(within(summary).getByText('Sharpe 中位数')).toBeInTheDocument()
    expect(within(summary).getByText('0.800')).toBeInTheDocument()
    expect(within(summary).getByText('最大回撤中位数')).toBeInTheDocument()
    expect(within(summary).getByText('-4.00%')).toBeInTheDocument()
    expect(within(summary).getByText('主动超额中位数')).toBeInTheDocument()
    expect(within(summary).getByText('0.50%')).toBeInTheDocument()
    expect(within(summary).getByText('信息比率中位数')).toBeInTheDocument()
    expect(within(summary).getByText('0.100')).toBeInTheDocument()
    expect(within(summary).getByText('600001 · -1.00%')).toBeInTheDocument()
    expect(within(summary).getByText('600001 · -8.00%')).toBeInTheDocument()

    const exported = JSON.parse(screen.getByRole('button', { name: '导出本次会话对比' }).dataset.export ?? '')
    expect(exported.summary).toMatchObject({
      median_total_return: 0.02,
      median_annualized_return: 0.2,
      median_sharpe: 0.8,
      median_maximum_drawdown: -0.04,
      median_excess_return: 0.005,
      median_information_ratio: 0.1,
    })
  })

  it('bounds the signed 15-sector universe at 15 or 30 deterministic stock requests', () => {
    const instruments = Array.from({ length: 15 }, (_, sectorIndex) => (
      Array.from({ length: 5 }, (_, stockIndex) => ({
        symbol: String(600000 + sectorIndex * 5 + stockIndex),
        instrument_type: 'STOCK' as const,
        name: `股票${sectorIndex + 1}-${stockIndex + 1}`,
        sector_code: String(801000 + sectorIndex),
        sector_name: `板块${sectorIndex + 1}`,
      }))
    )).flat()
    const broadDataset = {
      ...dataset,
      bar_count: 75 * 2 * 48,
      symbol_count: 75,
      stock_count: 75,
      sector_count: 15,
      eligible_symbols: instruments.map((row) => row.symbol),
      instruments,
    } satisfies Dataset
    render(<SingleStockFiveMinuteBacktestLab dataset={broadDataset} />)

    expect(screen.getByRole('button', { name: '运行 15 股 / 15 板块同参诊断' })).toBeInTheDocument()
    fireEvent.click(screen.getByRole('button', { name: '每板块 2 股' }))
    expect(screen.getByRole('button', { name: '运行 30 股 / 15 板块同参诊断' })).toBeInTheDocument()
  })

  it('can run two deterministic representatives per sector without changing shared parameters', async () => {
    vi.mocked(runSingleStockFiveMinuteBacktest)
      .mockResolvedValueOnce(resultFor(requestFor('600003')))
      .mockResolvedValueOnce(resultFor(requestFor('600001')))
      .mockResolvedValueOnce(resultFor(requestFor('600002')))
      .mockResolvedValueOnce(resultFor(requestFor('600004')))
    render(<SingleStockFiveMinuteBacktestLab dataset={dataset} />)

    fireEvent.click(screen.getByRole('button', { name: '每板块 2 股' }))
    fireEvent.click(screen.getByRole('button', { name: '运行 4 股 / 3 板块同参诊断' }))

    expect(await screen.findByText('同参批量完成：4/4 只代表股，覆盖 3/3 个板块')).toBeInTheDocument()
    expect(vi.mocked(runSingleStockFiveMinuteBacktest).mock.calls.map(([payload]) => payload.symbol)).toEqual([
      '600003', '600001', '600002', '600004',
    ])
    const payloads = vi.mocked(runSingleStockFiveMinuteBacktest).mock.calls.map(([payload]) => ({ ...payload, symbol: 'SAME' }))
    expect(new Set(payloads.map((payload) => JSON.stringify(payload))).size).toBe(1)
    const exported = JSON.parse(screen.getByRole('button', { name: '导出本次会话对比' }).dataset.export ?? '')
    expect(exported.representative_selection.requested_per_sector).toBe(2)
    expect(exported.status).toMatchObject({ requested: 4, completed: 4, requested_sectors: 3, completed_sectors: 3 })
    expect(exported.attempts.map(({ symbol }: { symbol: string }) => symbol)).toEqual(['600003', '600001', '600002', '600004'])
    fireEvent.click(screen.getByRole('button', { name: '每板块 1 股' }))
    expect(screen.queryByTestId('sector-batch-attempts')).not.toBeInTheDocument()
    expect(screen.queryByText('同参批量完成：4/4 只代表股，覆盖 3/3 个板块')).not.toBeInTheDocument()
  })

  it('measures within-sector agreement only from a complete finite two-stock pair', async () => {
    vi.mocked(runSingleStockFiveMinuteBacktest)
      .mockResolvedValueOnce(resultFor(requestFor('600003'), { totalReturn: '0.05', excessReturn: '0.02' }))
      .mockResolvedValueOnce(resultFor(requestFor('600001'), { totalReturn: '0.02', annualizedReturn: '0.12', sharpe: '1.1', excessReturn: '0.01', informationRatio: '0.4', maximumDrawdown: '-0.03' }))
      .mockResolvedValueOnce(resultFor(requestFor('600002'), { totalReturn: '-0.01', annualizedReturn: '-0.08', sharpe: '-0.5', excessReturn: '0.005', informationRatio: '0.1', maximumDrawdown: '-0.06' }))
      .mockResolvedValueOnce(resultFor(requestFor('600004'), { totalReturn: '0.03', excessReturn: '0.01' }))
    render(<SingleStockFiveMinuteBacktestLab dataset={dataset} />)

    fireEvent.click(screen.getByRole('button', { name: '每板块 2 股' }))
    fireEvent.click(screen.getByRole('button', { name: '运行 4 股 / 3 板块同参诊断' }))

    expect(await screen.findByText('同参批量完成：4/4 只代表股，覆盖 3/3 个板块')).toBeInTheDocument()
    const consistency = screen.getByTestId('sector-pair-consistency')
    expect(within(consistency).getByText('完整双股板块')).toBeInTheDocument()
    expect(within(consistency).getByText('1/1')).toBeInTheDocument()
    expect(within(consistency).getByText('0.00%')).toBeInTheDocument()
    expect(within(consistency).getByText('100.00%')).toBeInTheDocument()
    expect(within(consistency).getAllByText('3.00%')).toHaveLength(2)
    expect(within(consistency).getByText(/只含同时接纳两只且关键指标有限的板块/)).toBeInTheDocument()
    const details = screen.getByTestId('sector-pair-details')
    expect(within(details).getByText('完整双股板块明细（1）')).toBeInTheDocument()
    expect(within(details).getByText('801020 乙板块')).toBeInTheDocument()
    expect(within(details).getByText('600001 / 600002')).toBeInTheDocument()
    expect(within(details).getByText('2.00% / -1.00%')).toBeInTheDocument()
    expect(within(details).getByText('12.00% / -8.00%')).toBeInTheDocument()
    expect(within(details).getByText('1.100 / -0.500')).toBeInTheDocument()
    expect(within(details).getByText('-3.00% / -6.00%')).toBeInTheDocument()
    expect(within(details).getByText('1.00% / 0.50%')).toBeInTheDocument()
    expect(within(details).getByText('0.400 / 0.100')).toBeInTheDocument()
    expect(within(details).getByText('收益不一致 · 超额一致')).toBeInTheDocument()
    expect(within(details).getByText('3.00%')).toBeInTheDocument()
    const exported = JSON.parse(screen.getByRole('button', { name: '导出本次会话对比' }).dataset.export ?? '')
    expect(exported.summary.within_sector_consistency).toEqual({
      requested_pair_sector_count: 1,
      complete_pair_sector_count: 1,
      return_direction_agreement_count: 0,
      excess_direction_agreement_count: 1,
      median_absolute_return_spread: 0.03,
      worst_absolute_return_spread: {
        sector_code: '801020',
        sector_name: '乙板块',
        symbols: ['600001', '600002'],
        absolute_return_spread: 0.03,
      },
      pairs: [{
        sector_code: '801020',
        sector_name: '乙板块',
        symbols: ['600001', '600002'],
        total_returns: [0.02, -0.01],
        annualized_returns: [0.12, -0.08],
        sharpes: [1.1, -0.5],
        maximum_drawdowns: [-0.03, -0.06],
        excess_returns: [0.01, 0.005],
        information_ratios: [0.4, 0.1],
        return_direction_agreement: false,
        excess_direction_agreement: true,
        absolute_return_spread: 0.03,
      }],
    })
  })

  it('excludes a two-stock sector when one common risk metric is non-finite', async () => {
    vi.mocked(runSingleStockFiveMinuteBacktest)
      .mockResolvedValueOnce(resultFor(requestFor('600003')))
      .mockResolvedValueOnce(resultFor(requestFor('600001')))
      .mockResolvedValueOnce(resultFor(requestFor('600002'), { sharpe: 'NaN' }))
      .mockResolvedValueOnce(resultFor(requestFor('600004')))
    render(<SingleStockFiveMinuteBacktestLab dataset={dataset} />)

    fireEvent.click(screen.getByRole('button', { name: '每板块 2 股' }))
    fireEvent.click(screen.getByRole('button', { name: '运行 4 股 / 3 板块同参诊断' }))

    expect(await screen.findByText('同参批量完成：4/4 只代表股，覆盖 3/3 个板块')).toBeInTheDocument()
    const consistency = screen.getByTestId('sector-pair-consistency')
    expect(within(consistency).getByText('0/1')).toBeInTheDocument()
    expect(screen.queryByTestId('sector-pair-details')).not.toBeInTheDocument()
    const exported = JSON.parse(screen.getByRole('button', { name: '导出本次会话对比' }).dataset.export ?? '')
    expect(exported.summary.within_sector_consistency).toMatchObject({
      requested_pair_sector_count: 1,
      complete_pair_sector_count: 0,
      pairs: [],
    })
  })

  it('excludes an incomplete two-stock sector from the within-sector denominator', async () => {
    vi.mocked(runSingleStockFiveMinuteBacktest)
      .mockResolvedValueOnce(resultFor(requestFor('600003')))
      .mockResolvedValueOnce(resultFor(requestFor('600001')))
      .mockRejectedValueOnce(new Error('provider timeout'))
      .mockResolvedValueOnce(resultFor(requestFor('600004')))
    render(<SingleStockFiveMinuteBacktestLab dataset={dataset} />)

    fireEvent.click(screen.getByRole('button', { name: '每板块 2 股' }))
    fireEvent.click(screen.getByRole('button', { name: '运行 4 股 / 3 板块同参诊断' }))

    expect(await screen.findByText('同参批量部分完成：3/4 只代表股，覆盖 3/3 个板块')).toBeInTheDocument()
    const consistency = screen.getByTestId('sector-pair-consistency')
    expect(within(consistency).getByText('0/1')).toBeInTheDocument()
    expect(within(consistency).getAllByText('—')).toHaveLength(4)
    expect(within(consistency).getByText(/单股板块、请求失败、身份拒绝或非有限指标均不补值/)).toBeInTheDocument()
    expect(screen.queryByTestId('sector-pair-details')).not.toBeInTheDocument()
    const exported = JSON.parse(screen.getByRole('button', { name: '导出本次会话对比' }).dataset.export ?? '')
    expect(exported.summary.within_sector_consistency).toMatchObject({
      requested_pair_sector_count: 1,
      complete_pair_sector_count: 0,
      return_direction_agreement_count: 0,
      excess_direction_agreement_count: 0,
      median_absolute_return_spread: null,
      worst_absolute_return_spread: null,
      pairs: [],
    })
  })

  it('reports partial coverage when one sector request fails', async () => {
    vi.mocked(runSingleStockFiveMinuteBacktest)
      .mockResolvedValueOnce(resultFor(requestFor('600003')))
      .mockRejectedValueOnce(new Error('provider timeout'))
      .mockResolvedValueOnce(resultFor(requestFor('600004')))
    render(<SingleStockFiveMinuteBacktestLab dataset={dataset} />)

    fireEvent.click(screen.getByRole('button', { name: '运行 3 股 / 3 板块同参诊断' }))

    expect(await screen.findByText('同参批量部分完成：2/3 只代表股，覆盖 2/3 个板块')).toBeInTheDocument()
    expect(screen.getByText(/1 只代表股请求失败，失败结果未补造/)).toBeInTheDocument()
    expect(screen.getByText('本次会话个股回测对比（2/30，覆盖 2 个板块）')).toBeInTheDocument()
    expect(within(screen.getByTestId('sector-batch-summary')).getByText('指标有效代表股')).toBeInTheDocument()
    expect(within(screen.getByTestId('sector-batch-summary')).getAllByText('2/2')).toHaveLength(3)
    expect(within(screen.getByTestId('sector-batch-summary')).getByText(/只按 2 个成功代表股/)).toBeInTheDocument()
    const attempts = screen.getByTestId('sector-batch-attempts')
    expect(within(attempts).getByText('600001 乙一')).toBeInTheDocument()
    expect(within(attempts).getByText('请求失败')).toBeInTheDocument()
    const exported = JSON.parse(screen.getByRole('button', { name: '导出本次会话对比' }).dataset.export ?? '')
    expect(exported.classification).toBe('BROWSER_SESSION_ONLY_CROSS_SECTOR_SAME_PARAMETER_DIAGNOSTIC')
    expect(exported.dataset_identity).toEqual({ data_version: dataset.data_version, checksum: dataset.checksum })
    expect(exported.status).toEqual({ requested: 3, completed: 2, failed: 1, identity_rejected: 0, requested_sectors: 3, completed_sectors: 2 })
    expect(exported.requests.map((request: SingleStockFiveMinuteBacktestRequest) => request.symbol)).toEqual(['600003', '600001', '600004'])
    expect(exported.attempts.map(({ symbol, outcome, preview_hash }: { symbol: string; outcome: string; preview_hash: string | null }) => ({ symbol, outcome, preview_hash }))).toEqual([
      { symbol: '600003', outcome: 'COMPLETED', preview_hash: resultFor(requestFor('600003')).preview_hash },
      { symbol: '600001', outcome: 'REQUEST_FAILED', preview_hash: null },
      { symbol: '600004', outcome: 'COMPLETED', preview_hash: resultFor(requestFor('600004')).preview_hash },
    ])
    expect(exported.results).toHaveLength(2)
    expect(exported.summary).toMatchObject({ valid_metric_count: 2, positive_return_count: 2, positive_excess_count: 2 })
    expect(exported.creates_immutable_evidence).toBe(false)
    expect(exported.promotion).toBe('NOT_ELIGIBLE')
    expect(exported.state_mutation).toBe(false)
    expect(exported.external_order_transmission).toBe(false)
    await waitFor(() => expect(runSingleStockFiveMinuteBacktest).toHaveBeenCalledTimes(3))
  })

  it('excludes a successful response with non-finite metrics from the summary denominator', async () => {
    vi.mocked(runSingleStockFiveMinuteBacktest)
      .mockResolvedValueOnce(resultFor(requestFor('600003')))
      .mockResolvedValueOnce(resultFor(requestFor('600001'), { totalReturn: 'NaN' }))
      .mockRejectedValueOnce(new Error('provider timeout'))
    render(<SingleStockFiveMinuteBacktestLab dataset={dataset} />)

    fireEvent.click(screen.getByRole('button', { name: '运行 3 股 / 3 板块同参诊断' }))

    expect(await screen.findByText('同参批量部分完成：2/3 只代表股，覆盖 2/3 个板块')).toBeInTheDocument()
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

    fireEvent.click(screen.getByRole('button', { name: '运行 3 股 / 3 板块同参诊断' }))

    expect(await screen.findByText('同参批量部分完成：0/3 只代表股，覆盖 0/3 个板块')).toBeInTheDocument()
    expect(screen.queryByTestId('sector-batch-summary')).not.toBeInTheDocument()
    expect(screen.queryByText(/0\/0/)).not.toBeInTheDocument()
    expect(screen.getByText('本次会话个股回测对比（0/30，覆盖 0 个板块）')).toBeInTheDocument()
    expect(within(screen.getByTestId('sector-batch-attempts')).getAllByText('请求失败')).toHaveLength(3)
    const exported = JSON.parse(screen.getByRole('button', { name: '导出本次会话对比' }).dataset.export ?? '')
    expect(exported.status).toEqual({ requested: 3, completed: 0, failed: 3, identity_rejected: 0, requested_sectors: 3, completed_sectors: 0 })
    expect(exported.requests).toHaveLength(3)
    expect(exported.attempts.map(({ outcome }: { outcome: string }) => outcome)).toEqual([
      'REQUEST_FAILED', 'REQUEST_FAILED', 'REQUEST_FAILED',
    ])
    expect(exported.results).toEqual([])
    expect(exported.summary).toEqual({
      valid_metric_count: 0,
      positive_return_count: 0,
      positive_excess_count: 0,
      median_total_return: null,
      median_annualized_return: null,
      median_sharpe: null,
      median_maximum_drawdown: null,
      median_excess_return: null,
      median_information_ratio: null,
      worst_return: null,
      deepest_drawdown: null,
      within_sector_consistency: null,
    })
    expect(exported.creates_immutable_evidence).toBe(false)
    expect(exported.promotion).toBe('NOT_ELIGIBLE')
    expect(exported.state_mutation).toBe(false)
    expect(exported.external_order_transmission).toBe(false)
    await waitFor(() => expect(runSingleStockFiveMinuteBacktest).toHaveBeenCalledTimes(3))
  })

  it('rejects responses whose parameters or safety identity drift from the batch request', async () => {
    const parameterDrift = resultFor(requestFor('600001'))
    parameterDrift.parameters = { ...parameterDrift.parameters, cost_bps: '10' }
    const unsafe = {
      ...resultFor(requestFor('600004')),
      external_order_transmission: true,
    } as unknown as SingleStockFiveMinuteBacktest
    vi.mocked(runSingleStockFiveMinuteBacktest)
      .mockResolvedValueOnce(resultFor(requestFor('600003')))
      .mockResolvedValueOnce(parameterDrift)
      .mockResolvedValueOnce(unsafe)
    render(<SingleStockFiveMinuteBacktestLab dataset={dataset} />)

    fireEvent.click(screen.getByRole('button', { name: '运行 3 股 / 3 板块同参诊断' }))

    expect(await screen.findByText('同参批量部分完成：1/3 只代表股，覆盖 1/3 个板块')).toBeInTheDocument()
    expect(screen.getByText(/2 个响应身份或安全边界不匹配/)).toBeInTheDocument()
    expect(screen.getByText('本次会话个股回测对比（1/30，覆盖 1 个板块）')).toBeInTheDocument()
    const comparison = screen.getByTestId('single-stock-session-comparison')
    expect(within(comparison).queryByText(/600001/)).not.toBeInTheDocument()
    expect(within(comparison).queryByText(/600004/)).not.toBeInTheDocument()
    const attempts = screen.getByTestId('sector-batch-attempts')
    expect(within(attempts).getByText('600001 乙一')).toBeInTheDocument()
    expect(within(attempts).getByText('600004 丙一')).toBeInTheDocument()
    expect(within(attempts).getAllByText('身份拒绝')).toHaveLength(2)
    const exported = JSON.parse(screen.getByRole('button', { name: '导出本次会话对比' }).dataset.export ?? '')
    expect(exported.attempts.map(({ symbol, outcome }: { symbol: string; outcome: string }) => ({ symbol, outcome }))).toEqual([
      { symbol: '600003', outcome: 'COMPLETED' },
      { symbol: '600001', outcome: 'IDENTITY_REJECTED' },
      { symbol: '600004', outcome: 'IDENTITY_REJECTED' },
    ])
    await waitFor(() => expect(runSingleStockFiveMinuteBacktest).toHaveBeenCalledTimes(3))
  })
})
