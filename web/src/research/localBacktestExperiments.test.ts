import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BacktestPreview, HistoricalDataset } from '../types'
import {
  deleteLocalBacktestExperiment,
  loadLocalBacktestExperiments,
  saveLocalBacktestExperiment,
} from './localBacktestExperiments'

const dataset: HistoricalDataset = {
  provider: 'AKShare', provider_version: '1.18.84',
  classification: 'noncommercial_research_historical_only',
  data_version: 'bars-v1', quality_status: 'PASSED', checksum: 'dataset-checksum-v1',
  bar_count: 100, observed_start: '2024-01-01T15:00:00+08:00',
  observed_end: '2025-12-31T15:00:00+08:00', snapshot_readiness: false,
  real_data_paper_readiness: false, universe: { point_in_time: false, symbols: ['510300'] },
}

const result: BacktestPreview = {
  classification: 'AD_HOC_PREVIEW_CURRENT_UNIVERSE_NOT_PIT', promotion: 'NOT_ELIGIBLE',
  promotion_reasons: ['AD_HOC_PARAMETERS_NOT_PREDECLARED'],
  data_version: dataset.data_version, data_checksum: dataset.checksum,
  parameters: {
    start: '2024-01-02', end: '2025-12-31', factor: 'momentum', horizons: [20, 60, 120], top_k: 3,
    rebalance_every: 20, reserve: '0.05', trend_window: 120, volatility_window: 20,
    target_annual_volatility: '0.15', initial_cash: '1000000',
    transaction_cost_rate: '0.0015', slippage_rate: '0.0005',
  },
  session_count: 400, universe_size: 30,
  metrics: { total_return: '0.1', sharpe: '0.5', maximum_drawdown: '-0.1' },
  curve: [], terminal_positions: [], execution_timing: 'close decision, next open execution',
  external_order_transmission: false,
  preview_hash: 'a'.repeat(64),
}

beforeEach(() => {
  window.localStorage.clear()
  vi.useRealTimers()
})

describe('local backtest experiments', () => {
  it('binds records to the exact dataset identity and replaces duplicate results', () => {
    saveLocalBacktestExperiment('方案 A', result)
    saveLocalBacktestExperiment('方案 A 改名', result)
    expect(loadLocalBacktestExperiments(dataset)).toEqual([
      expect.objectContaining({ name: '方案 A 改名', preview_hash: result.preview_hash }),
    ])
    expect(loadLocalBacktestExperiments({ ...dataset, checksum: 'another-checksum' })).toEqual([])
  })

  it('ignores corrupt local data instead of presenting it as a valid experiment', () => {
    window.localStorage.setItem('qtrade.local-backtest-experiments.v1', JSON.stringify([
      { ...saveLocalBacktestExperiment('有效方案', result), preview_hash: 'not-a-hash' },
    ]))
    expect(loadLocalBacktestExperiments(dataset)).toEqual([])
  })

  it('deletes only the selected local result', () => {
    saveLocalBacktestExperiment('方案 A', result)
    const second = { ...result, preview_hash: 'b'.repeat(64) }
    saveLocalBacktestExperiment('方案 B', second)
    deleteLocalBacktestExperiment(second.preview_hash)
    expect(loadLocalBacktestExperiments(dataset).map((item) => item.name)).toEqual(['方案 A'])
  })

  it('rejects blank names and does not write an experiment', () => {
    expect(() => saveLocalBacktestExperiment('   ', result)).toThrow('实验名称需要 1-80 个字符')
    expect(loadLocalBacktestExperiments(dataset)).toEqual([])
  })

  it('keeps only the thirty most recently saved summaries', () => {
    for (let index = 0; index < 32; index += 1) {
      saveLocalBacktestExperiment(`方案 ${index}`, {
        ...result,
        preview_hash: index.toString(16).padStart(64, '0'),
      })
    }
    const records = loadLocalBacktestExperiments(dataset)
    expect(records).toHaveLength(30)
    expect(records[0].name).toBe('方案 31')
    expect(records.at(-1)?.name).toBe('方案 2')
  })
})
