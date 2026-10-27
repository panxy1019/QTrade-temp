import { useEffect, useState } from 'react'
import { Alert, Button, Card, Descriptions, Empty, Form, Input, InputNumber, Select, Space, Statistic, Table, Tag, Timeline, Typography, theme } from 'antd'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useResearchWorkflow } from '../components/ResearchWorkflowProvider'
import { StrategyComparisonPanel } from '../components/StrategyComparisonPanel'
import { EtfHistoryPanel } from '../components/EtfHistoryPanel'
import { JsonExportButton } from '../components/JsonExportButton'
import ChartRenderer from '../charts/ChartRenderer'
import { alpha158FeatureVersion, bindResearchData, canReuseArchivedResult, checkedSpecification, comparisonDraft, comparisonIssue, completedExperiments, finalStates, limitationLabels, stageLabels, strategyDraft, workflowGet, workflowPost } from '../research/qlibWorkflow'
import type { ArchivedResearchTemplate, ResearchAudit, ResearchSpec, ResearchSpecification, WorkflowExperiment, WorkflowResult } from '../research/qlibWorkflow'

function Layout({ title, description, children }: { title: string; description: string; children: React.ReactNode }) {
  const { catalog, error, refresh } = useResearchWorkflow()
  return <Space orientation="vertical" size="large" style={{ width: '100%' }}>
    <Space wrap><Typography.Title level={2} style={{ margin: 0 }}>{title}</Typography.Title><Tag color="blue">Qlib 日频研究</Tag><Button onClick={() => void refresh()}>刷新研究目录</Button></Space>
    <Typography.Text type="secondary">{description}</Typography.Text>
    {error && <Alert type="error" showIcon title={error} />}
    {catalog && !catalog.publication_available && <Alert type="warning" showIcon title="集群研究目录尚不可用" description="可以查看已开放配置；执行状态和实验结果不可用时不以旧成功状态代替。" />}
    {catalog?.publication_available && catalog.worker_fresh === false && <Alert type="error" showIcon title="集群任务心跳已过期，提交入口关闭；已归档结果仍可查阅" />}
    {catalog?.frozen_data_current === false && <Alert type="warning" showIcon title={`研究数据尚未覆盖最近完整交易日${catalog.expected_complete_day}`} description="仍可在已声明的冻结历史上研究；提交任务不会自动补齐新行情或改写旧实验。" />}
    {catalog?.publication_available && catalog.frozen_data_current === null && <Alert type="warning" showIcon title="研究交易日历不可用，无法判断冻结数据是否覆盖最近完整交易日" />}
    <Typography.Text type="secondary">任务由集群串行执行，状态约每五分钟发布；网页每15秒核对。旧ETF和75股分钟研究已移至历史工作区。</Typography.Text>
    {children}
  </Space>
}

function Audit({ audit }: { audit: ResearchAudit }) {
  const coverage = audit.source_repair_coverage as {
    state?: string; status_symbols?: number; input_symbols?: number; status_rows?: number;
    master_rows?: number;
  } | undefined
  const constraints = audit.execution_constraints as {
    source_snapshot_sha256?: string; matched_input_symbols?: number;
    counts?: { reconciled_bars?: number; missing_state_bars?: number; conflict_bars?: number;
      additional_buy_blocks?: number; additional_sell_blocks?: number }
  } | undefined
  return <Card size="small" title="数据与交易约束审计">
    <Space orientation="vertical" style={{ width: '100%' }}>
      <Space wrap><Tag color="orange">非完整历史PIT</Tag><Tag>不可晋级</Tag><Typography.Text>{audit.universe.status}</Typography.Text></Space>
      <Typography.Text>{(audit.supported_universe as { status?: string })?.status === 'TRAIN_RANK_EXECUTION_ALIGNED_EXCLUDING_STAR' ? '新工作流训练/排名/投资范围对齐，使用完成历史做动态资格与流动性选择；样本本身的幸存者偏差尚未消除。' : '原基线存在训练与可投资范围差异；其结果作为独立协议档案保留。'}</Typography.Text>
      {audit.limitations.map(reason => <Typography.Text key={reason}>• {limitationLabels[reason] ?? reason}</Typography.Text>)}
      {coverage && <Typography.Paragraph>本实验冻结的PIT修复覆盖：{coverage.status_symbols != null ? `状态回填${coverage.status_symbols}/${coverage.input_symbols ?? '—'}股、${coverage.status_rows?.toLocaleString() ?? '—'}行；来源主表${coverage.master_rows?.toLocaleString() ?? '—'}条。尚未用于精确规则回测，历史可得性待核验。` : '运行时没有已冻结的修复覆盖证据；不据此推断状态已认证。'} 完整来源摘要随实验JSON导出。</Typography.Paragraph>}
      {constraints && <>
        <Typography.Paragraph>已参与本次回测的附加状态限制：原始行情对账{constraints.counts?.reconciled_bars?.toLocaleString() ?? '—'}根，覆盖{constraints.matched_input_symbols ?? '—'}股；来源冲突{constraints.counts?.conflict_bars?.toLocaleString() ?? '—'}根双向限制。新增禁买{constraints.counts?.additional_buy_blocks?.toLocaleString() ?? '—'}根、禁卖{constraints.counts?.additional_sell_blocks?.toLocaleString() ?? '—'}根。缺状态{constraints.counts?.missing_state_bars?.toLocaleString() ?? '—'}根继续保留原近似限制，不能认定为已认证可交易。</Typography.Paragraph>
        <Typography.Text>以上为输入库全历史的掩码计数，不是测试期委托或实际成交次数。来源快照：{constraints.source_snapshot_sha256}。回填为事后诊断；ST禁买是研究政策，精确涨跌停与完整历史PIT仍未认证。逐股票逐日账本保存在集群归档中。</Typography.Text>
      </>}
    </Space>
  </Card>
}

