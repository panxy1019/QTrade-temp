import { Alert, Button, Card, Form, Input, InputNumber, Popconfirm, Select, Space, Switch, Table, Tag, Typography } from 'antd'
import { useState } from 'react'
import { createExperimentRunSpecDraft, runBacktestPreview, runExperimentRunSpecBacktest } from '../api/client'
import { EquityChart } from '../charts/EquityChart'
import { JsonExportButton } from './JsonExportButton'
import {
  deleteLocalBacktestExperiment,
  loadLocalBacktestExperiments,
  saveLocalBacktestExperiment,
} from '../research/localBacktestExperiments'
import type { LocalBacktestExperiment } from '../research/localBacktestExperiments'
import type { BacktestPreview, BacktestPreviewRequest, CausalPreviewTemplateId, ExperimentRunSpecBacktest, ExperimentRunSpecDraft, ExperimentRunSpecDraftRequest, HistoricalDataset, StrategyTemplate } from '../types'

interface BacktestFormValues {
  start: string
  end: string
  factor: BacktestPreviewRequest['factor']
  horizons: string
  top_k: number
  rebalance_every: number
  reserve: string | number
  trend_window: number
  volatility_window: number
  target_annual_volatility: string | number
  use_trend_filter: boolean
  use_volatility_scaling: boolean
  initial_cash: string | number
  transaction_cost_rate: string | number
  slippage_rate: string | number
}

interface RunSpecFormValues {
  experiment_id: string
  git_commit: string
  train_start: string
  train_end: string
  validation_start: string
  validation_end: string
  test_start: string
  test_end: string
  seed: number
}

interface BacktestLabProps {
  dataset: HistoricalDataset | null
  templates: StrategyTemplate[]
  initialTemplateId: CausalPreviewTemplateId | null
  gitCommit: string | null
}

const templatePresets: Record<CausalPreviewTemplateId, Partial<BacktestFormValues>> = {
  'etf-momentum-rotation-v1': {
    factor: 'momentum', horizons: '20,60,120', top_k: 3, rebalance_every: 20,
    reserve: '0.05', use_trend_filter: false, use_volatility_scaling: false,
  },
  'etf-momentum-trend-volatility-v1': {
    factor: 'momentum', horizons: '20,60,120', top_k: 3, rebalance_every: 20,
    reserve: '0.05', trend_window: 120, volatility_window: 20,
    target_annual_volatility: '0.15', use_trend_filter: true, use_volatility_scaling: true,
  },
  'cross-sectional-factor-ranking-v1': {
    factor: 'reversal', horizons: '20', top_k: 3, rebalance_every: 20,
    reserve: '0.05', trend_window: 120, volatility_window: 20,
    target_annual_volatility: '0.15', use_trend_filter: true, use_volatility_scaling: true,
  },
}

function toBacktestRequest(values: BacktestFormValues): BacktestPreviewRequest {
  return {
    start: values.start,
    end: values.end,
    factor: values.factor,
    horizons: values.horizons.split(',').map((value) => Number(value.trim())),
    top_k: values.top_k,
    rebalance_every: values.rebalance_every,
    reserve: String(values.reserve),
    trend_window: values.use_trend_filter ? values.trend_window : null,
    volatility_window: values.use_volatility_scaling ? values.volatility_window : null,
    target_annual_volatility: values.use_volatility_scaling ? String(values.target_annual_volatility) : null,
    initial_cash: String(values.initial_cash),
    transaction_cost_rate: String(values.transaction_cost_rate),
    slippage_rate: String(values.slippage_rate),
  }
}

