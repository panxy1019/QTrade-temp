import { Alert, Card, Empty, Input, Space, Table, Tabs, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useState } from 'react'
import type { HistoricalBarSeries, OrderRow, PlatformSnapshot, PositionRow } from '../types'
import { StatusTag } from '../components/StatusTag'
import { BacktestLab } from '../components/BacktestLab'
import { EquityChart } from '../charts/EquityChart'
import { MarketHistoryChart } from '../charts/MarketHistoryChart'
import { loadHistoricalBarSeries } from '../api/client'

const waiting = <Alert showIcon type="info" title="等待实时数据授权" description="适配器与回放链路已准备；当前未配置任何供应商令牌或真实连接。" />

export function MarketPage({ data }: { data: PlatformSnapshot | null }) {
  const historical = data?.market?.historical
  const [query, setQuery] = useState('')
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)
  const [series, setSeries] = useState<HistoricalBarSeries | null>(null)
  const [seriesError, setSeriesError] = useState<string | null>(null)
  const bars = (data?.historicalBars ?? []).filter((row) => row.symbol.includes(query.trim()))
  const activeSymbol = selectedSymbol ?? data?.historicalBars?.[0]?.symbol ?? null
  const datasetIdentity = historical ? `${historical.data_version}:${historical.checksum}` : null
  const displayedSeries = datasetIdentity
    && series?.symbol === activeSymbol
    && `${series.data_version}:${series.dataset_checksum}` === datasetIdentity ? series : null
  useEffect(() => {
    let active = true
    if (!activeSymbol || !datasetIdentity) {
      return () => { active = false }
    }
    void loadHistoricalBarSeries(activeSymbol).then((result) => {
      if (active) {
        setSeries(result)
        setSeriesError(null)
      }
    }).catch(() => {
      if (active) {
        setSeries(null)
        setSeriesError('历史 K 线加载失败；未使用缓存或合成数据替代。')
      }
    })
    return () => { active = false }
  }, [activeSymbol, datasetIdentity])
  const columns = [
    { title: '代码', dataIndex: 'symbol', fixed: 'left' as const },
    { title: '交易所', dataIndex: 'exchange' },
    { title: '交易日', dataIndex: 'trading_day' },
    { title: '开盘', dataIndex: 'open' },
    { title: '最高', dataIndex: 'high' },
    { title: '最低', dataIndex: 'low' },
    { title: '收盘', dataIndex: 'close' },
    { title: '成交量', dataIndex: 'volume', render: (value: string) => formatInteger(value) },
    { title: '数据性质', render: () => <Tag color="blue">历史日线收盘</Tag> },
  ]
  return <Page title="行情中心" subtitle="已验证历史日线与未来 Level-1 接入状态"><Space orientation="vertical" style={{ width: '100%' }}>{waiting}<Alert showIcon type={historical ? 'success' : 'warning'} title={historical ? `已验证历史数据：${historical.provider} ${historical.provider_version}` : '尚无校验通过的历史数据'} description={historical ? `${historical.bar_count} 根日线，${historical.observed_start} 至 ${historical.observed_end}；当前样本池非 PIT。` : '需要 checksum 与质量状态同时通过。'} /><Alert showIcon type="warning" title="以下是历史日线，不是实时行情" description="仅展示 checksum 验证通过的数据集；没有 Bid/Ask、实时价格或实时延迟，不参与 REAL_DATA_PAPER。" />{seriesError && <Alert showIcon type="error" title={seriesError} />}<Card title={`${activeSymbol ?? '—'} 已验证历史日 K 与成交量`}><MarketHistoryChart series={displayedSeries} /><Typography.Text type="secondary">{displayedSeries?.bars.length ? `${displayedSeries.bars.length}/${displayedSeries.total_count} 个完整交易日；数据版本 ${displayedSeries.data_version}。` : '点击下方 ETF 行切换标的。'} 图表来自同一 checksum 绑定的历史 Parquet，不是实时 K 线。</Typography.Text></Card><Input.Search placeholder="搜索 ETF 代码" value={query} onChange={(event) => setQuery(event.target.value)} allowClear /><Table rowKey="symbol" size="small" columns={columns} dataSource={bars} pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 920 }} onRow={(row) => ({ onClick: () => setSelectedSymbol(row.symbol), style: { cursor: 'pointer' } })} rowClassName={(row) => row.symbol === activeSymbol ? 'selected-market-row' : ''} locale={{ emptyText: <Empty description="暂无校验通过的历史日线；实时 snapshot 仍不可用" /> }} /></Space></Page>
}

