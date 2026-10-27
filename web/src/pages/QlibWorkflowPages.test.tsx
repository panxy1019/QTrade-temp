import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { Link, MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ResearchWorkflowProvider } from '../components/ResearchWorkflowProvider'
import catalog from '../test/fixtures/qlib-workflow.json'
import boundCatalog from '../test/fixtures/qlib-bound-workflow.json'
import { QlibBacktestPage, QlibResearchPage, QlibStrategyPage } from './QlibWorkflowPages'
import type { ArchivedResearchTemplate, ResearchSpec, WorkflowCatalog } from '../research/qlibWorkflow'
import { sourceBoundProtocol } from '../research/qlibWorkflow'

vi.mock('../charts/ChartRenderer', () => ({ default: () => <div /> }))
afterEach(() => { vi.unstubAllGlobals(); sessionStorage.clear() })

function mountBound({ mismatch = false, failFirstQueue = false, strategy = false } = {}) {
  let queueCount = 0
  const supplied = structuredClone(boundCatalog) as unknown as WorkflowCatalog
  const first = supplied.strategy_versions![0]
  const fetcher = vi.fn(async (input: RequestInfo | URL, options?: RequestInit) => {
    const path = String(input)
    if (path.endsWith('/specification')) {
      const spec = JSON.parse(String(options?.body)).spec as ResearchSpec
      const definition = { ...structuredClone(first), spec: mismatch ? { ...spec, topk: 99 } : spec,
        signal: { ...first.signal, model: spec.model } }
      return { ok: true, status: 200, json: async () => ({ ...definition,
        schema_version: 'qtrade.qlib-specification-preview.v1', spec: definition.spec,
        strategy_definition: definition, source_revision: definition.source_revision,
        expected_strategy_version: definition.strategy_version }) }
    }
    if (path.endsWith('/jobs') && failFirstQueue && queueCount++ === 0) {
      return { ok: false, status: 503, json: async () => ({ detail: 'test-only queue unavailable' }) }
    }
    const body = path.endsWith('/catalog') ? supplied : path.endsWith('/session')
      ? { configured: true, authenticated: true, user: 'research-test' } : { state: 'QUEUED' }
    return { ok: true, status: 200, json: async () => body }
  })
  vi.stubGlobal('fetch', fetcher)
  const path = '/research' + (strategy ? '?strategy_version=' + first.strategy_version : '')
  render(<ResearchWorkflowProvider><MemoryRouter initialEntries={[path]}><Routes>
    <Route path="/research" element={<QlibResearchPage />} />
    <Route path="/experiments" element={<div>已进入任务状态页</div>} />
  </Routes></MemoryRouter></ResearchWorkflowProvider>)
  return { supplied, fetcher, first }
}

it('freezes the selected immutable strategy before queueing and preserves a failed retry', async () => {
  const { fetcher, first } = mountBound({ failFirstQueue: true, strategy: true })
  await waitFor(() => expect(screen.getByLabelText('实验名称')).toHaveValue(first.spec.name))
  fireEvent.click(screen.getByRole('button', { name: '提交集群研究任务' }))
  await screen.findByText(/test-only queue unavailable/)
  fireEvent.click(screen.getByRole('button', { name: '提交集群研究任务' }))
  await screen.findByText('已进入任务状态页')
  const queues = fetcher.mock.calls.filter(([url]) => String(url).endsWith('/jobs'))
  expect(queues).toHaveLength(2)
  expect(JSON.parse(String(queues[0][1]?.body))).toEqual(JSON.parse(String(queues[1][1]?.body)))
  expect(JSON.parse(String(queues[0][1]?.body)).expected_strategy_version).toBe(first.strategy_version)
  expect(JSON.parse(String(queues[0][1]?.body)).spec.data_binding).toEqual(first.spec.data_binding)
  expect(fetcher.mock.calls.filter(([url]) => String(url).endsWith('/specification'))).toHaveLength(1)
})