function splitDefaults(dataset: HistoricalDataset): Omit<RunSpecFormValues, 'experiment_id' | 'git_commit' | 'seed'> {
  const start = Date.parse(`${dataset.observed_start.slice(0, 10)}T00:00:00Z`)
  const end = Date.parse(`${dataset.observed_end.slice(0, 10)}T00:00:00Z`)
  const day = 86_400_000
  const span = Math.max(5, Math.floor((end - start) / day))
  const trainEnd = start + Math.floor(span * 0.6) * day
  const validationStart = trainEnd + day
  const validationEnd = start + Math.floor(span * 0.8) * day
  const testStart = validationEnd + day
  const iso = (value: number) => new Date(value).toISOString().slice(0, 10)
  return {
    train_start: iso(start), train_end: iso(trainEnd),
    validation_start: iso(validationStart), validation_end: iso(validationEnd),
    test_start: iso(testStart), test_end: iso(end),
  }
}

function templateCompatibilityError(templateId: CausalPreviewTemplateId, request: BacktestPreviewRequest): string | null {
  if (templateId === 'etf-momentum-rotation-v1' && (
    request.factor !== 'momentum' || request.trend_window !== null
    || request.volatility_window !== null || request.target_annual_volatility !== null
  )) return 'ETF 动量轮动模板必须使用动量因子，且关闭趋势与波动率过滤。'
  if (templateId === 'etf-momentum-trend-volatility-v1' && (
    request.factor !== 'momentum' || request.trend_window === null
    || request.volatility_window === null || request.target_annual_volatility === null
  )) return 'ETF 动量 + 趋势波动率模板必须使用动量因子并启用两项过滤。'
  if (templateId === 'cross-sectional-factor-ranking-v1' && request.factor === 'momentum') {
    return '当前横截面预览请选择反转或低波动；动量值会进入另一套轮动实现。'
  }
  return null
}

