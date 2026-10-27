import { Alert, Card, Space, Table, Typography } from 'antd'
import type { HistoricalDataset } from '../types'
import { buildLocalBacktestComparisonExport, loadLocalBacktestExperiments } from '../research/localBacktestExperiments'
import type { LocalBacktestExperiment } from '../research/localBacktestExperiments'
import { JsonExportButton } from './JsonExportButton'

export function LegacyBacktestArchive({ dataset }: { dataset: HistoricalDataset | null }) {
  const records = dataset ? loadLocalBacktestExperiments(dataset) : []
  const exported = dataset && records.length ? buildLocalBacktestComparisonExport(dataset) : null
  return <Card title="旧日线本地实验档案" extra={<JsonExportButton filename="qtrade-legacy-local-backtests.json" value={exported} label="导出历史记录" />}>
    <Space orientation="vertical" style={{ width: '100%' }}>
      <Alert showIcon type="info" title="旧模板操作入口已移除" description="已有浏览器记录保留原参数、结果及身份，仅供查阅和导出。它们不是不可变实验或策略注册凭证；新的集群研究请使用策略研究页。" />
      {!dataset && <Typography.Text type="secondary">等待匹配的历史数据身份后读取本地记录。</Typography.Text>}
      <Table<LocalBacktestExperiment> rowKey="preview_hash" size="small" dataSource={records}
        pagination={{ pageSize: 5, showSizeChanger: false }} scroll={{ x: 1000 }}
        columns={[
          { title: '名称', dataIndex: 'name' },
          { title: '原保存时间', dataIndex: 'saved_at' },
          { title: '数据版本', dataIndex: 'data_version' },
          { title: '累计收益', render: (_, row) => percent(row.metrics.total_return) },
          { title: '最大回撤', render: (_, row) => percent(row.metrics.maximum_drawdown) },
          { title: '原结果身份', dataIndex: 'preview_hash', render: value => <Typography.Text copyable>{value}</Typography.Text> },
          { title: '导出', render: (_, row) => <JsonExportButton filename={`qtrade-legacy-${row.preview_hash}.json`} value={row} label="导出此记录" /> },
        ]}
        expandable={{ expandedRowRender: row => <>
          <Typography.Paragraph>原数据 checksum：{row.data_checksum}</Typography.Paragraph>
          <pre>{JSON.stringify({ parameters: row.parameters, metrics: row.metrics, binding: row.binding }, null, 2)}</pre>
        </> }} locale={{ emptyText: '此数据版本没有已保存的本地记录' }} />
      <Typography.Text type="secondary">按原数据版本与 checksum 匹配；不会载入参数重跑、改写或删除原记录。集群不可变历史实验仍在实验中心及回测分析中查看。</Typography.Text>
    </Space>
  </Card>
}

function percent(value?: string) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `${(parsed * 100).toFixed(2)}%` : '—'
}
