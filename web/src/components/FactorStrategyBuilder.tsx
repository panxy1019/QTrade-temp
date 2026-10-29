import { useEffect, useState } from 'react'
import { Alert, Button, Card, Descriptions, Empty, Input, InputNumber, Select, Space, Tag, Typography } from 'antd'
import { useResearchWorkflow } from './ResearchWorkflowProvider'
import { JsonExportButton } from './JsonExportButton'
import { workflowGet, workflowPost, limitationLabels } from '../research/qlibWorkflow'
import { checkedFactorCatalog, checkedFactorPreview } from '../research/factorStrategy'
import type { FactorCatalog, FactorPreview, FactorSpec } from '../research/factorStrategy'
import { canonicalJson, sha256Hex } from '../research/strategyIdentity'

export function FactorStrategyBuilder() {
  const { session } = useResearchWorkflow()
  const [catalog, setCatalog] = useState<FactorCatalog | null>(null)
  const [draft, setDraft] = useState<FactorSpec | null>(null)
  const [choice, setChoice] = useState<string | null>(null)
  const [preview, setPreview] = useState<FactorPreview | null>(null)
  const [requestId, setRequestId] = useState<string | null>(null)
  const [result, setResult] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [reload, setReload] = useState(0)
  useEffect(() => {
    const controller = new AbortController()
    void workflowGet<unknown>('/factor-strategies/catalog', { signal: controller.signal })
      .then(checkedFactorCatalog).then(value => {
        if (!controller.signal.aborted) {
          setCatalog(value); setDraft(null); setChoice(null); setPreview(null)
          setRequestId(null); setResult(null); setError(null)
        }
      }).catch(reason => {
        if (!controller.signal.aborted) { setCatalog(null); setError(String(reason)) }
      })
    return () => controller.abort()
  }, [reload])
  const change = (next: FactorSpec) => {
    setDraft(next); setPreview(null); setRequestId(null); setResult(null); setError(null)
  }
  const select = (version: string) => {
    const template = catalog?.templates.find(row => row.strategy_version === version)
    if (!template) return
    setChoice(version)
    change({ ...structuredClone(template.spec), name: (template.spec.name.slice(0, 52) + '-draft') })
  }
  const freeze = async () => {
    if (!draft || !catalog || busy) return
    setBusy(true); setError(null); setPreview(null); setRequestId(null); setResult(null)
    try {
      const value = await workflowPost<unknown>('/factor-strategies/specification', { spec: draft })
      setPreview(await checkedFactorPreview(value, draft, catalog))
    } catch (reason) { setError(String(reason)) }
    finally { setBusy(false) }
  }
  const submit = async () => {
    if (!draft || !catalog || !preview || busy) return
    const identity = requestId ?? crypto.randomUUID().replaceAll('-', '')
    setRequestId(identity); setBusy(true); setError(null)
    try {
      const value = await workflowPost<Record<string, unknown>>('/factor-strategies/jobs', {
        spec: draft, request_id: identity, expected_strategy_version: preview.expected_strategy_version,
        expected_catalog_sha256: preview.expected_catalog_sha256,
      })
      if (value.job_id !== identity || value.state !== 'QUEUED' || value.registration_only !== true
        || value.kind !== 'FACTOR_STRATEGY_REGISTER_V1' || value.live_trading_enabled !== false
        || value.external_order_transmission !== false || value.strategy_version !== preview.expected_strategy_version
        || value.spec_sha256 !== await sha256Hex(canonicalJson(draft))) throw new Error('登记回执与冻结规格不一致')
      setResult(value)
    } catch (reason) { setError(String(reason)) }
    finally { setBusy(false) }
  }
  const selected = catalog?.templates.find(row => row.strategy_version === choice)
  return <Card title="因子策略 Builder · 研究登记" extra={<Button disabled={busy} onClick={() => setReload(value => value + 1)}>刷新因子目录</Button>}>
    <Space orientation="vertical" style={{ width: '100%' }}>
      <Alert type="info" showIcon title="将已有因子和评价绑定为不可变研究策略" description="此入口登记 EXPERIMENT / NONE 合同。登记不运行组合回测，不激活模型，也不启动 PAPER。Qlib 模型训练与回测仍使用下方独立配置。" />
      {error && <Alert type="warning" showIcon title={error} />}
      {!catalog && <Empty description="暂无可核验因子目录，登记入口关闭" />}
      {catalog && <>
        <Typography.Text type="secondary">目录心跳：{catalog.worker.heartbeat_at}；提交时重新核对执行器与不可变档案。</Typography.Text>
        <label>因子 / 评价 / 数据版本 <Select aria-label="因子评价版本" style={{ width: 'min(100%, 900px)' }} disabled={busy}
          placeholder="选择已登记且已独立评价的因子" value={choice} onChange={select}
          options={catalog.templates.map(row => ({ value: row.strategy_version,
            label: `${row.factor_definition.alias} · ${row.evaluation_context.decision_scope.first_session}—${row.evaluation_context.decision_scope.last_session} · 数据${row.evaluation_context.data_binding.actual_end} · ${row.spec.evaluation_ref.evaluation_id}` }))} /></label>
      </>}
      {draft && selected && <>
        <Descriptions column={1} size="small" items={[
          { key: 'formula', label: '公式', children: selected.factor_definition.formula },
          { key: 'factor', label: '因子版本', children: <Typography.Text copyable>{selected.factor_definition.factor_version}</Typography.Text> },
          { key: 'data', label: '冻结数据版本', children: selected.evaluation_context.data_binding.data_version_id },
          { key: 'scope', label: '评价范围', children: `${selected.evaluation_context.instruments.length}只；${selected.evaluation_context.decision_scope.count}个请求会话；${selected.evaluation_context.selected_valid_ic_days}个有效 IC 日` },
          { key: 'knowledge', label: '时间语义', children: selected.evaluation_context.knowledge_mode },
        ]} />
        <Space wrap><Tag color="orange">历史选择偏差 / PIT 与复权未认证</Tag><Tag>毛标签诊断不等于净收益</Tag><Tag>尚未实现因子执行适配</Tag></Space>
        {selected.evaluation_context.limitations.map(reason => <Typography.Text key={reason} type="secondary">{limitationLabels[reason] ?? reason}</Typography.Text>)}
        <label>研究策略名称 <Input aria-label="因子策略名称" style={{ width: 360 }} disabled={busy} value={draft.name} maxLength={60} onChange={event => change({ ...draft, name: event.target.value })} /></label>
        {(['train', 'valid', 'test'] as const).map(segment => <Space key={segment} wrap>
          <Typography.Text>{({ train: '训练声明', valid: '验证声明', test: '测试声明' })[segment]}</Typography.Text>
          {([0, 1] as const).map(index => <Input key={index} aria-label={`因子${segment}${index === 0 ? '开始' : '结束'}`} type="date" disabled={busy}
            value={draft.segments[segment][index]} onChange={event => {
              const next = structuredClone(draft); next.segments[segment][index] = event.target.value; change(next)
            }} />)}
        </Space>)}
        <Typography.Text type="secondary">此阶段不训练模型；切分用于声明研究范围，不能将已看过的评价重新称为样本外。</Typography.Text>
        <Space wrap>
          {([['topk', '持仓数', 1, 100], ['n_drop', '每次淘汰数', 1, 20], ['hold_thresh', '最少持有会话', 2, 20]] as const).map(([key, label, min, max]) => <label key={key}>{label} <InputNumber aria-label={`因子${key}`} disabled={busy} min={min} max={max} value={draft.portfolio[key]}
            onChange={value => { if (typeof value === 'number') change({ ...draft, portfolio: { ...draft.portfolio, [key]: value } }) }} /></label>)}
          <label>风险仓位比例 <Input aria-label="因子风险仓位比例" style={{ width: 90 }} disabled={busy} value={draft.portfolio.risk_degree} onChange={event => change({ ...draft, portfolio: { ...draft.portfolio, risk_degree: event.target.value } })} /></label>
        </Space>
        <Space wrap>{([['buy_cost_bps', '买入成本 bp'], ['sell_cost_bps', '卖出成本 bp'], ['min_cost', '最低费用 元'], ['initial_cash', '初始资金 元']] as const).map(([key, label]) => <label key={key}>{label} <Input aria-label={`因子${key}`} style={{ width: 130 }} disabled={busy} value={draft.execution[key]} onChange={event => change({ ...draft, execution: { ...draft.execution, [key]: event.target.value } })} /></label>)}</Space>
        <Typography.Text>固定日频 TopkDropout、上一会话信号 / 下一会话开盘，滑点暂为 0；成本仅冻结到合同，未应用于因子毛标签。父版本沿用所选模板，不创建隐含晋级。</Typography.Text>
        <Space wrap>
          <Button disabled={busy || !session.authenticated} loading={busy} onClick={() => void freeze()}>预览因子策略合同</Button>
          <Button type="primary" disabled={busy || !session.authenticated || !preview || !!result} onClick={() => void submit()}>{requestId && !result ? '用原请求标识重试登记' : '提交研究策略登记'}</Button>
          {!session.authenticated && <Typography.Text>登录研究账户后可预览和提交。</Typography.Text>}
        </Space>
        {preview && <><Typography.Text copyable>冻结策略版本：{preview.expected_strategy_version}</Typography.Text><JsonExportButton filename={`factor-strategy-${preview.expected_strategy_version}.json`} value={preview} label="导出冻结合同" /></>}
        {result && <Alert type="success" showIcon title={`登记任务已入队：${String(result.job_id)}`} description="入队不等于登记完成；以集群任务的最终不可变回执为准。" action={<Button href={'#/experiments?job=' + String(result.job_id)}>查看实验中心</Button>} />}
      </>}
    </Space>
  </Card>
}