export function BacktestLab({ dataset, templates, initialTemplateId, gitCommit }: BacktestLabProps) {
  const [form] = Form.useForm<BacktestFormValues>()
  const [specForm] = Form.useForm<RunSpecFormValues>()
  const [result, setResult] = useState<BacktestPreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const [experimentName, setExperimentName] = useState('')
  const [saveStatus, setSaveStatus] = useState<string | null>(null)
  const [, setExperimentRevision] = useState(0)
  const [selectedTemplateId, setSelectedTemplateId] = useState<CausalPreviewTemplateId | null>(initialTemplateId)
  const [draft, setDraft] = useState<ExperimentRunSpecDraft | null>(null)
  const [draftRequest, setDraftRequest] = useState<ExperimentRunSpecDraftRequest | null>(null)
  const [draftError, setDraftError] = useState<string | null>(null)
  const [drafting, setDrafting] = useState(false)
  const [boundBacktest, setBoundBacktest] = useState<ExperimentRunSpecBacktest | null>(null)
  const [bindingError, setBindingError] = useState<string | null>(null)
  const [binding, setBinding] = useState(false)
  const trendFilterEnabled = Form.useWatch('use_trend_filter', form) ?? initialTemplateId !== 'etf-momentum-rotation-v1'
  const volatilityScalingEnabled = Form.useWatch('use_volatility_scaling', form) ?? initialTemplateId !== 'etf-momentum-rotation-v1'
  if (!dataset) {
    return <Card title="参数化回测实验室"><Alert showIcon type="warning" title="需要先准备校验通过的历史数据" description="实验室不会使用缓存、示例或合成行情替代真实数据。" /></Card>
  }
  const savedExperiments = loadLocalBacktestExperiments(dataset)

  const initialValues: BacktestFormValues = {
    start: dataset.observed_start.slice(0, 10),
    end: dataset.observed_end.slice(0, 10),
    factor: 'momentum',
    horizons: '20,60,120',
    top_k: 3,
    rebalance_every: 20,
    reserve: '0.05',
    trend_window: 120,
    volatility_window: 20,
    target_annual_volatility: '0.15',
    use_trend_filter: true,
    use_volatility_scaling: true,
    initial_cash: '1000000',
    transaction_cost_rate: '0.0015',
    slippage_rate: '0.0005',
    ...(initialTemplateId ? templatePresets[initialTemplateId] : {}),
  }
  const selectedTemplate = templates.find((template) => template.template_id === selectedTemplateId)
  const supportedTemplates = templates.filter((template) => template.causal_preview_supported)
  const runSpecInitialValues: RunSpecFormValues = {
    experiment_id: selectedTemplateId ? `${selectedTemplateId}-replay-001` : '',
    git_commit: gitCommit ?? '',
    ...splitDefaults(dataset),
    seed: 7,
  }
  const submit = async (values: BacktestFormValues) => {
    const payload = toBacktestRequest(values)
    if (selectedTemplateId) {
      const compatibilityError = templateCompatibilityError(selectedTemplateId, payload)
      if (compatibilityError) {
        setResult(null)
        setError(compatibilityError)
        return
      }
    }
    setRunning(true)
    setError(null)
    setBoundBacktest(null)
    setBindingError(null)
    try {
      setResult(await runBacktestPreview(payload))
      setSaveStatus(null)
    } catch (reason) {
      setResult(null)
      setError(reason instanceof Error ? reason.message : '即时回测失败')
    } finally {
      setRunning(false)
    }
  }
  const applyTemplate = (templateId: CausalPreviewTemplateId | null) => {
    setSelectedTemplateId(templateId)
    setResult(null)
    setError(null)
    setDraft(null)
    setDraftRequest(null)
    setDraftError(null)
    setBoundBacktest(null)
    setBindingError(null)
    if (!templateId) return
    form.setFieldsValue(templatePresets[templateId])
    specForm.setFieldValue('experiment_id', `${templateId}-replay-001`)
  }
  const generateDraft = async (values: RunSpecFormValues) => {
    if (!selectedTemplateId) {
      setDraft(null)
      setDraftError('请先选择一个已接入因果实验室的代码模板。')
      return
    }
    setDrafting(true)
    setDraftError(null)
    try {
      const backtestValues = await form.validateFields()
      const backtest = toBacktestRequest({
        ...backtestValues,
        start: values.test_start,
        end: values.test_end,
      })
      const compatibilityError = templateCompatibilityError(selectedTemplateId, backtest)
      if (compatibilityError) throw new Error(compatibilityError)
      form.setFieldsValue({ start: values.test_start, end: values.test_end })
      const request: ExperimentRunSpecDraftRequest = {
        template_id: selectedTemplateId,
        experiment_id: values.experiment_id,
        git_commit: values.git_commit,
        split: {
          train_start: values.train_start,
          train_end: values.train_end,
          validation_start: values.validation_start,
          validation_end: values.validation_end,
          test_start: values.test_start,
          test_end: values.test_end,
        },
        backtest,
        seed: values.seed,
      }
      setDraft(await createExperimentRunSpecDraft(request))
      setDraftRequest(request)
      setBoundBacktest(null)
      setBindingError(null)
    } catch (reason) {
      setDraft(null)
      setDraftRequest(null)
      setDraftError(reason instanceof Error ? reason.message : '运行规格草案生成失败')
    } finally {
      setDrafting(false)
    }
  }
  const runBoundBacktest = async () => {
    if (!draftRequest) {
      setBindingError('规格或回测参数已变化，请重新生成哈希草案。')
      return
    }
    setBinding(true)
    setBindingError(null)
    try {
      const response = await runExperimentRunSpecBacktest(draftRequest)
      setBoundBacktest(response)
      setResult(response.result)
      setError(null)
    } catch (reason) {
      setBoundBacktest(null)
      setBindingError(reason instanceof Error ? reason.message : '规格绑定回测失败')
    } finally {
      setBinding(false)
    }
  }
  const invalidateDraft = () => {
    setDraft(null)
    setDraftRequest(null)
    setDraftError(null)
    setBoundBacktest(null)
    setBindingError(null)
  }
  const saveResult = () => {
    if (!result) return
    if (result.data_version !== dataset.data_version || result.data_checksum !== dataset.checksum) {
      setSaveStatus('数据集已变化，请重新运行后再保存。')
      return
    }
    try {
      const saved = saveLocalBacktestExperiment(experimentName, result, boundBacktest)
      setExperimentRevision((value) => value + 1)
      setExperimentName(saved.name)
      setSaveStatus(`已保存“${saved.name}”；相同结果哈希会更新原记录。`)
    } catch (reason) {
      setSaveStatus(reason instanceof Error ? reason.message : '本地实验保存失败')
    }
  }
  const loadParameters = (record: LocalBacktestExperiment) => {
    const parameters = record.parameters
    form.setFieldsValue({
      start: parameters.start,
      end: parameters.end,
      factor: parameters.factor,
      horizons: parameters.horizons.join(','),
      top_k: parameters.top_k,
      rebalance_every: parameters.rebalance_every,
      reserve: parameters.reserve,
      trend_window: parameters.trend_window ?? 120,
      volatility_window: parameters.volatility_window ?? 20,
      target_annual_volatility: parameters.target_annual_volatility ?? '0.15',
      use_trend_filter: parameters.trend_window !== null,
      use_volatility_scaling: parameters.volatility_window !== null,
      initial_cash: parameters.initial_cash,
      transaction_cost_rate: parameters.transaction_cost_rate,
      slippage_rate: parameters.slippage_rate,
    })
    setResult(null)
    setError(null)
    setSelectedTemplateId(null)
    setDraft(null)
    setDraftRequest(null)
    setDraftError(null)
    setBoundBacktest(null)
    setBindingError(null)
    setExperimentName(record.name)
    setSaveStatus(`已载入“${record.name}”的参数；请重新运行以验证当前结果。`)
  }
  const removeExperiment = (previewHash: string) => {
    deleteLocalBacktestExperiment(previewHash)
    setExperimentRevision((value) => value + 1)
    setSaveStatus('本地实验记录已删除。')
  }
  const experimentColumns = [
    { title: '名称', dataIndex: 'name' },
    { title: '保存时间', dataIndex: 'saved_at', render: (value: string) => formatTimestamp(value) },
    {
      title: '规格身份',
      render: (_: unknown, row: LocalBacktestExperiment) => row.binding
        ? <Space orientation="vertical" size={0}>
            <Tag color="green">绑定草案</Tag>
            <Typography.Text type="secondary">规格 {row.binding.spec_hash.slice(0, 12)}… · 绑定 {row.binding.binding_hash.slice(0, 12)}…</Typography.Text>
          </Space>
        : <Tag>即时预览</Tag>,
    },
    { title: '参数', render: (_: unknown, row: LocalBacktestExperiment) => `${factorLabel(row.parameters.factor)} · ${row.parameters.horizons.join('/')} 日 · Top ${row.parameters.top_k} · ${row.parameters.rebalance_every} 日调仓` },
    { title: '累计收益', render: (_: unknown, row: LocalBacktestExperiment) => formatPercent(row.metrics.total_return) },
    { title: 'Sharpe', render: (_: unknown, row: LocalBacktestExperiment) => formatNumber(row.metrics.sharpe) },
    { title: '最大回撤', render: (_: unknown, row: LocalBacktestExperiment) => formatPercent(row.metrics.maximum_drawdown) },
    {
      title: '操作',
      render: (_: unknown, row: LocalBacktestExperiment) => <Space size={4}>
        <Button type="link" size="small" onClick={() => loadParameters(row)}>载入参数</Button>
        <Popconfirm title="删除这条本地实验记录？" onConfirm={() => removeExperiment(row.preview_hash)}>
          <Button type="link" danger size="small">删除</Button>
        </Popconfirm>
      </Space>,
    },
  ]

  return <Card title="参数化回测实验室" extra={<Typography.Text type="secondary">数据版本 {dataset.data_version}</Typography.Text>}>
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <Alert showIcon type="warning" title="即时预览，不是策略晋级证据" description="参数可随时调整；每次都从 checksum 校验通过的非 PIT 历史数据重新计算，不保存为正式实验，不覆盖既有策略，也不影响 PAPER 运行时。" />
      <Space wrap>
        <Typography.Text>代码基线模板</Typography.Text>
        <Select<CausalPreviewTemplateId> aria-label="基线策略模板" allowClear placeholder="选择已接入模板" value={selectedTemplateId ?? undefined} onChange={(value) => applyTemplate(value ?? null)} options={supportedTemplates.map((template) => ({ value: template.template_id as CausalPreviewTemplateId, label: template.name }))} style={{ width: 320 }} />
      </Space>
      {selectedTemplate && <Alert showIcon type="info" title={`已载入模板：${selectedTemplate.name}`} description="模板参数只是预填值，不是已冻结规格；修改后必须重新生成规格哈希，且当前历史数据已经可见，不能变成新的独立样本外证据。" />}
      <Form<BacktestFormValues> form={form} layout="vertical" initialValues={initialValues} onValuesChange={invalidateDraft} onFinish={(values) => { void submit(values) }}>
        <div className="backtest-lab-grid">
          <Form.Item name="start" label="开始日期" rules={[{ required: true }]}><Input type="date" /></Form.Item>
          <Form.Item name="end" label="结束日期" rules={[{ required: true }]}><Input type="date" /></Form.Item>
          <Form.Item name="factor" label="因子模型" rules={[{ required: true }]}><Select options={[
            { value: 'momentum', label: '动量' },
            { value: 'reversal', label: '反转' },
            { value: 'low_volatility', label: '低波动' },
          ]} /></Form.Item>
          <Form.Item name="horizons" label="因子周期（日）" rules={[{ required: true, pattern: /^\d+(\s*,\s*\d+){0,2}$/, message: '输入 1-3 个逗号分隔的周期' }]}><Input placeholder="20,60,120" /></Form.Item>
          <Form.Item name="top_k" label="持仓数量 Top K" rules={[{ required: true }]}><InputNumber min={1} max={10} precision={0} /></Form.Item>
          <Form.Item name="rebalance_every" label="调仓间隔（日）" rules={[{ required: true }]}><InputNumber min={1} max={120} precision={0} /></Form.Item>
          <Form.Item name="reserve" label="现金预留比例" rules={[{ required: true }]}><InputNumber min={0} max={0.5} step={0.01} stringMode /></Form.Item>
          <Form.Item name="use_trend_filter" label="启用趋势过滤" valuePropName="checked"><Switch /></Form.Item>
          <Form.Item name="trend_window" label="趋势过滤窗口" rules={[{ required: trendFilterEnabled }]}><InputNumber disabled={!trendFilterEnabled} min={2} max={252} precision={0} /></Form.Item>
          <Form.Item name="use_volatility_scaling" label="启用波动率缩放" valuePropName="checked"><Switch /></Form.Item>
          <Form.Item name="volatility_window" label="波动率窗口" rules={[{ required: volatilityScalingEnabled }]}><InputNumber disabled={!volatilityScalingEnabled} min={2} max={252} precision={0} /></Form.Item>
          <Form.Item name="target_annual_volatility" label="目标年化波动率" rules={[{ required: volatilityScalingEnabled }]}><InputNumber disabled={!volatilityScalingEnabled} min={0.01} max={1} step={0.01} stringMode /></Form.Item>
          <Form.Item name="initial_cash" label="初始资金" rules={[{ required: true }]}><InputNumber min={10000} max={100000000} step={10000} stringMode /></Form.Item>
          <Form.Item name="transaction_cost_rate" label="单边交易费率" rules={[{ required: true }]}><InputNumber min={0} max={0.05} step={0.0001} stringMode /></Form.Item>
          <Form.Item name="slippage_rate" label="滑点率" rules={[{ required: true }]}><InputNumber min={0} max={0.05} step={0.0001} stringMode /></Form.Item>
        </div>
        <Button type="primary" htmlType="submit" loading={running}>运行即时回测</Button>
      </Form>
      <Card type="inner" title="ExperimentRunSpec 哈希草案" extra={<Typography.Text type="secondary">13 字段 · 不落库</Typography.Text>}>
        <Space orientation="vertical" style={{ width: '100%' }}>
          <Alert showIcon type="warning" title="哈希完整不等于已经冻结" description="这里生成完整 13 字段及确定性哈希，并把测试窗载入上方因果回测；不会写入不可变实验存储。必须在接触新的互斥证据前另行持久化，并通过独立泄漏审查。" />
          <Form<RunSpecFormValues> name="experiment-run-spec-draft" form={specForm} layout="vertical" initialValues={runSpecInitialValues} onValuesChange={invalidateDraft} onFinish={(values) => { void generateDraft(values) }}>
            <div className="backtest-lab-grid">
              <Form.Item name="experiment_id" label="实验 ID" rules={[{ required: true }, { pattern: /^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$/, message: '输入 1-80 个安全字符' }]}><Input /></Form.Item>
              <Form.Item name="git_commit" label="源码 Commit" rules={[{ required: true }, { pattern: /^[0-9a-f]{40}$/, message: '需要完整 40 位 Git SHA' }]}><Input /></Form.Item>
              <Form.Item name="train_start" label="训练开始" rules={[{ required: true }]}><Input type="date" /></Form.Item>
              <Form.Item name="train_end" label="训练结束" rules={[{ required: true }]}><Input type="date" /></Form.Item>
              <Form.Item name="validation_start" label="验证开始" rules={[{ required: true }]}><Input type="date" /></Form.Item>
              <Form.Item name="validation_end" label="验证结束" rules={[{ required: true }]}><Input type="date" /></Form.Item>
              <Form.Item name="test_start" label="测试开始" rules={[{ required: true }]}><Input type="date" /></Form.Item>
              <Form.Item name="test_end" label="测试结束" rules={[{ required: true }]}><Input type="date" /></Form.Item>
              <Form.Item name="seed" label="随机种子" rules={[{ required: true }]}><InputNumber min={0} max={2147483647} precision={0} /></Form.Item>
            </div>
            <Button htmlType="submit" loading={drafting}>生成哈希化运行规格草案</Button>
          </Form>
          {draftError && <Alert showIcon type="error" title="规格草案未生成" description={draftError} />}
          {draft && <>
            <Alert showIcon type="success" title={`规格哈希 ${draft.spec_hash.slice(0, 16)}…`} description="哈希完整但未写入不可变存储；当前历史测试窗此前可见，不能变成新的未见证据。" />
            <Space wrap>
              <Tag color="orange">不可变存储 0/1</Tag>
              <Tag color="orange">独立泄漏审查 0/1</Tag>
              <Tag color="orange">新未见证据 0/1</Tag>
              <JsonExportButton filename={`qtrade-run-spec-draft-${draft.spec_hash.slice(0, 12)}.json`} value={draft} label="导出运行规格草案" />
              <Button onClick={() => { void runBoundBacktest() }} loading={binding}>按该哈希运行因果回测</Button>
            </Space>
          </>}
          {bindingError && <Alert showIcon type="error" title="规格绑定回测未完成" description={bindingError} />}
          {boundBacktest && <>
            <Alert showIcon type="success" title={`规格→结果绑定 ${boundBacktest.binding_hash.slice(0, 16)}…`} description="同一请求重新计算规格并运行，但仍未持久化为正式实验；绑定哈希不能替代独立泄漏审查或新未见证据。" />
            <Space wrap>
              <Tag color="green">单请求绑定 1/1</Tag>
              <Tag color="orange">不可变存储 0/1</Tag>
              <Tag color="orange">新未见证据 0/1</Tag>
              <JsonExportButton filename={`qtrade-bound-backtest-${boundBacktest.binding_hash.slice(0, 12)}.json`} value={boundBacktest} label="导出规格绑定回测" />
            </Space>
          </>}
        </Space>
      </Card>
      {error && <Alert showIcon type="error" title="即时回测未完成" description={error} />}
      {result && <>
        <Alert showIcon type="success" title={`已完成 ${result.session_count} 个会话、${result.universe_size} 个标的 · ${factorLabel(result.parameters.factor)}`} description={`结果哈希 ${result.preview_hash.slice(0, 16)}…；结论固定为 ${result.promotion}。`} />
        <div className="description-grid">
          <Metric label="累计收益" value={formatPercent(result.metrics.total_return)} />
          <Metric label="年化收益" value={formatPercent(result.metrics.annualized_return)} />
          <Metric label="Sharpe" value={formatNumber(result.metrics.sharpe)} />
          <Metric label="最大回撤" value={formatPercent(result.metrics.maximum_drawdown)} />
          <Metric label="同窗 Buy-and-Hold" value={formatPercent(result.metrics.benchmark_return)} />
          <Metric label="主动超额" value={formatPercent(result.metrics.excess_return)} />
          <Metric label="信息比率" value={formatNumber(result.metrics.information_ratio)} />
          <Metric label="平均敞口" value={formatPercent(result.metrics.average_exposure)} />
          <Metric label="模拟成交数" value={result.metrics.number_of_trades ?? '—'} />
          <Metric label="换手" value={formatNumber(result.metrics.turnover)} />
          <Metric label="交易成本" value={formatNumber(result.metrics.transaction_costs)} />
        </div>
        <EquityChart dark analytics={result} />
        <Typography.Text type="secondary">因果时序：收盘生成目标，下一可用开盘模拟成交。等权 Buy-and-Hold 使用同一窗口、初始资金、现金预留、成本、滑点和下一开盘执行；曲线逐交易日对齐后计算超额。该结果使用当前样本池，存在幸存者偏差，禁止直接作为实盘依据。</Typography.Text>
        <Space wrap>
          <Input aria-label="本地实验名称" placeholder="为本次结果命名" value={experimentName} maxLength={80} onChange={(event) => setExperimentName(event.target.value)} style={{ width: 280 }} />
          <Button onClick={saveResult}>保存到本地实验</Button>
          <JsonExportButton filename={`qtrade-backtest-${result.preview_hash.slice(0, 12)}.json`} value={result} label="导出完整结果" />
        </Space>
      </>}
      {saveStatus && <Alert showIcon type="info" title={saveStatus} />}
      <Card type="inner" title={`本地实验对比（${savedExperiments.length}/30）`} extra={<Typography.Text type="secondary">仅当前浏览器</Typography.Text>}>
        <Table<LocalBacktestExperiment>
          rowKey="preview_hash"
          size="small"
          columns={experimentColumns}
          dataSource={savedExperiments}
          pagination={{ pageSize: 5, showSizeChanger: false }}
          scroll={{ x: 980 }}
          locale={{ emptyText: '运行回测后可保存结果，用于反复调参与对比。' }}
        />
        <Typography.Text type="secondary">记录严格绑定当前数据版本与 checksum；绑定草案会额外保存 template/spec/binding 哈希。全部记录都仅在当前浏览器、可删除，不是不可变实验或注册记录，不参与晋级，也不会同步到其他设备。</Typography.Text>
      </Card>
    </Space>
  </Card>
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>
}

function formatNumber(value?: string) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toFixed(3) : '—'
}

function formatPercent(value?: string) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `${(parsed * 100).toFixed(2)}%` : '—'
}

function formatTimestamp(value: string) {
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleString('zh-CN', { hour12: false })
}

function factorLabel(value: BacktestPreviewRequest['factor']) {
  return { momentum: '动量', reversal: '反转', low_volatility: '低波动' }[value]
}
