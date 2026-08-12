import { Alert, Card, Empty, Input, Space, Table, Tabs, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { OrderRow, PlatformSnapshot, PositionRow } from '../types'
import { StatusTag } from '../components/StatusTag'

const waiting = <Alert showIcon type="info" title="等待实时数据授权" description="适配器与回放链路已准备；当前未配置任何供应商令牌或真实连接。" />

export function MarketPage() {
  return <Page title="行情中心" subtitle="A股、ETF 与指数 Level-1 行情"><Space orientation="vertical" style={{ width: '100%' }}>{waiting}<Input.Search placeholder="搜索证券代码或名称" /><Empty description="授权并连接行情供应商后显示实时行情与K线" /></Space></Page>
}

export function StrategyPage() {
  const columns = [
    { title: '策略名称', dataIndex: 'name' }, { title: '版本', dataIndex: 'version' },
    { title: 'Family', dataIndex: 'family' }, { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color="blue">{value}</Tag> },
    { title: '角色', dataIndex: 'role' }, { title: 'Sharpe', dataIndex: 'sharpe' }, { title: '最大回撤', dataIndex: 'mdd' },
  ]
  return <Page title="策略中心" subtitle="策略版本、运行状态与研究角色"><Table columns={columns} dataSource={[]} pagination={false} locale={{ emptyText: '暂无已注册策略' }} /></Page>
}

export function ResearchPage() {
  return <Page title="策略研究" subtitle="血缘、候选与 Champion/Challenger"><Card><Tabs items={[{ key: 'registry', label: '策略注册表', children: <Empty description="策略版本将从注册表加载" /> }, { key: 'lineage', label: '演化树', children: <Empty description="暂无可验证策略血缘" /> }, { key: 'rejected', label: '已拒绝', children: <Empty description="暂无已拒绝策略" /> }]} /></Card></Page>
}

export function ExperimentPage() {
  const columns = ['实验ID', '策略', 'Git提交', '数据版本', '训练期', '验证期', '测试期', '状态'].map((title) => ({ title, dataIndex: title }))
  return <Page title="实验中心" subtitle="可复现实验、指标与产物"><Table columns={columns} dataSource={[]} locale={{ emptyText: '尚未运行正式实验' }} /></Page>
}

export function BacktestPage() {
  return <Page title="回测分析" subtitle="资金曲线、超额收益、回撤与成本"><Card title="回测结果"><Typography.Text type="secondary">完成实验后显示资金曲线、滚动 Sharpe、敞口、换手和成交明细。</Typography.Text></Card></Page>
}

export function PaperPage({ data }: { data: PlatformSnapshot | null }) {
  return <Page title="模拟交易" subtitle="真实内核、模拟成交"><Space orientation="vertical" style={{ width: '100%' }}><Alert type="warning" showIcon title="仅模拟成交，不发送真实订单" /><Table<OrderRow> rowKey="internal_order_id" dataSource={data?.orders ?? []} columns={[{ title: '内部委托ID', dataIndex: 'internal_order_id' }, { title: '证券', dataIndex: 'symbol' }, { title: '状态', dataIndex: 'status' }, { title: '数量', dataIndex: 'quantity' }, { title: '已成交', dataIndex: 'filled_quantity' }]} /></Space></Page>
}

export function PositionsPage({ data }: { data: PlatformSnapshot | null }) {
  const columns: ColumnsType<PositionRow> = [{ title: '证券', dataIndex: 'symbol' }, { title: '数量', dataIndex: 'quantity' }, { title: '可卖数量', render: () => '—' }, { title: '成本', dataIndex: 'average_cost' }, { title: '已实现盈亏', dataIndex: 'realized_pnl' }, { title: 'T+N状态', render: () => <Tag>按日期有效规则</Tag> }]
  return <Page title="持仓" subtitle="数量、成本、盈亏与可卖状态"><Table rowKey="symbol" columns={columns} dataSource={data?.positions ?? []} /></Page>
}

export function OrdersPage({ data }: { data: PlatformSnapshot | null }) {
  return <Page title="委托与成交" subtitle="可追溯订单状态机"><Table<OrderRow> rowKey="internal_order_id" dataSource={data?.orders ?? []} columns={[{ title: '内部委托ID', dataIndex: 'internal_order_id' }, { title: '策略', render: () => '—' }, { title: '证券', dataIndex: 'symbol' }, { title: '数量', dataIndex: 'quantity' }, { title: '成交数量', dataIndex: 'filled_quantity' }, { title: '状态', dataIndex: 'status' }]} /></Page>
}

export function RiskPage({ data }: { data: PlatformSnapshot | null }) {
  const state = data?.system.risk_state ?? 'HALTED'
  return <Page title="风控中心" subtitle="风险状态、敞口与数据新鲜度"><Alert type={state === 'HALTED' ? 'error' : 'success'} showIcon title={<Space>当前风险状态 <StatusTag value={state} prominent /></Space>} /><Card className="risk-grid"><Descriptions values={[['Kill State', state], ['Gateway', data?.health.gateway_connected ? 'CONNECTED' : 'DISCONNECTED'], ['Reconciliation', data?.health.reconciliation_status ?? 'UNKNOWN'], ['Exposure', '—'], ['Concentration', '—'], ['Drawdown', '—']]}/></Card></Page>
}

export function DataCenterPage({ data }: { data: PlatformSnapshot | null }) {
  const reportCount = data?.reports.length ?? 0
  return <Page title="数据中心" subtitle="供应商、质量、存储与回放"><Space orientation="vertical" style={{ width: '100%' }}>{waiting}<Descriptions values={[['Provider', '未配置'], ['连接状态', 'DISCONNECTED'], ['订阅数量', '0'], ['数据版本', '—'], ['质量状态', '等待数据'], ['Replay', '可用'], ['每日报告', String(reportCount)]]}/></Space></Page>
}

export function MonitorPage({ data }: { data: PlatformSnapshot | null }) {
  const entries = Object.entries(data?.metrics ?? {})
  return <Page title="系统监控" subtitle="服务健康、延迟、队列与持久化"><Card><Descriptions values={[['Service Health', data?.health.healthy ? 'HEALTHY' : 'DEGRADED'], ['Event Queue', String(data?.metrics.event_queue_depth ?? 0)], ['Market Data Lag', String(data?.metrics.market_data_lag ?? '—')], ['Order Rate', String(data?.metrics.orders_per_min ?? 0)], ['Reject Rate', String(data?.metrics.reject_rate ?? 0)], ['Fill Rate', String(data?.metrics.fill_rate ?? 0)], ['Runtime Owner', 'SQLite 单进程租约'], ['指标数量', String(entries.length)]]}/></Card></Page>
}

function Descriptions({ values }: { values: [string, string][] }) {
  return <div className="description-grid">{values.map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>
}

function Page({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return <Space orientation="vertical" size={18} style={{ width: '100%' }}><div><Typography.Title level={2}>{title}</Typography.Title><Typography.Text type="secondary">{subtitle}</Typography.Text></div>{children}</Space>
}
