import { render, screen } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { StrategyComparisonPanel } from './StrategyComparisonPanel'
const get = vi.hoisted(() => vi.fn())
vi.mock('../research/qlibWorkflow', () => ({ workflowGet: get }))
vi.mock('../charts/ChartRenderer', () => ({ default: () => <div>历史净值图</div> }))
vi.mock('./JsonExportButton', () => ({ JsonExportButton: () => <button>导出历史</button> }))
beforeEach(() => { get.mockReset() })
function result() {
  return { comparison_id: 'a'.repeat(64), signal_start: '2024-01-02', signal_end: '2026-10-23', common_rows: 888,
    bindings: { dataset_sha256: 'b'.repeat(64), code_revision: 'c'.repeat(40), source_snapshot_sha256: 'd'.repeat(64), protocol: { segments: {}, label: 'open label', account: 10000000, benchmark: 'SH000300', portfolio: { topk: 50 }, exchange: { open_cost: .0008 } } },
    limitations: ['FULL_PIT_NOT_CERTIFIED'], strategies: Object.fromEntries(['momentum20', 'reversal5', 'lowvol20', 'multifactor_equal_rank', 'equal_weight_universe', 'ridge', 'lightgbm'].map(id => [id, { allocation: id === 'equal_weight_universe' ? 'EQUAL_WEIGHT_ALL' : 'TOPK_DROPOUT_COMMON_CONFIG', metrics: { ic: null, rank_ic: .02, total_return_net: .1 }, curve: [{ date: '2024-01-02', equity: 1, turnover: 0, cost: 0 }] }])) }
}
it('shows historical evidence and missing metrics without fake zeros', async () => {
  get.mockResolvedValue({ publication_available: true, comparison: result() })
  render(<StrategyComparisonPanel />)
  expect(await screen.findByText('20日动量')).toBeInTheDocument()
  expect(screen.getAllByText('—').length).toBeGreaterThan(0)
  expect(screen.getByText(/全池等权每日调整/)).toBeInTheDocument()
  expect(screen.getByText('历史净值图')).toBeInTheDocument()
  expect(get).toHaveBeenCalledWith('/online/strategy-comparison')
})
it('does not substitute fallback results when publication is unavailable', async () => {
  get.mockResolvedValue({ publication_available: false, comparison: result() })
  render(<StrategyComparisonPanel />)
  await screen.findByText('尚无通过校验的统一历史策略对照')
  expect(screen.queryByText('历史净值图')).not.toBeInTheDocument()
})
it('shows unavailable on request error', async () => {
  get.mockRejectedValue(new Error('history offline'))
  render(<StrategyComparisonPanel />)
  await screen.findByText('Error: history offline')
  expect(screen.queryByText('历史净值图')).not.toBeInTheDocument()
})