it('keeps edited version selection stable across polling and refuses a drifted preview', async () => {
  const { supplied, fetcher } = mountBound({ mismatch: true })
  await waitFor(() => expect(screen.getByLabelText('实验名称')).toHaveValue(supplied.default_spec.name))
  fireEvent.change(screen.getByLabelText('实验名称'), { target: { value: 'edited-current-day' } })
  supplied.default_spec = structuredClone(supplied.default_linear_spec)
  fireEvent.click(screen.getByRole('button', { name: '刷新研究目录' }))
  await waitFor(() => expect(screen.getByLabelText('实验名称')).toHaveValue('edited-current-day'))
  fireEvent.click(screen.getByRole('button', { name: '提交集群研究任务' }))
  await screen.findByText(/冻结规格与本次编辑不一致/)
  expect(fetcher.mock.calls.some(([url]) => String(url).endsWith('/jobs'))).toBe(false)
})

function mount(authenticated: boolean, page: 'research' | 'strategies' = 'research', source?: string) {
  const supplied = source ? { ...catalog, source_snapshot_sha256: source,
    default_spec: { ...catalog.default_spec, source_snapshot_sha256: source },
    default_linear_spec: { ...catalog.default_linear_spec, source_snapshot_sha256: source } } : catalog
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input)
    const body = path.endsWith('/catalog') ? supplied
      : path.endsWith('/session') ? { configured: true, authenticated, user: authenticated ? 'research-test' : null }
      : { job_id: 'test', state: 'QUEUED' }
    return { ok: true, status: path.endsWith('/jobs') ? 202 : 200, json: async () => body }
  })
  vi.stubGlobal('fetch', fetcher)
  render(<ResearchWorkflowProvider><MemoryRouter initialEntries={['/' + page]}><Routes>
    <Route path="/research" element={<QlibResearchPage />} />
    <Route path="/strategies" element={<QlibStrategyPage />} />
    <Route path="/experiments" element={<div>已进入任务状态页</div>} />
  </Routes></MemoryRouter></ResearchWorkflowProvider>)
  return fetcher
}

