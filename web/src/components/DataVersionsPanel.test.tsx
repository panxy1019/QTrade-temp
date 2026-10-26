import { act, fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { DataVersionsPanel, type DataCatalogResponse } from './DataVersionsPanel'

const mocks = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn() }))
vi.mock('../research/qlibWorkflow', () => ({ workflowGet: mocks.get, workflowPost: mocks.post }))
const head = 'a'.repeat(64), epoch = 'b'.repeat(64), target = 'c'.repeat(64)
const response: DataCatalogResponse = { publication_available: true, worker_fresh: true,
  catalog: { head_version_id: head, head_event_id: epoch, total_versions: 2,
    versions_truncated: false, receipts: [], versions: [
      { version_id: head, actual_end: '2026-10-26', admitted_at: '2026-10-26T12:00:00Z',
        is_head: true, current_symbols: 987, historical_symbols: 990, blocked_symbols: 3 },
      { version_id: target, actual_end: '2026-10-23', admitted_at: '2026-10-23T12:00:00Z',
        is_head: false, current_symbols: 985, historical_symbols: 990, blocked_symbols: 5 },
    ] } }
const props = { authenticated: true, ready: true, expectedHead: head, refreshModels: vi.fn(async () => {}) }

beforeEach(() => { mocks.get.mockReset().mockResolvedValue(structuredClone(response)); mocks.post.mockReset(); props.refreshModels.mockClear() })

it('requires login, fresh control, worker and matching head before exposing writes', async () => {
  const { rerender } = render(<DataVersionsPanel {...props} authenticated={false} />)
  expect(await screen.findByRole('button', { name: '审阅数据回退' })).toBeDisabled()
  rerender(<DataVersionsPanel {...props} ready={false} />)
  expect(screen.getByRole('button', { name: '审阅数据回退' })).toBeDisabled()
  rerender(<DataVersionsPanel {...props} expectedHead={'f'.repeat(64)} />)
  expect(screen.getByRole('button', { name: '审阅数据回退' })).toBeDisabled()
  rerender(<DataVersionsPanel {...props} />)
  expect(screen.getByRole('button', { name: '审阅数据回退' })).toBeEnabled()
  mocks.get.mockResolvedValue({ ...response, worker_fresh: false })
  fireEvent.click(screen.getByRole('button', { name: '刷新数据版本' }))
  await waitFor(() => expect(screen.getByRole('button', { name: '审阅数据回退' })).toBeDisabled())
  expect(mocks.post).not.toHaveBeenCalled()
})

it('preserves retry identity and shows queued receipt without declaring success', async () => {
  mocks.post.mockRejectedValueOnce(new Error('queue unavailable')).mockResolvedValueOnce({ state: 'QUEUED' })
  render(<DataVersionsPanel {...props} />)
  fireEvent.click(await screen.findByRole('button', { name: '审阅数据回退' }))
  expect(await screen.findByText('旧数据不会恢复为最新可用预测')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '批准数据回退' }))
  await screen.findByText('Error: queue unavailable')
  await waitFor(() => expect(screen.getByRole('button', { name: '批准数据回退' })).not.toHaveClass('ant-btn-loading'))
  const original = mocks.post.mock.calls[0][1]
  expect(original).toEqual({ request_id: expect.stringMatching(/^[a-f0-9]{32}$/),
    version_id: target, expected_head: head, expected_event_id: epoch, reason: 'DATA_QUALITY_REVIEW' })
  fireEvent.click(screen.getByRole('button', { name: '批准数据回退' }))
  await screen.findByText(/当前版本尚未确认更改，以集群回执为准/)
  expect(mocks.post.mock.calls[1][1]).toEqual(original)
  expect(props.refreshModels).toHaveBeenCalledOnce()
  expect(screen.getByText('当前版本')).toBeInTheDocument()
})

