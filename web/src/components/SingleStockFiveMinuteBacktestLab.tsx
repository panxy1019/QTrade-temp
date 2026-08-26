import { Alert, Button, Card, Form, InputNumber, Select, Space, Table, Typography } from 'antd'
import { useState } from 'react'
import { runSingleStockFiveMinuteBacktest } from '../api/client'
import { EquityChart } from '../charts/EquityChart'
import type { FiveMinuteResearchReport, SingleStockFiveMinuteBacktest, SingleStockFiveMinuteBacktestRequest } from '../types'
import { JsonExportButton } from './JsonExportButton'

type VerifiedFiveMinuteDataset = NonNullable<FiveMinuteResearchReport['minute_dataset']>

interface FormValues {
  symbol: string
  start: string
  end: string
  cost_bps: string | number
  short_window: number
  long_window: number
  volatility_window: number
  maximum_minute_volatility: string | number
  maximum_intraday_drawdown: string | number
  reduction_multiplier: string | number
  cooldown_bars: number
}

export function SingleStockFiveMinuteBacktestLab({ dataset }: { dataset: VerifiedFiveMinuteDataset | null }) {
  const [result, setResult] = useState<SingleStockFiveMinuteBacktest | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  if (!dataset) {
    return <Card title="个股 5 分钟因果回测"><Alert showIcon type="warning" title="长历史 5 分钟数据不可用" description="系统不会用模拟行情替代真实且校验通过的数据。" /></Card>
  }
  const stocks = dataset.instruments.filter((row) => row.instrument_type === 'STOCK')
  const tradingDays = dataset.trading_days
  const initialValues: FormValues = {
    symbol: stocks[0]?.symbol ?? dataset.eligible_symbols[0],
    start: tradingDays[Math.max(0, tradingDays.length - 20)],
    end: tradingDays.at(-1) ?? tradingDays[0],
    cost_bps: '5',
    short_window: 12,
    long_window: 48,
    volatility_window: 24,
    maximum_minute_volatility: '0.02',
    maximum_intraday_drawdown: '0.04',
    reduction_multiplier: '0.98',
    cooldown_bars: 6,
  }
  const submit = async (values: FormValues) => {
    const payload: SingleStockFiveMinuteBacktestRequest = {
      ...values,
      cost_bps: String(values.cost_bps),
      maximum_minute_volatility: String(values.maximum_minute_volatility),
      maximum_intraday_drawdown: String(values.maximum_intraday_drawdown),
      reduction_multiplier: String(values.reduction_multiplier),
    }
    setRunning(true)
    setError(null)
    try {
      setResult(await runSingleStockFiveMinuteBacktest(payload))
    } catch (reason) {
      setResult(null)
      setError(reason instanceof Error ? reason.message : '个股 5 分钟回测失败')
    } finally {
      setRunning(false)
    }
  }
  const reasons = result
    ? Object.entries(result.execution.reason_counts)
      .sort((left, right) => right[1] - left[1])
      .map(([reason, count]) => `${reason} ${count}`)
      .join('；')
    : ''

  return <Card title="个股 5 分钟因果回测" extra={<Typography.Text type="secondary">{dataset.symbol_count} 股 · {dataset.sector_count} 板块 · {dataset.session_count} 日</Typography.Text>}>
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <Alert showIcon type="info" title="可任选覆盖个股并反复调参" description="基准是该股买入持有；修正策略只允许在本根 5 分钟 Bar 收盘生成信号，并于下一根 Bar 开盘降低或恢复敞口。单次最多 120 个交易日，所选区间之前的数据只作因果预热。" />
      <Form<FormValues> name="single-stock-five-minute-backtest" layout="vertical" initialValues={initialValues} onFinish={(values) => { void submit(values) }}>
        <div className="backtest-lab-grid">
          <Form.Item name="symbol" label="个股" rules={[{ required: true }]}><Select showSearch optionFilterProp="label" options={stocks.map((row) => ({ value: row.symbol, label: `${row.symbol} ${row.name} · ${row.sector_name}` }))} /></Form.Item>
          <Form.Item name="start" label="开始交易日" rules={[{ required: true }]}><Select showSearch options={tradingDays.map((value) => ({ value, label: value }))} /></Form.Item>
          <Form.Item name="end" label="结束交易日" rules={[{ required: true }]}><Select showSearch options={tradingDays.map((value) => ({ value, label: value }))} /></Form.Item>
          <Form.Item name="cost_bps" label="单边成本（bps）" rules={[{ required: true }]}><InputNumber min={0} max={100} step={1} stringMode /></Form.Item>
          <Form.Item name="short_window" label="短趋势窗口（Bar）" rules={[{ required: true }]}><InputNumber min={2} max={96} precision={0} /></Form.Item>
          <Form.Item name="long_window" label="长趋势窗口（Bar）" rules={[{ required: true }]}><InputNumber min={3} max={240} precision={0} /></Form.Item>
          <Form.Item name="volatility_window" label="波动窗口（Bar）" rules={[{ required: true }]}><InputNumber min={2} max={240} precision={0} /></Form.Item>
          <Form.Item name="maximum_minute_volatility" label="5 分钟波动上限" rules={[{ required: true }]}><InputNumber min={0.0001} max={1} step={0.001} stringMode /></Form.Item>
          <Form.Item name="maximum_intraday_drawdown" label="日内回撤上限" rules={[{ required: true }]}><InputNumber min={0.001} max={0.999} step={0.005} stringMode /></Form.Item>
          <Form.Item name="reduction_multiplier" label="触发后敞口倍数" rules={[{ required: true }]}><InputNumber min={0} max={0.999} step={0.01} stringMode /></Form.Item>
          <Form.Item name="cooldown_bars" label="恢复冷却（Bar）" rules={[{ required: true }]}><InputNumber min={0} max={240} precision={0} /></Form.Item>
        </div>
        <Button type="primary" htmlType="submit" loading={running}>运行个股 5 分钟回测</Button>
      </Form>
      {error && <Alert showIcon type="error" title="个股回测未完成" description={error} />}
      {result && <>
        <Alert showIcon type="success" title={`已完成 ${result.symbol} ${result.instrument.name} · ${result.selected_session_count} 日 / ${result.selected_bar_count} Bar`} description={`使用 ${result.warmup_session_count} 日因果预热；结果哈希 ${result.preview_hash.slice(0, 16)}…；固定结论 ${result.promotion}。`} />
        <div className="description-grid">
          <Metric label="买入持有收益" value={formatPercent(result.metrics.base.total_return)} />
          <Metric label="分钟修正收益" value={formatPercent(result.metrics.overlay.total_return)} />
          <Metric label="相对基准" value={formatPercent(result.metrics.excess_return)} />
          <Metric label="买入持有年化收益" value={formatPercent(result.metrics.base.annualized_return)} />
          <Metric label="修正后年化收益" value={formatPercent(result.metrics.overlay.annualized_return)} />
          <Metric label="修正后年化波动" value={formatPercent(result.metrics.overlay.annualized_volatility)} />
          <Metric label="修正后 Sharpe" value={formatNumber(result.metrics.overlay.sharpe)} />
          <Metric label="信息比率" value={formatNumber(result.metrics.information_ratio)} />
          <Metric label="修正后最大回撤" value={formatPercent(result.metrics.overlay.maximum_drawdown)} />
          <Metric label="平均敞口" value={formatPercent(result.execution.mean_exposure)} />
          <Metric label="减仓 Bar 比例" value={formatPercent(result.execution.reduced_exposure_ratio)} />
          <Metric label="换手" value={formatNumber(result.execution.turnover)} />
          <Metric label="成本扣减" value={formatPercent(result.execution.transaction_cost)} />
          <Metric label="策略哈希" value={`${result.policy_hash.slice(0, 12)}…`} />
        </div>
        <EquityChart dark analytics={result} />
        <Typography.Text type="secondary">曲线：实线为分钟修正策略，虚线为该个股 Buy-and-Hold；按每年 {result.metrics.annualization.sessions_per_year} 个交易日、每日 {result.metrics.annualization.bars_per_session} 根 5 分钟 Bar 年化，Sharpe 使用零无风险利率；{reasons || '本区间没有风险触发'}。前复权价格可能包含未来复权知识，当前成分股样本非 PIT，本结果不可直接晋级 PAPER/LIVE。</Typography.Text>
        <Table<SingleStockFiveMinuteBacktest['daily'][number]>
          rowKey="trading_day"
          size="small"
          dataSource={result.daily}
          pagination={{ pageSize: 8, showSizeChanger: false }}
          columns={[
            { title: '交易日', dataIndex: 'trading_day' },
            { title: '买入持有', render: (_: unknown, row) => formatPercent(row.base.total_return) },
            { title: '分钟修正', render: (_: unknown, row) => formatPercent(row.overlay.total_return) },
            { title: '相对基准', render: (_: unknown, row) => formatPercent(row.excess_return) },
            { title: '平均敞口', render: (_: unknown, row) => formatPercent(row.execution.mean_exposure) },
            { title: '换手', render: (_: unknown, row) => formatNumber(row.execution.turnover) },
          ]}
        />
        <JsonExportButton filename={`qtrade-${result.symbol}-five-minute-${result.preview_hash.slice(0, 12)}.json`} value={result} label="导出个股回测" />
      </>}
    </Space>
  </Card>
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
