import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { loadDeployment, loadUnifiedSummary } from '../api/client'
import { workflowGet } from '../research/qlibWorkflow'
import { unifiedSummary } from '../test/unifiedFixtures'
import type { PlatformSnapshot } from '../types'
import { OverviewPage } from './OverviewPage'

vi.mock('../api/client', () => ({ loadUnifiedSummary: vi.fn(), loadDeployment: vi.fn() }))
vi.mock('../research/qlibWorkflow', () => ({ workflowGet: vi.fn() }))
vi.mock('../components/ResearchWorkflowProvider', () => ({ useResearchWorkflow: () => ({ catalog: null, error: null }) }))

function onlineCatalog() {
  return { publication_available: true, worker_fresh: true, active_model: 'approved-model', models: [],
    data_version: { version_id: 'data-version', actual_end: '2026-10-26', current_symbols: 987 },
    published_at: new Date().toISOString(), expected_complete_day: '2026-10-26', current_prediction_usable: true,
    data_schedule: { state: 'WAITING_FOR_CURRENT_COLLECTION' }, auto_predict: { current_prediction_id: 'prediction-id',
      predictions: [{ prediction_id: 'prediction-id', state: 'COMPLETE', bindings: { model_id: 'approved-model', data_version_id: 'data-version', data_end: '2026-10-26' },
        contract: { generated_at: new Date().toISOString(), expected_execution_at: new Date(Date.now() + 3600_000).toISOString() } }] } }
}
function forwardReply() {
  return { publication_available: true, service_fresh: true, publication: { schema_version: 'qtrade.forward-service.v1',
    live_trading_enabled: false, external_order_transmission: false, state: 'RUNNING',
    heartbeat_at: new Date().toISOString(), generated_at: new Date().toISOString(), source_revision: 'forward-code',
    books: [{ strategy_id: 'model-paper', mode: 'PAPER', risk_state: 'HALTED', recovery_required: true, running: false,
      frozen_targets: [{ frozen_at: '2026-10-26T17:00:00Z' }], sessions: [] },
    { strategy_id: 'model-shadow', mode: 'SHADOW', risk_state: 'NORMAL', recovery_required: false, running: true,
      frozen_targets: [], sessions: [] }] } }
}
let online: ReturnType<typeof onlineCatalog>
let forward: ReturnType<typeof forwardReply>
function mount() {
  const legacy = { account: { cash: '999999' }, pnl: { realized: '9000' }, backtestAnalytics: { curve: [{ equity: 999 }] } } as unknown as PlatformSnapshot
  return render(<MemoryRouter><OverviewPage data={legacy} publicNode /></MemoryRouter>)
}
describe('current business overview', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    online = onlineCatalog(); forward = forwardReply()
    vi.mocked(loadUnifiedSummary).mockResolvedValue(unifiedSummary())
    vi.mocked(loadDeployment).mockResolvedValue({ git_commit_sha: 'web-code' } as never)
    vi.mocked(workflowGet).mockImplementation(async path => (path === '/online/catalog' ? online
      : path === '/online/forward-books' ? forward : { publication_available: false, publication: null }) as never)
  })
  afterEach(() => vi.useRealTimers())
  it('separates actual target coverage, Qlib scope, frozen books and legacy history', async () => {
    mount()
    expect(await screen.findByText('当前研究预测有效')).toBeInTheDocument()
    expect(screen.getByText('1497 / 1500 · PARTIAL')).toBeInTheDocument()
    expect(screen.getByText('0 / 1500 · PARTIAL')).toBeInTheDocument()
    expect(screen.getByText('987')).toBeInTheDocument()
    expect(screen.getByText('HALTED · 需人工恢复 · 停止')).toBeInTheDocument()
    expect(screen.getByText('NORMAL · 仅观察／不启用交易运行时')).toBeInTheDocument()
    expect(screen.getByText('未发生；净值尚无可报告值')).toBeInTheDocument()
    expect(screen.getByText('未发生；仅目标与行情')).toBeInTheDocument()
    expect(screen.queryByText('999999')).not.toBeInTheDocument()
    expect(screen.queryByText('PAPER 现金')).not.toBeInTheDocument()
    expect(screen.queryByText(/历史研究曲线/)).not.toBeInTheDocument()
    expect(screen.getByRole('link', { name: '已完成历史回测对照' })).toHaveAttribute('href', '/backtest')
    expect(screen.getByText('web-code')).toBeInTheDocument()
  })
  it('does not treat mismatched prediction identities as current', async () => {
    online.auto_predict.predictions[0].bindings.model_id = 'previous-model'
    mount()
    expect(await screen.findByText('当前研究预测不可用')).toBeInTheDocument()
    expect(screen.queryByText('当前研究预测有效')).not.toBeInTheDocument()
  })
  it('expires prediction and forward freshness even while a cached API says fresh', async () => {
    vi.useFakeTimers()
    online = onlineCatalog(); forward = forwardReply()
    online.auto_predict.predictions[0].contract.expected_execution_at = new Date(Date.now() + 1000).toISOString()
    forward.publication.heartbeat_at = new Date(Date.now() - 719_000).toISOString()
    await act(async () => { mount() })
    expect(screen.getByText('当前研究预测有效')).toBeInTheDocument()
    await act(async () => { await vi.advanceTimersByTimeAsync(2000) })
    expect(screen.getByText('当前研究预测不可用')).toBeInTheDocument()
    expect(screen.getByText(/股票前向 RUNNING · 心跳过期，仅历史记录/)).toBeInTheDocument()
  })
  it('clears a failed source independently and cancels page requests on unmount', async () => {
    const view = mount()
    await screen.findByText('当前研究预测有效')
    vi.mocked(workflowGet).mockImplementation(async path => {
      if (path === '/online/catalog') throw new Error('503')
      return { publication_available: false, publication: null } as never
    })
    fireEvent.click(screen.getByText('刷新业务状态'))
    expect(await screen.findByText('读取失败：503')).toBeInTheDocument()
    expect(screen.queryByText('prediction-id')).not.toBeInTheDocument()
    expect(screen.getByText('1497 / 1500 · PARTIAL')).toBeInTheDocument()
    const signal = vi.mocked(workflowGet).mock.calls.at(-1)?.[1]?.signal
    view.unmount()
    await waitFor(() => expect(signal?.aborted).toBe(true))
  })
})
