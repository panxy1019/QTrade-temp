import { Alert, Button, Card, Form, InputNumber, Select, Space, Steps, Table, Tabs, Tag, Typography } from 'antd'
import { useState } from 'react'
import { runIntradayFactorBacktest } from '../api/client'
import { EquityChart } from '../charts/EquityChart'
import type { FiveMinuteResearchReport, IntradayFactorBacktest, IntradayFactorBacktestRequest } from '../types'
import { JsonExportButton } from './JsonExportButton'

type VerifiedFiveMinuteDataset = NonNullable<FiveMinuteResearchReport['minute_dataset']>

interface FormValues {
  factor: IntradayFactorBacktestRequest['factor']
  start: string
  end: string
  cost_bps: string | number
  minimum_body_return_bps: string | number
}

const workflowLabels: Record<string, string> = {
  FACTOR_DEFINITION: '因子定义',
  FACTOR_VALIDATION: '有效性检验',
  CORRELATION_VALIDATION: '相关性检验',
  STRATEGY_CONSTRUCTION: '策略构造',
  CAUSAL_BACKTEST: '因果回测',
  PAPER: '模拟盘',
  LIVE: '实盘',
}

const gateLabels: Record<string, string> = {
  minimum_observation_count: '至少 250 个观测',
  minimum_signal_count: '至少 20 次信号',
  positive_mean_signal_return: '信号后平均收益为正',
  positive_conditional_excess_return: '条件超额收益为正',
  positive_rank_ic: 'Rank IC 为正',
  positive_sector_ratio_at_least_60pct: '至少 60% 行业为正',
  maximum_absolute_factor_correlation_below_0_90: '相关性绝对值低于 0.90',
  base_cost_total_return_positive: '基础成本下总收益为正',
  base_cost_excess_return_positive: '基础成本下超额收益为正',
}