export function StrategyPage({ data }: { data: PlatformSnapshot | null }) {
  const columns = [
    { title: '策略名称', dataIndex: 'name' }, { title: 'Record 哈希', dataIndex: 'version' },
    { title: 'Family', dataIndex: 'family' }, { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color="blue">{value}</Tag> },
    { title: '角色', dataIndex: 'role' },
    { title: '证据', dataIndex: 'evidence_status', render: () => <Tag color="orange">已验证·不可晋级</Tag> },
    { title: 'Sharpe', render: (_: unknown, row: { metrics: Record<string, string> }) => row.metrics.sharpe ?? '—' },
    { title: '最大回撤', render: (_: unknown, row: { metrics: Record<string, string> }) => row.metrics.maximum_drawdown ?? '—' },
  ]
  return <Page title="策略中心" subtitle="不可变记录与结果验证过的实验候选"><Alert showIcon type="warning" title="实验候选，不是已注册或已批准策略" description="每行均通过 comparison、record、result 与 artifact 哈希校验；spec_hash 仅校验格式，当前产物未携带足够字段供 API 独立复算。当前样本池非 PIT，状态 NOT_ELIGIBLE、角色 NONE，也没有已注册谱系，不是 Champion 或运行中 PAPER 策略。" /><Table rowKey="strategy_id" columns={columns} dataSource={data?.strategies ?? []} pagination={{ pageSize: 8, showSizeChanger: false }} locale={{ emptyText: '暂无 record/result/artifact 验证通过的实验候选' }} /></Page>
}

