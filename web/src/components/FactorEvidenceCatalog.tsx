import { Alert, Card, Empty, Space, Table, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { FactorSearchReport, IntradayFactorLongWindowReport } from '../types'
import { buildEvidenceFactorCatalog } from '../research/factorEvidenceCatalog'
import type { EvidenceFactorCatalogRow } from '../research/factorEvidenceCatalog'
import { JsonExportButton } from './JsonExportButton'

export function FactorEvidenceCatalog({
  factorSearch,
  intradayReport,
}: {
  factorSearch: FactorSearchReport | null | undefined
  intradayReport: IntradayFactorLongWindowReport | null | undefined
}) {
  const rows = buildEvidenceFactorCatalog(factorSearch, intradayReport)
  const columns: ColumnsType<EvidenceFactorCatalogRow> = [
    { title: '因子', dataIndex: 'factor_id', width: 260, render: (_, row) => <Space orientation="vertical" size={0}><Typography.Text strong>{row.name}</Typography.Text><Typography.Text code>{row.factor_id}</Typography.Text></Space> },
    { title: '角色', dataIndex: 'role', width: 180, render: (value: EvidenceFactorCatalogRow['role']) => <Tag color={value === 'UNTESTED_L2_HYPOTHESIS' ? 'default' : 'blue'}>{value}</Tag> },
    { title: '公式', dataIndex: 'formula', width: 390, render: (value: string) => <Typography.Text code>{value}</Typography.Text> },
    { title: '数据与因果时点', width: 360, render: (_, row) => <Space orientation="vertical" size={0}><Typography.Text>{row.interval} · {row.required_data}</Typography.Text><Typography.Text type="secondary">{row.known_at} → {row.earliest_execution}</Typography.Text></Space> },
    { title: '数据状态', dataIndex: 'data_status', width: 220 },
    { title: '有效性状态', width: 260, render: (_, row) => <Space orientation="vertical" size={0}><Tag color={row.validation_tone}>{row.validation_status}</Tag><Typography.Text type="secondary">{row.limitation}</Typography.Text></Space> },
    { title: '证据绑定', dataIndex: 'evidence_binding', width: 280, render: (value: string) => <Typography.Text code>{value.length > 32 ? `${value.slice(0, 32)}…` : value}</Typography.Text> },
  ]
  return <Card
    title={`因子定义与数据契约目录 · ${rows.length}`}
    extra={<JsonExportButton filename="qtrade-read-only-factor-evidence-catalog.json" value={rows} label="导出因子目录" />}
  >
    <Space orientation="vertical" style={{ width: '100%' }}>
      <Alert showIcon type="info" title="只读证据索引，不是 FactorRegistry" description="目录只聚合当前已签名的日频因子搜索、三连阳长期报告、可选平仓因子与 L2 就绪契约；未知公式失败关闭，不执行任意代码，不保存定义，不创建策略、实验、PAPER/LIVE 或订单权限。" />
      {rows.length > 0
        ? <Table<EvidenceFactorCatalogRow> rowKey="factor_id" size="small" columns={columns} dataSource={rows} pagination={false} scroll={{ x: 1950 }} />
        : <Empty description="当前没有可绑定的因子证据" />}
    </Space>
  </Card>
}
