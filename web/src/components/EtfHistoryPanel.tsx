import { useEffect, useState } from 'react'
import { Alert, Button, Card, Descriptions, Empty, Select, Space, Table, Typography } from 'antd'
import ChartRenderer from '../charts/ChartRenderer'
import { workflowGet } from '../research/qlibWorkflow'
import { JsonExportButton } from './JsonExportButton'

interface LedgerRow {
  session: string; phase: string; cash: string; net_asset_value: string; drawdown: string
  assumed_fees: string; assumed_slippage_cost: string; turnover_previous_nav: string
  holdings: { symbol: string; quantity: number; market_value: string }[]
}
interface HistoryPublication {
  report: {
    simulation_id: string; generated_at: string; limitations: string[]
    assumptions: { initial_cash: string; lot_size: number; transaction_cost_rate: string; slippage_rate: string }
    bindings: { cohort: string[]; warmup_sessions: string[]; evaluation_sessions: string[]; implementation_revision: string }
    strategies: Record<string, Record<string, string>>
  }
  artifacts: Record<string, { records: LedgerRow[] }>
}
const names: Record<string, string> = {
  positive20_top3_daily: '20日正动量 · 每日前3等权',
  fixed_pool_equal_weight_daily: '固定全池 · 每日等权',
}
const limitations: Record<string, string> = {
  ASSUMPTION_BASED_HISTORICAL_SIMULATION_NOT_PAPER_OR_FORWARD_EVIDENCE: '历史模拟使用明确假设，不是 PAPER 或真实前向记录',
  CURRENT_DIAGNOSTIC_COHORT_NOT_HISTORICAL_PIT_MEMBERSHIP: '固定当前 ETF 池，历史时点成员资格未认证',
  SINA_NATIVE_PRICES_ADJUSTMENT_AND_DISTRIBUTIONS_SPLITS_NOT_RECONCILED: '原生价格的复权、分红与拆分尚未核对',
  LOT_FEE_SLIPPAGE_AND_NEXT_OPEN_FILL_RULES_ARE_SIMULATION_ASSUMPTIONS: '手数、费用、滑点与下一开盘成交规则均为模拟假设',
  NO_CAPACITY_PARTICIPATION_SPREAD_LIMIT_SUSPENSION_OR_SETTLEMENT_ENFORCEMENT: '未执行真实容量、参与率、价差、涨跌停、停牌或结算约束',
  LONG_ONLY_NO_BORROWING_CASH_INTEREST_ZERO_NO_TERMINAL_LIQUIDATION: '仅做多、不借贷、现金利息为零、期末不强制清仓',
  RETROSPECTIVELY_CAPTURED_HISTORY_HAS_NO_HISTORICAL_AVAILABLE_AT_PROOF: '数据为事后采集，无法证明历史时点的信息可得性',
  SHORT_COMMON_WINDOW_ANNUALIZED_METRICS_ARE_DESCRIPTIVE_NOT_CERTIFICATION: '共同评价窗口较短，年化指标不能证明长期有效性',
  NO_LEGACY_STITCHING_NO_FILL_FORWARD_NO_AUTOMATIC_SHADOW_METRIC_UPDATE: '未拼接旧数据或前填缺失值；不会自动改写 SHADOW 指标',
}
function number(value: string | undefined, percent = false) {
  if (value == null || !Number.isFinite(Number(value))) return '—'
  return percent ? `${(Number(value) * 100).toFixed(2)}%` : Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}
