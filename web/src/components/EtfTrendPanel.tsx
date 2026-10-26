import { useEffect, useState } from 'react'
import { Alert, Button, Card, Descriptions, Empty, Space, Table, Typography } from 'antd'
import { workflowGet } from '../research/qlibWorkflow'
import { JsonExportButton } from './JsonExportButton'

interface EtfTrendDiagnostic {
  as_of: string; state: string; price_basis: string; required_symbols: number; qualified_close_symbols: number
  sessions: string[]; scores: { instrument: string; score: number }[]
  close_histories: Record<string, { date: string; close: string }[]>
  excluded: { symbol: string; reason: string }[]; limitations: string[]; receipt_sha256: string
  bindings: { master_sha256: string; calendar_sha256: string; code_revision: string }
}
interface EtfTrendReply { publication_available: boolean; current_input: boolean; diagnostic: EtfTrendDiagnostic | null }
const labels: Record<string, string> = {
  VENDOR_NATIVE_PRICE_NOT_CERTIFIED_UNADJUSTED_OR_ADJUSTED: '供应商原生收盘价，尚未认证为不复权或复权价格',
  DISTRIBUTIONS_AND_SPLITS_NOT_RECONCILED: '分红、拆分与合并尚未核对',
  CURRENT_POOL_SURVIVORSHIP_BIAS: '当前ETF观察池存在幸存者偏差',
  CLOSE_ONLY_NOT_AMOUNT_VWAP_OR_EXECUTION_ADMISSION: '仅验证收盘价，不构成成交额、VWAP或执行准入',
  NOT_COMPARABLE_TO_ADJUSTED_STOCK_MODEL_OR_HISTORICAL_RETURN_SERIES: '不能与复权股票模型或历史收益序列直接比较',
}

export function EtfTrendPanel() {
  const [reply, setReply] = useState<EtfTrendReply | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [refresh, setRefresh] = useState(0)
  useEffect(() => {
    let active = true
    let inFlight = false
    const load = async () => {
      if (inFlight) return
      inFlight = true
      try {
        const value = await workflowGet<EtfTrendReply>('/online/etf-trend')
        if (active) { setReply(value); setError(null) }
      } catch (reason) { if (active) { setReply(null); setError(String(reason)) } }
      finally { inFlight = false }
    }
    void load()
    const timer = window.setInterval(() => void load(), 30_000)
    return () => { active = false; window.clearInterval(timer) }
  }, [refresh])
  const value = reply?.publication_available ? reply.diagnostic : null
  const scores = new Map(value?.scores?.map(row => [row.instrument, row.score]))
  const rows = value ? Object.entries(value.close_histories).map(([instrument, history]) => ({
    instrument, first: history[0]?.close, last: history.at(-1)?.close, score: scores.get(instrument),
  })) : []
  return <Card title="ETF原生收盘价趋势诊断" extra={<Button onClick={() => setRefresh(n => n + 1)}>刷新ETF诊断</Button>}>
    <Space orientation="vertical" style={{ width: '100%' }}>
      <Typography.Paragraph>独立使用连续21个实际交易日的供应商原生收盘价，展示20日正趋势。复权与分红尚未认证，不能套用股票模型的复权输入协议；此诊断不代表持仓、成交或PAPER收益。</Typography.Paragraph>
      {error && <Alert type="error" title="ETF诊断读取失败" description={error} />}
      {!reply && !error && <Typography.Text>正在读取ETF诊断…</Typography.Text>}
      {reply && !value && <Empty description="尚无通过校验的ETF收盘价诊断" />}
      {value && <>
        {!reply?.current_input && <Alert type="warning" title="ETF档案已过期，仅保留历史观察" description="此档案不符合当前完整交易日要求，不作为当前ETF输入。" />}
        <Alert type="warning" title="收盘价诊断未获执行准入" description="价格复权、分红拆分、完整行情和交易约束尚未认证；没有委托、成交或前向收益。" />
        <Descriptions column={2} size="small" items={[
          { key: 'asof', label: 'ETF行情截止', children: value.as_of },
          { key: 'coverage', label: '合格／请求ETF', children: `${value.qualified_close_symbols} / ${value.required_symbols}` },
          { key: 'window', label: '实际交易日区间', children: `${value.sessions[0]}—${value.sessions.at(-1)}（${value.sessions.length}日）` },
          { key: 'positive', label: '正趋势ETF', children: value.scores.length },
          { key: 'basis', label: '价格来源口径', children: '新浪原生收盘价；复权未经认证' },
          { key: 'current', label: '当前输入', children: reply?.current_input ? '符合当前日期要求' : '历史档案' },
        ]} />
        {value.state === 'INPUT_UNAVAILABLE' && <Alert type="info" title="没有完整21交易日收盘价的ETF" />}
        <Table rowKey="instrument" dataSource={rows} pagination={false} columns={[
          { title: 'ETF证券', dataIndex: 'instrument' },
          { title: '起始原生收盘价', dataIndex: 'first' },
          { title: '截止原生收盘价', dataIndex: 'last' },
          { title: '20日正趋势分数', dataIndex: 'score', render: (score: number | undefined) => score === undefined ? '无正趋势' : `${(score * 100).toFixed(3)}%` },
        ]} />
        <Typography.Paragraph>正趋势分数为期末收盘价／期初收盘价−1，仅保留大于零的结果；未出现正趋势的合格ETF仍列在表中。分数不是含分红收益。</Typography.Paragraph>
        {value.excluded.length > 0 && <Typography.Paragraph>缺少完整价格或来源不合格的ETF：{value.excluded.map(row => row.symbol).join('、')}</Typography.Paragraph>}
        {value.limitations.map(reason => <Typography.Text key={reason}>• {labels[reason] ?? reason}</Typography.Text>)}
        <Typography.Paragraph copyable>来源收据：{value.receipt_sha256}</Typography.Paragraph>
        <Typography.Paragraph copyable>观察池身份：{value.bindings.master_sha256}</Typography.Paragraph>
        <Typography.Paragraph copyable>执行源码：{value.bindings.code_revision}</Typography.Paragraph>
        <JsonExportButton filename={`etf-close-diagnostic-${value.as_of}.json`} value={value} />
      </>}
    </Space>
  </Card>
}