export function ResearchPage({ data }: { data: PlatformSnapshot | null }) {
  const report = data?.experiments
  const walkForward = data?.walkForward
  const robustness = data?.robustness
  const factorSearch = data?.factorSearch
  const robustFactorSearch = data?.robustFactorSearch
  const stockTransfer = data?.stockSectorTransfer
  const aggregate = walkForward?.result.aggregate_metrics
  const factorMetrics = factorSearch?.test.selected_metrics
  const benchmarkMetrics = factorSearch?.test.buy_hold_metrics
  const robustCandidateId = robustFactorSearch?.selection?.robust_candidate_id
  const robustSummary = robustFactorSearch?.candidate_summaries.find(
    (item) => item.candidate_id === robustCandidateId,
  )
  const robustStress = robustFactorSearch?.stress_tests.evaluations.find(
    (item) => item.candidate_id === robustCandidateId,
  )
  const doubledCost = robustStress?.cost_stresses.find(
    (item) => item.scenario_id === 'double_cost',
  )
  const robustBootstrap = robustFactorSearch?.bootstrap.evaluations.find(
    (item) => item.candidate_id === robustCandidateId,
  )
  const pollutedAudit = robustFactorSearch?.polluted_audit
  const transferFull = stockTransfer?.formal_period?.full_metrics
  const transferDouble = stockTransfer?.formal_period?.doubled_cost_metrics
  const transferAudit = stockTransfer?.polluted_audit
  const weakestSector = [...(stockTransfer?.sector_only ?? [])].sort(
    (left, right) => Number(left.metrics.total_return) - Number(right.metrics.total_return),
  )[0]
  return <Page title="策略研究" subtitle="实验候选、独立样本外证据与晋级边界">
    <Alert showIcon type="warning" title={`晋级结论：${report?.promotion ?? '未评估'}`} description={(report?.promotion_reasons ?? ['缺少可验证研究产物']).join(' / ')} />
    <Card><Tabs items={[
      { key: 'registry', label: '实验候选', children: <Space orientation="vertical"><Typography.Text>record/result/artifact 验证通过：{report?.experiments.length ?? 0} 个不可变实验记录。</Typography.Text><Typography.Text type="secondary">spec_hash 未独立复算；这些记录尚未注册为 StrategyVersion，也不存在可展示的 Champion、Challenger 或演化血缘。</Typography.Text></Space> },
      { key: 'factor-search', label: '因子搜索', children: <Space orientation="vertical"><Typography.Text>发现期通过：{factorSearch?.factor_selection?.passing_factor_ids.join('、') || '无'}；验证候选：{factorSearch?.validation.candidate_count ?? 0} 个。</Typography.Text><Typography.Text>验证胜者：{factorSearch?.validation.selected_candidate_id ?? '—'}；确认段收益 {formatPercent(String(factorMetrics?.total_return ?? '0'))}，Sharpe {String(factorMetrics?.sharpe ?? '—')}，最大回撤 {formatPercent(String(factorMetrics?.maximum_drawdown ?? '0'))}。</Typography.Text><Typography.Text>同期 Buy-and-Hold：{formatPercent(String(benchmarkMetrics?.total_return ?? '0'))}；主动超额：{formatPercent(factorSearch?.test.terminal_excess_return ?? '0')}。</Typography.Text><Typography.Text type="secondary">因子 Rank IC 仅用于发现期筛选；参数只按验证期选择。确认期曾被项目历史实验查看，不是全新盲测，结果固定为 {factorSearch?.promotion ?? '未评估'}，不能接入 PAPER。</Typography.Text></Space> },
      { key: 'factor-robustness', label: '因子鲁棒性', children: <Space orientation="vertical">
        <Typography.Text>冻结候选：{robustFactorSearch?.selection?.candidate_count ?? 0} 个；季度窗口：{robustFactorSearch?.selection?.window_ids.length ?? 0} 个；季度硬门槛合格：{robustFactorSearch?.selection?.window_gate_eligible_candidate_ids.length ?? 0} 个。</Typography.Text>
        <Typography.Text>鲁棒候选：{robustCandidateId ?? '无'}；正收益窗口 {formatPercent(robustSummary?.positive_window_ratio ?? '0')}；中位 Sharpe {formatNumber(robustSummary?.median_window_sharpe)}；最差 Sharpe {formatNumber(robustSummary?.worst_window_sharpe)}。</Typography.Text>
        <Typography.Text>双倍成本收益 {formatPercent(String(doubledCost?.metrics.total_return ?? '0'))}；邻参正收益比例 {formatPercent(robustStress?.parameter_neighbors.positive_ratio ?? '0')}；股票池子样本正收益比例 {formatPercent(robustStress?.universe_subsets.positive_ratio ?? '0')}；Bootstrap 正收益概率 {formatPercent(robustBootstrap?.probability_positive ?? '0')}。</Typography.Text>
        <Typography.Text>2026 污染审计：收益 {formatPercent(String(pollutedAudit?.selected_metrics?.total_return ?? '0'))}；主动超额 {formatPercent(pollutedAudit?.terminal_excess_return ?? '0')}；最大回撤 {formatPercent(String(pollutedAudit?.selected_metrics?.maximum_drawdown ?? '0'))}。</Typography.Text>
        <Typography.Text type="secondary">通过的是复用 2024–2025 数据的预声明研究鲁棒门槛，不是新的独立样本外确认。2026 只做污染审计；当前结论仍为 {robustFactorSearch?.promotion ?? '未评估'}，不能接入 PAPER。</Typography.Text>
      </Space> },
      { key: 'stock-sector-transfer', label: '多板块个股迁移', children: <Space orientation="vertical">
        <Typography.Text>数据：{stockTransfer?.data?.sector_count ?? 0} 个板块 × 每板块 {stockTransfer?.data?.stocks_per_sector ?? 0} 只，共 {stockTransfer?.data?.symbol_count ?? 0} 只股票、{stockTransfer?.data?.bar_count ?? 0} 根日线；统一 {stockTransfer?.data?.adjustment?.toUpperCase() ?? '—'}。</Typography.Text>
        <Typography.Text>锁定策略：{stockTransfer?.locked_strategy?.candidate_id ?? '—'}；2019–2025 正收益年份 {stockTransfer?.formal_period?.positive_year_count ?? 0}/7，单板块 {stockTransfer?.formal_period?.positive_sector_only_count ?? 0}/15，留一板块 {stockTransfer?.formal_period?.positive_leave_one_sector_out_count ?? 0}/15。</Typography.Text>
        <Typography.Text>全期收益 {formatPercent(String(transferFull?.total_return ?? '0'))}，年化 {formatPercent(String(transferFull?.annualized_return ?? '0'))}，Sharpe {formatNumber(String(transferFull?.sharpe ?? '0'))}，最大回撤 {formatPercent(String(transferFull?.maximum_drawdown ?? '0'))}；双倍成本收益 {formatPercent(String(transferDouble?.total_return ?? '0'))}。</Typography.Text>
        <Typography.Text>最弱单板块：{weakestSector?.sector_name ?? '—'}，收益 {formatPercent(String(weakestSector?.metrics.total_return ?? '0'))}；Bootstrap 正收益概率 {formatPercent(stockTransfer?.bootstrap.probability_positive ?? '0')}，5% 分位 {formatPercent(stockTransfer?.bootstrap.p05_total_return ?? '0')}。</Typography.Text>
        <Typography.Text>2026 污染审计：策略 {formatPercent(String(transferAudit?.strategy_metrics?.total_return ?? '0'))}，等权 {formatPercent(String(transferAudit?.equal_weight_metrics?.total_return ?? '0'))}，超额 {formatPercent(transferAudit?.terminal_excess_return ?? '0')}。</Typography.Text>
        <Typography.Text type="secondary">研究硬门：{stockTransfer?.transfer_gate?.passed ? '通过' : '未通过'}；但股票池是 2026 当前成分股向历史回投、后复权也不是时点一致数据，且未模拟涨跌停成交，结论固定为 {stockTransfer?.promotion ?? '未评估'}，不能接入 PAPER。</Typography.Text>
      </Space> },
      { key: 'walk-forward', label: '滚动样本外', children: <Space orientation="vertical"><Typography.Text>独立测试折数：{walkForward?.result.folds.length ?? 0}；正收益折比例：{walkForward?.result.fold_pass_ratio ?? '—'}</Typography.Text><Typography.Text>聚合 Sharpe：{String(aggregate?.sharpe ?? '—')}；聚合收益：{String(aggregate?.total_return ?? '—')}</Typography.Text><Typography.Text type="secondary">仅验证期选择候选；完整折区间互不重叠。结论：{walkForward?.promotion ?? '未评估'}。</Typography.Text></Space> },
      { key: 'robustness', label: '稳健性诊断', children: <Space orientation="vertical"><Typography.Text>场景数：{robustness?.result.scenario_metrics.length ?? 0}；最差 Sharpe：{robustness?.result.worst_sharpe ?? '—'}</Typography.Text><Typography.Text>离散度：{robustness?.result.dispersion ?? '—'}；存在负 Sharpe 场景：{robustness?.result.any_negative_sharpe ? '是' : '否'}</Typography.Text><Typography.Text type="secondary">这是复用样本的拒绝后诊断，不是新的独立测试证据。结论：{robustness?.promotion ?? '未评估'}。</Typography.Text></Space> },
      { key: 'lineage', label: '演化树', children: <Empty description="当前实验尚未注册策略血缘" /> },
      { key: 'rejected', label: '未晋级', children: <Typography.Text type="secondary">当前样本池存在幸存者偏差，且缺少 PAPER/SHADOW 证据。</Typography.Text> },
    ]} /></Card>
  </Page>
}

