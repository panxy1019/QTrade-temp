import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter, Route, Routes } from 'react-router-dom'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { ResearchWorkflowProvider } from '../components/ResearchWorkflowProvider'
import catalog from '../test/fixtures/qlib-workflow.json'
import { QlibResearchPage, QlibStrategyPage } from './QlibWorkflowPages'

vi.mock('../charts/ChartRenderer', () => ({ default: () => <div /> }))
afterEach(() => { vi.unstubAllGlobals(); sessionStorage.clear() })

function mount(authenticated: boolean, page: 'research' | 'strategies' = 'research') {
  const fetcher = vi.fn(async (input: RequestInfo | URL) => {
    const path = String(input)
    const body = path.endsWith('/catalog') ? catalog
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
})
