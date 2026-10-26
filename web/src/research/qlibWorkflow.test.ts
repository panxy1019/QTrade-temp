import { afterEach, describe, expect, it, vi } from 'vitest'
import { canReuseArchivedResult, comparisonDraft, comparisonIssue, completedExperiments, sourceBoundProtocol, workflowGet } from './qlibWorkflow'
import type { ArchivedResearchTemplate, ResearchSpec, WorkflowExperiment, WorkflowResult } from './qlibWorkflow'

afterEach(() => vi.unstubAllGlobals())

const spec: ResearchSpec = {
  name: 'test', dataset_id: 'same-input', model: 'linear', features: 'Alpha158',
  segments: { train: ['2017-04-05', '2021-12-28'], valid: ['2022-01-04', '2023-12-26'], test: ['2024-01-02', '2026-10-23'] },
  topk: 50, n_drop: 5, hold_thresh: 2, risk_degree: 0.95, buy_cost_bps: 8,
  sell_cost_bps: 13, min_cost: 5, initial_cash: 10000000, seed: 1, universe_policy: 'supported_a_share',
}
const result = () => ({ spec: structuredClone(spec), dataset_sha256: 'same', source_revision: 'same', comparison_protocol: 'aligned-v1', curve: [{ date: '2024-01-02' }] }) as WorkflowResult

const template = (): ArchivedResearchTemplate => ({
  schema_version: 'qtrade.research-template.v1', base_experiment_id: 'reference',
  base_run_id: 'reference-run', base_result_sha256: 'a'.repeat(64),
  source_revision: 'b'.repeat(40), dataset_sha256: 'c'.repeat(64),
  source_snapshot_sha256: 'd'.repeat(64), comparison_protocol: sourceBoundProtocol,
  spec: { ...structuredClone(spec), source_snapshot_sha256: 'd'.repeat(64) },
  live_trading_enabled: false, external_order_transmission: false, point_in_time: false,
  real_data_paper_readiness: false, promotion: 'NOT_ELIGIBLE',
})

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
  it('binds model comparisons to identical historical source evidence', () => {
    const left = result(), right = result()
    left.comparison_protocol = right.comparison_protocol = 'ALIGNED_SUPPORTED_UNIVERSE_SOURCE_BOUND_CONSTRAINTS_V2'
    expect(comparisonIssue(left, right)).toMatch(/身份缺失/)
    left.source_snapshot_sha256 = 'a'.repeat(64); right.source_snapshot_sha256 = 'b'.repeat(64)
    expect(comparisonIssue(left, right)).toMatch(/来源快照不同/)
    right.source_snapshot_sha256 = left.source_snapshot_sha256
    expect(comparisonIssue(left, right)).toBeNull()
  })
  it('creates a model-only draft without mutating any archived input', () => {
    const original = template(), before = structuredClone(original)
    const draft = comparisonDraft(original, 'reference')
    expect(draft).toEqual({ ...original.spec, name: 'test-vs-lightgbm', model: 'lightgbm' })
    draft.segments.test[1] = '2025-01-01'
    expect(original).toEqual(before)
    const long = template(); long.spec.name = 'a'.repeat(60)
    expect(comparisonDraft(long, 'reference').name.length).toBeLessThanOrEqual(60)
  })
  it('rejects misbound templates and does not offer legacy configuration reuse', () => {
    expect(() => comparisonDraft(template(), 'another')).toThrow(/身份不完整/)
    const changed = template(); changed.spec.source_snapshot_sha256 = 'e'.repeat(64)
    expect(() => comparisonDraft(changed, 'reference')).toThrow(/身份不完整/)
    const candidate = { ...result(), comparison_protocol: sourceBoundProtocol,
      source_snapshot_sha256: 'd'.repeat(64), spec: template().spec }
    expect(canReuseArchivedResult(candidate)).toBe(true)
    expect(canReuseArchivedResult({ ...candidate, spec: null })).toBe(false)
    expect(canReuseArchivedResult(result())).toBe(false)
  })
  it('preserves the server explanation when an archived template cannot be reused', async () => {
    vi.stubGlobal('fetch', vi.fn(async () => ({ ok: false, status: 422,
      json: async () => ({ detail: '当前执行器代码与原实验不同' }) })))
    await expect(workflowGet('/experiments/reference/template')).rejects.toThrow('当前执行器代码与原实验不同')
  })
})
