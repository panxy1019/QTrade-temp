import { cleanup, render, screen } from '@testing-library/react'
import { afterEach, describe, expect, it, vi } from 'vitest'
import { SignalsPage } from './SignalsPage'
import { workflowGet } from '../research/qlibWorkflow'

vi.mock('../research/qlibWorkflow', () => ({ workflowGet: vi.fn() }))
afterEach(() => { cleanup(); vi.resetAllMocks() })
const fixture = { publication_available: true, current_input: true, total: 1,
  manifest: { signal_family_id: 'f'.repeat(64), data_end: '2026-10-26', generated_at: '2026-10-26T15:00:00Z', expected_execution_at: '2026-10-27T01:30:00Z', qualified_instruments: 885, unavailable: ['etf_positive_trend20'], limitations: ['FULL_PIT_NOT_CERTIFIED'], bindings: { data_version_id: 'd'.repeat(64), code_revision: 'c'.repeat(40) } },
  families: [{ name: 'momentum20', score_count: 885, target_count: 50, allocation_method: 'TOPK_EQUAL_WEIGHT' }],
  rows: [{ instrument: 'SH600001', score: 0.12, indicative_weight: '0.019' }] }

describe('daily research signal observations', () => {
  it('shows bounded input scores and indicative weights with execution limits', async () => {
    vi.mocked(workflowGet).mockResolvedValue(fixture)
    render(<SignalsPage />)
    expect(await screen.findByText('SH600001')).toBeInTheDocument()
    expect(screen.getByText('1.900%')).toBeInTheDocument()
    expect(screen.getByText('指示权重（非持仓）')).toBeInTheDocument()
    expect(screen.getByText(/尚未形成已执行持仓/)).toBeInTheDocument()
    expect(workflowGet).toHaveBeenCalledWith('/online/signals?strategy=momentum20&offset=0&limit=50')
  })
  it('keeps expired archives visible with a clear current-input block', async () => {
    vi.mocked(workflowGet).mockResolvedValue({ ...fixture, current_input: false })
    render(<SignalsPage />)
    expect(await screen.findByText('此档案已不符合当前输入或执行时间要求')).toBeInTheDocument()
    expect(screen.getByText('SH600001')).toBeInTheDocument()
  })
  it('does not replace missing publication with sample scores', async () => {
    vi.mocked(workflowGet).mockResolvedValue({ publication_available: false })
    render(<SignalsPage />)
    expect(await screen.findByText('尚无通过校验的信号发布')).toBeInTheDocument()
    expect(screen.queryByText('SH600001')).not.toBeInTheDocument()
  })
})