export function Login() {
  const { session, refresh } = useResearchWorkflow()
  const [error, setError] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [form] = Form.useForm()
  const login = async (values: { user: string; password: string }) => {
    setBusy(true); setError(null)
    try { await workflowPost('/login', values); form.resetFields(['password']); await refresh() }
    catch (reason) { setError(String(reason)); form.resetFields(['password']) }
    finally { setBusy(false) }
  }
  return <Card title="研究账户">
    {session.authenticated ? <Space wrap>
      <Tag color="green">{session.user} 已登录</Tag><Typography.Text>可提交研究任务及审阅模型控制操作</Typography.Text>
      <Button onClick={() => void workflowPost('/logout', {}).then(refresh)}>退出研究登录</Button>
    </Space> : <>
      {!session.configured && <Alert type="warning" showIcon title="研究登录配置未就绪，提交入口保持关闭" />}
      <Form form={form} layout="inline" initialValues={{ user: 'panxy1019' }} onFinish={values => void login(values)}>
        <Form.Item name="user" label="研究账户" rules={[{ required: true }]}><Input autoComplete="username" /></Form.Item>
        <Form.Item name="password" label="研究密码" rules={[{ required: true }]}><Input.Password autoComplete="current-password" /></Form.Item>
        <Button htmlType="submit" loading={busy} disabled={!session.configured}>登录</Button>
      </Form>
      <Typography.Text type="secondary">这是本平台独立研究账户，与Vercel管理账户和券商账户分开。密码不写入浏览器本地实验。</Typography.Text>
    </>}
    {error && <Alert type="error" title={error} />}
  </Card>
}

