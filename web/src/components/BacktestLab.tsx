import { Alert, Button, Card, Form, Input, InputNumber, Popconfirm, Select, Space, Table, Typography } from 'antd'
import { useState } from 'react'
import { runBacktestPreview } from '../api/client'
import { EquityChart } from '../charts/EquityChart'
import { JsonExportButton } from './JsonExportButton'
import {
  deleteLocalBacktestExperiment,
  loadLocalBacktestExperiments,
  saveLocalBacktestExperiment,
} from '../research/localBacktestExperiments'
import type { LocalBacktestExperiment } from '../research/localBacktestExperiments'
import type { BacktestPreview, BacktestPreviewRequest, HistoricalDataset } from '../types'

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
  initial_cash: string | number
  transaction_cost_rate: string | number
  slippage_rate: string | number
}

export function BacktestLab({ dataset }: { dataset: HistoricalDataset | null }) {
  const [form] = Form.useForm<BacktestFormValues>()
  const [result, setResult] = useState<BacktestPreview | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const [experimentName, setExperimentName] = useState('')
  const [saveStatus, setSaveStatus] = useState<string | null>(null)
  const [, setExperimentRevision] = useState(0)
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
    initial_cash: '1000000',
    transaction_cost_rate: '0.0015',
    slippage_rate: '0.0005',
  }
  const submit = async (values: BacktestFormValues) => {
    const payload: BacktestPreviewRequest = {
      ...values,
      horizons: values.horizons.split(',').map((value) => Number(value.trim())),
      reserve: String(values.reserve),
      target_annual_volatility: String(values.target_annual_volatility),
      initial_cash: String(values.initial_cash),
      transaction_cost_rate: String(values.transaction_cost_rate),
      slippage_rate: String(values.slippage_rate),
    }
    setRunning(true)
    setError(null)
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
  const saveResult = () => {
    if (!result) return
    if (result.data_version !== dataset.data_version || result.data_checksum !== dataset.checksum) {
      setSaveStatus('数据集已变化，请重新运行后再保存。')
      return
    }
    try {
      const saved = saveLocalBacktestExperiment(experimentName, result)
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
      initial_cash: parameters.initial_cash,
      transaction_cost_rate: parameters.transaction_cost_rate,
      slippage_rate: parameters.slippage_rate,
    })
    setResult(null)
    setError(null)
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
      <Form<BacktestFormValues> form={form} layout="vertical" initialValues={initialValues} onFinish={(values) => { void submit(values) }}>
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
          <Form.Item name="trend_window" label="趋势过滤窗口" rules={[{ required: true }]}><InputNumber min={2} max={252} precision={0} /></Form.Item>
          <Form.Item name="volatility_window" label="波动率窗口" rules={[{ required: true }]}><InputNumber min={2} max={252} precision={0} /></Form.Item>
          <Form.Item name="target_annual_volatility" label="目标年化波动率" rules={[{ required: true }]}><InputNumber min={0.01} max={1} step={0.01} stringMode /></Form.Item>
          <Form.Item name="initial_cash" label="初始资金" rules={[{ required: true }]}><InputNumber min={10000} max={100000000} step={10000} stringMode /></Form.Item>
          <Form.Item name="transaction_cost_rate" label="单边交易费率" rules={[{ required: true }]}><InputNumber min={0} max={0.05} step={0.0001} stringMode /></Form.Item>
          <Form.Item name="slippage_rate" label="滑点率" rules={[{ required: true }]}><InputNumber min={0} max={0.05} step={0.0001} stringMode /></Form.Item>
        </div>
        <Button type="primary" htmlType="submit" loading={running}>运行即时回测</Button>
      </Form>
      {error && <Alert showIcon type="error" title="即时回测未完成" description={error} />}
      {result && <>
        <Alert showIcon type="success" title={`已完成 ${result.session_count} 个会话、${result.universe_size} 个标的 · ${factorLabel(result.parameters.factor)}`} description={`结果哈希 ${result.preview_hash.slice(0, 16)}…；结论固定为 ${result.promotion}。`} />
        <div className="description-grid">
          <Metric label="累计收益" value={formatPercent(result.metrics.total_return)} />
          <Metric label="年化收益" value={formatPercent(result.metrics.annualized_return)} />
          <Metric label="Sharpe" value={formatNumber(result.metrics.sharpe)} />
          <Metric label="最大回撤" value={formatPercent(result.metrics.maximum_drawdown)} />
          <Metric label="平均敞口" value={formatPercent(result.metrics.average_exposure)} />
          <Metric label="模拟成交数" value={result.metrics.number_of_trades ?? '—'} />
          <Metric label="换手" value={formatNumber(result.metrics.turnover)} />
          <Metric label="交易成本" value={formatNumber(result.metrics.transaction_costs)} />
        </div>
        <EquityChart dark analytics={result} />
        <Typography.Text type="secondary">因果时序：收盘生成目标，下一可用开盘模拟成交。该结果使用当前样本池，存在幸存者偏差，禁止直接作为实盘依据。</Typography.Text>
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
        <Typography.Text type="secondary">记录严格绑定当前数据版本与 checksum；这里只保存参数和摘要，不保存为正式实验、不参与晋级，也不会同步到其他设备。</Typography.Text>
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
