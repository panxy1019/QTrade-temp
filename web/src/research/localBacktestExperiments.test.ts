import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { BacktestPreview, ExperimentRunSpecBacktest, HistoricalDataset } from '../types'
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
  benchmark: { strategy_id: 'equal-weight-buy-hold-benchmark-v1', decision_timing: 'first session close, next open execution', first_decision_known_symbols_only: true, current_universe_survivorship_bias: true, reserve: '0.05', transaction_cost_rate: '0.0015', slippage_rate: '0.0005', execution_timing: 'close decision, next open execution', metrics: { total_return: '0.05' } },
  curve: [], terminal_positions: [], execution_timing: 'close decision, next open execution',
  external_order_transmission: false,
  preview_hash: 'a'.repeat(64),
}

const boundResult: ExperimentRunSpecBacktest = {
  classification: 'READ_ONLY_HASH_BOUND_EXPERIMENT_PREVIEW_NOT_PERSISTED',
  template_id: 'etf-momentum-rotation-v1',
  data_checksum: dataset.checksum,
  dataset_point_in_time: false,
  spec_hash: 'b'.repeat(64),
  preview_hash: result.preview_hash,
  binding_hash: 'c'.repeat(64),
  spec: {
    experiment_id: 'rotation-replay-001',
    strategy_id: 'etf-momentum-rotation-v1',
    git_commit: 'd'.repeat(40),
    data_version: dataset.data_version,
    universe: ['510300'],
    factor_set: ['momentum_20', 'momentum_60', 'momentum_120'],
    label: 'next_session_portfolio_return',
    split: {
      train_start: '2024-01-02', train_end: '2024-12-31',
      validation_start: '2025-01-01', validation_end: '2025-12-31',
      test_start: '2026-01-01', test_end: '2026-08-12',
    },
    model: 'qtrade.research.multi_asset.MomentumAllocation',
    parameters: [], cost_model_id: 'explicit-rate-plus-slippage-v1',
    transaction_cost: '0.0015', seed: 7,
  },
  result,
  gates: {
    single_request_spec_result_binding: true,
    spec_hash_recomputed_before_run: true,
    verified_dataset_reloaded_before_run: true,
    preview_hash_bound_to_spec_hash: true,
    immutable_artifact_persisted: false,
    independent_leakage_review_passed: false,
    fresh_unseen_test_evidence: false,
  },
  creates_immutable_experiment: false,
  registry_write: false,
  paper_runtime_start: false,
  live_runtime_start: false,
  state_mutation: false,
  external_order_transmission: false,
}

beforeEach(() => {
  window.localStorage.clear()
  vi.useRealTimers()
})

describe('local backtest experiments', () => {
  it('labels an ordinary preview as browser-local without a run-spec binding', () => {
    const saved = saveLocalBacktestExperiment('即时方案', result)
    expect(saved.binding).toBeNull()
    expect(loadLocalBacktestExperiments(dataset)[0].binding).toBeNull()
  })

  it('preserves the exact template, spec, and binding identity for a bound preview', () => {
    const saved = saveLocalBacktestExperiment('绑定方案', result, boundResult)
    expect(saved.binding).toEqual({
      classification: 'BROWSER_LOCAL_REPLAY_BINDING_NOT_IMMUTABLE',
      template_id: 'etf-momentum-rotation-v1',
      spec_hash: 'b'.repeat(64),
      binding_hash: 'c'.repeat(64),
      creates_immutable_experiment: false,
      registry_write: false,
      paper_runtime_start: false,
      live_runtime_start: false,
    })
    expect(loadLocalBacktestExperiments(dataset)[0].binding).toEqual(saved.binding)
  })

  it.each([
    ['结果哈希', { preview_hash: 'e'.repeat(64) }],
    ['数据 checksum', { data_checksum: 'another-checksum' }],
  ])('rejects a mismatched bound preview by %s without writing local data', (_label, override) => {
    expect(() => saveLocalBacktestExperiment('错配方案', result, {
      ...boundResult,
      ...override,
    })).toThrow('规格绑定与当前回测结果不一致，未保存')
    expect(loadLocalBacktestExperiments(dataset)).toEqual([])
  })

  it('loads legacy schema-v1 records without binding metadata as ordinary previews', () => {
    const legacy = Object.fromEntries(Object.entries(
      saveLocalBacktestExperiment('旧方案', result),
    ).filter(([key]) => key !== 'binding'))
    window.localStorage.setItem('qtrade.local-backtest-experiments.v1', JSON.stringify([legacy]))
    expect(loadLocalBacktestExperiments(dataset)[0]).toEqual(expect.objectContaining({
      name: '旧方案', binding: null,
    }))
  })

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