describe('Qlib research workflow pages', () => {
  it('lists only integrated models and exposes the non-PIT boundary', async () => {
    mount(false, 'strategies')
    await screen.findByText('Ridge 线性模型')
    expect(screen.getByText('LightGBM')).toBeInTheDocument()
    expect(screen.getByText('非完整历史PIT')).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '查看旧ETF和分钟研究档案' })).toHaveAttribute('href', '#/archive')
  })
  it('keeps anonymous submission disabled', async () => {
    const fetcher = mount(false)
    await screen.findByLabelText('目标持仓Top K')
    expect(screen.getByRole('button', { name: '提交集群研究任务' })).toBeDisabled()
    expect(fetcher.mock.calls.some(([input]) => String(input).endsWith('/jobs'))).toBe(false)
  })
  it('submits the fixed complete spec with a retry identity and follows the job', async () => {
    const fetcher = mount(true)
    const submit = await screen.findByRole('button', { name: '提交集群研究任务' })
    await waitFor(() => expect(submit).toBeEnabled())
    await waitFor(() => expect(screen.getByLabelText('实验名称')).toHaveValue('alpha158-lightgbm'))
    fireEvent.click(submit)
    await screen.findByText('已进入任务状态页')
    const call = (fetcher.mock.calls as unknown as Array<[string, RequestInit]>).find(([url]) => url.endsWith('/jobs'))!
    expect(call[1].credentials).toBe('same-origin')
    const payload = JSON.parse(call[1].body as string)
    expect(payload.spec).toEqual(catalog.default_spec)
    expect(payload.request_id).toMatch(/^[a-f0-9]{32}$/)
    expect(sessionStorage.getItem('qtrade.pending-research-job')).toContain(payload.request_id)
  })
  it('explains applied source restrictions separately from historical certification', async () => {
    const result = {
      experiment_id: 'source-bound', run_id: 'source-bound-run', state: 'COMPLETE',
      model: 'Alpha158 + Ridge', symbols: 891, feature_count: 158, data_end: '2026-10-23',
      metrics: {}, curve: [], spec: { ...catalog.default_spec, source_snapshot_sha256: 'a'.repeat(64) },
      comparison_protocol: sourceBoundProtocol, source_snapshot_sha256: 'a'.repeat(64),
      portfolio_evaluation: { start: '2024-01-02', end: '2026-10-23' },
      signal_evaluation: { start: '2024-01-02', end: '2026-10-21' },
      audit: { ...catalog.datasets[0].audit, source_repair_coverage: {
        status_symbols: 33, input_symbols: 990, status_rows: 79299, master_rows: 9022,
      }, execution_constraints: {
        source_snapshot_sha256: 'a'.repeat(64), matched_input_symbols: 32,
        counts: { reconciled_bars: 70000, missing_state_bars: 2000000, conflict_bars: 500,
          additional_buy_blocks: 900, additional_sell_blocks: 500 },
      } },
    }
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => ({
      ok: true, json: async () => String(input).endsWith('/catalog') ? catalog
        : String(input).endsWith('/session') ? { configured: true, authenticated: false } : result,
    })))
    render(<ResearchWorkflowProvider><MemoryRouter initialEntries={['/backtest?experiment=source-bound']}>
      <QlibBacktestPage />
    </MemoryRouter></ResearchWorkflowProvider>)
    expect(await screen.findByText(/原始行情对账70,000根/)).toBeInTheDocument()
    expect(screen.getByText(/ST禁买是研究政策，精确涨跌停与完整历史PIT仍未认证/)).toBeInTheDocument()
    expect(screen.getByText(/来源快照：aaaa/)).toBeInTheDocument()
    expect(screen.getByRole('link', { name: '复用配置创建对照' })).toHaveAttribute('href', '#/research?from=source-bound')
  })
  it('shows and submits the pinned source identity alongside model configuration', async () => {
    const source = 'a'.repeat(64)
    const fetcher = mount(true, 'research', source)
    await screen.findByText('本任务固定来源：' + source)
    fireEvent.click(screen.getByRole('button', { name: '提交集群研究任务' }))
    await screen.findByText('已进入任务状态页')
    const call = (fetcher.mock.calls as unknown as Array<[string, RequestInit]>).find(([url]) => url.endsWith('/jobs'))!
    expect(JSON.parse(call[1].body as string).spec.source_snapshot_sha256).toBe(source)
  })
  it('reuses archived inputs despite refreshed defaults and retries with the same request identity', async () => {
    const archived = archiveTemplate()
    let refreshed = false, submissions = 0
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      const body = path.endsWith('/catalog') ? { ...catalog,
        source_snapshot_sha256: (refreshed ? 'e' : 'f').repeat(64),
        default_spec: { ...catalog.default_spec, dataset_id: refreshed ? 'changed-default' : catalog.default_spec.dataset_id },
        ...(refreshed ? { frozen_data_current: false, expected_complete_day: '2026-10-26' } : {}) }
        : path.endsWith('/session') ? { configured: true, authenticated: true, user: 'research-test' }
        : path.endsWith('/template') ? archived
        : ++submissions === 1 ? { detail: '队列临时不可用' } : { state: 'QUEUED' }
      return { ok: !('detail' in body), status: 'detail' in body ? 503 : 200, json: async () => body }
    })
    vi.stubGlobal('fetch', fetcher)
    renderResearch('/research?from=reference')
    await screen.findByText('已载入归档实验的固定配置')
    expect(screen.getByLabelText('实验名称')).toHaveValue('frozen-reference-vs-lightgbm')
    expect(screen.getByLabelText('目标持仓Top K')).toHaveValue('40')
    expect(screen.getByLabelText('卖出成本bp')).toHaveValue('15')
    expect(screen.getByLabelText('随机种子')).toHaveValue('17')
    expect(screen.getByText('本任务固定来源：' + 'a'.repeat(64))).toBeInTheDocument()
    fireEvent.change(screen.getByLabelText('实验名称'), { target: { value: 'chosen-comparison' } })
    fireEvent.click(screen.getByRole('button', { name: '提交集群研究任务' }))
    await screen.findByText(/队列临时不可用/)
    refreshed = true
    fireEvent.click(screen.getByRole('button', { name: '刷新研究目录' }))
    await screen.findByText('研究数据尚未覆盖最近完整交易日2026-10-26')
    expect(screen.getByLabelText('实验名称')).toHaveValue('chosen-comparison')
    fireEvent.click(screen.getByRole('button', { name: '提交集群研究任务' }))
    await screen.findByText('已进入任务状态页')
    const calls = (fetcher.mock.calls as unknown as Array<[string, RequestInit]>).filter(([url]) => url.endsWith('/jobs'))
    const first = JSON.parse(calls[0][1].body as string), second = JSON.parse(calls[1][1].body as string)
    expect(second).toEqual(first)
    expect(second.spec).toEqual({ ...archived.spec, name: 'chosen-comparison', model: 'lightgbm' })
  })
  it('keeps submission closed after a legacy template error instead of falling back to defaults', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => ({
      ok: !String(input).endsWith('/template'), status: String(input).endsWith('/template') ? 422 : 200,
      json: async () => String(input).endsWith('/catalog') ? catalog
        : String(input).endsWith('/session') ? { configured: true, authenticated: true }
        : { detail: '该档案未保存当前协议的完整来源配置，请新建默认研究任务' },
    })))
    renderResearch('/research?from=legacy')
    await screen.findByText('该档案未保存当前协议的完整来源配置，请新建默认研究任务')
    expect(screen.getByRole('button', { name: '提交集群研究任务' })).toBeDisabled()
    expect(screen.getByLabelText('实验名称')).not.toHaveValue(catalog.default_spec.name)
    expect(screen.getByRole('link', { name: '新建默认研究' })).toHaveAttribute('href', '#/research')
  })
  it('ignores a late response for a different archived configuration', async () => {
    let resolveFirst!: (response: unknown) => void
    const other = archiveTemplate(); other.base_experiment_id = 'other'; other.spec.name = 'other-reference'
    other.source_snapshot_sha256 = other.spec.source_snapshot_sha256 = 'd'.repeat(64)
    const fetcher = vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path.endsWith('/reference/template')) return new Promise(resolve => { resolveFirst = resolve })
      return { ok: true, json: async () => path.endsWith('/catalog') ? catalog
        : path.endsWith('/session') ? { configured: true, authenticated: true } : other }
    })
    vi.stubGlobal('fetch', fetcher)
    renderResearch('/research?from=reference', true)
    await waitFor(() => expect(resolveFirst).toBeDefined())
    expect(screen.getByRole('button', { name: '提交集群研究任务' })).toBeDisabled()
    fireEvent.click(screen.getByRole('link', { name: '切换归档' }))
    await screen.findByText('本任务固定来源：' + 'd'.repeat(64))
    await act(async () => { resolveFirst({ ok: true, json: async () => archiveTemplate() }) })
    expect(screen.getByLabelText('实验名称')).toHaveValue('other-reference-vs-lightgbm')
    expect(screen.getByText('本任务固定来源：' + 'd'.repeat(64))).toBeInTheDocument()
  })
})