export function ExperimentPage({ data }: { data: PlatformSnapshot | null }) {
  const columns = [
    { title: '实验ID', dataIndex: 'experiment_id' },
    { title: '模型', dataIndex: 'model' },
    { title: '类型', dataIndex: 'scenario_type' },
    { title: '参数', dataIndex: 'parameters', render: (value: Record<string, string>) => Object.entries(value ?? {}).map(([key, item]) => `${key}=${item}`).join(', ') || '基准' },
    { title: '样本划分', dataIndex: 'split', render: (value: { test_start: string; test_end: string }) => value ? `${value.test_start} 至 ${value.test_end}` : '—' },
    { title: 'Universe', dataIndex: 'universe_size' },
    { title: '测试期收益', render: (_: unknown, row: { metrics: Record<string, string> }) => row.metrics.total_return ?? '—' },
    { title: 'Sharpe', render: (_: unknown, row: { metrics: Record<string, string> }) => row.metrics.sharpe ?? '—' },
    { title: '最大回撤', render: (_: unknown, row: { metrics: Record<string, string> }) => row.metrics.maximum_drawdown ?? '—' },
  ]
  return <Page title="实验中心" subtitle="真实 AKShare 历史数据上的可复现实验"><Alert showIcon type="warning" title={data?.experiments.classification ?? '尚无验证产物'} description={`数据版本：${data?.experiments.data_version ?? '—'}；每行已验证 record/result/artifact 哈希，但当前结论仍为 ${data?.experiments.promotion ?? '未评估'}。`} /><Table rowKey="experiment_id" columns={columns} dataSource={data?.experiments.experiments ?? []} pagination={{ pageSize: 8, showSizeChanger: false }} scroll={{ x: 1400 }} locale={{ emptyText: '尚未运行验证实验' }} /></Page>
}

