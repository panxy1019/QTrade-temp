import { fireEvent, render, screen } from '@testing-library/react'
import { beforeEach, expect, it, vi } from 'vitest'
import { EtfShadowPanel } from './EtfShadowPanel'

const get = vi.hoisted(() => vi.fn())
vi.mock('../research/qlibWorkflow', () => ({ workflowGet: get }))
vi.mock('./JsonExportButton', () => ({ JsonExportButton: () => <button>导出 ETF 前向记录</button> }))
beforeEach(() => get.mockReset())
function publication() {
  return { schema_version: 'qtrade.etf-shadow-service.v1', state: 'RUNNING', heartbeat_at: '2026-10-27T00:00:00Z',
    strategy_id: 'etf_positive_trend20_native_close_rotation_v1', contract_id: 'a'.repeat(64),
    contract: { portfolio: { top_k: 3, risk_degree: '0.95', rebalance_sessions: 1 } }, errors: [], limitations: [],
    entries: [{ target: { target_id: 'b'.repeat(64), frozen_at: '2026-10-27T00:00:00Z', qualified_inputs: 30,
      bindings: { as_of: '2026-10-26', expected_execution_at: '2026-10-27T09:30:00+08:00', diagnostic_id: 'c'.repeat(64) },
      weights: [{ instrument: 'SH510300', weight: '0.95' }] }, observation: null }] }
}

it('shows frozen ETF allocation and pending observation without invented performance', async () => {
  get.mockResolvedValue({ publication_available: true, service_fresh: true, current_target: true, publication: publication() })
  render(<EtfShadowPanel />)
  await screen.findByText('SH510300')
  expect(get).toHaveBeenCalledWith('/online/etf-forward')
  expect(screen.getByText('95.000%')).toBeInTheDocument()
  expect(screen.getByText('尚未取得完整后续收据')).toBeInTheDocument()
  expect(screen.getByText('SHADOW 不计算组合收益')).toBeInTheDocument()
  expect(screen.getByText('ETF 仅作 SHADOW 观察，PAPER 准入仍被阻断')).toBeInTheDocument()
})

it('retains stale history with a warning and separates unallocated target from actual cash', async () => {
  const value = publication()
  value.entries[0].target.weights = []
  get.mockResolvedValue({ publication_available: true, service_fresh: false, current_target: false, publication: value })
  render(<EtfShadowPanel />)
  await screen.findByText('合格 ETF 中无正趋势，目标暂不分配')
  expect(screen.getByText('ETF 观察服务已停止或心跳过期；以下为历史记录')).toBeInTheDocument()
  expect(screen.getByText(/不是实际现金余额或零收益/)).toBeInTheDocument()
})

it('hides unavailable or wrong-schema publications rather than displaying targets', async () => {
  get.mockResolvedValue({ publication_available: true, publication: { ...publication(), schema_version: 'qtrade.forward-service.v1' } })
  render(<EtfShadowPanel />)
  await screen.findByText('尚无通过校验的 ETF SHADOW 发布')
  expect(screen.queryByText('SH510300')).not.toBeInTheDocument()
})

it('clears old targets after refresh failure', async () => {
  get.mockResolvedValueOnce({ publication_available: true, service_fresh: true, current_target: true, publication: publication() })
    .mockRejectedValueOnce(new Error('offline'))
  render(<EtfShadowPanel />)
  await screen.findByText('SH510300')
  fireEvent.click(screen.getByRole('button', { name: '刷新 ETF 目标' }))
  await screen.findByText('ETF 前向记录读取失败')
  expect(screen.queryByText('SH510300')).not.toBeInTheDocument()
})
