import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, describe, expect, it, vi } from 'vitest'
import { loadDataQuality } from '../api/client'
import { DataQualitySummary } from './DataQualitySummary'

vi.mock('../api/client', () => ({ loadDataQuality: vi.fn() }))
const fixture = {
  live_trading_enabled: false, read_only: true, stale: false, snapshot_age_seconds: 5,
  source: { commit_sha: 'a'.repeat(40), sha256: 'b'.repeat(64) },
  publication: {
    observed_at: '2026-10-28T02:00:00Z', production_admission_effect: 'NONE',
    execution_admitted: false, case_counts: { SUSPENDED: 1, DATA_GAP: 1, UNKNOWN: 1 },
    source_issues: [],
    recent_status: { available: true, observed_at: '2026-10-28T01:55:00Z',
      window_start: '2026-10-23', window_end: '2026-10-27', content_verified_symbols: 140,
      progress: { completed_symbols: 140, scope_symbols: 990, unresolved_symbols: 850 } },
    cases: [{ symbol: '512580', frequency: '5min', trading_day: '2026-10-27',
      classification: 'DATA_GAP', coverage: 'PARTIAL', original_task_state: 'FAILED',
      source_observed_at: null, reason: 'PARTIAL_PRICE_WINDOW', original_error: 'MINUTE_WINDOW_GAP_REASON_UNKNOWN',
      retry_recommendation: 'KEEP_EXISTING_RETRY' }],
  },
}

describe('independent data quality', () => {
  beforeEach(() => { vi.mocked(loadDataQuality).mockResolvedValue(fixture as never) })
  it('separates window, actual observation and scoped partial data from authority', async () => {
    render(<DataQualitySummary />)
    expect(await screen.findByText('140 / 990；剩余 850')).toBeInTheDocument()
    expect(screen.getByText('2026-10-23 → 2026-10-27')).toBeInTheDocument()
    expect(screen.getByText('512580')).toBeInTheDocument()
    expect(screen.getByText('PARTIAL')).toBeInTheDocument()
    expect(screen.getByText(/模型仍须通过原输入验证/)).toBeInTheDocument()
    expect(screen.getByText('诊断已接入；生产规则变更仍需审批')).toBeInTheDocument()
  })
  it('removes previous quality success after error without depending on the market directory', async () => {
    render(<DataQualitySummary />)
    await screen.findByText('512580')
    vi.mocked(loadDataQuality).mockRejectedValue(new Error('source unavailable'))
    fireEvent.click(screen.getByText('刷新质量摘要'))
    expect(await screen.findByText(/独立质量摘要暂不可用：source unavailable/)).toBeInTheDocument()
    expect(screen.queryByText('512580')).not.toBeInTheDocument()
    expect(screen.getByText(/此摘要失败不会隐藏下方行情目录/)).toBeInTheDocument()
  })
  it('does not present an elevated permission payload as a diagnostic success', async () => {
    vi.mocked(loadDataQuality).mockResolvedValue({ ...fixture, publication: {
      ...fixture.publication, execution_admitted: true,
    } } as never)
    render(<DataQualitySummary />)
    expect(await screen.findByText(/质量摘要权限声明不符/)).toBeInTheDocument()
    expect(screen.queryByText('512580')).not.toBeInTheDocument()
  })
  it('labels old data as stale without treating current retrieval as a new data window', async () => {
    vi.mocked(loadDataQuality).mockResolvedValue({ ...fixture, stale: true } as never)
    render(<DataQualitySummary />)
    expect(await screen.findByText('质量摘要已过期，请核对来源时刻')).toBeInTheDocument()
    expect(screen.getByText('2026-10-23 → 2026-10-27')).toBeInTheDocument()
  })
})
