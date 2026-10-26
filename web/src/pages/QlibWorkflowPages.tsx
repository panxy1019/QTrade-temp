import { useEffect, useState } from 'react'
import { Alert, Button, Card, Descriptions, Empty, Form, Input, InputNumber, Select, Space, Statistic, Table, Tag, Timeline, Typography, theme } from 'antd'
import { useNavigate, useSearchParams } from 'react-router-dom'
import { useResearchWorkflow } from '../components/ResearchWorkflowProvider'
import { JsonExportButton } from '../components/JsonExportButton'
import ChartRenderer from '../charts/ChartRenderer'
import { comparisonIssue, completedExperiments, finalStates, limitationLabels, stageLabels, workflowGet, workflowPost } from '../research/qlibWorkflow'
import type { ResearchAudit, ResearchSpec, WorkflowExperiment, WorkflowResult } from '../research/qlibWorkflow'

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
        <Typography.Text>来源快照：{constraints.source_snapshot_sha256}。回填为事后诊断；ST禁买是研究政策，精确涨跌停与完整历史PIT仍未认证。逐股票逐日账本保存在集群归档中。</Typography.Text>
      </>}
    </Space>
  </Card>
}

function Login() {
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
      <Tag color="green">{session.user} 已登录</Tag><Typography.Text>仅可提交和取消研究任务</Typography.Text>
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
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [requestId, setRequestId] = useState<string | null>(null)
  useEffect(() => {
    if (!catalog) return
    const spec = params.get('model') === 'linear' ? catalog.default_linear_spec : catalog.default_spec
    const { segments, ...rest } = spec
    form.setFieldsValue({
      ...rest, train_start: segments.train[0], train_end: segments.train[1],
      valid_start: segments.valid[0], valid_end: segments.valid[1],
      test_start: segments.test[0], test_end: segments.test[1],
    })
  // Set the initial configuration once per chosen model, not on each polling response.
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [form, params.get('model'), !!catalog])
  const submit = async (values: FormSpec) => {
    setBusy(true); setError(null)
    const identity = requestId ?? crypto.randomUUID().replaceAll('-', '')
    setRequestId(identity)
    const { train_start, train_end, valid_start, valid_end, test_start, test_end, ...rest } = values
    const spec: ResearchSpec = {
      ...rest, dataset_id: catalog!.default_spec.dataset_id, features: 'Alpha158',
      universe_policy: 'supported_a_share',
      segments: { train: [train_start, train_end], valid: [valid_start, valid_end], test: [test_start, test_end] },
    }
    try {
      await workflowPost('/jobs', { request_id: identity, spec })
      sessionStorage.setItem('qtrade.pending-research-job', JSON.stringify({ id: identity, spec }))
      await refresh()
      navigate('/experiments?job=' + identity)
    } catch (reason) { setError(String(reason)) }
    finally { setBusy(false) }
  }
  return <Layout title="策略研究" description="冻结数据、时间切分、模型、组合与成本后，提交集群任务。">
    <Login />
    {catalog?.datasets[0] && <Card title="研究输入">
      <Typography.Paragraph>{catalog.datasets[0].id} · {catalog.datasets[0].symbols}股输入库存 · {catalog.datasets[0].rows.toLocaleString()}根日线 · 截至{catalog.datasets[0].end}。训练和排名排除科创板，实际动态资格数量以实验审计为准。</Typography.Paragraph>
      {catalog.source_snapshot_sha256 && <Typography.Paragraph>历史状态来源快照将在表单初始化时固定；后续回填不会改变这份任务。需要比较模型时，请使用同一来源快照。</Typography.Paragraph>}
      <Audit audit={catalog.datasets[0].audit} />
    </Card>}
    <Card title="Qlib训练与回测配置">
      <Form form={form} layout="vertical" onFinish={values => void submit(values)} onValuesChange={() => { setRequestId(null); setError(null) }}>
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
        <Button style={{ marginTop: 16 }} type="primary" htmlType="submit" loading={busy} disabled={!session.authenticated || !catalog?.publication_available || catalog.worker_fresh === false}>提交集群研究任务</Button>
      </Form>
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
  return <Layout title="实验中心" description="统一查看Qlib任务状态、固定规格、归档身份与逐实验限制。">
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
      { title: '累计收益', render: (_, row) => percent(row.metrics?.total_return_net) },
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
      <JsonExportButton filename={result.experiment_id + '-result.json'} value={result} />
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
  return <Layout title="回测分析" description="选择已完成Qlib实验，查看固定结果；统一协议后进行模型对照。">
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

