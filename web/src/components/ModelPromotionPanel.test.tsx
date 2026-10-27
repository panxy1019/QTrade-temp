import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ModelPromotionPanel } from './ModelPromotionPanel'

afterEach(() => vi.unstubAllGlobals())

it('queues an explicit bounded policy and retains a failed request identity', async () => {
  const refresh = vi.fn(async () => {})
  const fetcher = vi.fn().mockRejectedValueOnce(new Error('test-only lost response'))
    .mockResolvedValue({ ok: true, status: 202, json: async () => ({ state: 'QUEUED' }) })
  vi.stubGlobal('fetch', fetcher)
  render(<ModelPromotionPanel value={{ policy_id: 'a'.repeat(64),
    policy: { mode: 'OFF', allowed_models: [], allowed_model_ids: [] } }}
    authenticated ready refresh={refresh} />)
  fireEvent.click(screen.getByRole('button', { name: '启用 LightGBM 自动批准' }))
  await screen.findByText(/test-only lost response/)
  fireEvent.click(screen.getByRole('button', { name: '启用 LightGBM 自动批准' }))
  await waitFor(() => expect(refresh).toHaveBeenCalledTimes(1))
  const first = JSON.parse(fetcher.mock.calls[0][1].body)
  expect(JSON.parse(fetcher.mock.calls[1][1].body)).toEqual(first)
  expect(first.policy).toEqual({ mode: 'ENABLED', allowed_models: ['lightgbm'], allowed_model_ids: [] })
  expect(first.explicit_human_authorization).toBe(true)
  expect(first.expected_policy_id).toBe('a'.repeat(64))
})

it('disables controls for anonymous or unverified policy observations', () => {
  render(<ModelPromotionPanel authenticated={false} ready={false} refresh={async () => {}} />)
  expect(screen.getByRole('button', { name: '启用 LightGBM 自动批准' })).toBeDisabled()
  expect(screen.getByRole('button', { name: '关闭自动批准' })).toBeDisabled()
})
