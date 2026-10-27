import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { RollingPolicyPanel, type RollingCatalog } from './RollingPolicyPanel'

const post = vi.hoisted(() => vi.fn())
vi.mock('../research/qlibWorkflow', () => ({ workflowPost: post }))
const value: RollingCatalog = { mode: 'OFF', state: 'CONTROL_READY', policy_id: 'a'.repeat(64),
  policy: { mode: 'OFF', custom_sessions: null, model: 'lightgbm', train_sessions: 1260,
    valid_sessions: 126, test_sessions: 60, seed: 20261026 }, tasks: [] }

beforeEach(() => post.mockReset())

it('does not expose a write action without authentication or a ready control', () => {
  const { rerender } = render(<RollingPolicyPanel value={value} ready authenticated={false} refresh={async () => {}} />)
  expect(screen.getByRole('button', { name: '配置训练政策' })).toBeDisabled()
  rerender(<RollingPolicyPanel value={value} ready={false} authenticated refresh={async () => {}} />)
  expect(screen.getByRole('button', { name: '配置训练政策' })).toBeDisabled()
  expect(post).not.toHaveBeenCalled()
})

it('preserves the reviewed policy and retry identity without implicitly enabling training', async () => {
  post.mockRejectedValueOnce(new Error('queue unavailable')).mockResolvedValueOnce({ state: 'QUEUED' })
  const refresh = vi.fn(async () => {})
  render(<RollingPolicyPanel value={value} ready authenticated refresh={refresh} />)
  fireEvent.click(screen.getByRole('button', { name: '配置训练政策' }))
  expect(await screen.findByText('仅创建研究候选，批准政策保持独立')).toBeInTheDocument()
  fireEvent.click(screen.getByRole('button', { name: '保存训练政策' }))
  await screen.findByText('Error: queue unavailable')
  await waitFor(() => expect(screen.getByRole('button', { name: '保存训练政策' })).not.toHaveClass('ant-btn-loading'))
  const first = post.mock.calls[0][1]
  expect(first.policy.mode).toBe('OFF')
  expect(first.expected_policy_id).toBe(value.policy_id)
  fireEvent.click(screen.getByRole('button', { name: '保存训练政策' }))
  await screen.findByText(/训练政策已排队/)
  expect(post.mock.calls[1][1]).toEqual(first)
  expect(refresh).toHaveBeenCalledOnce()
})

it('invalidates the open policy review when its bound revision changes', async () => {
  const props = { value, ready: true, authenticated: true, refresh: async () => {} }
  const { rerender } = render(<RollingPolicyPanel {...props} />)
  fireEvent.click(screen.getByRole('button', { name: '配置训练政策' }))
  await screen.findByText('审阅滚动训练政策')
  rerender(<RollingPolicyPanel {...props} value={{ ...value, policy_id: 'b'.repeat(64) }} />)
  expect(screen.getByRole('button', { name: '保存训练政策' })).toBeDisabled()
  expect(screen.getByText('政策已变化，请关闭后重新审阅')).toBeInTheDocument()
  expect(post).not.toHaveBeenCalled()
})
