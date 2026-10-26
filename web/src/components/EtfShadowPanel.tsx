import { useEffect, useState } from 'react'
import { Alert, Button, Card, Descriptions, Empty, Space, Table, Typography } from 'antd'
import { workflowGet } from '../research/qlibWorkflow'
import { JsonExportButton } from './JsonExportButton'

interface Target {
  target_id: string; frozen_at: string; qualified_inputs: number
  bindings: { as_of: string; expected_execution_at: string; diagnostic_id: string }
  weights: Array<{ instrument: string; weight: string }>
}
interface Observation {
  session: string; observed_at: string; processed_at: string
  observed_closes: Array<{ instrument: string; close: string }>; missing_target_inputs: string[]
}
interface Entry { target: Target; observation: Observation | null }
interface Publication {
  schema_version: 'qtrade.etf-shadow-service.v1'
  state: string; heartbeat_at: string; strategy_id: string; contract_id: string
  contract: { portfolio: { top_k: number; risk_degree: string; rebalance_sessions: number } }
  entries: Entry[]; errors: unknown[]; limitations: string[]
}
interface Reply {
  publication_available: boolean; service_fresh: boolean; current_target: boolean
  publication: Publication | null
}

export function EtfShadowPanel() {
  const [reply, setReply] = useState<Reply | null>(null)
  const [error, setError] = useState(false)
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    let active = true
    let inFlight = false
    const load = async () => {
      if (inFlight) return
      inFlight = true
      try {
        const value = await workflowGet<Reply>('/online/etf-forward')
        if (active) { setReply(value); setError(false) }
      } catch { if (active) { setReply(null); setError(true) } }
      finally { inFlight = false }
    }
    void load()
    const timer = window.setInterval(() => void load(), 30_000)
    return () => { active = false; window.clearInterval(timer) }
  }, [refresh])
  const value = reply?.publication_available && reply.publication?.schema_version === 'qtrade.etf-shadow-service.v1' ? reply.publication : null
  const entries = value?.entries ?? []
  const latest = [...entries].sort((a, b) => b.target.bindings.expected_execution_at.localeCompare(a.target.bindings.expected_execution_at))[0]
  const portfolio = value?.contract.portfolio
  return <Card title="ETF 趋势轮动 · SHADOW" extra={<Button onClick={() => setRefresh(n => n + 1)}>刷新 ETF 目标</Button>}>
    <Space orientation="vertical" style={{ width: '100%' }}>
      <Typography.Paragraph>20 日正趋势筛选后，按合同取排名靠前的 ETF 等权配置。目标在下一交易日开盘前冻结，之后只记录实际取得的收盘行情；目标权重不代表已执行持仓。</Typography.Paragraph>
      <Alert type="warning" showIcon title="ETF 仅作 SHADOW 观察，PAPER 准入仍被阻断"
        description="复权、分红拆分、手数、费用、结算和交易限制尚未完成认证。本模块不产生订单、成交、持仓、现金、净值或组合收益；历史策略对照仍需同口径历史数据。" />
      {error && <Alert type="error" title="ETF 前向记录读取失败" />}
      {!reply && !error && <Typography.Text>正在读取 ETF 前向记录…</Typography.Text>}
      {reply && !value && <Empty description="尚无通过校验的 ETF SHADOW 发布" />}
      {value && <>
        <Alert type={reply?.service_fresh ? 'info' : 'warning'} showIcon
          title={reply?.service_fresh ? `ETF 观察服务：${value.state}` : 'ETF 观察服务已停止或心跳过期；以下为历史记录'}
          description={`心跳：${value.heartbeat_at}。${reply?.current_target ? '存在符合当前时点的开盘前目标。' : '当前没有可用的开盘前目标；历史冻结记录仍保留。'}`} />
        <Descriptions size="small" column={2} items={[
          { key: 'topk', label: '正趋势最多选取', children: `${portfolio?.top_k} 只 ETF` },
          { key: 'risk', label: '总目标权重上限', children: `${(Number(portfolio?.risk_degree) * 100).toFixed(1)}%` },
          { key: 'frequency', label: '目标调整间隔', children: `${portfolio?.rebalance_sessions} 个交易日` },
          { key: 'history', label: '历史策略指标', children: '待同口径历史验证，不填零值' },
          { key: 'contract', label: '策略合同', children: <Typography.Text copyable>{value.contract_id}</Typography.Text> },
        ]} />
        {latest ? <>
          <Descriptions size="small" column={1} items={[
            { key: 'asof', label: '目标行情截止', children: latest.target.bindings.as_of },
            { key: 'frozen', label: '实际冻结时刻', children: latest.target.frozen_at },
            { key: 'execute', label: '预定观察交易日开盘', children: latest.target.bindings.expected_execution_at },
            { key: 'qualified', label: '连续窗口合格 ETF', children: latest.target.qualified_inputs },
          ]} />
          {!latest.target.weights.length && <Alert type="info" title="合格 ETF 中无正趋势，目标暂不分配" description="这是未分配的研究目标，不是实际现金余额或零收益。" />}
          <Table rowKey="instrument" dataSource={latest.target.weights} pagination={false} columns={[
            { title: 'ETF', dataIndex: 'instrument' },
            { title: '目标权重（非持仓）', dataIndex: 'weight', render: (weight: string) => `${(Number(weight) * 100).toFixed(3)}%` },
          ]} />
        </> : <Empty description="服务尚未冻结 ETF 目标，不显示示例配置" />}
        <Table<Entry> rowKey={row => row.target.target_id} dataSource={entries} pagination={{ pageSize: 5 }} scroll={{ x: 750 }} columns={[
          { title: '目标对应开盘', render: (_, row) => row.target.bindings.expected_execution_at },
          { title: '实际冻结', render: (_, row) => row.target.frozen_at },
          { title: '后续行情观察', render: (_, row) => row.observation?.observed_at ?? '尚未取得完整后续收据' },
          { title: '已观察／缺少目标行情', render: (_, row) => row.observation ? `${row.observation.observed_closes.length} / ${row.observation.missing_target_inputs.length}` : '尚无记录' },
          { title: '组合收益', render: () => 'SHADOW 不计算组合收益' },
        ]} />
        {!!value.errors.length && <Alert type="warning" title="部分 ETF 记录等待修复" description="查看导出中的错误与来源身份；现有记录不会自动补造成完整观察。" />}
        <JsonExportButton filename="qtrade-etf-shadow.json" value={value} />
      </>}
    </Space>
  </Card>
}
