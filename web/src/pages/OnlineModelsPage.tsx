import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Descriptions, Modal, Space, Table, Tag, Typography } from 'antd'
import { useResearchWorkflow } from '../components/ResearchWorkflowProvider'
import { JsonExportButton } from '../components/JsonExportButton'
import { workflowGet, workflowPost } from '../research/qlibWorkflow'
import { Login } from './QlibWorkflowPages'
import { RollingPolicyPanel, type RollingCatalog } from '../components/RollingPolicyPanel'

interface Definition {
  model_id: string; model_run_id: string; name: string; training_data_end: string
  training_code_revision: string; feature_count: number; limitations: string[]
  portfolio: { topk: number; n_drop: number }; metrics: Record<string, unknown>
  segments: Record<string, [string, string]>
  cost_assumptions: { open_cost: number; close_cost: number; min_cost: number; impact_cost: number; deal_price: string }
}
interface Model { definition: Definition; state: string; activated_at: string | null }
interface Validation {
  validation_id: string; model_bundle_sha256: string; score_count: number
  contract: { data_version_id: string; data_end: string; generated_at: string; expected_execution_at: string }
}
interface Command {
  request_id: string; operation: 'ACTIVATE' | 'ROLLBACK'; model_id: string
  expected_active: string | null; data_version_id: string; validation_id: string
}
interface Catalog {
  publication_available: boolean; worker_fresh: boolean; published_at?: string; data_schedule_fresh?: boolean
  active_model: string | null; models: Model[]; validations: Validation[]
  expected_complete_day: string | null
  data_version: null | { version_id: string; actual_end: string; current_symbols: number; historical_symbols: number; blocked_symbols: string[] }
  commands: Array<{ request_id: string; state: string; operation: string; error_code: string | null; at: string }>
  current_prediction_usable?: boolean
  data_schedule?: { state: string; fresh: boolean; heartbeat_at?: string; last_cycle_state?: string; required_day?: string; data_end?: string }
  auto_predict?: { enabled: boolean; state: string; current_prediction_id?: string | null; predictions?: Array<{
    prediction_id: string; state: string; updated_at: string; error_code: string | null
    bindings: { model_id: string; data_end: string; data_version_id: string }
    contract?: { generated_at: string; expected_execution_at: string }; score_count?: number
    limitations?: string[]
  }> }; auto_train?: RollingCatalog
}
const labels: Record<string, string> = { TRAINED: '训练完成', VALIDATING: '验证中', VALIDATED: '验证通过', STAGED: '待人工批准', ACTIVE: '当前研究模型', FAILED: '失败', RETIRED: '历史模型' }
const predictionLabels: Record<string, string> = { WAITING_FOR_APPROVAL: '等待人工批准模型', RUNNING: '计算中', QUEUED: '等待计算', COMPLETE: '已封存', SUPERSEDED: '已被后续模型、数据或时效替代', INTERRUPTED: '中断，等待恢复核对', FAILED: '失败', BLOCKED_STALE_DATA: '数据未覆盖最近完整交易日', BLOCKED_INPUT_UNAVAILABLE: '输入不可用', BLOCKED_INPUT_OR_LAUNCH_FAILURE: '输入或启动失败', BLOCKED_STORAGE_RESERVE: '磁盘保留空间不足', EVENT_WORKER_INTEGRATION_PENDING: '调度尚未接入' }
const dataLabels: Record<string, string> = { CURRENT_DAY_ALREADY_READY: '当日版本已就绪', DAILY_VERSION_PUBLISHED: '新日版本已发布', PREPARING_DAILY_INPUTS: '正在准备当日输入', WAITING_FOR_CURRENT_COLLECTION: '等待日常采集空闲', WAITING_FOR_CURRENT_DAILY_INPUTS: '等待最新日线', WAITING_FOR_RETRY: '等待重试', WAITING_FOR_BUILD_RETRY: '等待构建重试', BLOCKED_MISSED_SESSION_REQUIRES_BACKFILL: '存在断档，需要补齐后再推进', BLOCKED_EXECUTION_WINDOW_PASSED: '已错过本次研究执行时点', BLOCKED_STORAGE_RESERVE: '磁盘保留空间不足', BLOCKED_DAILY_INPUT_CHANGED_REVIEW_REQUIRED: '当日输入或代码变化，需要核对', NOT_DEPLOYED: '尚未部署' }