export function QlibStrategyPage() {
  const { catalog } = useResearchWorkflow()
  return <Layout title="策略中心" description="组合特征、预测模型、持仓规则与成本，建立可复现的研究配置。">
    <Card title="已接入的研究配置">
      <Table rowKey="id" pagination={false} dataSource={catalog?.models ?? []} columns={[
        { title: '预测模型', dataIndex: 'name' },
        { title: '特征', render: () => 'Alpha158 · 158个价量特征' },
        { title: '组合', render: () => 'TopkDropout · Top50/淘汰5（可配置）' },
        { title: '模型参数', render: (_, row) => JSON.stringify(row.parameters) },
        { title: '操作', render: (_, row) => <Button type="primary" href={'#/research?model=' + row.id}>配置并运行</Button> },
      ]} />
    </Card>
    <Card title="已发布的策略版本">
      <Typography.Paragraph>选择完整研究合同作为起点；编辑参数后会冻结新的版本。158 列是模型特征，不代表 158 个独立有效 Alpha。</Typography.Paragraph>
      <Table rowKey="strategy_version" dataSource={catalog?.strategy_versions ?? []} pagination={{ pageSize: 5 }} scroll={{ x: 1100 }} columns={[
        { title: '策略版本', dataIndex: 'strategy_version', render: value => <Typography.Text copyable>{value}</Typography.Text> },
        { title: '模型 / 特征', render: (_, row) => `${row.spec.model} · ${row.feature_set.version}` },
        { title: '组合', render: (_, row) => `${row.portfolio.method} · Top${row.portfolio.topk} / 淘汰${row.portfolio.n_drop}` },
        { title: '成本', render: (_, row) => `买 ${row.execution.buy_cost_bps} bp / 卖 ${row.execution.sell_cost_bps} bp / 最低 ${row.execution.min_cost} 元` },
        { title: '数据截止', render: (_, row) => row.spec.data_binding?.actual_end ?? '未绑定' },
        { title: '操作', render: (_, row) => <Button href={'#/research?strategy_version=' + row.strategy_version}>以此版本创建研究</Button> },
      ]} expandable={{ expandedRowRender: row => <pre>{JSON.stringify(row, null, 2)}</pre> }} locale={{ emptyText: '尚无已发布的版本合同' }} />
    </Card>
    <Card title="策略注册与生命周期">
      <Alert type="info" showIcon title="研究登记不授予模拟执行或实盘权限" description="状态、关联任务和已有谱系来自策略注册器；模型工件在训练完成前仍为空，不把模型名称当作已训练模型版本。" />
      <Table rowKey="strategy_id" dataSource={catalog?.strategies ?? []} pagination={{ pageSize: 5 }} scroll={{ x: 1100 }} columns={[
        { title: '名称 / 家族', render: (_, row) => `${row.name ?? row.strategy_id} · ${row.family ?? 'QLIB'}` },
        { title: '状态', dataIndex: 'state', render: value => <Tag>{value}</Tag> },
        { title: '策略版本', dataIndex: 'version', render: value => <Typography.Text copyable>{value}</Typography.Text> },
        { title: '信号 / 模型', render: (_, row) => `${row.contract.signal.kind} · ${row.contract.signal.model} · ${row.model_artifacts?.map(item => item.model_id).join('、') || row.contract.signal.model_artifact_id || '训练工件尚未生成'}` },
        { title: '关联任务', render: (_, row) => (row.job_ids ?? []).join('、') || '暂无任务' },
      ]} expandable={{ expandedRowRender: row => <>
        <Descriptions column={1} items={[
          { key: 'data', label: '数据版本', children: row.contract.spec.data_binding?.data_version_id ?? '未绑定' },
          { key: 'feature', label: '特征版本', children: row.contract.feature_set.version },
          { key: 'code', label: '代码版本', children: row.contract.source_revision },
          { key: 'models', label: '已训练模型工件', children: row.model_artifacts?.map(item => `${item.model_id}（任务 ${item.job_id}）`).join('；') || '尚无已完成训练工件' },
          { key: 'portfolio', label: '组合与执行成本', children: `${row.contract.portfolio.method} · Top${row.contract.portfolio.topk} · ${row.contract.execution.timing} · 买${row.contract.execution.buy_cost_bps}bp/卖${row.contract.execution.sell_cost_bps}bp/最低${row.contract.execution.min_cost}元` },
        ]} />
        <Typography.Text>已有谱系：{row.lineage ? JSON.stringify(row.lineage) : '未登记父版本'}</Typography.Text>
        <JsonExportButton filename={`qtrade-strategy-${row.version}.json`} value={row} label="导出注册合同" />
      </> }} locale={{ emptyText: '尚无已注册的 Qlib 研究策略' }} />
    </Card>
    {catalog?.datasets[0] && <Audit audit={catalog.datasets[0].audit} />}
    <Alert type="info" showIcon title="模型与组合分别配置" description="当前开放Ridge线性模型与LightGBM，使用相同特征预处理和持仓协议对照。其他Qlib模型尚未接通，不显示为可运行选项。" />
    <Button href="#/archive">查看旧ETF和分钟研究档案</Button>
  </Layout>
}

type FormSpec = Omit<ResearchSpec, 'segments'> & Record<'train_start' | 'train_end' | 'valid_start' | 'valid_end' | 'test_start' | 'test_end', string>

