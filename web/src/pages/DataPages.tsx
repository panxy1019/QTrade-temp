import { Alert, Card, Empty, Input, Space, Table, Tabs, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import type { OrderRow, PlatformSnapshot, PositionRow } from '../types'
import { StatusTag } from '../components/StatusTag'
import { EquityChart } from '../charts/EquityChart'

const waiting = <Alert showIcon type="info" title="等待实时数据授权" description="适配器与回放链路已准备；当前未配置任何供应商令牌或真实连接。" />

export function MarketPage({ data }: { data: PlatformSnapshot | null }) {
  const historical = data?.market?.historical
  return <Page title="行情中心" subtitle="A股、ETF 与指数 Level-1 行情"><Space orientation="vertical" style={{ width: '100%' }}>{waiting}<Alert showIcon type={historical ? 'success' : 'warning'} title={historical ? `已验证历史数据：${historical.provider} ${historical.provider_version}` : '尚无校验通过的历史数据'} description={historical ? `${historical.bar_count} 根日线，${historical.observed_start} 至 ${historical.observed_end}；当前样本池非 PIT。` : '需要 checksum 与质量状态同时通过。'} /><Input.Search placeholder="搜索证券代码或名称" /><Empty description="实时 snapshot 仍不可用；不会用历史收盘价冒充行情" /></Space></Page>
}

export function StrategyPage({ data }: { data: PlatformSnapshot | null }) {
  const columns = [
    { title: '策略名称', dataIndex: 'name' }, { title: '版本', dataIndex: 'version' },
    { title: 'Family', dataIndex: 'family' }, { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color="blue">{value}</Tag> },
    { title: '角色', dataIndex: 'role' },
    { title: 'Sharpe', render: (_: unknown, row: { metrics: Record<string, string> }) => row.metrics.sharpe ?? '—' },
    { title: '最大回撤', render: (_: unknown, row: { metrics: Record<string, string> }) => row.metrics.maximum_drawdown ?? '—' },
  ]
  return <Page title="策略中心" subtitle="策略版本、运行状态与研究角色"><Alert showIcon type="info" title="研究实验，不是已批准策略" description="所有 AKShare 结果均为当前样本池探索，角色保持 NONE，不可视为 Champion。" /><Table rowKey="strategy_id" columns={columns} dataSource={data?.strategies ?? []} pagination={false} locale={{ emptyText: '暂无已验证研究记录' }} /></Page>
}

export function ResearchPage({ data }: { data: PlatformSnapshot | null }) {
  const report = data?.experiments
  const walkForward = data?.walkForward
  const robustness = data?.robustness
  const aggregate = walkForward?.result.aggregate_metrics
  return <Page title="策略研究" subtitle="血缘、候选与 Champion/Challenger"><Alert showIcon type="warning" title={`晋级结论：${report?.promotion ?? '未评估'}`} description={(report?.promotion_reasons ?? ['缺少可验证研究产物']).join(' / ')} /><Card><Tabs items={[{ key: 'registry', label: '策略注册表', children: <Typography.Text>已加载 {report?.experiments.length ?? 0} 个不可变实验记录。</Typography.Text> }, { key: 'walk-forward', label: '滚动样本外', children: <Space orientation="vertical"><Typography.Text>独立测试折数：{walkForward?.result.folds.length ?? 0}；正收益折比例：{walkForward?.result.fold_pass_ratio ?? '—'}</Typography.Text><Typography.Text>聚合 Sharpe：{String(aggregate?.sharpe ?? '—')}；聚合收益：{String(aggregate?.total_return ?? '—')}</Typography.Text><Typography.Text type="secondary">仅验证期选择候选；完整折区间互不重叠。结论：{walkForward?.promotion ?? '未评估'}。</Typography.Text></Space> }, { key: 'robustness', label: '稳健性诊断', children: <Space orientation="vertical"><Typography.Text>场景数：{robustness?.result.scenario_metrics.length ?? 0}；最差 Sharpe：{robustness?.result.worst_sharpe ?? '—'}</Typography.Text><Typography.Text>离散度：{robustness?.result.dispersion ?? '—'}；存在负 Sharpe 场景：{robustness?.result.any_negative_sharpe ? '是' : '否'}</Typography.Text><Typography.Text type="secondary">这是复用样本的拒绝后诊断，不是新的独立测试证据。结论：{robustness?.promotion ?? '未评估'}。</Typography.Text></Space> }, { key: 'lineage', label: '演化树', children: <Empty description="当前实验尚未形成已批准血缘" /> }, { key: 'rejected', label: '未晋级', children: <Typography.Text type="secondary">当前样本池存在幸存者偏差，且缺少 PAPER/SHADOW 证据。</Typography.Text> }]} /></Card></Page>
}

export function ExperimentPage({ data }: { data: PlatformSnapshot | null }) {
  const columns = [
    { title: '实验ID', dataIndex: 'experiment_id' },
    { title: '类型', dataIndex: 'scenario_type' },
    { title: '测试期收益', render: (_: unknown, row: { metrics: Record<string, string> }) => row.metrics.total_return ?? '—' },
    { title: 'Sharpe', render: (_: unknown, row: { metrics: Record<string, string> }) => row.metrics.sharpe ?? '—' },
    { title: '最大回撤', render: (_: unknown, row: { metrics: Record<string, string> }) => row.metrics.maximum_drawdown ?? '—' },
  ]
  return <Page title="实验中心" subtitle="真实 AKShare 历史数据上的可复现实验"><Alert showIcon type="info" title={data?.experiments.classification ?? '尚无验证产物'} description={`数据版本：${data?.experiments.data_version ?? '—'}`} /><Table rowKey="experiment_id" columns={columns} dataSource={data?.experiments.experiments ?? []} locale={{ emptyText: '尚未运行验证实验' }} /></Page>
}

export function BacktestPage({ data }: { data: PlatformSnapshot | null }) {
  const analytics = data?.backtestAnalytics
  const leader = analytics?.leader
  const walkForward = data?.walkForward
  const aggregate = walkForward?.result.aggregate_metrics
  const folds = walkForward?.result.folds ?? []
  const hasWalkForwardEvidence = folds.length > 0
  const positiveFolds = folds.filter((fold) => Number(fold.test_metrics.total_return) > 0).length
  const foldColumns = [
    { title: '折', dataIndex: 'fold', width: 56 },
    { title: '验证期选中候选', dataIndex: 'selected_candidate_id' },
    { title: '独立测试窗', render: (_: unknown, row: typeof folds[number]) => `${row.split.test_start} 至 ${row.split.test_end}` },
    { title: '测试收益', render: (_: unknown, row: typeof folds[number]) => formatPercent(String(row.test_metrics.total_return)) },
    { title: '测试 Sharpe', render: (_: unknown, row: typeof folds[number]) => formatNumber(String(row.test_metrics.sharpe)) },
    { title: '最大回撤', render: (_: unknown, row: typeof folds[number]) => formatPercent(String(row.test_metrics.maximum_drawdown)) },
    { title: '测试期交易', render: (_: unknown, row: typeof folds[number]) => String(row.test_metrics.number_of_trades ?? '—') },
  ]
  return <Page title="回测分析" subtitle="资金曲线、回撤、敞口、换手与成本">
    <Alert showIcon type="warning" title="探索性领先者，不是 Champion" description={leader ? `${leader.experiment_id}：测试窗 ${analytics?.split?.test_start ?? '—'} 至 ${analytics?.split?.test_end ?? '—'}，收益 ${formatPercent(leader.metrics.total_return)}，Sharpe ${formatNumber(leader.metrics.sharpe)}，最大回撤 ${formatPercent(leader.metrics.maximum_drawdown)}` : '暂无已验证结果'} />
    <Card title="已验证全期资金曲线与回撤"><EquityChart dark analytics={analytics} />{leader && <Descriptions values={[["测试窗平均敞口", formatPercent(leader.metrics.test_exposure)], ["测试窗换手", formatNumber(leader.metrics.test_turnover)], ["测试窗交易成本", formatPercent(leader.metrics.test_transaction_cost)]]} />}<Typography.Text type="secondary">图为完整模拟期，测试窗指标在上方单列。记录、资金曲线和回撤文件的哈希均已验证；当前样本池非 PIT，且缺少 PAPER/SHADOW 观察，禁止晋级。</Typography.Text></Card>
    <Alert showIcon type={hasWalkForwardEvidence ? 'error' : 'info'} title={hasWalkForwardEvidence ? `三折独立样本外：${positiveFolds}/${folds.length} 折正收益` : '暂无已验证滚动样本外证据'} description={hasWalkForwardEvidence ? `聚合收益 ${formatPercent(String(aggregate?.total_return ?? ''))}，聚合 Sharpe ${formatNumber(String(aggregate?.sharpe ?? ''))}，最大回撤 ${formatPercent(String(aggregate?.maximum_drawdown ?? ''))}。这组预先分折证据优先于上方事后挑选的探索性领先者。` : '滚动样本外报告缺失或未通过校验；不能据此声称策略通过或失败。'} />
    <Card title="验证期选择后的独立测试结果"><Table rowKey="fold" size="small" columns={foldColumns} dataSource={folds} pagination={false} scroll={{ x: 920 }} locale={{ emptyText: '暂无已验证滚动样本外结果' }} />{hasWalkForwardEvidence && <Typography.Text type="secondary">候选只按验证期 Sharpe 选择，选择前不可读取对应测试窗；三个测试区间互不重叠。当前结论：{walkForward?.promotion ?? '未评估'}，不得因探索性领先者的正收益覆盖样本外败绩。</Typography.Text>}</Card>
  </Page>
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
  const historical = data?.market?.historical
  return <Page title="数据中心" subtitle="供应商、质量、存储与回放"><Space orientation="vertical" style={{ width: '100%' }}>{waiting}<Descriptions values={[['Provider', historical ? `${historical.provider} ${historical.provider_version}` : '未配置'], ['Snapshot 连接', data?.market.provider_connected ? 'CONNECTED' : 'DISCONNECTED'], ['Snapshot 就绪', data?.market.snapshot_readiness ? 'YES' : 'NO'], ['数据版本', historical?.data_version ?? '—'], ['质量状态', historical?.quality_status ?? '等待数据'], ['历史 Bar', String(historical?.bar_count ?? 0)], ['PIT 样本池', historical?.universe.point_in_time ? 'YES' : 'NO'], ['Replay', '可用'], ['每日报告', String(reportCount)]]}/></Space></Page>
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

function formatNumber(value?: string) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toFixed(3) : '—'
}

function formatPercent(value?: string) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `${(parsed * 100).toFixed(2)}%` : '—'
}