function ModelEvidence({ definition: d }: { definition: Definition }) {
  const metric = (key: string, percent = false) => typeof d.metrics[key] === 'number' && Number.isFinite(d.metrics[key])
    ? `${((d.metrics[key] as number) * (percent ? 100 : 1)).toFixed(percent ? 2 : 4)}${percent ? '%' : ''}` : '未提供'
  return <Descriptions column={1} size="small" items={[
    { key: 'test', label: '历史样本外区间', children: d.segments.test?.join(' 至 ') ?? '未提供' },
    { key: 'ic', label: 'IC / RankIC', children: `${metric('ic')} / ${metric('rank_ic')}` },
    { key: 'return', label: '历史净年化 / Sharpe', children: `${metric('annual_return_net', true)} / ${metric('sharpe_net')}` },
    { key: 'risk', label: '最大回撤 / 日均换手', children: `${metric('max_drawdown', true)} / ${metric('mean_daily_turnover', true)}` },
    { key: 'cost', label: '成本与成交假设', children: `买入 ${d.cost_assumptions.open_cost * 10000} bp，卖出 ${d.cost_assumptions.close_cost * 10000} bp，最低 ${d.cost_assumptions.min_cost} 元；冲击 ${d.cost_assumptions.impact_cost * 10000} bp；${d.cost_assumptions.deal_price === 'open' ? '下一交易日开盘价' : d.cost_assumptions.deal_price}` },
  ]} />
}