export function IntradayFactorResearchLab({ dataset }: { dataset: VerifiedFiveMinuteDataset | null }) {
  const [result, setResult] = useState<IntradayFactorBacktest | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)

  if (!dataset) {
    return <Card title="分钟因子研究工作台"><Alert showIcon type="warning" title="长历史 5 分钟数据不可用" description="系统不会用合成行情或不完整缓存替代经过 checksum 验证的多股票数据。" /></Card>
  }

  const eligibleEndDays = dataset.trading_days.slice(0, -1)
  const defaultEndIndex = eligibleEndDays.length - 1
  const initialValues: FormValues = {
    factor: 'opening_three_ten_minute_bullish',
    start: eligibleEndDays[Math.max(0, defaultEndIndex - 19)],
    end: eligibleEndDays[defaultEndIndex],
    cost_bps: '5',
    minimum_body_return_bps: '0',
  }
  const submit = async (values: FormValues) => {
    const payload: IntradayFactorBacktestRequest = {
      ...values,
      cost_bps: String(values.cost_bps),
      minimum_body_return_bps: String(values.minimum_body_return_bps),
    }
    setRunning(true)
    setError(null)
    try {
      setResult(await runIntradayFactorBacktest(payload))
    } catch (reason) {
      setResult(null)
      setError(reason instanceof Error ? reason.message : '分钟因子研究失败')
    } finally {
      setRunning(false)
    }
  }
  const curve = result ? {
    curve: [
      { session: '起点', equity: '1', drawdown: '0', benchmark_equity: '1', benchmark_drawdown: '0', excess_return: '0' },
      ...buildChartCurve(result),
    ],
  } : null
  const stressRows = result
    ? Object.entries(result.backtest.cost_stress).map(([cost_bps, value]) => ({ cost_bps, ...value }))
    : []
  const gateRows = result
    ? Object.entries(result.research_gate.criteria).map(([gate_id, passed]) => ({ gate_id, passed }))
    : []
  const correlationRows = result
    ? Object.entries(result.correlation_validation.correlations).map(([factor, correlation]) => ({ factor, correlation }))
    : []

  return <Card title="分钟因子研究工作台" extra={<Typography.Text type="secondary">{dataset.symbol_count} 股 · {dataset.sector_count} 板块 · 原生 5 分钟</Typography.Text>}>
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <Alert showIcon type="info" title="从因子到模拟盘的完整门控" description="三根 10 分钟阳线在 10:00 才确认，最早取 10:05 标记的下一根 5 分钟 Bar 开盘价（区间起点 10:00）买入，并按 A 股 T+1 取下一交易日 09:35 标记 Bar 的开盘价（区间起点 09:30）退出。当前参数研究是事后非 PIT 检验，永远不会自动进入 PAPER 或 LIVE。" />
      <Form<FormValues> name="intraday-factor-research-backtest" layout="vertical" initialValues={initialValues} onFinish={(values) => { void submit(values) }}>
        <div className="backtest-lab-grid">
          <Form.Item name="factor" label="因子" rules={[{ required: true }]}><Select options={[{ value: 'opening_three_ten_minute_bullish', label: '09:30 起三根 10 分钟阳线' }]} /></Form.Item>
          <Form.Item name="start" label="开始交易日" rules={[{ required: true }]}><Select showSearch options={eligibleEndDays.map((value) => ({ value, label: value }))} /></Form.Item>
          <Form.Item name="end" label="结束交易日" rules={[{ required: true }]}><Select showSearch options={eligibleEndDays.map((value) => ({ value, label: value }))} /></Form.Item>
          <Form.Item name="cost_bps" label="单边成本（bps）" rules={[{ required: true }]}><InputNumber min={0} max={100} step={1} stringMode /></Form.Item>
          <Form.Item name="minimum_body_return_bps" label="每根 10 分钟实体下限（bps）" rules={[{ required: true }]}><InputNumber min={0} max={1000} step={1} stringMode /></Form.Item>
        </div>
        <Button type="primary" htmlType="submit" loading={running}>运行 75 股因子检验与回测</Button>
      </Form>
      {error && <Alert showIcon type="error" title="因子研究未完成" description={error} />}
      {result && <>
        <Alert
          showIcon
          type={result.research_gate.passed ? 'success' : 'warning'}
          title={result.research_gate.passed ? '研究硬门通过，但仍不可自动晋级' : '回测已完成，研究硬门未通过'}
          description={`实际触发 ${result.event_summary.signal_count}/${result.event_summary.observation_count} 次；${result.event_summary.positive_sector_count}/${result.event_summary.sector_count} 个行业信号后均值为正。当前结论 ${result.promotion}，PAPER ${result.paper_readiness}，LIVE ${result.live_readiness}。`}
        />
        <Steps
          size="small"
          responsive
          items={result.workflow.map((stage) => ({
            title: workflowLabels[stage.id] ?? stage.id,
            content: stage.status,
            status: stage.status === 'FAILED' ? 'error' : ['PASSED', 'AVAILABLE', 'COMPLETED'].includes(stage.status) ? 'finish' : 'wait',
          }))}
        />
        <div className="description-grid">
          <Metric label="累计收益" value={formatPercent(result.backtest.strategy_metrics.total_return)} />
          <Metric label="年化收益" value={formatPercent(result.backtest.strategy_metrics.annualized_return)} />
          <Metric label="Sharpe" value={formatNumber(result.backtest.strategy_metrics.sharpe)} />
          <Metric label="最大回撤" value={formatPercent(result.backtest.strategy_metrics.maximum_drawdown)} />
          <Metric label="超额收益" value={formatPercent(result.backtest.strategy_metrics.excess_return)} />
          <Metric label="胜率" value={formatPercent(result.backtest.strategy_metrics.win_rate)} />
          <Metric label="Rank IC" value={formatNumber(result.factor_validation.rank_ic)} />
          <Metric label="ICIR" value={formatNumber(result.factor_validation.icir)} />
          <Metric label="信号率" value={formatPercent(result.event_summary.signal_rate)} />
          <Metric label="条件超额" value={formatPercent(result.event_summary.conditional_excess_return)} />
        </div>
        <EquityChart dark analytics={curve} />
        <Tabs items={[
          {
            key: 'contract',
            label: '因子与时序',
            children: <Space orientation="vertical" style={{ width: '100%' }}>
              <Typography.Text code>{result.factor_definition.expression}</Typography.Text>
              <Typography.Text>数据：{result.factor_definition.source_interval}；信号已知：{result.factor_definition.known_at}；入场：{result.strategy.entry_rule}；退出：{result.strategy.exit_rule}。</Typography.Text>
              <Typography.Text type="secondary">响应哈希 {result.preview_hash}；数据 checksum {result.dataset_checksum}；读取 {result.source_bar_count.toLocaleString('zh-CN')} 根已验证 Bar，无状态写入、无外部订单。</Typography.Text>
            </Space>,
          },
          {
            key: 'gates',
            label: '有效性与相关性',
            children: <Space orientation="vertical" style={{ width: '100%' }}>
              <Table rowKey="gate_id" size="small" pagination={false} dataSource={gateRows} columns={[
                { title: '检验门槛', dataIndex: 'gate_id', render: (value: string) => gateLabels[value] ?? value },
                { title: '结果', dataIndex: 'passed', render: (value: boolean) => value ? <Tag color="green">通过</Tag> : <Tag color="red">未通过</Tag> },
              ]} />
              <Table rowKey="factor" size="small" pagination={false} dataSource={correlationRows} columns={[
                { title: '相关开盘特征', dataIndex: 'factor' },
                { title: 'Pearson 相关系数', dataIndex: 'correlation', render: (value: string) => formatNumber(value) },
                { title: '绝对值门槛', render: () => `< ${result.correlation_validation.maximum_absolute_correlation}` },
              ]} />
            </Space>,
          },
          {
            key: 'stress',
            label: '成本压力',
            children: <Table rowKey="cost_bps" size="small" pagination={false} dataSource={stressRows} columns={[
              { title: '单边成本 bps', dataIndex: 'cost_bps' },
              { title: '策略收益', render: (_: unknown, row: typeof stressRows[number]) => formatPercent(row.strategy_metrics.total_return) },
              { title: '年化收益', render: (_: unknown, row: typeof stressRows[number]) => formatPercent(row.strategy_metrics.annualized_return) },
              { title: 'Sharpe', render: (_: unknown, row: typeof stressRows[number]) => formatNumber(row.strategy_metrics.sharpe) },
              { title: '最大回撤', render: (_: unknown, row: typeof stressRows[number]) => formatPercent(row.strategy_metrics.maximum_drawdown) },
              { title: '超额收益', render: (_: unknown, row: typeof stressRows[number]) => formatPercent(row.strategy_metrics.excess_return) },
            ]} />,
          },
          {
            key: 'sectors',
            label: '15 行业',
            children: <GroupTable rows={result.backtest.sectors} nameLabel="行业" />,
          },
          {
            key: 'symbols',
            label: '75 个股',
            children: <GroupTable rows={result.backtest.symbols} nameLabel="证券" />,
          },
        ]} />
        <Alert showIcon type="warning" title={`五档买卖盘强度：${result.l2_factor_readiness.readiness}`} description={`公式 ${result.l2_factor_readiness.formula} 需要 ${result.l2_factor_readiness.required_data}。当前没有获授权的 L2 来源，系统不会从 OHLCV 伪造买一至买五/卖一至卖五。`} />
        <JsonExportButton filename={`qtrade-intraday-factor-${result.preview_hash.slice(0, 12)}.json`} value={result} label="导出因子研究证据" />
      </>}
    </Space>
  </Card>
}

