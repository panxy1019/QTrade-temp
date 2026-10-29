import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import fixture from '../../../tests/fixtures/factor_strategy_register_v1.json'
import { FactorStrategyBuilder } from './FactorStrategyBuilder'
import { canonicalJson, sha256Hex } from '../research/strategyIdentity'

const identity = vi.hoisted(() => ({ authenticated: true }))
vi.mock('./ResearchWorkflowProvider', () => ({ useResearchWorkflow: () => ({ session: identity }) }))
vi.mock('antd', async importOriginal => {
  const original = await importOriginal<typeof import('antd')>()
  return { ...original, Select: (props: { 'aria-label': string; value?: string; disabled?: boolean;
    options: Array<{ value: string; label: string }>; onChange: (value: string) => void }) =>
    <select aria-label={props['aria-label']} value={props.value ?? ''} disabled={props.disabled} onChange={event => props.onChange(event.target.value)}>
      <option value="">选择</option>{props.options.map(row => <option key={row.value} value={row.value}>{row.label}</option>)}
    </select> }
})
afterEach(() => { vi.unstubAllGlobals(); identity.authenticated = true })

async function seal<T extends object>(value: T) {
  const body = Object.fromEntries(Object.entries(value).filter(([key]) => key !== 'evidence_sha256'))
  return { ...body, evidence_sha256: await sha256Hex(canonicalJson(body)) }
}

function mount(failFirst = false, unavailable = false) {
  const requests: Array<Record<string, unknown>> = []
  const fetcher = vi.fn(async (url: RequestInfo | URL, options?: RequestInit) => {
    const path = String(url)
    if (path.endsWith('/catalog')) return { ok: true, json: async () => unavailable ? {} : fixture.catalog }
    const body = JSON.parse(String(options?.body))
    if (path.endsWith('/specification')) {
      const base = Object.fromEntries(Object.entries(fixture.definition).filter(([key]) =>
        !['evidence_sha256', 'strategy_id', 'strategy_version'].includes(key))) as Omit<typeof fixture.definition, 'evidence_sha256' | 'strategy_id' | 'strategy_version'>
      const unsigned = { ...base, spec: body.spec, portfolio: { ...base.portfolio, ...body.spec.portfolio },
        execution: { ...base.execution, ...body.spec.execution } }
      const version = await sha256Hex(canonicalJson(unsigned))
      const definition = await seal({ ...unsigned, strategy_version: version, strategy_id: 'factor-' + version })
      return { ok: true, json: async () => seal({ schema_version: 'qtrade.factor-strategy-preview.v1',
        spec: body.spec, source_revision: fixture.catalog.worker.source_revision, strategy_definition: definition,
        expected_strategy_version: version, expected_catalog_sha256: fixture.catalog.catalog_sha256,
        live_trading_enabled: false, external_order_transmission: false }) }
    }
    requests.push(body)
    if (failFirst && requests.length === 1) throw new Error('response lost after request')
    return { ok: true, json: async () => ({ job_id: body.request_id, state: 'QUEUED',
      kind: 'FACTOR_STRATEGY_REGISTER_V1', registration_only: true,
      strategy_version: body.expected_strategy_version, spec_sha256: await sha256Hex(canonicalJson(body.spec)),
      live_trading_enabled: false, external_order_transmission: false }) }
  })
  vi.stubGlobal('fetch', fetcher)
  render(<FactorStrategyBuilder />)
  return { requests, fetcher }
}

async function choose() {
  const select = await screen.findByLabelText('因子评价版本')
  fireEvent.change(select, { target: { value: fixture.catalog.templates[0].strategy_version } })
  return screen.findByLabelText('因子策略名称')
}

describe('factor strategy registration controls', () => {
  it('closes an unverifiable catalog without fabricated default choices', async () => {
    const { fetcher } = mount(false, true)
    await screen.findByText('因子目录身份或权限边界不匹配', { exact: false })
    expect(screen.queryByLabelText('因子评价版本')).not.toBeInTheDocument()
    expect(fetcher.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false)
  })

  it('requires login and invalidates preview when the draft changes', async () => {
    mount()
    const name = await choose()
    const submit = screen.getByRole('button', { name: '提交研究策略登记' })
    expect(submit).toBeDisabled()
    fireEvent.click(screen.getByRole('button', { name: '预览因子策略合同' }))
    await waitFor(() => expect(submit).toBeEnabled())
    fireEvent.change(name, { target: { value: 'changed-factor-draft' } })
    expect(submit).toBeDisabled()
    expect(screen.queryByText('冻结策略版本：', { exact: false })).not.toBeInTheDocument()
  })

  it('reuses one request id after a lost response and never claims a backtest', async () => {
    const { requests } = mount(true)
    await choose()
    fireEvent.click(screen.getByRole('button', { name: '预览因子策略合同' }))
    const submit = screen.getByRole('button', { name: '提交研究策略登记' })
    await waitFor(() => expect(submit).toBeEnabled())
    fireEvent.click(submit)
    await screen.findByText('response lost after request', { exact: false })
    fireEvent.click(screen.getByRole('button', { name: '用原请求标识重试登记' }))
    await screen.findByText('登记任务已入队：', { exact: false })
    expect(requests).toHaveLength(2)
    expect(requests[0]).toEqual(requests[1])
    expect(screen.getByText('入队不等于登记完成；以集群任务的最终不可变回执为准。')).toBeVisible()
    expect(screen.getByRole('button', { name: '提交研究策略登记' })).toBeDisabled()
  })

  it('keeps mutations disabled for anonymous readers', async () => {
    identity.authenticated = false
    const { fetcher } = mount()
    await choose()
    expect(screen.getByRole('button', { name: '预览因子策略合同' })).toBeDisabled()
    expect(screen.getByRole('button', { name: '提交研究策略登记' })).toBeDisabled()
    expect(fetcher.mock.calls.some(([, options]) => options?.method === 'POST')).toBe(false)
  })
})