export function BacktestPage({ data }: { data: PlatformSnapshot | null }) {
  const analytics = data?.backtestAnalytics
  const leader = analytics?.leader
  const benchmark = analytics?.benchmark
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
  const tradeColumns = [
    { title: '交易日', dataIndex: 'trading_day' }, { title: '证券', dataIndex: 'symbol' },
    { title: '方向', dataIndex: 'side', render: (value: string) => <Tag color={value === 'BUY' ? 'red' : 'green'}>{value === 'BUY' ? '买入' : '卖出'}</Tag> },
    { title: '模拟价格', dataIndex: 'price' }, { title: '数量', dataIndex: 'quantity' },
    { title: '模拟名义金额', dataIndex: 'notional', render: (value: string) => formatInteger(value) },
    { title: '模拟交易成本', dataIndex: 'cost', render: (value: string) => formatNumber(value) },
  ]
  const positionColumns = [{ title: '证券', dataIndex: 'symbol' }, { title: '期末数量', dataIndex: 'quantity' }]
  const monthlyColumns = [{ title: '月份', dataIndex: 'month' }, { title: '月收益', dataIndex: 'return', render: (value: string) => formatPercent(value) }]
  return <Page title="回测分析" subtitle="资金曲线、回撤、敞口、换手与成本">
    <BacktestLab key={data?.market.historical ? `${data.market.historical.data_version}:${data.market.historical.checksum}` : 'no-dataset'} dataset={data?.market.historical ?? null} />
    <Alert showIcon type="warning" title="探索性领先者，不是 Champion" description={leader ? `${leader.experiment_id}：测试窗 ${analytics?.split?.test_start ?? '—'} 至 ${analytics?.split?.test_end ?? '—'}，收益 ${formatPercent(leader.metrics.total_return)}，Sharpe ${formatNumber(leader.metrics.sharpe)}，最大回撤 ${formatPercent(leader.metrics.maximum_drawdown)}` : '暂无已验证结果'} />
    <Card title="已验证全期策略、基准、超额收益与回撤"><EquityChart dark analytics={analytics} />{leader && <Descriptions values={[["测试窗平均敞口", formatPercent(leader.metrics.test_exposure)], ["测试窗换手", formatNumber(leader.metrics.test_turnover)], ["测试窗交易成本", formatPercent(leader.metrics.test_transaction_cost)], ["Buy-and-Hold 测试收益", benchmark ? formatPercent(benchmark.metrics.total_return) : '—']]} />}<Typography.Text type="secondary">图为完整模拟期；Buy-and-Hold 仅在记录、资金曲线、回撤 checksum 全部通过且交易日逐点对齐时展示。测试窗指标在上方单列；当前样本池非 PIT，且缺少 PAPER/SHADOW 观察，禁止晋级。</Typography.Text></Card>
    <Alert showIcon type={hasWalkForwardEvidence ? 'error' : 'info'} title={hasWalkForwardEvidence ? `三折独立样本外：${positiveFolds}/${folds.length} 折正收益` : '暂无已验证滚动样本外证据'} description={hasWalkForwardEvidence ? `聚合收益 ${formatPercent(String(aggregate?.total_return ?? ''))}，聚合 Sharpe ${formatNumber(String(aggregate?.sharpe ?? ''))}，最大回撤 ${formatPercent(String(aggregate?.maximum_drawdown ?? ''))}。这组预先分折证据优先于上方事后挑选的探索性领先者。` : '滚动样本外报告缺失或未通过校验；不能据此声称策略通过或失败。'} />
    <Card title="验证期选择后的独立测试结果"><Table rowKey="fold" size="small" columns={foldColumns} dataSource={folds} pagination={false} scroll={{ x: 920 }} locale={{ emptyText: '暂无已验证滚动样本外结果' }} />{hasWalkForwardEvidence && <Typography.Text type="secondary">候选只按验证期 Sharpe 选择，选择前不可读取对应测试窗；三个测试区间互不重叠。当前结论：{walkForward?.promotion ?? '未评估'}，不得因探索性领先者的正收益覆盖样本外败绩。</Typography.Text>}</Card>
    <Card title="已验证月度收益"><Table rowKey="month" size="small" columns={monthlyColumns} dataSource={analytics?.monthly_returns ?? []} pagination={{ pageSize: 12, showSizeChanger: false }} locale={{ emptyText: '暂无已验证月度收益' }} /><Typography.Text type="secondary">月末对上月末计算；首月相对完整模拟期首个净值点。</Typography.Text></Card>
    <Card title="研究回测模拟成交"><Alert showIcon type="info" title="以下成交均为历史回测模拟成交，不是市场真实成交" /><Table rowKey={(row) => `${row.trading_day}-${row.symbol}-${row.side}-${row.quantity}`} size="small" columns={tradeColumns} dataSource={analytics?.trades ?? []} pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 900 }} locale={{ emptyText: '暂无已验证模拟成交' }} /></Card>
    <Card title="研究回测期末持仓"><Table rowKey="symbol" size="small" columns={positionColumns} dataSource={analytics?.positions ?? []} pagination={false} locale={{ emptyText: '期末无持仓或暂无已验证持仓产物' }} /></Card>
    <Card title="研究回测风险与运行契约"><Descriptions values={analytics?.risk_summary && analytics?.run_metadata ? [["全期最大回撤", formatPercent(analytics.risk_summary.maximum_drawdown)], ["全期换手", formatNumber(analytics.risk_summary.turnover)], ["模拟交易成本", formatNumber(analytics.risk_summary.transaction_costs)], ["因果时序", "收盘决策，下一开盘执行"], ["模拟会话数", String(analytics.run_metadata.session_count)]] : []} /><Typography.Text type="secondary">这是可从资金曲线与模拟成交独立重算的历史研究风险摘要，以及 checksum 绑定的运行契约；不是当前 PAPER 账户风险。未提供逐日证据的引擎自报字段不会展示为已验证。</Typography.Text></Card>
  </Page>
}