export function OnlineModelsPage() {
  const { session } = useResearchWorkflow()
  const [catalog, setCatalog] = useState<Catalog | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [review, setReview] = useState<{ command: Command; model: Model; proof: Validation } | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const [now, setNow] = useState(() => Date.now())
  const sequence = useRef(0)
  const refresh = useCallback(async () => {
    const request = ++sequence.current
    try { const result = await workflowGet<Catalog>('/online/catalog'); if (sequence.current === request) { setCatalog(result); setError(null) } }
    catch (reason) { if (sequence.current === request) { setCatalog(null); setError(String(reason)) } }
  }, [])
  const invalidate = useCallback(() => { sequence.current++ }, [])
  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0)
    const timer = window.setInterval(() => void refresh(), 15000)
    const clock = window.setInterval(() => setNow(Date.now()), 1000)
    return () => { invalidate(); window.clearTimeout(initial); window.clearInterval(timer); window.clearInterval(clock) }
  }, [refresh, invalidate])
  const data = catalog?.data_version
  const ready = !!(catalog?.publication_available && catalog.worker_fresh && data && data.actual_end === catalog.expected_complete_day)
  const latestProof = (model: Model) => catalog?.validations.filter(v => v.model_bundle_sha256 === model.definition.model_id && v.contract.data_version_id === data?.version_id).sort((a, b) => b.contract.generated_at.localeCompare(a.contract.generated_at))[0]
  const choose = (model: Model) => {
    const proof = latestProof(model)
    if (!proof || !data || !catalog) return
    setReview({ model, proof, command: { request_id: crypto.randomUUID().replaceAll('-', ''), operation: model.state === 'RETIRED' ? 'ROLLBACK' : 'ACTIVATE', model_id: model.definition.model_id, expected_active: catalog.active_model, data_version_id: data.version_id, validation_id: proof.validation_id } })
  }
  const changed = review && (review.command.data_version_id !== data?.version_id || review.command.expected_active !== catalog?.active_model || catalog?.models.find(m => m.definition.model_id === review.command.model_id)?.state !== review.model.state || Date.parse(review.proof.contract.expected_execution_at) <= now)
  const submit = async () => {
    if (!review) return
    setBusy(true)
    try {
      const result = await workflowPost<{ state: string; error_code?: string }>('/online/actions', review.command)
      setNotice(result.state === 'QUEUED' ? `操作已排队：${review.command.request_id}。以集群回执为准，通常在下一次发布后显示。` : `集群回执：${result.state} ${result.error_code ?? ''}`)
      setReview(null); await refresh()
    } catch (reason) { setError(String(reason)) }
    finally { setBusy(false) }
  }
  return <Space orientation="vertical" size="large" style={{ width: '100%' }}>
    <Space wrap><Typography.Title level={2} style={{ margin: 0 }}>在线模型管理</Typography.Title><Tag color="blue">Qlib 研究</Tag><Button onClick={() => void refresh()}>刷新模型目录</Button></Space>
    <Typography.Paragraph>查看已验收的数据版本、冻结候选和操作记录。模型批准只影响研究模型选择；每日自动预测与重训按下方实际状态显示。</Typography.Paragraph>
    <Login />
    {error && <Alert type="error" showIcon title={error} />}
    {notice && <Alert type="info" showIcon title={notice} />}
    {!catalog?.publication_available && <Alert type="warning" showIcon title="在线模型目录尚不可用" description="等待集群发布经校验的目录，模型操作入口保持关闭。" />}
    {catalog?.publication_available && !catalog.worker_fresh && <Alert type="error" showIcon title="模型控制心跳已过期，操作入口关闭" />}
    <Card title="最近合格的 Qlib 数据版本">
      {data ? <Descriptions column={1} items={[
        { key: 'day', label: '真实数据截止日', children: `${data.actual_end}（最近完整交易日：${catalog?.expected_complete_day ?? '未知'}）` },
        { key: 'scope', label: '已验收范围', children: `${data.current_symbols} 只当日合格 / ${data.historical_symbols} 只历史保留` },
        { key: 'missing', label: '当日缺口', children: data.blocked_symbols.join('、') || '无' },
        { key: 'version', label: '不可变版本', children: <Typography.Text copyable>{data.version_id}</Typography.Text> },
      ]} /> : <Typography.Text>尚无可核验的数据版本。</Typography.Text>}
    </Card>
    {catalog?.data_schedule && <Alert type={catalog.data_schedule_fresh ? 'info' : 'warning'} showIcon title={`日版本调度：${dataLabels[catalog.data_schedule.last_cycle_state ?? catalog.data_schedule.state] ?? catalog.data_schedule.state}`} description={`调度心跳${catalog.data_schedule_fresh ? '有效' : '不可用或过期'}；要求截止日 ${catalog.data_schedule.required_day ?? '未知'}。运行中的调度进程不等于当天数据已经验收；以最近合格版本及其缺口为准。`} />}
    <Alert type="info" showIcon title={`每日自动预测：${catalog?.auto_predict?.enabled ? '已启用' : '未启用'}；自动重训：${catalog?.auto_train?.mode ?? '未接入'}`} description="当前候选输入验证与生产自动预测分开记录。验证分数未取得真实前向收益；完整历史 PIT、复权与交易规则认证仍有限制。" />
    <RollingPolicyPanel value={catalog?.auto_train} authenticated={session.authenticated} ready={!!catalog?.worker_fresh} refresh={refresh} />
    {catalog?.auto_predict?.enabled && <Card title="每日自动预测记录">
      <Typography.Paragraph>{predictionLabels[catalog.auto_predict.state] ?? catalog.auto_predict.state}。{catalog.current_prediction_usable ? '存在身份匹配且尚未过期的研究预测。' : '当前没有可使用的研究预测。'}分数仅用于研究排序，不是交易指令或收益承诺。</Typography.Paragraph>
      <Table rowKey="prediction_id" dataSource={catalog.auto_predict.predictions ?? []} pagination={{ pageSize: 5 }} scroll={{ x: 1000 }} columns={[
        { title: '数据截止', render: (_, row) => row.bindings.data_end },
        { title: '状态', render: (_, row) => predictionLabels[row.state] ?? row.state },
        { title: '模型身份', render: (_, row) => <Typography.Text copyable={{ text: row.bindings.model_id }}>{row.bindings.model_id.slice(0, 12)}</Typography.Text> },
        { title: '证券数', dataIndex: 'score_count', render: value => value ?? '尚无结果' },
        { title: '实际生成', render: (_, row) => row.contract?.generated_at ?? '尚未生成' },
        { title: '预期研究执行', render: (_, row) => row.contract?.expected_execution_at ?? '尚无结果' },
        { title: '诊断', dataIndex: 'error_code' },
      ]} expandable={{ expandedRowRender: row => <Space orientation="vertical"><Typography.Text>预测身份：{row.prediction_id}</Typography.Text><Typography.Text>数据版本：{row.bindings.data_version_id}</Typography.Text>{row.limitations?.map(reason => <Typography.Text key={reason}>• {reason}</Typography.Text>)}</Space> }} />
    </Card>}
    <Card title="候选与历史模型" extra={<JsonExportButton filename="qtrade-online-models.json" value={catalog} />}>
      <Table<Model> rowKey={row => row.definition.model_id} dataSource={catalog?.models ?? []} pagination={false} scroll={{ x: 1000 }} columns={[
        { title: '模型', render: (_, row) => <Space orientation="vertical"><b>{row.definition.name}</b><Typography.Text copyable={{ text: row.definition.model_id }}>{row.definition.model_id.slice(0, 16)}</Typography.Text></Space> },
        { title: '状态', render: (_, row) => <Tag color={row.state === 'ACTIVE' ? 'green' : 'orange'}>{labels[row.state] ?? row.state}</Tag> },
        { title: '训练数据截止', render: (_, row) => row.definition.training_data_end },
        { title: '最新输入验证', render: (_, row) => { const proof = latestProof(row); return proof ? `${proof.contract.data_end} · ${proof.score_count} 只策略合格证券` : '当前数据尚无通过的验证' } },
        { title: '组合', render: (_, row) => `Top${row.definition.portfolio.topk} / 淘汰${row.definition.portfolio.n_drop}` },
        { title: '操作', render: (_, row) => { const proof = latestProof(row); return <Button disabled={!session.authenticated || !ready || !['STAGED', 'RETIRED'].includes(row.state) || !proof || Date.parse(proof.contract.expected_execution_at) <= now} onClick={() => choose(row)}>{row.state === 'RETIRED' ? '审阅回滚' : '审阅激活'}</Button> } },
      ]} expandable={{ expandedRowRender: row => <Space orientation="vertical"><ModelEvidence definition={row.definition} /><Typography.Text>训练身份：{row.definition.model_run_id}；代码：{row.definition.training_code_revision}</Typography.Text>{row.definition.limitations.map(reason => <Typography.Text key={reason}>• {reason}</Typography.Text>)}</Space> }} />
    </Card>
    <Card title="模型操作回执"><Table rowKey="request_id" dataSource={catalog?.commands ?? []} pagination={{ pageSize: 10 }} scroll={{ x: 800 }} columns={[{ title: '时间', dataIndex: 'at' }, { title: '请求', dataIndex: 'request_id' }, { title: '操作', dataIndex: 'operation' }, { title: '结果', dataIndex: 'state' }, { title: '拒绝原因', dataIndex: 'error_code' }]} /></Card>
    <Modal open={!!review} title={review?.command.operation === 'ROLLBACK' ? '审阅研究模型回滚' : '审阅研究模型激活'} okText="批准并提交" cancelText="取消" confirmLoading={busy} onCancel={() => !busy && setReview(null)} onOk={() => void submit()} okButtonProps={{ disabled: !ready || !session.authenticated || !!changed }}>
      {review && <Space orientation="vertical">
        <Typography.Text>{review.model.definition.name} · Top{review.model.definition.portfolio.topk}</Typography.Text>
        <Typography.Text>模型：{review.command.model_id}</Typography.Text>
        <Typography.Text>数据：{review.command.data_version_id}</Typography.Text>
        <Typography.Text>输入验证：{review.proof.contract.data_end}，{review.proof.score_count} 只证券；预期研究执行时刻 {review.proof.contract.expected_execution_at}</Typography.Text>
        <ModelEvidence definition={review.model.definition} />
        <Alert type="warning" showIcon title="研究限制随模型保留" description="当前观察池存在幸存者偏差，历史 PIT 和精确交易约束尚未完整认证。此操作不会开启实盘或自动重训；每日预测是否启用以上方实际状态为准。" />
        {changed && <Alert type="error" title="目录已变化，请关闭并重新审阅" />}
      </Space>}
    </Modal>
  </Space>
}