export function EtfHistoryPanel() {
  const [value, setValue] = useState<HistoryPublication | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [revision, setRevision] = useState(0)
  const [selected, setSelected] = useState('positive20_top3_daily')
  useEffect(() => {
    let active = true
    const controller = new AbortController()
    const timer = window.setTimeout(() => { controller.abort(); if (active) { setValue(null); setError('历史对照读取超时，请重试'); setLoading(false) } }, 15000)
    void workflowGet<{ publication_available: boolean; comparison: HistoryPublication | null }>('/online/etf-history', { signal: controller.signal })
      .then(reply => { if (active && !controller.signal.aborted) { setValue(reply.publication_available ? reply.comparison : null); setError(null) } })
      .catch(() => { if (active) { setValue(null); setError('历史对照读取失败，请重试') } })
      .finally(() => { window.clearTimeout(timer); if (active) setLoading(false) })
    return () => { active = false; controller.abort(); window.clearTimeout(timer) }
  }, [revision])
  const report = value?.report
  const ledger = value?.artifacts[`${selected}/ledger.json`]?.records ?? []
  const evaluation = ledger.filter(row => row.phase === 'EVALUATION')
  return <Card title="ETF 同池历史组合模拟" extra={<Button onClick={() => { setLoading(true); setRevision(n => n + 1) }}>刷新 ETF 历史对照</Button>} loading={loading}>
    <Space orientation="vertical" style={{ width: '100%' }}>
      <Typography.Paragraph>两种固定规则在同一 ETF 池、相同日期与成本假设下比较；前一日收盘决策、下一交易日开盘模拟成交。此处为历史组合模拟，与股票模型对照及真实 SHADOW 观察分开。</Typography.Paragraph>
      {error && <Alert type="error" title={error} />}
      {!value && !loading && <Empty description="尚无通过校验的 ETF 历史组合模拟" />}
      {report && <>
        <Alert type="warning" showIcon title="假设驱动的历史模拟，不构成 PAPER 准入" description="当前证券池非历史 PIT；原生价格的复权与分红拆分尚未认证。手数、费用及滑点属于模拟假设；未模拟真实停牌、涨跌停、容量与结算限制。短区间不能支持稳健性结论。" />
        <Descriptions size="small" column={2} items={[
          { key: 'pool', label: '固定共同 ETF 池', children: `${report.bindings.cohort.length} 只；不拼接旧数据、不前填缺失值` },
          { key: 'warmup', label: '共同暖启动', children: `${report.bindings.warmup_sessions.length} 个收盘会话` },
          { key: 'range', label: '评价区间', children: `${report.bindings.evaluation_sessions[0]} — ${report.bindings.evaluation_sessions.at(-1)}（${report.bindings.evaluation_sessions.length} 日）` },
          { key: 'cash', label: '初始模拟资金', children: number(report.assumptions.initial_cash) },
          { key: 'cost', label: '每边假设费率／滑点', children: `${number(report.assumptions.transaction_cost_rate, true)}／${number(report.assumptions.slippage_rate, true)}` },
          { key: 'lot', label: '假设交易单位', children: `${report.assumptions.lot_size} 份；两组均为95%目标预算` },
        ]} />
        <Table rowKey="id" pagination={false} scroll={{ x: 850 }} dataSource={Object.entries(report.strategies).map(([id, metrics]) => ({ id, metrics }))} columns={[
          { title: '策略', render: (_, row) => names[row.id] ?? row.id },
          { title: '假设成本后累计收益', render: (_, row) => number(row.metrics.total_return, true) },
          { title: '最大回撤', render: (_, row) => number(row.metrics.maximum_drawdown, true) },
          { title: '模拟成交笔数', render: (_, row) => number(row.metrics.number_of_trades) },
          { title: '累计假设费用', render: (_, row) => number(row.metrics.total_assumed_fees) },
          { title: '期末模拟资产', render: (_, row) => number(row.metrics.terminal_net_asset_value) },
        ]} />
        <Typography.Paragraph>等权基准覆盖固定全池，不限于正动量证券；差异同时来自信号选择与组合配置。最终收盘信号没有下一开盘数据时不成交；期末持仓按收盘估值，不强制清仓。现金利息为零。</Typography.Paragraph>
        <Select aria-label="选择ETF历史模拟策略" value={selected} onChange={setSelected} style={{ width: 310 }} options={Object.entries(names).map(([value, label]) => ({ value, label }))} />
        <ChartRenderer style={{ height: 300 }} option={{ animation: false, tooltip: { trigger: 'axis' }, xAxis: { type: 'category', data: evaluation.map(row => row.session) }, yAxis: { type: 'value', scale: true }, series: [{ name: '假设成本后模拟资产', type: 'line', showSymbol: false, data: evaluation.map(row => Number(row.net_asset_value)) }] }} />
        <Table rowKey="session" size="small" dataSource={evaluation} pagination={{ pageSize: 8 }} scroll={{ x: 800 }} columns={[
          { title: '日期', dataIndex: 'session' },
          { title: '模拟现金', render: (_, row) => number(row.cash) },
          { title: '模拟资产', render: (_, row) => number(row.net_asset_value) },
          { title: '双边换手／前日资产', render: (_, row) => number(row.turnover_previous_nav, true) },
          { title: '假设费用', render: (_, row) => number(row.assumed_fees) },
          { title: '持仓数', render: (_, row) => row.holdings.length },
        ]} expandable={{ expandedRowRender: row => <Typography.Paragraph>{row.holdings.map(h => `${h.symbol}：${h.quantity}份（${number(h.market_value)}）`).join('；') || '现金状态'}</Typography.Paragraph> }} />
        <Typography.Paragraph>完整目标、模拟成交和逐日会计记录可导出核验；下列限制适用于全部结果。</Typography.Paragraph>
        {report.limitations.map(reason => <Typography.Text key={reason}>• {limitations[reason] ?? reason}</Typography.Text>)}
        <Typography.Paragraph copyable>实验身份：{report.simulation_id}</Typography.Paragraph>
        <Typography.Paragraph copyable>执行源码：{report.bindings.implementation_revision}</Typography.Paragraph>
        <JsonExportButton filename={`${report.simulation_id}-etf-history.json`} value={value} />
      </>}
    </Space>
  </Card>
}
