import { render, screen } from '@testing-library/react'
import { describe, expect, it, vi } from 'vitest'
import type { ReservedTailDiagnosticReport } from '../types'
import { MinuteBacktestChart } from './MinuteBacktestChart'
import { ReservedTailChart } from './ReservedTailChart'

vi.mock('./ChartRenderer', () => ({
  default: ({ option }: { option: { xAxis: { data: string[] } } }) => (
    <div>{option.xAxis.data.join('|')}</div>
  ),
}))

describe('minute backtest timestamps', () => {
  it('renders UTC evidence timestamps as China market time', async () => {
    render(<MinuteBacktestChart report={{ curve: [
      { timestamp: '2026-08-14T01:35:00+00:00', base_equity: 1, overlay_equity: 1 },
      { timestamp: '2026-08-14T07:00:00+00:00', base_equity: 1, overlay_equity: 1 },
    ] }} />)
    expect(await screen.findByText('08-14 09:35|08-14 15:00')).toBeInTheDocument()
  })

  it('renders reserved-tail evidence on the same China market clock', async () => {
    const report = {
      curve: [{
        timestamp: '2026-08-25T01:35:00+00:00',
        base_equity: 1,
        candidate_equity: 1,
        comparator_equity: 1,
      }],
    } as unknown as ReservedTailDiagnosticReport
    render(<ReservedTailChart report={report} />)
    expect(await screen.findByText('08-25 09:35')).toBeInTheDocument()
  })
})
