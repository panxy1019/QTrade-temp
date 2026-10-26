import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { afterEach, expect, it, vi } from 'vitest'
import { ResearchWorkflowProvider } from '../components/ResearchWorkflowProvider'
import workflow from '../test/fixtures/qlib-workflow.json'
import { OnlineModelsPage } from './OnlineModelsPage'

afterEach(() => vi.unstubAllGlobals())

function catalog() {
  return {
    publication_available: true, worker_fresh: true, active_model: null,
    expected_complete_day: '2026-10-26',
    data_version: { version_id: 'd'.repeat(64), actual_end: '2026-10-26', current_symbols: 982, historical_symbols: 990, blocked_symbols: ['002827'] },
    models: [{ state: 'STAGED', definition: { model_id: 'a'.repeat(64), model_run_id: 'frozen-ridge', name: 'Alpha158 + Ridge',
      training_data_end: '2026-10-23', training_code_revision: 'b'.repeat(40), feature_count: 158,
      limitations: ['Current universe has survivor bias'], portfolio: { topk: 50, n_drop: 5 },
      segments: { test: ['2024-01-02', '2026-10-23'] },
      cost_assumptions: { open_cost: 0.0008, close_cost: 0.0013, min_cost: 5, impact_cost: 0, deal_price: 'open' },
      metrics: { ic: 0.0193, rank_ic: 0.0469, annual_return_net: 0.2671, sharpe_net: 1.24, max_drawdown: -0.32, mean_daily_turnover: 0.1759 },
    } }],
    validations: [{ validation_id: '1'.repeat(64), model_bundle_sha256: 'a'.repeat(64), score_count: 888,
      contract: { data_version_id: 'd'.repeat(64), data_end: '2026-10-26', generated_at: new Date().toISOString(), expected_execution_at: new Date(Date.now() + 3600000).toISOString() } }],
    commands: [], auto_predict: { enabled: false }, auto_train: { mode: 'OFF' },
  }
}

function mount(authenticated = true) {
  let value = catalog(), failed = false
  const posts: Record<string, unknown>[] = []
  const fetcher = vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
    const path = String(input)
    if (path.endsWith('/actions')) {
      posts.push(JSON.parse(String(init?.body)))
      return { ok: !failed, status: failed ? 503 : 202, json: async () => failed ? { detail: '队列暂不可用，保留原请求重试' } : { state: 'QUEUED' } }
    }
    return { ok: true, status: 200, json: async () => path.endsWith('/online/catalog') ? value
      : path.endsWith('/session') ? { configured: true, authenticated, user: authenticated ? 'test' : null } : workflow }
  })
  vi.stubGlobal('fetch', fetcher)
  render(<ResearchWorkflowProvider><MemoryRouter><OnlineModelsPage /></MemoryRouter></ResearchWorkflowProvider>)
  return { posts, fetcher, update: (next: ReturnType<typeof catalog>) => { value = next }, fail: (next: boolean) => { failed = next } }
}

it('shows qualified scope separately from candidate scores and prevents anonymous control', async () => {
  const state = mount(false)
  await screen.findByText('982 只当日合格 / 990 只历史保留')
  expect(screen.getByText('2026-10-26 · 888 只策略合格证券')).toBeInTheDocument()
  expect(screen.getByText('每日自动预测：未启用；自动重训：OFF')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '审阅激活' })).toBeDisabled()
  expect(state.posts).toHaveLength(0)
})

it('requires review of frozen evidence and preserves the request identity on queue failure', async () => {
  const state = mount()
  const review = await screen.findByRole('button', { name: '审阅激活' })
  await waitFor(() => expect(review).toBeEnabled())
  fireEvent.click(review)
  await screen.findByText('历史样本外区间')
  expect(screen.getByText('2024-01-02 至 2026-10-23')).toBeInTheDocument()
  expect(screen.getByText(/买入 8 bp，卖出 13 bp/)).toBeInTheDocument()
  expect(state.posts).toHaveLength(0)
  state.fail(true)
  fireEvent.click(screen.getByRole('button', { name: '批准并提交' }))
  await screen.findByText(/队列暂不可用/)
  state.fail(false)
  fireEvent.click(screen.getByRole('button', { name: '批准并提交' }))
  await screen.findByText(/操作已排队/)
  expect(state.posts).toHaveLength(2)
  expect(state.posts[1]).toEqual(state.posts[0])
  expect(state.posts[0]).toMatchObject({ operation: 'ACTIVATE', expected_active: null, data_version_id: 'd'.repeat(64), validation_id: '1'.repeat(64) })
  expect(state.posts[0]).not.toHaveProperty('actor')
})

it('disables an open approval when its data version changes', async () => {
  const state = mount()
  const review = await screen.findByRole('button', { name: '审阅激活' })
  await waitFor(() => expect(review).toBeEnabled())
  fireEvent.click(review)
  const changed = catalog()
  changed.data_version.version_id = 'e'.repeat(64)
  state.update(changed)
  fireEvent.click(screen.getByRole('button', { name: '刷新模型目录' }))
  await screen.findByText('目录已变化，请关闭并重新审阅')
  expect(screen.getByRole('button', { name: '批准并提交' })).toBeDisabled()
  expect(state.posts).toHaveLength(0)
})

it('ignores older in-flight catalog responses after a newer refresh', async () => {
  const state = mount()
  await screen.findByText('982 只当日合格 / 990 只历史保留')
  let release!: (value: unknown) => void
  state.fetcher.mockImplementationOnce(() => new Promise(resolve => { release = resolve as typeof release }))
  fireEvent.click(screen.getByRole('button', { name: '刷新模型目录' }))
  const changed = catalog()
  changed.worker_fresh = false
  state.update(changed)
  fireEvent.click(screen.getByRole('button', { name: '刷新模型目录' }))
  await screen.findByText('模型控制心跳已过期，操作入口关闭')
  await act(async () => release({ ok: true, status: 200, json: async () => catalog() }))
  expect(screen.getByText('模型控制心跳已过期，操作入口关闭')).toBeInTheDocument()
  expect(screen.getByRole('button', { name: '审阅激活' })).toBeDisabled()
})

it('distinguishes enabled daily inference from having an approved current prediction', async () => {
  const state = mount()
  await screen.findByText('982 只当日合格 / 990 只历史保留')
  const next = { ...catalog(), auto_predict: { enabled: true, state: 'WAITING_FOR_APPROVAL', predictions: [], current_prediction_id: null } }
  state.update(next)
  fireEvent.click(screen.getByRole('button', { name: '刷新模型目录' }))
  await screen.findByText('每日自动预测记录')
  expect(screen.getByText(/等待人工批准模型。当前没有可使用的研究预测/)).toBeInTheDocument()
  expect(state.posts).toHaveLength(0)
})

it('shows a historical data gap even while the version scheduler is alive', async () => {
  const state = mount()
  await screen.findByText('982 只当日合格 / 990 只历史保留')
  const next = { ...catalog(), data_schedule: { state: 'WAITING', fresh: true, last_cycle_state: 'BLOCKED_MISSED_SESSION_REQUIRES_BACKFILL', required_day: '2026-10-26', data_end: '2026-10-22' } }
  state.update(next)
  fireEvent.click(screen.getByRole('button', { name: '刷新模型目录' }))
  await screen.findByText('日版本调度：存在断档，需要补齐后再推进')
  expect(screen.getByText(/运行中的调度进程不等于当天数据已经验收/)).toBeInTheDocument()
})
