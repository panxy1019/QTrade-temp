import { describe, expect, it } from 'vitest'
import { comparisonIssue, completedExperiments } from './qlibWorkflow'
import type { ResearchSpec, WorkflowExperiment, WorkflowResult } from './qlibWorkflow'

const spec: ResearchSpec = {
  name: 'test', dataset_id: 'same-input', model: 'linear', features: 'Alpha158',
  segments: { train: ['2017-04-05', '2021-12-28'], valid: ['2022-01-04', '2023-12-26'], test: ['2024-01-02', '2026-10-23'] },
  topk: 50, n_drop: 5, hold_thresh: 2, risk_degree: 0.95, buy_cost_bps: 8,
  sell_cost_bps: 13, min_cost: 5, initial_cash: 10000000, seed: 1, universe_policy: 'supported_a_share',
}
const result = () => ({ spec: structuredClone(spec), dataset_sha256: 'same', source_revision: 'same', comparison_protocol: 'aligned-v1', curve: [{ date: '2024-01-02' }] }) as WorkflowResult

describe('controlled Qlib comparisons', () => {
  it('defaults to the newest completed run, ahead of the imported old reference', () => {
    const rows = [
      { experiment_id: 'legacy', state: 'COMPLETE', completed_at: '2026-10-25T00:00:00Z' },
      { experiment_id: 'newest', state: 'COMPLETE', completed_at: '2026-10-26T00:00:00Z' },
      { experiment_id: 'pending', state: 'QUEUED', completed_at: '2026-10-27T00:00:00Z' },
    ] as WorkflowExperiment[]
    expect(completedExperiments(rows).map(row => row.experiment_id)).toEqual(['newest', 'legacy'])
    expect(rows[0].experiment_id).toBe('legacy')
  })
  it('accepts model-only changes despite JSON property ordering', () => {
    const left = result(), right = result()
    right.spec = Object.fromEntries(Object.entries(right.spec!).reverse()) as unknown as ResearchSpec
    right.spec.model = 'lightgbm'; right.spec.name = 'tree'
    expect(comparisonIssue(left, right)).toBeNull()
  })
  it('rejects legacy, changed inputs, costs, dates and source', () => {
    const left = result(), legacy = result(); legacy.spec = null
    expect(comparisonIssue(left, legacy)).toMatch(/协议/)
    const changed = result(); changed.spec!.segments.test[1] = '2025-01-01'
    expect(comparisonIssue(left, changed)).toMatch(/时间/)
    const cost = result(); cost.spec!.sell_cost_bps = 1
    expect(comparisonIssue(left, cost)).toMatch(/成本/)
    const data = result(); data.dataset_sha256 = 'other'
    expect(comparisonIssue(left, data)).toMatch(/数据/)
    const code = result(); code.source_revision = 'other'
    expect(comparisonIssue(left, code)).toMatch(/代码/)
    const dates = result(); dates.curve[0].date = '2024-01-03'
    expect(comparisonIssue(left, dates)).toMatch(/日期/)
  })
})