function archiveTemplate(): ArchivedResearchTemplate {
  return {
    schema_version: 'qtrade.research-template.v1', base_experiment_id: 'reference',
    base_run_id: 'reference-run', base_result_sha256: 'b'.repeat(64), source_revision: 'c'.repeat(40),
    dataset_sha256: 'd'.repeat(64), source_snapshot_sha256: 'a'.repeat(64),
    comparison_protocol: sourceBoundProtocol,
    spec: { ...structuredClone(catalog.default_linear_spec) as ResearchSpec,
      name: 'frozen-reference', topk: 40, sell_cost_bps: 15, seed: 17, source_snapshot_sha256: 'a'.repeat(64) },
    live_trading_enabled: false, external_order_transmission: false, point_in_time: false,
    real_data_paper_readiness: false, promotion: 'NOT_ELIGIBLE',
  }
}

function renderResearch(path: string, switchLink = false) {
  render(<ResearchWorkflowProvider><MemoryRouter initialEntries={[path]}>
    {switchLink && <Link to="/research?from=other">切换归档</Link>}
    <Routes><Route path="/research" element={<QlibResearchPage />} />
      <Route path="/experiments" element={<div>已进入任务状态页</div>} /></Routes>
  </MemoryRouter></ResearchWorkflowProvider>)
}
