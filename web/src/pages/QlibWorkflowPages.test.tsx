import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ResearchWorkflowProvider } from '../components/ResearchWorkflowProvider'
import catalog from '../test/fixtures/qlib-workflow.json'
import { QlibBacktestPage, QlibResearchPage, QlibStrategyPage } from './QlibWorkflowPages'

vi.mock('../charts/ChartRenderer', () => ({ default: () => <div /> }))
afterEach(() => { vi.unstubAllGlobals(); sessionStorage.clear() })

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
      metrics: {}, curve: [], spec: catalog.default_spec,
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
})