export function PaperPage({ data }: { data: PlatformSnapshot | null }) {
  return <Page title="模拟交易" subtitle="真实内核、模拟成交"><Space orientation="vertical" style={{ width: '100%' }}><Alert type="warning" showIcon title="仅模拟成交，不发送真实订单" description="账户现金、订单与成交来自当前持久化 PAPER 运行时；没有实时市价时不计算或展示虚构权益。" /><Descriptions values={[["现金", data?.account.cash ?? '—'], ["账户权益", data?.account.equity ?? '实时市价不可用'], ["持仓数", String(data?.positions.length ?? 0)], ["订单数", String(data?.orders.length ?? 0)], ["模拟成交数", String(data?.trades.length ?? 0)], ["已实现盈亏", data?.pnl.realized ?? '—']]} /><Table<OrderRow> rowKey="internal_order_id" dataSource={data?.orders ?? []} columns={[{ title: '内部委托ID', dataIndex: 'internal_order_id' }, { title: '策略', dataIndex: 'strategy_id' }, { title: '证券', dataIndex: 'symbol' }, { title: '方向', dataIndex: 'side' }, { title: '限价', dataIndex: 'price' }, { title: '状态', dataIndex: 'status' }, { title: '数量', dataIndex: 'quantity' }, { title: '已成交', dataIndex: 'filled_quantity' }]} pagination={{ pageSize: 10, showSizeChanger: false }} locale={{ emptyText: '当前 PAPER 运行时暂无委托' }} /></Space></Page>
}

export function PositionsPage({ data }: { data: PlatformSnapshot | null }) {
  const columns: ColumnsType<PositionRow> = [{ title: '证券', dataIndex: 'symbol' }, { title: '数量', dataIndex: 'quantity' }, { title: '可卖数量', dataIndex: 'sellable_quantity', render: (value: number | null) => value ?? '不可验证' }, { title: '平均成本', dataIndex: 'average_cost' }, { title: '成本口径持仓额', dataIndex: 'cost_basis_value' }, { title: '已实现盈亏', dataIndex: 'realized_pnl' }, { title: '实时估值', render: () => <Tag color="orange">市价不可用</Tag> }, { title: '可卖性日期', dataIndex: 'as_of_date' }]
  return <Page title="持仓" subtitle="持久化 PAPER 数量、成本与日期有效可卖性"><Alert showIcon type="warning" title="当前没有可信实时市价" description="市值、浮动盈亏和持仓权重保持 unavailable；不会用历史收盘价冒充当前估值。可卖数量仅在运行时 SellabilityLedger 可用时展示。" /><Table rowKey="symbol" columns={columns} dataSource={data?.positions ?? []} locale={{ emptyText: '当前 PAPER 账户无持仓' }} /></Page>
}