function GroupTable({ rows, nameLabel }: { rows: IntradayFactorBacktest['backtest']['sectors']; nameLabel: string }) {
  return <Table
    rowKey="group_id"
    size="small"
    dataSource={rows}
    pagination={{ pageSize: 8, showSizeChanger: false }}
    columns={[
      { title: nameLabel, dataIndex: 'group_name' },
      { title: '信号数', dataIndex: 'signal_count' },
      { title: '信号后均值', dataIndex: 'mean_signal_forward_return', render: (value: string) => formatPercent(value) },
      { title: '信号胜率', dataIndex: 'signal_hit_rate', render: (value: string) => formatPercent(value) },
      { title: '策略收益', render: (_: unknown, row: typeof rows[number]) => formatPercent(row.strategy_metrics.total_return) },
      { title: '超额收益', render: (_: unknown, row: typeof rows[number]) => formatPercent(row.strategy_metrics.excess_return) },
      { title: '最大回撤', render: (_: unknown, row: typeof rows[number]) => formatPercent(row.strategy_metrics.maximum_drawdown) },
    ]}
    scroll={{ x: 900 }}
  />
}

function buildChartCurve(result: IntradayFactorBacktest) {
  let strategyPeak = 1
  let benchmarkPeak = 1
  return result.backtest.curve.map((point) => {
    const strategy = Number(point.strategy_equity)
    const benchmark = Number(point.benchmark_equity)
    strategyPeak = Math.max(strategyPeak, strategy)
    benchmarkPeak = Math.max(benchmarkPeak, benchmark)
    return {
      session: point.trading_day,
      equity: point.strategy_equity,
      drawdown: String(strategy / strategyPeak - 1),
      benchmark_equity: point.benchmark_equity,
      benchmark_drawdown: String(benchmark / benchmarkPeak - 1),
      excess_return: point.excess_equity,
    }
  })
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>
}

function formatNumber(value: string) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toFixed(3) : '—'
}

function formatPercent(value: string) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `${(parsed * 100).toFixed(2)}%` : '—'
}
