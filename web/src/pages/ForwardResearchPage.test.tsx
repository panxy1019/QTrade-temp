import { fireEvent, render, screen } from '@testing-library/react'
import { afterEach, expect, it, vi } from 'vitest'
import { ForwardResearchPage } from './ForwardResearchPage'

afterEach(() => vi.unstubAllGlobals())

function publication() {
  return {
    schema_version: 'qtrade.forward-service.v1', generated_at: '2026-10-27T08:10:00+00:00',
    heartbeat_at: '2026-10-27T08:10:00+00:00', source_revision: 'c'.repeat(40), state: 'RUNNING',
    live_trading_enabled: false, external_order_transmission: false,
    books: [{
      strategy_id: 'momentum20', mode: 'PAPER', risk_state: 'HALTED', running: false, recovery_required: true,
      target_count: 1, archive: { kind: 'signal-family', artifact_id: 'a'.repeat(64), manifest_sha256: 'b'.repeat(64), model_id: null },
      frozen_targets: [{ target_id: 't'.repeat(64), signal_id: 'a'.repeat(64), data_version: 'd'.repeat(64), source_revision: 'c'.repeat(40),
        generated_at: '2026-10-26T09:01:00+00:00', execution_at: '2026-10-27T09:30:00+08:00', frozen_at: '2026-10-26T17:00:00+00:00',
        weight_count: 1, weights: [{ instrument: 'SH600036', weight: '0.5' }], weights_truncated: false }],
      sessions: [{ day: '2026-10-27', receipt_state: 'COMPLETE', state: 'PAPER_OBSERVED', target_id: 't'.repeat(64),
        observed_at: '2026-10-27T08:00:00+00:00', signal_id: 'a'.repeat(64), data_version: 'd'.repeat(64), market_evidence: 'e'.repeat(64), calendar_evidence: 'f'.repeat(64),
        fill_count: 1, fills: [{ symbol: 'SH600036', side: 'BUY', quantity: 500, price: '10', fee: '5', timestamp: '2026-10-27T08:00:00+00:00' }], fills_truncated: false,
        holding_count: 1, holdings: [{ instrument: 'SH600036', quantity: 500 }], holdings_truncated: false,
        order_count: 1, orders: [{ symbol: 'SH600036', side: 'BUY', quantity: 500, status: 'FILLED', reason: null }], orders_truncated: false,
        blocked_count: 1, blockers: [{ instrument: 'SZ000001', reason: 'UNKNOWN_TRADING_CONSTRAINTS' }], blockers_truncated: false,
        blocked_reasons: { UNKNOWN_TRADING_CONSTRAINTS: 1 }, cash: '4995.00', net_asset_value: '10495.00', forward_return: '0.0495', turnover: '0.5',
        limitations: ['合成网页测试；不是实际前向实绩'],
      }],
    }], errors: [], limitations: ['完整历史 PIT 尚未认证'],
  }
}

function serve(value: ReturnType<typeof publication> | null, fresh = true, available = true) {
  const fetcher = vi.fn(async () => ({ ok: true, json: async () => ({ publication_available: available, service_fresh: fresh, publication: value }) }))
  vi.stubGlobal('fetch', fetcher)
  return fetcher
}

it('shows prospective target timestamps and keeps paper recovery explicit without write controls', async () => {
  const fetcher = serve(publication())
  render(<ForwardResearchPage />)
  await screen.findByText('前向服务：RUNNING · 心跳有效')
  expect(screen.getByText('2026-10-26T17:00:00+00:00')).toBeInTheDocument()
  expect(screen.getByText('2026-10-27T09:30:00+08:00')).toBeInTheDocument()
  expect(screen.getByText('PAPER 已停止接收新风险；需要人工对账与显式恢复')).toBeInTheDocument()
  expect(screen.getByText('LIVE_TRADING_ENABLED=false')).toBeInTheDocument()
  expect(screen.queryByRole('button', { name: /恢复|下单/ })).not.toBeInTheDocument()
  expect(fetcher).toHaveBeenCalledWith('/api/research/online/forward-books', { cache: 'no-store', credentials: 'same-origin' })
  expect(fetcher).toHaveBeenCalledWith('/api/research/online/etf-forward', { cache: 'no-store', credentials: 'same-origin' })
})

it('positions route opens actual holdings rather than target weights', async () => {
  serve(publication())
  render(<ForwardResearchPage focus="holdings" />)
  await screen.findByText('实际模拟持仓数量')
  expect(screen.getByRole('heading', { name: '模拟持仓与资金' })).toBeInTheDocument()
  expect(screen.getByText('500')).toBeInTheDocument()
  expect(screen.queryByText('冻结目标权重')).not.toBeInTheDocument()
  expect(screen.getByText('持仓来自已经完成的模拟成交与账本，不用目标权重代替。')).toBeInTheDocument()
})

it('orders route shows bounded actual simulated orders and fills with processing clocks', async () => {
  const value = publication()
  value.books[0].sessions[0].fill_count = 73
  value.books[0].sessions[0].fills_truncated = true
  serve(value)
  render(<ForwardResearchPage focus="fills" />)
  await screen.findByText('FILLED')
  expect(screen.getByRole('heading', { name: '模拟订单与成交' })).toBeInTheDocument()
  expect(screen.getByText('成交：展示 1 / 共 73 条；其余记录保留在完整账本')).toBeInTheDocument()
  expect(screen.getByRole('columnheader', { name: '实际处理时间' })).toBeInTheDocument()
  expect(screen.getByRole('columnheader', { name: '模拟成交价' })).toBeInTheDocument()
})

it('risk route shows actual constraint blockers and stale service warning', async () => {
  serve(publication(), false)
  render(<ForwardResearchPage focus="risk" />)
  await screen.findByText('前向服务心跳已过期或停止；以下仅为最后一次发布')
  expect(screen.getAllByText('ST、停牌、精确涨跌停或公司行动状态未知')).toHaveLength(2)
  expect(screen.getByText('SZ000001')).toBeInTheDocument()
  expect(screen.getByText('完整历史 PIT 尚未认证')).toBeInTheDocument()
})

it('keeps absent future sessions and all shadow metrics unreported', async () => {
  const value = publication()
  value.books[0].mode = 'SHADOW'
  value.books[0].recovery_required = false
  value.books[0].sessions = []
  serve(value)
  render(<ForwardResearchPage focus="holdings" />)
  await screen.findByText('SHADOW 不建立持仓或现金账户。')
  expect(screen.getAllByText('尚无可报告值').length).toBeGreaterThanOrEqual(4)
  expect(screen.queryByText('0.00%')).not.toBeInTheDocument()
  expect(screen.queryByText('500')).not.toBeInTheDocument()
})

it('unavailable or failed publication never falls back to previous or sample holdings', async () => {
  serve(publication(), false, false)
  render(<ForwardResearchPage focus="holdings" />)
  await screen.findByText('尚无通过校验的前向账本发布；不展示示例持仓或收益。')
  expect(screen.queryByText('SH600036')).not.toBeInTheDocument()
  vi.stubGlobal('fetch', vi.fn(async () => { throw new Error('network') }))
  fireEvent.click(screen.getByRole('button', { name: '刷新前向账本' }))
  await screen.findByText('前向账本读取失败；当前没有可展示的已校验发布。')
  expect(screen.queryByText('SH600036')).not.toBeInTheDocument()
})