export function OrdersPage({ data }: { data: PlatformSnapshot | null }) {
  return <Page title="委托与成交" subtitle="持久化 PAPER 订单状态机与模拟成交"><Space orientation="vertical" style={{ width: '100%' }}><Alert showIcon type="info" title="以下成交均为 PAPER 模拟成交，不是交易所或券商回报" /><Card title="委托"><Table<OrderRow> rowKey="internal_order_id" dataSource={data?.orders ?? []} columns={[{ title: '内部委托ID', dataIndex: 'internal_order_id' }, { title: '策略', dataIndex: 'strategy_id' }, { title: '组合', dataIndex: 'portfolio_id' }, { title: '证券', dataIndex: 'symbol' }, { title: '方向', dataIndex: 'side' }, { title: '限价', dataIndex: 'price' }, { title: '数量', dataIndex: 'quantity' }, { title: '成交数量', dataIndex: 'filled_quantity' }, { title: '状态', dataIndex: 'status' }, { title: '更新时间', dataIndex: 'updated_at' }]} pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 1300 }} locale={{ emptyText: '当前 PAPER 运行时暂无委托' }} /></Card><Card title="模拟成交"><Table rowKey="trade_id" dataSource={data?.trades ?? []} columns={[{ title: '成交ID', dataIndex: 'trade_id' }, { title: '策略', dataIndex: 'strategy_id' }, { title: '证券', dataIndex: 'symbol' }, { title: '方向', dataIndex: 'side' }, { title: '价格', dataIndex: 'price' }, { title: '数量', dataIndex: 'quantity' }, { title: '费用', dataIndex: 'fee' }, { title: '时间', dataIndex: 'timestamp' }, { title: '性质', render: () => <Tag color="orange">PAPER 模拟</Tag> }]} pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 1200 }} locale={{ emptyText: '当前持久化运行时暂无已应用模拟成交' }} /></Card></Space></Page>
}

export function RiskPage({ data }: { data: PlatformSnapshot | null }) {
  const risk = data?.risk
  const state = risk?.state ?? 'HALTED'
  return <Page title="风控中心" subtitle="可从 PAPER 账本与订单重算的风险摘要"><Alert type={state === 'HALTED' ? 'error' : 'success'} showIcon title={<Space>当前风险状态 <StatusTag value={state} prominent /></Space>} description="敞口采用持仓成本加未成交 BUY 限价名义额，不是实时市值风险；没有可信实时市价时，回撤保持 unavailable。" /><Card className="risk-grid"><Descriptions values={[['Kill State', state], ['Gateway', risk?.gateway_connected ? 'CONNECTED' : 'DISCONNECTED'], ['Reconciliation', risk?.reconciliation_status ?? 'UNKNOWN'], ['成本口径持仓额', risk?.position_cost_basis ?? '—'], ['未成交买单名义额', risk?.open_buy_notional ?? '—'], ['成本口径总敞口', risk?.gross_exposure ?? '—'], ['最大持仓集中度', risk?.largest_position_concentration === null || risk?.largest_position_concentration === undefined ? '无持仓' : formatPercent(risk.largest_position_concentration)], ['未完成订单', String(risk?.open_order_count ?? 0)], ['已实现盈亏', risk?.realized_pnl ?? '—'], ['实时回撤', '市价不可用']]}/></Card></Page>
}

export function DataCenterPage({ data }: { data: PlatformSnapshot | null }) {
  const reportCount = data?.reports.length ?? 0
  const historical = data?.market?.historical
  return <Page title="数据中心" subtitle="供应商、质量、存储与回放"><Space orientation="vertical" style={{ width: '100%' }}>{waiting}<Descriptions values={[['Provider', historical ? `${historical.provider} ${historical.provider_version}` : '未配置'], ['Snapshot 连接', data?.market.provider_connected ? 'CONNECTED' : 'DISCONNECTED'], ['Snapshot 就绪', data?.market.snapshot_readiness ? 'YES' : 'NO'], ['数据版本', historical?.data_version ?? '—'], ['质量状态', historical?.quality_status ?? '等待数据'], ['历史 Bar', String(historical?.bar_count ?? 0)], ['PIT 样本池', historical?.universe.point_in_time ? 'YES' : 'NO'], ['Replay', '可用'], ['已验证每日报告', String(reportCount)]]}/><Card title="PAPER 每日报告历史"><Table rowKey="report_id" dataSource={data?.reports ?? []} columns={[{ title: '交易日', dataIndex: 'trading_day' }, { title: '报告ID', dataIndex: 'report_id' }, { title: '数据版本', dataIndex: 'data_version' }, { title: '策略版本', dataIndex: 'strategy_versions', render: (versions: string[]) => versions.join('、') }, { title: '生成时间', dataIndex: 'generated_at' }, { title: '完整性', render: () => <Tag color="green">SHA-256 已验证</Tag> }]} pagination={{ pageSize: 10, showSizeChanger: false }} locale={{ emptyText: '暂无经哈希与 schema 校验的 PAPER 每日报告；当前未启用自动日终生成' }} /></Card></Space></Page>
}

