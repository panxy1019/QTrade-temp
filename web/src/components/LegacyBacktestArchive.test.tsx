import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import type { HistoricalDataset } from '../types'
import { LegacyBacktestArchive } from './LegacyBacktestArchive'

vi.mock('./JsonExportButton', () => ({ JsonExportButton: ({ value, label }: { value: unknown; label: string }) => <button disabled={!value} data-export={JSON.stringify(value)}>{label}</button> }))
afterEach(() => { window.localStorage.clear() })

const dataset = {
  provider: 'archived-provider', provider_version: 'v1', data_version: 'frozen-daily',
  checksum: 'a'.repeat(64), universe: { point_in_time: false },
} as HistoricalDataset
const record = {
  schema_version: 1, name: '原动量实验', saved_at: '2026-08-27T06:00:00+08:00',
  preview_hash: 'b'.repeat(64), data_version: dataset.data_version, data_checksum: dataset.checksum,
  session_count: 100, universe_size: 30,
  parameters: {
    start: '2026-01-01', end: '2026-08-12', factor: 'momentum', horizons: [20, 60, 120],
    top_k: 3, rebalance_every: 20, reserve: '0.05', trend_window: null,
    volatility_window: null, target_annual_volatility: null, initial_cash: '1000000',
    transaction_cost_rate: '0.0015', slippage_rate: '0.0005',
  },
  metrics: { total_return: '0.01', maximum_drawdown: '-0.05' },
  binding: {
    classification: 'BROWSER_LOCAL_REPLAY_BINDING_NOT_IMMUTABLE',
    template_id: 'etf-momentum-rotation-v1', spec_hash: 'c'.repeat(64), binding_hash: 'd'.repeat(64),
    creates_immutable_experiment: false, registry_write: false,
    paper_runtime_start: false, live_runtime_start: false,
  },
}
const key = 'qtrade.local-backtest-experiments.v1'

it('preserves and exports original saved records without replay or mutation controls', () => {
  const original = JSON.stringify([record])
  window.localStorage.setItem(key, original)
  render(<LegacyBacktestArchive dataset={dataset} />)
  expect(screen.getByText(record.name)).toBeInTheDocument()
  expect(screen.getByText('1.00%')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '载入参数' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '删除' })).not.toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '运行即时回测' })).not.toBeInTheDocument()
  expect(screen.queryByLabelText('基线策略模板')).not.toBeInTheDocument()
  expect(JSON.parse(screen.getByRole('button', { name: '导出此记录' }).getAttribute('data-export')!)).toEqual(record)
  const exported = JSON.parse(screen.getByRole('button', { name: '导出历史记录' }).getAttribute('data-export')!)
  expect(exported.records).toEqual([record])
  expect(exported.state_mutation).toBe(false)
  fireEvent.click(screen.getByRole('button', { name: 'Expand row' }))
  expect(screen.getByText(/"template_id": "etf-momentum-rotation-v1"/)).toBeInTheDocument()
  expect(window.localStorage.getItem(key)).toBe(original)
})

it('does not relabel unmatched saved records with the current dataset identity', () => {
  const original = JSON.stringify([{ ...record, data_checksum: 'e'.repeat(64) }])
  window.localStorage.setItem(key, original)
  render(<LegacyBacktestArchive dataset={dataset} />)
  expect(screen.queryByText(record.name)).not.toBeInTheDocument()
  expect(screen.getByRole('button', { name: '导出历史记录' })).toBeDisabled()
  expect(window.localStorage.getItem(key)).toBe(original)
})
