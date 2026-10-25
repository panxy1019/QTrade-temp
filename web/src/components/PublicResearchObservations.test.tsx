import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, expect, test, vi } from 'vitest'
import { PublicResearchObservations } from './PublicResearchObservations'
import { loadPublicResearchObservations } from '../api/client'

vi.mock('../api/client', () => ({ loadPublicResearchObservations: vi.fn() }))
const load = vi.mocked(loadPublicResearchObservations)
beforeEach(() => { load.mockReset() })

test('unavailable publication never displays a healthy state', async () => {
  load.mockRejectedValue(new Error('503'))
  render(<PublicResearchObservations />)
  expect(await screen.findByText('发布通道不可用：503')).toBeInTheDocument()
  expect(screen.queryByText('数据与发布通道检查正常')).not.toBeInTheDocument()
})

test('a failed refresh replaces the prior healthy label', async () => {
  load.mockResolvedValue({
    schema_version: 'qtrade.public-observations.v1', external_order_transmission: false,
    healthy: true, published_at: '2026-10-25T08:00:00Z', publication_age_seconds: 10,
    maximum_publication_age_seconds: 1200, worker_alive_at_publication: true,
    streams: { daily: {}, minute: {} }, datasets: { daily: { latest_bars: [] }, minute: { latest_bars: [] } },
    experiments: [],
  } as never)
  render(<PublicResearchObservations mode="monitor" />)
  expect(await screen.findByText('数据与发布通道检查正常')).toBeInTheDocument()
  load.mockRejectedValue(new Error('timeout'))
  fireEvent.click(screen.getByRole('button', { name: '刷新发布状态' }))
  await waitFor(() => expect(screen.queryByText('数据与发布通道检查正常')).not.toBeInTheDocument())
  expect(screen.getByText('发布通道不可用：timeout')).toBeInTheDocument()
})