export function QlibResearchPage() {
  const { catalog, session, refresh } = useResearchWorkflow()
  const [params] = useSearchParams()
  const navigate = useNavigate()
  const [form] = Form.useForm<FormSpec>()
  const pinnedSource = Form.useWatch('source_snapshot_sha256', form) as string | undefined
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [requestId, setRequestId] = useState<string | null>(null)
  const [preview, setPreview] = useState<ResearchSpecification | null>(null)
  const baseId = params.has('from') ? params.get('from')! : null
  const requestedModel = params.get('model')
  const requestedStrategyVersion = params.get('strategy_version')
  const configurationKey = JSON.stringify([baseId, requestedModel, requestedStrategyVersion])
  const [initialization, setInitialization] = useState<{
    key: string; spec: ResearchSpec; template?: ArchivedResearchTemplate; startingVersion?: string
  } | null>(null)
  const [initializationError, setInitializationError] = useState<{ key: string; message: string } | null>(null)
  const [reloadVersion, setReloadVersion] = useState(0)
  const initializedKey = initialization?.key
  const ready = initializedKey === configurationKey
  const hasCatalog = catalog !== null
  useEffect(() => {
    if (!catalog || initializedKey === configurationKey) return
    let active = true
    const timer = window.setTimeout(() => {
      setInitializationError(null); setError(null); setRequestId(null); setPreview(null)
      const initialize = async () => {
        try {
          if (requestedModel !== null && requestedModel !== 'linear' && requestedModel !== 'lightgbm') {
            throw new Error('该预测模型尚未开放，配置未载入')
          }
          let template: ArchivedResearchTemplate | undefined
          let spec: ResearchSpec
          if (requestedStrategyVersion !== null) {
            if (baseId !== null || !/^[a-f0-9]{64}$/.test(requestedStrategyVersion)) throw new Error('策略起点参数不明确，配置未载入')
            const definition = catalog.strategy_versions?.find(row => row.strategy_version === requestedStrategyVersion)
            if (!definition) throw new Error('所选策略版本未发布，配置未载入')
            spec = strategyDraft(definition)
          } else if (baseId !== null) {
            if (!/^[A-Za-z0-9][A-Za-z0-9._-]{0,120}$/.test(baseId)) throw new Error('归档实验标识无效，配置未载入')
            template = await workflowGet<ArchivedResearchTemplate>('/experiments/' + encodeURIComponent(baseId) + '/template')
            spec = comparisonDraft(template, baseId, requestedModel ?? undefined)
          } else {
            spec = structuredClone(requestedModel === 'linear' ? catalog.default_linear_spec : catalog.default_spec)
          }
          if (!active) return
          const { segments, ...rest } = spec
          form.resetFields()
          form.setFieldsValue({
            ...rest, train_start: segments.train[0], train_end: segments.train[1],
            valid_start: segments.valid[0], valid_end: segments.valid[1],
            test_start: segments.test[0], test_end: segments.test[1],
          })
          setInitialization({ key: configurationKey, spec, template, startingVersion: requestedStrategyVersion ?? undefined })
        } catch (reason) {
          if (active) setInitializationError({ key: configurationKey,
            message: reason instanceof Error ? reason.message : '归档配置载入失败' })
        }
      }
      void initialize()
    }, 0)
    return () => { active = false; window.clearTimeout(timer) }
  // Freeze once per requested configuration; catalog polling must not reset edits or retries.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form, baseId, requestedModel, requestedStrategyVersion, hasCatalog, initializedKey, reloadVersion])
  const invalidate = () => { setRequestId(null); setError(null); setPreview(null) }
  const editedSpec = (values: FormSpec): ResearchSpec => {
    if (!initialization) throw new Error('研究配置未就绪')
    const { train_start, train_end, valid_start, valid_end, test_start, test_end, ...rest } = values
    return {
      ...initialization.spec, ...rest, dataset_id: initialization.spec.dataset_id,
      features: initialization.spec.features, universe_policy: initialization.spec.universe_policy,
      source_snapshot_sha256: initialization.spec.source_snapshot_sha256,
      segments: { train: [train_start, train_end], valid: [valid_start, valid_end], test: [test_start, test_end] },
    }
  }
  const selectSpec = (spec: ResearchSpec, startingVersion?: string) => {
    const { segments, ...rest } = structuredClone(spec)
    form.resetFields()
    form.setFieldsValue({ ...rest, train_start: segments.train[0], train_end: segments.train[1],
      valid_start: segments.valid[0], valid_end: segments.valid[1], test_start: segments.test[0], test_end: segments.test[1] })
    setInitialization({ key: configurationKey, spec: structuredClone(spec), startingVersion })
    invalidate()
  }
  const selectData = (id: string) => {
    const selected = catalog?.datasets.find(row => row.id === id)
    if (!ready || !selected || !initialization) return
    try {
      if (!selected.data_binding) throw new Error('所选数据没有 READY 版本描述，未载入')
      const spec = bindResearchData(editedSpec(form.getFieldsValue(true)), selected.data_binding)
      spec.segments.test[1] = selected.data_binding.actual_end
      selectSpec(spec)
    } catch (reason) { setError(String(reason)) }
  }
  const selectStrategy = (version: string) => {
    const selected = catalog?.strategy_versions?.find(row => row.strategy_version === version)
    if (!selected) return
    try { selectSpec(strategyDraft(selected), version) }
    catch (reason) { setError(String(reason)) }
  }
  const freeze = async (spec: ResearchSpec) => checkedSpecification(
    await workflowPost<ResearchSpecification>('/specification', { spec }), spec)
  const showPreview = async () => {
    if (!ready || busy || !session.authenticated || !initialization?.spec.data_binding) return
    setBusy(true); setError(null)
    try { setPreview(await freeze(editedSpec(await form.validateFields()))) }
    catch (reason) { setPreview(null); setError(String(reason)) }
    finally { setBusy(false) }
  }
  const submit = async (values: FormSpec) => {
    if (!ready || !initialization || busy || !session.authenticated || !catalog?.publication_available || catalog.worker_fresh === false) return
    setBusy(true); setError(null)
    const identity = requestId ?? crypto.randomUUID().replaceAll('-', '')
    setRequestId(identity)
    const spec = editedSpec(values)
    try {
      const frozen = spec.data_binding ? (preview ? checkedSpecification(preview, spec) : await freeze(spec)) : null
      if (frozen) setPreview(frozen)
      await workflowPost('/jobs', { request_id: identity, spec,
        ...(frozen ? { expected_strategy_version: frozen.expected_strategy_version } : {}) })
      sessionStorage.setItem('qtrade.pending-research-job', JSON.stringify({ id: identity, spec,
        ...(frozen ? { strategy_version: frozen.expected_strategy_version, strategy_definition: frozen.strategy_definition } : {}) }))
      await refresh()
      navigate('/experiments?job=' + identity)
    } catch (reason) { setError(String(reason)) }
    finally { setBusy(false) }
  }
  const template = ready ? initialization?.template : undefined
  const dataset = catalog?.datasets.find(row => row.id === initialization?.spec.dataset_id)
  return <Layout title="策略研究" description="冻结数据、时间切分、模型、组合与成本后，提交集群任务。">
    <Login />
    {baseId !== null && !ready && initializationError?.key !== configurationKey && <Alert type="info" title="正在校验并载入归档配置，提交入口暂不可用" />}
    {initializationError?.key === configurationKey && <Alert type="error" showIcon title={initializationError.message} description="不会使用当前默认值补齐或替代归档配置。" action={<Space><Button onClick={() => setReloadVersion(v => v + 1)}>重试载入</Button><Button href="#/research">新建默认研究</Button></Space>} />}
    {template && <Alert type="info" showIcon title="已载入归档实验的固定配置" description={<>
      <Typography.Paragraph>基于 <Typography.Link href={'#/backtest?experiment=' + encodeURIComponent(template.base_experiment_id)}>{template.spec.name}</Typography.Link>；原数据哈希：{template.dataset_sha256}。</Typography.Paragraph>
      <Typography.Text>载入时仅调整实验名称和预测模型，数据、来源、时间、组合、成本及种子沿用原配置。修改其他参数后，不再视为纯模型对照；最终结果仍会核对实际数据与执行器代码版本。</Typography.Text>
    </>} />}
    {ready && dataset && <Card title="研究输入">
      <Typography.Paragraph>{dataset.id} · {dataset.symbols}股输入库存 · {dataset.rows.toLocaleString()}根日线 · 截至{dataset.end}。训练和排名排除科创板，实际动态资格数量以实验审计为准。</Typography.Paragraph>
      {catalog?.source_snapshot_sha256 && <Typography.Paragraph>历史状态来源快照将在表单初始化时固定；后续回填不会改变这份任务。需要比较模型时，请使用同一来源快照。</Typography.Paragraph>}
      <Audit audit={dataset.audit} />
    </Card>}
    <Card title="Qlib训练与回测配置">
      <Space orientation="vertical" style={{ width: '100%', marginBottom: 16 }}>
        <label>不可变研究数据版本 <Select aria-label="不可变研究数据版本" disabled={!ready || busy} value={ready ? initialization?.spec.dataset_id : undefined} style={{ width: 'min(100%, 650px)' }}
          onChange={selectData} options={(catalog?.datasets ?? []).filter(row => row.data_binding || row.id === initialization?.spec.dataset_id).map(row => ({ value: row.id, label: `${row.end} · ${row.data_binding ? 'READY' : '历史固定合同'} · ${row.id}`, disabled: !row.data_binding }))} /></label>
        <label>特征集版本 <Select aria-label="特征集版本" disabled value={initialization?.spec.feature_set_version ?? 'historical-alpha158'} style={{ width: 340 }} options={[
          { value: alpha158FeatureVersion, label: alpha158FeatureVersion }, { value: 'historical-alpha158', label: 'Alpha158 · 原历史合同' },
        ]} /></label>
        <label>已发布策略版本起点 <Select aria-label="已发布策略版本起点" disabled={!ready || busy} value={initialization?.startingVersion} placeholder="从已发布完整合同载入" style={{ width: 'min(100%, 650px)' }} onChange={selectStrategy}
          options={(catalog?.strategy_versions ?? []).map(row => ({ value: row.strategy_version, label: `${row.spec.name} · ${row.spec.model} · ${row.strategy_version}` }))} /></label>
      </Space>
      {ready && pinnedSource && <Typography.Paragraph copyable>本任务固定来源：{pinnedSource}</Typography.Paragraph>}
      {ready && initialization?.spec.data_binding && <Descriptions column={1} items={[
        { key: 'version', label: '固定数据版本', children: initialization.spec.data_binding.data_version_id },
        { key: 'event', label: 'READY 事件', children: initialization.spec.data_binding.ready_event_id },
        { key: 'scope', label: '支持证券', children: initialization.spec.data_binding.supported_symbols },
      ]} />}
      <Form form={form} disabled={!ready || busy} layout="vertical" onFinish={values => void submit(values)} onValuesChange={invalidate}>
        <Form.Item name="source_snapshot_sha256" hidden><Input /></Form.Item>
        <div className="backtest-lab-grid">
          <Form.Item name="name" label="实验名称" rules={[{ required: true, pattern: /^[A-Za-z0-9][A-Za-z0-9._-]{0,59}$/ }]}><Input /></Form.Item>
          <Form.Item name="model" label="预测模型" rules={[{ required: true }]}><Select options={catalog?.models.map(r => ({ value: r.id, label: r.name }))} /></Form.Item>
          {(['train', 'valid', 'test'] as const).flatMap(part => (['start', 'end'] as const).map(edge => <Form.Item key={part + edge} name={part + '_' + edge} label={({ train: '训练', valid: '验证', test: '测试' })[part] + (edge === 'start' ? '开始' : '结束')} rules={[{ required: true }]}><Input type="date" /></Form.Item>))}
          <Form.Item name="topk" label="目标持仓Top K" rules={[{ required: true }]}><InputNumber min={10} max={100} /></Form.Item>
          <Form.Item name="n_drop" label="每日淘汰名额" rules={[{ required: true }]}><InputNumber min={1} max={20} /></Form.Item>
          <Form.Item name="hold_thresh" label="最少持有交易步" rules={[{ required: true }]}><InputNumber min={2} max={20} /></Form.Item>
          <Form.Item name="risk_degree" label="目标股票仓位" rules={[{ required: true }]}><InputNumber min={0.1} max={0.95} step={0.05} /></Form.Item>
          <Form.Item name="buy_cost_bps" label="买入成本bp" rules={[{ required: true }]}><InputNumber min={1} max={100} /></Form.Item>
          <Form.Item name="sell_cost_bps" label="卖出成本bp" rules={[{ required: true }]}><InputNumber min={1} max={100} /></Form.Item>
          <Form.Item name="min_cost" label="最低成本元" rules={[{ required: true }]}><InputNumber min={0} max={100} /></Form.Item>
          <Form.Item name="initial_cash" label="初始研究资金" rules={[{ required: true }]}><InputNumber min={100000} max={100000000} /></Form.Item>
          <Form.Item name="seed" label="随机种子" rules={[{ required: true }]}><InputNumber min={0} max={2147483647} /></Form.Item>
        </div>
        <Alert type="info" title="固定合同：Alpha158＋训练期拟合归一化，下一交易日开盘，TopkDropout，前日量1%容量；分区边界须隔离未来标签。" description="已有历史已被查看，所有结果保持事后研究诊断。模型超参数采用已声明固定配置，当前表单不进行无界搜索。" />
        <Button style={{ marginTop: 16 }} type="primary" htmlType="submit" loading={busy} disabled={!ready || !session.authenticated || !catalog?.publication_available || catalog.worker_fresh === false}>提交集群研究任务</Button>
        {initialization?.spec.data_binding && <Button style={{ marginLeft: 12 }} disabled={!ready || busy || !session.authenticated || !catalog?.publication_available || catalog.worker_fresh === false} onClick={() => void showPreview()}>预览冻结策略规格</Button>}
      </Form>
      {preview && <Card type="inner" title="本次冻结研究规格">
        <Typography.Paragraph copyable>策略版本：{preview.expected_strategy_version}</Typography.Paragraph>
        <Typography.Paragraph>编辑参数会清除此预览并生成新版本；目录刷新保持当前选择。入队后以集群独立校验及回执为准，不激活模型或授予交易权限。</Typography.Paragraph>
        <JsonExportButton filename={`qtrade-specification-${preview.expected_strategy_version}.json`} value={preview} label="导出冻结规格" />
        <pre>{JSON.stringify(preview.spec, null, 2)}</pre>
      </Card>}
      {requestId && <Typography.Paragraph copyable>幂等请求ID：{requestId}</Typography.Paragraph>}
      {error && <Alert type="error" showIcon title={error} description="规格未改变时重试沿用同一请求ID，避免重复计算。" />}
    </Card>
  </Layout>
}

export function QlibExperimentPage() {
  const { catalog, session, refresh } = useResearchWorkflow()
  const [error, setError] = useState<string | null>(null)
  const rows = [...(catalog?.experiments ?? [])]
  const pending = sessionStorage.getItem('qtrade.pending-research-job')
  if (pending) {
    try {
      const value = JSON.parse(pending) as { id: string; spec: ResearchSpec }
      if (!rows.some(r => r.experiment_id === value.id) && catalog?.datasets[0]) rows.unshift({
        experiment_id: value.id, spec: value.spec, name: value.spec.name, model: value.spec.model,
        state: 'AWAITING_PUBLICATION', engine: 'QLIB', audit: catalog.datasets[0].audit,
      })
    } catch { /* Ignore malformed local navigation aid; server catalog is authoritative. */ }
  }
  const cancel = async (id: string) => {
    try { await workflowPost('/jobs/' + id + '/cancel', {}); await refresh() }
    catch (reason) { setError(String(reason)) }
  }
  return <Layout title="实验中心" description="查看研究任务进度与原日期归档；已完成历史结果到回测分析对照，前向目标和后来观察到模拟交易查看。">
    <Login />
    <Card title="集群执行器"><Descriptions items={[
      { key: 'state', label: '状态', children: catalog?.worker.state ?? '不可用' },
      { key: 'heartbeat', label: '状态快照心跳', children: catalog?.worker.heartbeat_at ?? '—' },
      { key: 'parallel', label: '并发', children: '1；任务最长60分钟，异常中断不自动重跑' },
    ]} /></Card>
    {error && <Alert type="error" title={error} />}
    <Table<WorkflowExperiment> rowKey="experiment_id" dataSource={rows} pagination={{ pageSize: 10 }} scroll={{ x: 1150 }} expandable={{ expandedRowRender: row => <Space orientation="vertical" style={{ width: '100%' }}>
      <Typography.Text copyable>实验ID：{row.experiment_id}</Typography.Text>
      <Typography.Text copyable>规格哈希：{row.spec_sha256 ?? '原始基线参见归档'}</Typography.Text>
      <Typography.Text copyable>代码：{row.source_revision ?? '待执行器绑定'}</Typography.Text>
      {row.error_code && <Alert type="error" title={row.error_code} />}
      <Timeline items={(row.events ?? []).map(event => ({ children: event.at + ' · ' + (stageLabels[event.state] ?? event.state) }))} />
      <Audit audit={row.audit} />
      <JsonExportButton filename={row.experiment_id + '.json'} value={row} label="导出实验记录" />
    </Space> }} columns={[
      { title: '实验', dataIndex: 'name' }, { title: '模型', dataIndex: 'model' },
      { title: '状态', render: (_, row) => <Tag color={row.state === 'COMPLETE' ? 'blue' : finalStates.has(row.state) ? 'orange' : 'processing'}>{stageLabels[row.state] ?? row.state}</Tag> },
      { title: '历史测试区间 / 完成时间', render: (_, row) => `${row.spec?.segments.test.join(' 至 ') ?? '原区间见档案'}；完成 ${row.completed_at ?? '尚未完成'}` },
      { title: '历史累计收益', render: (_, row) => percent(row.metrics?.total_return_net) },
      { title: '研究结论', render: () => <Tag color="orange">非PIT诊断·不可晋级</Tag> },
      { title: '操作', render: (_, row) => <Space>
        {row.state === 'COMPLETE' && <Button href={'#/backtest?experiment=' + row.experiment_id}>分析结果</Button>}
        {!finalStates.has(row.state) && <Button disabled={!session.authenticated} onClick={() => void cancel(row.experiment_id)}>申请取消</Button>}
      </Space> },
    ]} />
  </Layout>
}

const percent = (value: number | null | undefined) => value == null ? '—' : (value * 100).toFixed(2) + '%'
const number = (value: number | null | undefined) => value == null ? '—' : value.toFixed(4)

function Result({ result }: { result: WorkflowResult }) {
  const { token } = theme.useToken()
  const metrics = result.metrics
  return <Space orientation="vertical" style={{ width: '100%' }} size="large">
    <Card title={result.model + ' · ' + result.run_id}>
      <Typography.Paragraph>组合评价 {result.portfolio_evaluation.start}—{result.portfolio_evaluation.end}；IC标签 {result.signal_evaluation.start}—{result.signal_evaluation.end}。输入数据截至{result.data_end}。</Typography.Paragraph>
      <Typography.Paragraph>实际资格区间覆盖{result.symbols}只股票，{result.feature_count}个特征；每日可用股票数随已完成历史和成交额变化。完整资格区间保存在集群实验审计中。</Typography.Paragraph>
      <Space wrap size="large">{[
        ['累计收益', percent(metrics.total_return_net)], ['年化收益', percent(metrics.annual_return_net)],
        ['Sharpe', number(metrics.sharpe_net)], ['最大回撤', percent(metrics.max_drawdown)],
        ['IC', number(metrics.ic)], ['RankIC', number(metrics.rank_ic)],
        ['ICIR（未年化）', number(metrics.icir)], ['RankICIR（未年化）', number(metrics.rank_icir)],
        ['双边日均换手', percent(metrics.mean_daily_turnover)], ['基准累计收益', percent(metrics.benchmark_return)],
      ].map(([title, value]) => <Statistic key={title} title={title} value={value} />)}</Space>
      <ChartRenderer style={{ height: 380 }} option={{
        animation: false, textStyle: { color: token.colorText },
        tooltip: { trigger: 'axis' }, legend: { data: ['成本后净值', '沪深300', '回撤'], textStyle: { color: token.colorText } },
        grid: { left: 60, right: 60, bottom: 45 }, xAxis: { type: 'category', data: result.curve.map(r => r.date) },
        yAxis: [{ type: 'value', scale: true }, { type: 'value', max: 0, axisLabel: { formatter: (v: number) => (v * 100).toFixed(0) + '%' } }],
        series: [
          { name: '成本后净值', type: 'line', showSymbol: false, data: result.curve.map(r => r.equity) },
          { name: '沪深300', type: 'line', showSymbol: false, data: result.curve.map(r => r.benchmark_equity) },
          { name: '回撤', type: 'line', yAxisIndex: 1, showSymbol: false, data: result.curve.map(r => r.drawdown) },
        ],
      }} />
    </Card>
    <Audit audit={result.audit} />
    <Card title="固定配置与结果身份">
      <Typography.Paragraph copyable>结果哈希：{result.evidence_sha256}</Typography.Paragraph>
      <Typography.Paragraph copyable>数据哈希：{result.dataset_sha256}</Typography.Paragraph>
      <Typography.Paragraph copyable>源码：{result.source_revision}</Typography.Paragraph>
      <Typography.Paragraph>对照协议：{result.comparison_protocol}</Typography.Paragraph>
      <Typography.Paragraph><pre>{JSON.stringify(result.spec ?? { note: '原始固定基线，完整规格在集群归档' }, null, 2)}</pre></Typography.Paragraph>
      <Space wrap>
        <JsonExportButton filename={result.experiment_id + '-result.json'} value={result} />
        <Button disabled={!canReuseArchivedResult(result)} href={canReuseArchivedResult(result) ? '#/research?from=' + encodeURIComponent(result.experiment_id) : undefined}>复用配置创建对照</Button>
      </Space>
      {!canReuseArchivedResult(result) && <Typography.Paragraph type="secondary">此档案缺少当前来源协议的完整配置，不能用默认值补齐；可新建研究任务。</Typography.Paragraph>}
    </Card>
  </Space>
}

export function QlibBacktestPage() {
  const { catalog } = useResearchWorkflow()
  const [params, setParams] = useSearchParams()
  const [selected, setSelected] = useState<string | undefined>(params.get('experiment') ?? undefined)
  const [compareId, setCompareId] = useState<string | undefined>()
  const [result, setResult] = useState<WorkflowResult | null>(null)
  const [other, setOther] = useState<WorkflowResult | null>(null)
  const [error, setError] = useState<string | null>(null)
  const completed = completedExperiments(catalog?.experiments ?? [])
  const firstCompleted = completed[0]?.experiment_id
  useEffect(() => {
    let active = true
    const id = selected ?? firstCompleted
    if (!id) return
    const timer = window.setTimeout(() => {
      setResult(null); setError(null)
      void workflowGet<WorkflowResult>('/experiments/' + id).then(value => { if (active) setResult(value) })
        .catch(reason => { if (active) setError(String(reason)) })
    }, 0)
    return () => { active = false; window.clearTimeout(timer) }
  }, [selected, firstCompleted])
  useEffect(() => {
    let active = true
    const timer = window.setTimeout(() => {
      setOther(null)
      if (compareId) void workflowGet<WorkflowResult>('/experiments/' + compareId)
        .then(value => { if (active) setOther(value) }).catch(reason => { if (active) setError(String(reason)) })
    }, 0)
    return () => { active = false; window.clearTimeout(timer) }
  }, [compareId])
  const issue = result && other ? comparisonIssue(result, other) : null
  return <Layout title="回测分析" description="仅对照已完成的历史实验，保留各自测试区间、来源和成本假设；此处净值不是今天的 PAPER 或 SHADOW 结果。">
    <StrategyComparisonPanel />
    <EtfHistoryPanel />
    <Space wrap>
      <Select aria-label="选择Qlib实验" placeholder="选择已完成实验" style={{ minWidth: 330 }} value={selected ?? completed[0]?.experiment_id} options={completed.map(row => ({ value: row.experiment_id, label: row.name + ' · ' + row.model }))} onChange={value => { setSelected(value); setParams({ experiment: value }) }} />
      <Select aria-label="选择对照实验" allowClear placeholder="选择另一实验进行对照" style={{ minWidth: 330 }} value={compareId} options={completed.filter(row => row.experiment_id !== (selected ?? completed[0]?.experiment_id)).map(row => ({ value: row.experiment_id, label: row.name }))} onChange={setCompareId} />
      <Button href="#/research">新建研究任务</Button>
    </Space>
    {error && <Alert type="error" showIcon title={error} />}
    {completed.length === 0 && <Empty description="尚无已完成的统一实验，请先到策略研究提交任务" />}
    {result && <Result result={result} />}
    {result && other && <Card title="统一策略对照">
      {issue ? <Alert type="warning" showIcon title="当前两份结果不可直接作模型增量比较" description={issue} /> : <>
        <Alert type="info" title="输入、时间、组合、成本、种子与协议一致；比较预测模型差异。研究限制仍适用于两份结果。" />
        <Table rowKey="experiment_id" pagination={false} dataSource={[result, other]} columns={[
          { title: '模型', dataIndex: 'model' },
          { title: '累计收益', render: (_, row) => percent(row.metrics.total_return_net) },
          { title: 'Sharpe', render: (_, row) => number(row.metrics.sharpe_net) },
          { title: '最大回撤', render: (_, row) => percent(row.metrics.max_drawdown) },
          { title: 'RankIC', render: (_, row) => number(row.metrics.rank_ic) },
          { title: '日均双边换手', render: (_, row) => percent(row.metrics.mean_daily_turnover) },
        ]} />
      </>}
    </Card>}
  </Layout>
}

export function QlibArchivePage({ children }: { children: React.ReactNode }) {
  return <Layout title="历史工作区" description="原QTrade的ETF、75股分钟工具和固定报告；与Qlib实验分别保留身份。">
    <Alert type="warning" showIcon title="历史工具与报告" description="日期保留原始数据范围，不能理解为当前Qlib工作流的一部分。" />
    {children}
  </Layout>
}
