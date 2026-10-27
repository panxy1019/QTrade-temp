import { useEffect, useState } from 'react'
import { Alert, Button, Card, Col, Descriptions, Row, Space, Table, Tag, Typography } from 'antd'
import { Link } from 'react-router-dom'
import { loadDeployment, loadUnifiedSummary } from '../api/client'
import { useResearchWorkflow } from '../components/ResearchWorkflowProvider'
import { useReadOnlySnapshot } from '../hooks/useReadOnlySnapshot'
import { workflowGet } from '../research/qlibWorkflow'
import { ageSeconds, chinaTime } from '../research/unifiedMarket'
import type { Catalog as OnlineCatalog } from './OnlineModelsPage'
import type { Reply as ForwardReply } from './ForwardResearchPage'
import type { Reply as EtfReply } from '../components/EtfShadowPanel'
import type { PlatformSnapshot } from '../types'

const readUnified = async (signal: AbortSignal) => {
  const value = await loadUnifiedSummary({ signal })
  if (value.live_trading_enabled !== false || !value.master?.universe_version) throw new Error('统一目录身份无效')
  return value
}
const readOnline = (signal: AbortSignal) => workflowGet<OnlineCatalog>('/online/catalog', { signal })
const readForward = (signal: AbortSignal) => workflowGet<ForwardReply>('/online/forward-books', { signal })
const readEtf = (signal: AbortSignal) => workflowGet<EtfReply>('/online/etf-forward', { signal })
const readDeployment = (signal: AbortSignal) => loadDeployment({ signal })
const unavailable = (error: string | null) => error ? `读取失败：${error}` : '尚无可用发布'
const identity = (value: string | null | undefined) => value ?? '未取得'