it('invalidates review if the head epoch changes even when version is unchanged', async () => {
  render(<DataVersionsPanel {...props} />)
  fireEvent.click(await screen.findByRole('button', { name: '审阅数据回退' }))
  await screen.findByText('审阅不可变数据版本回退')
  mocks.get.mockResolvedValue({ ...response, catalog: { ...response.catalog, head_event_id: 'd'.repeat(64) } })
  fireEvent.click(screen.getByRole('button', { name: '刷新数据版本' }))
  await screen.findByText('数据版本或事件已变化，请关闭后重新审阅')
  expect(screen.getByRole('button', { name: '批准数据回退' })).toBeDisabled()
  expect(mocks.post).not.toHaveBeenCalled()
})

it('clears unavailable catalog and prevents stale review submission', async () => {
  render(<DataVersionsPanel {...props} />)
  fireEvent.click(await screen.findByRole('button', { name: '审阅数据回退' }))
  mocks.get.mockRejectedValue(new Error('publication unavailable'))
  fireEvent.click(screen.getByRole('button', { name: '刷新数据版本' }))
  await screen.findByText('Error: publication unavailable')
  expect(screen.getByRole('button', { name: '批准数据回退' })).toBeDisabled()
  expect(mocks.post).not.toHaveBeenCalled()
})

it('renders actual rejected receipts and research limitations', async () => {
  mocks.get.mockResolvedValue({ ...response, catalog: { ...response.catalog, receipts: [
    { request_id: 'e'.repeat(32), state: 'REJECTED', at: '2026-10-26T12:00:00Z',
      version_id: target, error_code: 'DATA_ROLLBACK_HEAD_EVENT_CHANGED' },
  ] } })
  render(<DataVersionsPanel {...props} />)
  expect(await screen.findByText('REJECTED')).toBeInTheDocument()
  expect(screen.getByText('DATA_ROLLBACK_HEAD_EVENT_CHANGED')).toBeInTheDocument()
  expect(screen.getByText(/不授予 PAPER\/SHADOW 执行权限/)).toBeInTheDocument()
})

it('creates a new request identity if the reviewed reason changes after failed submission', async () => {
  mocks.post.mockRejectedValue(new Error('queue unavailable'))
  render(<DataVersionsPanel {...props} />)
  fireEvent.click(await screen.findByRole('button', { name: '审阅数据回退' }))
  fireEvent.click(await screen.findByRole('button', { name: '批准数据回退' }))
  await screen.findByText('Error: queue unavailable')
  await waitFor(() => expect(screen.getByRole('button', { name: '批准数据回退' })).not.toHaveClass('ant-btn-loading'))
  const original = mocks.post.mock.calls[0][1]
  fireEvent.mouseDown(screen.getByRole('combobox', { name: '回退原因' }))
  fireEvent.click(await screen.findByText('Provider 回归问题'))
  fireEvent.click(screen.getByRole('button', { name: '批准数据回退' }))
  await waitFor(() => expect(mocks.post).toHaveBeenCalledTimes(2))
  expect(mocks.post.mock.calls[1][1].reason).toBe('PROVIDER_REGRESSION')
  expect(mocks.post.mock.calls[1][1].request_id).not.toBe(original.request_id)
})

it('ignores a delayed older successful fetch after newer publication failure', async () => {
  render(<DataVersionsPanel {...props} />)
  await screen.findByRole('button', { name: '审阅数据回退' })
  let release!: (value: DataCatalogResponse) => void
  mocks.get.mockImplementationOnce(() => new Promise(resolve => { release = resolve }))
  fireEvent.click(screen.getByRole('button', { name: '刷新数据版本' }))
  mocks.get.mockRejectedValueOnce(new Error('new failure'))
  fireEvent.click(screen.getByRole('button', { name: '刷新数据版本' }))
  await screen.findByText('Error: new failure')
  await act(async () => release(response))
  expect(screen.getByText('Error: new failure')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: '审阅数据回退' })).not.toBeInTheDocument()
})
