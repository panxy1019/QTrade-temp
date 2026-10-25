import { Alert, Button, Card, Space, Table, Tag, Typography } from 'antd'
import { useCallback, useEffect, useState } from 'react'
import { loadPublicResearchObservations } from '../api/client'
import type { PublicResearchObservation } from '../types'
import { JsonExportButton } from './JsonExportButton'

export function PublicResearchObservations({ mode = 'market' }: { mode?: 'market' | 'monitor' | 'experiments' }) {
  const [data, setData] = useState<PublicResearchObservation | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(false)
  const refresh = useCallback(async () => {
    setLoading(true)
    try {
      const result = await loadPublicResearchObservations()
      if (result.schema_version !== 'qtrade.public-observations.v1' || result.external_order_transmission !== false) {
        throw new Error('只读发布快照格式不匹配')
      }
      setData(result)
      setError(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '发布通道读取失败')
    } finally {
      setLoading(false)
    }
  }, [])
  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0)
    const interval = window.setInterval(() => void refresh(), 60_000)
    return () => { window.clearTimeout(initial); window.clearInterval(interval) }
  }, [refresh])
  const title = mode === 'experiments' ? '集群持久化实验' : '持续数据与新鲜度 · 研究观测'
  const healthy = data?.healthy === true && !error
  const streamRows = data ? (['daily', 'minute'] as const).map((kind) => ({
    kind, label: kind === 'daily' ? 'ETF 日线' : '股票五分钟线', ...data.streams[kind],
  })) : []
  const bars = data ? (['daily', 'minute'] as const).flatMap((kind) => data.datasets[kind].latest_bars.map((bar) => ({ ...bar, kind }))) : []
  return <Card title={title} extra={<Button onClick={() => void refresh()} loading={loading}>刷新发布状态</Button>}>
    <Space orientation="vertical" style={{ width: '100%' }}>
      <Alert showIcon type={error ? 'error' : healthy ? 'success' : 'warning'}
        title={error ? `发布通道不可用：${error}` : data ? healthy ? '数据与发布通道检查正常' : '数据或发布通道需要检查' : '正在读取集群发布快照'}
        description={data ? `发布于 ${data.published_at}；当前发布年龄 ${Math.round(data.publication_age_seconds)} 秒，上限 ${data.maximum_publication_age_seconds} 秒。采集进程在发布时${data.worker_alive_at_publication ? '有正常心跳' : '心跳异常'}。` : '此通道独立于冻结的历史研究数据；无法读取时不沿用旧的正常状态。'} />
      {mode !== 'experiments' && <>
        <Table size="small" rowKey="kind" pagination={false} dataSource={streamRows} scroll={{ x: 850 }} columns={[
          { title: '数据流', dataIndex: 'label' },
          { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color={value === 'FRESH' ? 'green' : 'orange'}>{value}</Tag> },
          { title: '覆盖标的', render: (_, row) => `${row.observed_symbols}/${row.required_symbols}` },
          { title: '本批 Bars', dataIndex: 'bar_count' },
          { title: '最早最新时间', dataIndex: 'latest_minimum' },
          { title: '应覆盖至', dataIndex: 'expected_through' },
        ]} />
        {mode === 'market' && <Table size="small" rowKey={(row) => `${row.kind}:${row.symbol}`} dataSource={bars} pagination={{ pageSize: 8 }} scroll={{ x: 700 }} columns={[
          { title: '代码', dataIndex: 'symbol' },
          { title: '频率', dataIndex: 'kind', render: (value: string) => value === 'daily' ? '日线' : '5 分钟' },
          { title: '收盘价', dataIndex: 'close' },
          { title: '成交量', dataIndex: 'volume' },
          { title: '市场时间', dataIndex: 'timestamp' },
        ]} />}
      </>}
      {mode === 'experiments' && <>
        <Typography.Text>共 {data?.experiments.length ?? 0} 个集群归档。正式运行保存完整规格、数据版本、参数、成本、代码提交、结果和产物哈希；原有实验按历史归档保留。</Typography.Text>
        <Table size="small" rowKey="experiment_id" dataSource={data?.experiments ?? []} pagination={{ pageSize: 8 }} scroll={{ x: 1150 }} columns={[
          { title: '实验 ID', dataIndex: 'experiment_id' },
          { title: '持久化类型', dataIndex: 'persistence_kind', render: (value: string) => value === 'FORMAL_IMMUTABLE_RUN' ? '正式不可变运行' : '已验证历史归档' },
          { title: '测试窗口', render: (_, row) => `${row.split.test_start} 至 ${row.split.test_end}` },
          { title: '测试收益', render: (_, row) => row.metrics.total_return },
          { title: 'Sharpe', render: (_, row) => row.metrics.sharpe },
          { title: '结果 Hash', dataIndex: 'result_hash', render: (value: string) => <Typography.Text copyable={{ text: value }}>{value.slice(0, 12)}…</Typography.Text> },
        ]} />
        <Alert showIcon type="info" title="持久化状态与研究结论分别判断" description="保存成功不代表策略有效或可以晋级。当前仍是非 PIT 历史研究；新运行属于事后延续诊断，不声称获得未见测试证据。网页本地保存的预览不会自动成为正式实验。" />
      </>}
      <Space wrap><JsonExportButton filename="qtrade-public-observations.json" value={data} /><Typography.Text type="secondary">只读发布；不会启动 PAPER 或实盘。</Typography.Text></Space>
    </Space>
  </Card>
}