export function OverviewPage({ publicNode }: { data: PlatformSnapshot | null; dark?: boolean; publicNode: boolean }) {
  const [refresh, setRefresh] = useState(0)
  const [now, setNow] = useState(Date.now)
  const unified = useReadOnlySnapshot(readUnified, 60_000, refresh)
  const online = useReadOnlySnapshot(readOnline, 15_000, refresh)
  const forward = useReadOnlySnapshot(readForward, 30_000, refresh)
  const etf = useReadOnlySnapshot(readEtf, 30_000, refresh)
  const deployment = useReadOnlySnapshot(readDeployment, 60_000, refresh)
  const { catalog, error: researchError } = useResearchWorkflow()
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer) }, [])
  const source = unified.value
  const sourceAge = ageSeconds(source?.built_at, now)
  const models = online.value?.publication_available ? online.value : null
  const modelAge = ageSeconds(models?.published_at, now)
  const modelFresh = !!models?.worker_fresh && modelAge !== null && modelAge <= 720
  const bookPublication = forward.value?.publication_available && forward.value.publication?.schema_version === 'qtrade.forward-service.v1'
    && forward.value.publication.live_trading_enabled === false && forward.value.publication.external_order_transmission === false ? forward.value.publication : null
  const books = bookPublication?.books ?? []
  const bookAge = ageSeconds(bookPublication?.heartbeat_at, now)
  const bookFresh = !!forward.value?.service_fresh && bookAge !== null && bookAge <= 720 && bookPublication?.state !== 'STOPPED'
  const etfPublication = etf.value?.publication_available && etf.value.publication?.schema_version === 'qtrade.etf-shadow-service.v1' ? etf.value.publication : null
  const etfAge = ageSeconds(etfPublication?.heartbeat_at, now)
  const etfFresh = !!etf.value?.service_fresh && etfAge !== null && etfAge <= 720 && etfPublication?.state !== 'STOPPED'
  const predicted = models?.auto_predict?.predictions?.find(row => row.prediction_id === models.auto_predict?.current_prediction_id)
  const predictionAge = ageSeconds(predicted?.contract?.generated_at, now)
  const predictionCurrent = !!models?.current_prediction_usable && modelFresh && predicted?.state === 'COMPLETE'
    && predicted.bindings.model_id === models.active_model && predicted.bindings.data_version_id === models.data_version?.version_id
    && predicted.bindings.data_end === models.data_version?.actual_end
    && predictionAge !== null && !!predicted.contract && Date.parse(predicted.contract.expected_execution_at) > now
  const active = models?.models.find(row => row.definition.model_id === models.active_model)
  const complete = catalog?.experiments.filter(row => row.state === 'COMPLETE') ?? []
  const tasks = catalog?.experiments.filter(row => !['COMPLETE', 'FAILED', 'CANCELLED', 'INTERRUPTED'].includes(row.state)) ?? []
  return <Space orientation="vertical" size={18} style={{ width: '100%' }}>
    <div className="page-title-row"><div><Typography.Title level={2}>总览</Typography.Title>
      <Typography.Text type="secondary">统一采集、研究任务、在线模型与前向观察的当前业务状态</Typography.Text></div>
      <Space><Tag color="blue">仅研究 / PAPER / SHADOW</Tag><Button onClick={() => setRefresh(n => n + 1)}>刷新业务状态</Button></Space>
    </div>
    <Alert showIcon type="info" title="各环节独立核对，不以采集成功代替模型或策略就绪"
      description="证券池目标数量、行情已覆盖数量、Qlib可用股票数和实际模拟账本分别展示。所有时间标为上海时区；历史回测结果保留原区间，不作为今天的收益。LIVE_TRADING_ENABLED=false。" />
    <Row gutter={[16, 16]}>
      <Col xs={24} xl={12}><Card title="1 · 统一目标池与日常采集" extra={<Link to="/market">查看行情</Link>}>
        {!source ? <Alert type="warning" title={unavailable(unified.error)} /> : <>
          {(sourceAge === null || sourceAge > 1200 || Object.values(source.incremental_freshness ?? {}).some(row => row.status === 'STALE_PUBLICATION'))
            && <Alert type="warning" showIcon title="统一目录发布迟到或时钟无效；以下为最后发布的状态" />}
          <Typography.Paragraph>观察目标 {source.master.target_actual} / {source.master.target_count} 只；股票 {source.master.target_by_type.STOCK ?? 0}，ETF {source.master.target_by_type.ETF ?? 0}。目标池不等于已覆盖池。</Typography.Paragraph>
          <Table size="small" pagination={false} rowKey="frequency" dataSource={(['day', '5min'] as const).map(frequency => ({ frequency, status: source.incremental_freshness?.[frequency] }))}
            columns={[
              { title: '频率', render: (_, row) => row.frequency === 'day' ? '日线' : '五分钟' },
              { title: '当前时间齐备 / 应有', render: (_, row) => row.status ? `${row.status.ready_symbols} / ${row.status.required_symbols} · ${row.status.status}` : '尚无核验' },
              { title: '应覆盖至（上海）', render: (_, row) => chinaTime(row.status?.expected_end) },
            ]} />
          <Descriptions size="small" column={1} items={[
            { key: 'state', label: '采集调度', children: source.master.collection?.state ?? '未取得' },
            { key: 'collection', label: '采集进度更新（上海）', children: chinaTime(source.master.collection?.updated_at) },
            { key: 'snapshot', label: '目录发布（上海）', children: `${chinaTime(source.built_at)} · ${ageSeconds(source.built_at, now) ?? '未知'} 秒前` },
            { key: 'universe', label: '证券池版本', children: <Typography.Text copyable>{source.master.universe_version}</Typography.Text> },
          ]} />
          <Typography.Text type="secondary">新浪原生价格，复权与历史可得时点未认证。<Link to="/data">查看质量、缺口与长期回补</Link></Typography.Text>
        </>}
      </Card></Col>
      <Col xs={24} xl={12}><Card title="2 · Qlib 日版本与研究预测" extra={<Link to="/models">模型与版本详情</Link>}>
        {!models ? <Alert type="warning" title={unavailable(online.error)} /> : <>
          <Space wrap><Tag color={modelFresh ? 'blue' : 'orange'}>模型服务{modelFresh ? '心跳有效' : '心跳过期或时钟未知'}</Tag>
            <Tag>{models.data_schedule?.last_cycle_state ?? models.data_schedule?.state ?? '日版本调度状态未取得'}</Tag>
            <Tag color={predictionCurrent ? 'blue' : 'orange'}>{predictionCurrent ? '当前研究预测有效' : '当前研究预测不可用'}</Tag></Space>
          <Descriptions size="small" column={1} items={[
            { key: 'day', label: '最近完整交易日', children: identity(models.expected_complete_day) },
            { key: 'end', label: 'Qlib 实际数据截止', children: identity(models.data_version?.actual_end) },
            { key: 'count', label: '本版本实际可用股票', children: models.data_version?.current_symbols ?? '未取得' },
            { key: 'version', label: '数据版本', children: <Typography.Text copyable>{identity(models.data_version?.version_id)}</Typography.Text> },
            { key: 'model', label: '人工批准 ACTIVE', children: active ? `${active.definition.name} · ${active.definition.model_id}` : identity(models.active_model) },
            { key: 'prediction', label: '预测身份', children: identity(predicted?.prediction_id) },
            { key: 'generated', label: '预测生成（上海）', children: chinaTime(predicted?.contract?.generated_at) },
            { key: 'execution', label: '预测对应开盘（上海）', children: chinaTime(predicted?.contract?.expected_execution_at) },
            { key: 'published', label: '模型目录发布（上海）', children: chinaTime(models.published_at) },
            { key: 'schedule', label: '日版本调度心跳（上海）', children: `${chinaTime(models.data_schedule?.heartbeat_at)} · ${models.data_schedule_fresh === true ? '服务端核验有效' : '尚无有效心跳证明'}` },
          ]} />
          <Typography.Text type="secondary">日版本不会自动覆盖全部统一池。数据更新可触发研究预测，模型换代须人工批准；预测目标不是成交。</Typography.Text>
        </>}
      </Card></Col>
    </Row>
    <Card title="3 · 持久前向账本与 ETF 观察" extra={<Link to="/paper">查看冻结目标与观察明细</Link>}>
      {!bookPublication ? <Alert type="warning" title={`股票前向：${unavailable(forward.error)}`} /> : <>
        <Typography.Paragraph>股票前向 {bookPublication.state} · {bookFresh ? '心跳有效' : '心跳过期，仅历史记录'}；心跳 {chinaTime(bookPublication.heartbeat_at)}；生成 {chinaTime(bookPublication.generated_at)}。</Typography.Paragraph>
        <Table size="small" rowKey={row => `${row.strategy_id}:${row.mode}`} pagination={{ pageSize: 6 }} scroll={{ x: 850 }} dataSource={books} columns={[
          { title: '策略 / 模式', render: (_, row) => `${row.strategy_id} · ${row.mode}` },
          { title: '风险 / 恢复', render: (_, row) => row.mode === 'SHADOW' ? `${row.risk_state} · 仅观察／不启用交易运行时` : `${row.risk_state}${row.recovery_required ? ' · 需人工恢复' : ''}${row.running ? ' · 运行' : ' · 停止'}` },
          { title: '最新冻结目标（上海）', render: (_, row) => chinaTime(row.frozen_targets[0]?.frozen_at) },
          { title: '最近已观察日', render: (_, row) => row.sessions[0]?.day ?? '尚无完整日观察' },
          { title: '最近观察 / 结果', render: (_, row) => `${row.sessions[0]?.state ?? '未发生'}；${row.mode === 'SHADOW' ? '仅目标与行情' : row.sessions[0]?.net_asset_value == null ? '净值尚无可报告值' : `模拟净值 ${row.sessions[0].net_asset_value}`}` },
        ]} />
      </>}
      <Typography.Paragraph>{etfPublication ? `ETF SHADOW：${etfPublication.state} · ${etfFresh ? '心跳有效' : '心跳过期，仅历史记录'}；心跳 ${chinaTime(etfPublication.heartbeat_at)}；已封存 ${etfPublication.entries.length} 份目标，${etfPublication.entries.filter(row => row.observation !== null).length} 份后来行情观察。` : `ETF SHADOW：${unavailable(etf.error)}`}</Typography.Paragraph>
      <Typography.Text type="secondary">PAPER 的交易约束未知时阻断调仓；HALT 须人工对账恢复。SHADOW 不产生订单、成交、持仓、净值或组合收益，冻结目标不代表开盘已执行。</Typography.Text>
    </Card>
    <Row gutter={[16, 16]}>
      <Col xs={24} xl={12}><Card title="研究任务与已完成历史对照">
        <Typography.Paragraph>{catalog?.publication_available ? `集群任务 ${catalog.worker.state}；进行中 ${tasks.length}，已完成历史档案 ${complete.length}。心跳 ${chinaTime(catalog.worker.heartbeat_at)}。` : unavailable(researchError)}</Typography.Paragraph>
        <Space wrap><Link to="/research">配置并提交研究</Link><Link to="/experiments">任务与原日期归档</Link><Link to="/backtest">已完成历史回测对照</Link><Link to="/archive">旧范围研究参考</Link></Space>
        <Typography.Paragraph type="secondary">历史指标属于实验固定区间和假设，不能作为当天结果或模型自动批准依据。</Typography.Paragraph>
      </Card></Col>
      <Col xs={24} xl={12}><Card title="4 · 网页与业务发布身份">
        <Descriptions size="small" column={1} items={[
          { key: 'node', label: '当前节点', children: publicNode ? '公开只读研究节点' : '本地安全核心' },
          { key: 'web', label: '网页代码版本', children: identity(deployment.value?.git_commit_sha) },
          { key: 'forward', label: '股票前向代码版本', children: identity(bookPublication?.source_revision) },
          { key: 'catalog', label: '统一目录证据', children: identity(source?.evidence_sha256) },
        ]} />
        <Typography.Text type="secondary">不同发布物各有时间与身份；刷新页面不代表采集或模型已刷新。<Link to="/monitor">查看原平台监控</Link></Typography.Text>
      </Card></Col>
    </Row>
  </Space>
}
