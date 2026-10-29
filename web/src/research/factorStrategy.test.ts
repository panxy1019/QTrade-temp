import { describe, expect, it } from 'vitest'
import fixture from '../../../tests/fixtures/factor_strategy_register_v1.json'
import { checkedFactorCatalog, checkedFactorPreview } from './factorStrategy'
import type { FactorSpec } from './factorStrategy'
import { canonicalJson, sha256Hex } from './strategyIdentity'
import { completedExperiments } from './qlibWorkflow'
import type { WorkflowExperiment } from './qlibWorkflow'
const spec = fixture.spec as unknown as FactorSpec

async function seal<T extends object>(value: T) {
  const body = Object.fromEntries(Object.entries(value).filter(([key]) => key !== 'evidence_sha256'))
  return { ...body, evidence_sha256: await sha256Hex(canonicalJson(body)) }
}

async function preview() {
  return seal({ schema_version: 'qtrade.factor-strategy-preview.v1',
    spec: fixture.spec, source_revision: fixture.catalog.worker.source_revision,
    strategy_definition: fixture.definition, expected_strategy_version: fixture.definition.strategy_version,
    expected_catalog_sha256: fixture.catalog.catalog_sha256,
    live_trading_enabled: false, external_order_transmission: false })
}

describe('factor registration browser boundary', () => {
  it('keeps registry completion out of the portfolio backtest result selector', () => {
    const rows = [{ engine: 'FACTOR_REGISTRATION', state: 'COMPLETE', experiment_id: 'factor' },
      { engine: 'QLIB', state: 'COMPLETE', experiment_id: 'backtest' }] as WorkflowExperiment[]
    expect(completedExperiments(rows).map(row => row.experiment_id)).toEqual(['backtest'])
  })
  it('accepts the actual shared contract and a fully bound preview', async () => {
    const catalog = await checkedFactorCatalog(fixture.catalog)
    expect((await checkedFactorPreview(await preview(), spec, catalog)).strategy_definition)
      .toEqual(fixture.definition)
  })

  it('keeps stable content identity separate from heartbeat identity', async () => {
    const next = structuredClone(fixture.catalog)
    next.worker.heartbeat_at = '2026-10-28T13:00:00+00:00'
    expect((await checkedFactorCatalog(await seal(next))).catalog_sha256).toBe(fixture.catalog.catalog_sha256)
    next.templates[0].spec.portfolio.topk += 1
    await expect(checkedFactorCatalog(await seal(next))).rejects.toThrow('摘要')
  })

  it('rejects unsupported catalogs and tampered diagnostic identities', async () => {
    await expect(checkedFactorCatalog({ schema_version: 'qtrade.research-workflow.v1' })).rejects.toThrow()
    const next = structuredClone(fixture.catalog)
    next.templates[0].evaluation_context.decision_scope.count += 1
    await expect(checkedFactorCatalog(next)).rejects.toThrow()
  })

  it('rejects a resigned preview for another spec, source or catalog', async () => {
    const catalog = await checkedFactorCatalog(fixture.catalog)
    const original = await preview()
    for (const patch of [{ expected_catalog_sha256: '0'.repeat(64) },
      { source_revision: '0'.repeat(40) }, { spec: { ...fixture.spec, name: 'other' } }]) {
      await expect(checkedFactorPreview(await seal({ ...original, ...patch }), spec, catalog)).rejects.toThrow()
    }
  })

  it('does not turn a resigned registry definition into execution authority', async () => {
    const catalog = await checkedFactorCatalog(fixture.catalog)
    const original = await preview()
    const definition = await seal({ ...fixture.definition, paper_admission: true })
    await expect(checkedFactorPreview(await seal({ ...original, strategy_definition: definition }), spec, catalog)).rejects.toThrow()
  })
})
