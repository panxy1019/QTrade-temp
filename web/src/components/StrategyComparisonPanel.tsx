import { useEffect, useState } from 'react'
import { Alert, Button, Card, Descriptions, Empty, Select, Space, Table, Typography } from 'antd'
import ChartRenderer from '../charts/ChartRenderer'
import { workflowGet } from '../research/qlibWorkflow'
import { JsonExportButton } from './JsonExportButton'

const names: Record<string, string> = { momentum20: '20日动量', reversal5: '5日反转', lowvol20: '20日低波动', multifactor_equal_rank: '多因子等秩', ridge: 'Alpha158 + Ridge', lightgbm: 'Alpha158 + LightGBM', equal_weight_universe: '全池等权（配置对照）' }
export interface StrategyComparison {
  comparison_id: string; created_at: string; signal_start: string; signal_end: string; common_rows: number
  bindings: { dataset_sha256: string; code_revision: string; source_snapshot_sha256: string; protocol: { segments: Record<string, string[]>; label: string; portfolio: Record<string, unknown>; exchange: Record<string, unknown>; account: number; benchmark: string; source_constraint_policy: string } }
  limitations: string[]
  strategies: Record<string, { allocation: string; metrics: Record<string, number | null>; curve: { date: string; equity: number; turnover: number; cost: number }[] }>
}
const labels: Record<string, string> = {
  CURRENT_POOL_SURVIVORSHIP_AND_SELECTION_BIAS: '当前股票池存在幸存者与历史筛选偏差', FULL_PIT_NOT_CERTIFIED: '完整历史PIT尚未认证', HISTORICAL_AVAILABLE_AT_UNKNOWN: '历史信息可得时点未知', POST_HOC_OOS_NOT_FORWARD_PERFORMANCE: '事后样本外回测，不是前向实绩', DAILY_OPEN_CAPACITY_AND_LIMIT_PROXIES_NOT_AUCTION_REPLAY: '日线开盘、成交容量与涨跌停代理不能替代集合竞价回放', EQUAL_UNIVERSE_IS_ALLOCATION_CONTROL_NOT_IDENTICAL_TOPK_PORTFOLIO: '全池等权采用独立配置规则，不属于相同TopK持仓规则比较', ETF_PROVIDER_UNAVAILABLE: '本历史对照未纳入ETF；ETF收盘价诊断单独展示',
}
function metric(value: number | null | undefined, percent = false) {
  return value == null || !Number.isFinite(value) ? '—' : percent ? (value * 100).toFixed(2) + '%' : value.toFixed(4)
}
export function StrategyComparisonPanel() {
  const [value, setValue] = useState<StrategyComparison | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [selected, setSelected] = useState('lightgbm')
  const [refresh, setRefresh] = useState(0)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let active = true
    let inFlight = false
    const load = async () => {
      if (inFlight) return
      inFlight = true
      try {
        const reply = await workflowGet<{ publication_available: boolean; comparison: StrategyComparison | null }>('/online/strategy-comparison')
        if (active) { setValue(reply.publication_available ? reply.comparison : null); setError(null); setLoading(false) }
      } catch (reason) { if (active) { setValue(null); setError(String(reason)); setLoading(false) } }
      finally { inFlight = false }
    }
    void load()
    const timer = window.setInterval(() => void load(), 30000)
    return () => { active = false; window.clearInterval(timer) }
  }, [refresh])
  const chosen = value?.strategies[selected]
  const rows = value ? Object.entries(value.strategies).map(([id, row]) => ({ id, ...row })) : []
  return <Card title="统一历史策略对照" extra={<Button onClick={() => setRefresh(n => n + 1)}>刷新历史对照</Button>} loading={loading}>
    <Space orientation="vertical" style={{ width: '100%' }}>
      <Typography.Paragraph>冻结样本外区间的七类历史研究结果。六类排名信号共享TopkDropout配置；全池等权为配置对照。此处不展示每日预测或PAPER前向收益。</Typography.Paragraph>
      {error && <Alert type="error" title={error} />}
      {!value && !loading && <Empty description="尚无通过校验的统一历史策略对照" />}
      {value && <>
        <Alert type="warning" showIcon title="诊断性历史结果，不可据此晋级交易" description="当前股票池存在幸存者偏差，历史PIT与交易限制未完整认证；每项限制适用于全部策略。等权恒定信号的IC无定义，显示为—。" />
        <Descriptions size="small" column={2} items={[
          { key: 'range', label: '信号区间', children: `${value.signal_start}—${value.signal_end}` },
          { key: 'rows', label: '共同逐日股票记录', children: value.common_rows.toLocaleString() },
          { key: 'pool', label: '股票池', children: '逐日同时具备两模型分数和完整21交易日价格；不前填缺失值' },
          { key: 'clock', label: '成交时点', children: '前一交易日信号驱动下一开盘；首日可能为空仓' },
          { key: 'split', label: '时间切分', children: JSON.stringify(value.bindings.protocol.segments) },
          { key: 'label', label: 'IC标签', children: value.bindings.protocol.label },
          { key: 'policy', label: '归档交易约束版本', children: value.bindings.protocol.source_constraint_policy },
          { key: 'cash', label: '初始资金／基准', children: `${value.bindings.protocol.account.toLocaleString()}／${value.bindings.protocol.benchmark}` },
        ]} />
        <Table rowKey="id" pagination={false} scroll={{ x: 1050 }} dataSource={rows} columns={[
          { title: '策略', render: (_, row) => names[row.id] ?? row.id },
          { title: 'IC', render: (_, row) => metric(row.metrics.ic) },
          { title: 'RankIC', render: (_, row) => metric(row.metrics.rank_ic) },
          { title: '成本后累计收益', render: (_, row) => metric(row.metrics.total_return_net, true) },
          { title: '成本后年化收益', render: (_, row) => metric(row.metrics.annual_return_net, true) },
          { title: 'Sharpe', render: (_, row) => metric(row.metrics.sharpe_net) },
          { title: '最大回撤', render: (_, row) => metric(row.metrics.max_drawdown, true) },
          { title: '日均双边换手', render: (_, row) => metric(row.metrics.mean_daily_turnover, true) },
        ]} />
        <Typography.Paragraph>全池等权每日调整至共同池等权，使用相同风险仓位和Exchange成本/容量限制；不使用TopkDropout的TopK、淘汰数和最短持有期。其收益差异不能仅归因于信号。</Typography.Paragraph>
        <Select aria-label="选择历史净值策略" value={selected} style={{ width: 280 }} onChange={setSelected} options={Object.keys(value.strategies).map(id => ({ value: id, label: names[id] ?? id }))} />
        {chosen && <ChartRenderer style={{ height: 340 }} option={{ animation: false, tooltip: { trigger: 'axis' }, grid: { left: 60, right: 30, bottom: 40 }, xAxis: { type: 'category', data: chosen.curve.map(r => r.date) }, yAxis: { type: 'value', scale: true }, series: [{ name: `${names[selected]} · 成本后净值`, type: 'line', showSymbol: false, data: chosen.curve.map(r => r.equity) }] }} />}
        <Typography.Paragraph>共同持仓配置：{JSON.stringify(value.bindings.protocol.portfolio)}</Typography.Paragraph>
        <Typography.Paragraph>共同成本与交易约束：{JSON.stringify(value.bindings.protocol.exchange)}</Typography.Paragraph>
        {value.limitations.map(reason => <Typography.Text key={reason}>• {labels[reason] ?? reason}</Typography.Text>)}
        <Typography.Paragraph copyable>不可变对照身份：{value.comparison_id}</Typography.Paragraph>
        <Typography.Paragraph copyable>冻结数据：{value.bindings.dataset_sha256}</Typography.Paragraph>
        <Typography.Paragraph copyable>来源快照：{value.bindings.source_snapshot_sha256}</Typography.Paragraph>
        <Typography.Paragraph copyable>执行源码：{value.bindings.code_revision}</Typography.Paragraph>
        <JsonExportButton filename={`${value.comparison_id}-comparison.json`} value={value} />
      </>}
    </Space>
  </Card>
}

