import { useEffect, useState } from 'react'
import { Alert, Card, Descriptions, Select, Space, Table, Typography } from 'antd'
import { workflowGet } from '../research/qlibWorkflow'
import { EtfTrendPanel } from '../components/EtfTrendPanel'
import { EtfShadowPanel } from '../components/EtfShadowPanel'

interface Signals {
  publication_available: boolean; current_input: boolean
  manifest?: { signal_family_id: string; data_end: string; generated_at: string
    expected_execution_at: string; qualified_instruments: number; unavailable: string[]
    limitations: string[]; bindings: { data_version_id: string; code_revision: string } }
  families: Array<{ name: string; score_count: number; target_count: number; allocation_method: string }>
  rows: Array<{ instrument: string; score: number; indicative_weight: string }>; total: number
}
const names: Record<string, string> = { momentum20: '20 日动量', reversal5: '5 日反转', lowvol20: '20 日低波动', multifactor_equal_rank: '多因子等权排名', equal_weight_universe: '共同股票池等权', etf_positive_trend20: 'ETF 正趋势' }

export function SignalsPage() {
  const [strategy, setStrategy] = useState('momentum20')
  const [page, setPage] = useState(1)
  const [data, setData] = useState<Signals | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let active = true
    const refresh = async () => {
      try {
        const value = await workflowGet<Signals>(`/online/signals?strategy=${strategy}&offset=${(page - 1) * 50}&limit=50`)
        if (active) { setData(value); setError(null) }
      } catch (e) { if (active) { setData(null); setError(String(e)) } }
    }
    void refresh()
    const timer = setInterval(() => { void refresh() }, 30_000)
    return () => { active = false; clearInterval(timer) }
  }, [strategy, page])
  const m = data?.manifest
  return <Space orientation="vertical" style={{ width: '100%' }}>
    <Typography.Title level={2}>每日研究信号</Typography.Title>
    <Alert type="warning" showIcon title="信号分数与指示权重分别展示" description="股票信号使用连续 21 个交易会话的复权收盘输入。指示权重不代表委托、成交或 PAPER 准入；已冻结目标及实际观察记录请查看前向研究。ETF 使用下方独立口径。" />
    {error && <Alert type="error" title="信号读取失败" description={error} />}
    {!error && !data && <Typography.Text>正在读取信号…</Typography.Text>}
    {data && !data.publication_available && <Alert type="info" title="尚无通过校验的信号发布" />}
    {data?.publication_available && m && <>
      {!data.current_input && <Alert type="error" title="此档案已不符合当前输入或执行时间要求" description="保留历史观察，不作为当前研究信号。请检查在线模型的数据版本及采集状态。" />}
      <Card title="不可变输入与时点"><Descriptions column={2} items={[
        { key: 'day', label: '行情截止', children: m.data_end },
        { key: 'count', label: '共同合格股票', children: m.qualified_instruments },
        { key: 'generated', label: '实际生成时间', children: m.generated_at },
        { key: 'execute', label: '预定研究执行时间', children: m.expected_execution_at },
        { key: 'data', label: '数据版本', children: <Typography.Text copyable>{m.bindings.data_version_id}</Typography.Text> },
        { key: 'code', label: '代码版本', children: <Typography.Text copyable>{m.bindings.code_revision}</Typography.Text> },
      ]} />
      <Typography.Paragraph>当前股票池存在幸存者偏差；完整历史 PIT、复权和交易约束仍未认证。ETF 当前未纳入此 Provider：{m.unavailable.map(s => names[s] ?? s).join('、') || '无缺项'}。</Typography.Paragraph>
      <Typography.Text type="secondary">{m.limitations.join(' · ')}</Typography.Text></Card>
      <Card title="独立信号与组合输入">
        <Select aria-label="信号家族" value={strategy} style={{ width: 250 }} options={data.families.map(f => ({ value: f.name, label: `${names[f.name] ?? f.name}（${f.score_count}）` }))} onChange={s => { setData(null); setError(null); setStrategy(s); setPage(1) }} />
        <Typography.Paragraph>排名类信号取前 50 只等权、总指示权重 95%；共同股票池等权使用全部合格股票。不同因子分数量纲不同，不能直接比较绝对大小。统一历史策略对照请查看回测分析中的独立对照报告。</Typography.Paragraph>
        <Table rowKey="instrument" dataSource={data.rows} pagination={{ current: page, pageSize: 50, total: data.total, showSizeChanger: false, onChange: p => { setData(null); setError(null); setPage(p) } }} columns={[
          { title: '证券', dataIndex: 'instrument' },
          { title: '信号分数', dataIndex: 'score', render: (v: number) => v.toFixed(6) },
          { title: '指示权重（非持仓）', dataIndex: 'indicative_weight', render: (v: string) => `${(Number(v) * 100).toFixed(3)}%` },
        ]} />
      </Card>
    </>}
    <EtfTrendPanel />
    <EtfShadowPanel />
  </Space>
}
