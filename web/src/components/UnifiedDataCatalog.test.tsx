import { fireEvent, render, screen, waitFor } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { UnifiedDataCatalog } from './UnifiedDataCatalog'
import { loadUnifiedSecurities, loadUnifiedSummary } from '../api/client'

vi.mock('../api/client', () => ({ loadUnifiedSecurities: vi.fn(), loadUnifiedSummary: vi.fn() }))

const summary = {
  built_at: '2026-10-26T09:00:00Z', snapshot_age_seconds: 10, live_trading_enabled: false,
  master: { target_actual: 1500, target_count: 1500, master_count: 5500, legacy_union_count: 1032,
    legacy_active_retained: 1032, top_up_count: 468, target_by_type: { STOCK: 1470, ETF: 30 },
    universe_version: 'a'.repeat(64), vendor_master_observed_at: '2026-10-25T00:00:00Z',
    frequency_counts: { day: { CONTENT_COVERAGE_VERIFIED: 1000, PLANNED: 500 },
      '5min': { CONTENT_COVERAGE_VERIFIED: 149, QUARANTINED: 1, PLANNED: 1350 } } },
}
const securities = { live_trading_enabled: false, universe_version: 'a'.repeat(64), total: 1500,
  rows: [{ instrument_id: 'SSE.600036', symbol: '600036', name: '招商银行', security_type: 'STOCK',
    status: 'ACTIVE_OBSERVED', legacy_sources: ['daily1000'], source: [], issues: [],
    listing_date: '2002-04-09', delisting_date: null,
    frequencies: { day: { state: 'CONTENT_COVERAGE_VERIFIED', issues: [], observed_start: '2016-10-24', observed_end: '2026-10-23', bar_count: 2400 },
      '5min': { state: 'QUARANTINED', issues: ['STALE_END'], observed_start: '2023-10-24', observed_end: '2025-11-14', bar_count: 24000 } } }] }

describe('unified observed data', () => {
  beforeEach(() => {
    vi.mocked(loadUnifiedSummary).mockResolvedValue(summary as never)
    vi.mocked(loadUnifiedSecurities).mockResolvedValue(securities as never)
  })
  it('separates target and accepted coverage and queries symbols on the server', async () => {
    render(<UnifiedDataCatalog />)
    expect(await screen.findByText('观察目标 1500 / 1500 只独立证券')).toBeInTheDocument()
    expect(screen.getByText(/股票 1470/)).toBeInTheDocument()
    expect(screen.getByText(/内容与覆盖通过：1000/)).toBeInTheDocument()
    expect(screen.getByText(/当前观察池有幸存者/)).toBeInTheDocument()
    const input = screen.getByLabelText('查询统一证券')
    fireEvent.change(input, { target: { value: '600036' } })
    fireEvent.keyDown(input, { key: 'Enter', code: 'Enter' })
    await waitFor(() => expect(loadUnifiedSecurities).toHaveBeenLastCalledWith(
      expect.objectContaining({ query: '600036', offset: 0 })))
  })
  it('rejects mismatched versions and hides previous success after refresh failure', async () => {
    render(<UnifiedDataCatalog />)
    await screen.findByText('SSE.600036 招商银行')
    vi.mocked(loadUnifiedSecurities).mockResolvedValue({ ...securities, universe_version: 'b'.repeat(64) } as never)
    fireEvent.click(screen.getByText('刷新统一目录'))
    expect(await screen.findByText(/统一目录不可用：证券池身份变化/)).toBeInTheDocument()
    expect(screen.queryByText('SSE.600036 招商银行')).not.toBeInTheDocument()
  })
})