export function MonitorPage({ data }: { data: PlatformSnapshot | null }) {
  const entries = Object.entries(data?.metrics ?? {})
  const audit = data?.audit ?? []
  const auditAvailable = data?.audit != null
  const alerts = audit.filter((event) => event.severity === 'ERROR' || event.severity === 'CRITICAL')
  const minute = data?.minuteOverlay
  if (!auditAvailable) {
    return <Page title="系统监控" subtitle="服务健康、延迟、队列与持久化"><Alert showIcon type="error" title="持久化审计历史不可用" description="其他运行时快照仍可查看，但不能据此声称当前没有告警。" /><Card title="最近持久化审计与告警"><Empty description="审计历史不可用" /></Card></Page>
  }
  return <Page title="系统监控" subtitle="服务健康、延迟、队列与持久化"><Space orientation="vertical" style={{ width: '100%' }}><Card><Descriptions values={[['Service Health', data?.health.healthy ? 'HEALTHY' : 'DEGRADED'], ['Event Queue', String(data?.metrics.event_queue_depth ?? 0)], ['Market Data Lag', String(data?.metrics.market_data_lag ?? '—')], ['Order Rate', String(data?.metrics.orders_per_min ?? 0)], ['Reject Rate', String(data?.metrics.reject_rate ?? 0)], ['Fill Rate', String(data?.metrics.fill_rate ?? 0)], ['Runtime Owner', 'SQLite 单进程租约'], ['指标数量', String(entries.length)], ['持久化告警', data?.audit === null ? '不可用' : String(alerts.length)]]}/></Card><Alert showIcon type={minute?.readiness === 'READY' ? 'success' : 'warning'} title={`分钟修正：${minute?.readiness ?? 'UNAVAILABLE'}`} description={minute?.readiness === 'READY' ? `${minute.ready_symbol_count}/${minute.symbol_count} 个标的已完成 1 分钟预热，只允许降低日线基础仓位。` : '尚未接入授权实时 Tick 或分钟状态未完全预热；不会产生分钟 PAPER 目标。'} /><Card title="分钟级时间序列修正"><Descriptions values={[['行情 Provider', minute?.provider ?? '未配置'], ['分钟周期', minute?.bar_interval ?? '1m'], ['Provider 连接', minute?.provider_connected ? 'CONNECTED' : 'DISCONNECTED'], ['监控标的', String(minute?.symbol_count ?? 0)], ['已就绪标的', String(minute?.ready_symbol_count ?? 0)], ['仓位权限', '只减仓，不加仓'], ['执行边界', '封闭分钟线后的下一 Tick'], ['实盘订单', minute?.external_order_transmission ? '允许' : '禁止'], ['晋级状态', minute?.promotion ?? 'NOT_ELIGIBLE']]}/><Typography.Text type="secondary">分钟层不在盘中重搜因子参数；目标版本按交易日冻结，弱趋势、分钟波动、日内回撤及恢复冷却只负责收缩风险。</Typography.Text></Card>{data?.audit === null && <Alert showIcon type="error" title="持久化审计历史不可用" description="其他运行时快照仍可查看，但不能据此声称当前没有告警。" />}<Card title="最近持久化审计与告警"><Table rowKey={(row) => `${row.timestamp}-${row.event_type}-${row.order_id ?? ''}`} size="small" dataSource={audit} columns={[{ title: '时间', dataIndex: 'timestamp' }, { title: '级别', dataIndex: 'severity' }, { title: '事件', dataIndex: 'event_type' }, { title: '订单', dataIndex: 'order_id', render: (value: string | null) => value ?? '—' }, { title: '消息', dataIndex: 'message' }]} pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 900 }} locale={{ emptyText: data?.audit === null ? '审计历史不可用' : '暂无持久化审计事件' }} /><Typography.Text type="secondary">最多显示最近 500 条严格校验的 SQLite 审计记录；ERROR/CRITICAL 计入告警。SQLite 表不是哈希链或 WORM 存储。</Typography.Text></Card></Space></Page>
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

function formatInteger(value?: string) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? Math.round(parsed).toLocaleString('zh-CN') : '—'
}
