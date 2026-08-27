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

interface SectorBatchStatus {
  completed: number
  failed: number
  requested: number
}

function toRequest(values: FormValues, symbol = values.symbol): SingleStockFiveMinuteBacktestRequest {
  return {
    ...values,
    symbol,
    cost_bps: String(values.cost_bps),
    maximum_minute_volatility: String(values.maximum_minute_volatility),
    maximum_intraday_drawdown: String(values.maximum_intraday_drawdown),
    reduction_multiplier: String(values.reduction_multiplier),
  }
}

export function SingleStockFiveMinuteBacktestLab({ dataset }: { dataset: VerifiedFiveMinuteDataset | null }) {
  const [form] = Form.useForm<FormValues>()
  const [result, setResult] = useState<SingleStockFiveMinuteBacktest | null>(null)
  const [sessionRuns, setSessionRuns] = useState<SingleStockFiveMinuteBacktest[]>([])
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const [batchRunning, setBatchRunning] = useState(false)
  const [batchProgress, setBatchProgress] = useState(0)
  const [batchStatus, setBatchStatus] = useState<SectorBatchStatus | null>(null)
  if (!dataset) {
    return <Card title="个股 5 分钟因果回测"><Alert showIcon type="warning" title="长历史 5 分钟数据不可用" description="系统不会用模拟行情替代真实且校验通过的数据。" /></Card>
  }
  const stocks = dataset.instruments.filter((row) => row.instrument_type === 'STOCK')
  const representativesBySector = new Map<string, typeof stocks[number]>()
  for (const stock of [...stocks].sort((left, right) => {
    const sectorOrder = (left.sector_code ?? '').localeCompare(right.sector_code ?? '')
    return sectorOrder || left.symbol.localeCompare(right.symbol)
  })) {
    if (stock.sector_code && !representativesBySector.has(stock.sector_code)) {
      representativesBySector.set(stock.sector_code, stock)
    }
  }
  const sectorRepresentatives = [...representativesBySector.values()]
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
    setRunning(true)
    setError(null)
    setBatchStatus(null)
    setBatchProgress(0)
    try {
      const next = await runSingleStockFiveMinuteBacktest(toRequest(values))
      setResult(next)
      setSessionRuns((current) => [
        next,
        ...current.filter((row) => row.preview_hash !== next.preview_hash),
      ].slice(0, 15))
    } catch (reason) {
      setResult(null)
      setError(reason instanceof Error ? reason.message : '个股 5 分钟回测失败')
    } finally {
      setRunning(false)
    }
  }
  const runSectorBatch = async () => {
    let values: FormValues
    try {
      values = await form.validateFields()
    } catch {
      return
    }
    setBatchRunning(true)
    setBatchProgress(0)
    setBatchStatus(null)
    setResult(null)
    setError(null)
    const completed: SingleStockFiveMinuteBacktest[] = []
    let failed = 0
    for (const [index, representative] of sectorRepresentatives.entries()) {
      try {
        completed.push(await runSingleStockFiveMinuteBacktest(toRequest(values, representative.symbol)))
      } catch {
        failed += 1
      }
      setBatchProgress(index + 1)
    }
    setSessionRuns(completed.slice(0, 15))
    setBatchStatus({ completed: completed.length, failed, requested: sectorRepresentatives.length })
    setBatchRunning(false)
  }
  const reasons = result
    ? Object.entries(result.execution.reason_counts)
      .sort((left, right) => right[1] - left[1])
      .map(([reason, count]) => `${reason} ${count}`)
      .join('；')
    : ''
  const sessionSectorCount = new Set(sessionRuns.map((row) => row.instrument.sector_code)).size

  return <Card title="个股 5 分钟因果回测" extra={<Typography.Text type="secondary">{dataset.symbol_count} 股 · {dataset.sector_count} 板块 · {dataset.session_count} 日</Typography.Text>}>
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <Alert showIcon type="info" title="可任选覆盖个股并反复调参" description="基准是该股买入持有；修正策略只允许在本根 5 分钟 Bar 收盘生成信号，并于下一根 Bar 开盘降低或恢复敞口。单次最多 120 个交易日，所选区间之前的数据只作因果预热。" />
      <Form<FormValues> form={form} name="single-stock-five-minute-backtest" layout="vertical" initialValues={initialValues} onValuesChange={() => { setResult(null); setError(null); setBatchStatus(null); setBatchProgress(0) }} onFinish={(values) => { void submit(values) }}>
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
        <Form.Item noStyle shouldUpdate>
          {({ getFieldsValue }) => {
            const values = { ...initialValues, ...getFieldsValue(true) } as FormValues
            const requiredHistory = Math.max(values.long_window, values.volatility_window + 1)
            return <Alert
              style={{ marginBottom: 16 }}
              showIcon
              type="info"
              title="当前分钟修正规则草案（未运行）"
              description={<Space orientation="vertical" size={2}>
                <Typography.Text code>{`BASE_TARGET: ${values.symbol} constant 100% exposure / Buy-and-Hold benchmark`}</Typography.Text>
                <Typography.Text code>{`TREND: SMA_CLOSE_${values.short_window} < SMA_CLOSE_${values.long_window} → EXPOSURE ≤ ${formatDraftNumber(values.reduction_multiplier)}`}</Typography.Text>
                <Typography.Text code>{`VOL: STDEV(RET_CLOSE, ${values.volatility_window}) ≥ ${formatDraftPercent(values.maximum_minute_volatility)} → EXPOSURE ≤ ${formatDraftNumber(values.reduction_multiplier)}`}</Typography.Text>
                <Typography.Text code>{`DRAWDOWN: CLOSE / SESSION_PEAK - 1 ≤ -${formatDraftPercent(values.maximum_intraday_drawdown)} → EXPOSURE = 0`}</Typography.Text>
                <Typography.Text code>{`RECOVERY: ${values.cooldown_bars} completed safe Bars, then still safe → EXPOSURE = 1`}</Typography.Text>
                <Typography.Text code>{`CLOCK: KNOWN_AT_5M_CLOSE_T → APPLY_NEXT_5M_OPEN; WARMUP ≥ ${requiredHistory} Bars; COST ${values.cost_bps} bps / exposure change`}</Typography.Text>
                <Typography.Text type="secondary">这是从当前表单推导的只读修正草案：基础目标固定为该股满敞口，分钟层只能减仓、清仓或恢复，不能加到 100% 以上；它不是独立选股/买入因子，也不是服务端策略规范、回测证据、注册或 PAPER/LIVE 准入。</Typography.Text>
              </Space>}
            />
          }}
        </Form.Item>
        <Space wrap>
          <Button type="primary" htmlType="submit" loading={running} disabled={batchRunning}>运行个股 5 分钟回测</Button>
          <Button onClick={() => { void runSectorBatch() }} loading={batchRunning} disabled={running || sectorRepresentatives.length === 0}>运行 {sectorRepresentatives.length} 板块同参诊断</Button>
        </Space>
      </Form>
      {batchRunning && <Typography.Text type="secondary">同参批量进度：{batchProgress}/{sectorRepresentatives.length}；请求按确定性代表股顺序逐一执行。</Typography.Text>}
      {batchStatus && <Alert
        showIcon
        type={batchStatus.failed === 0 ? 'success' : 'warning'}
        title={batchStatus.failed === 0 ? `同参批量完成：${batchStatus.completed}/${batchStatus.requested} 个板块` : `同参批量部分完成：${batchStatus.completed}/${batchStatus.requested} 个板块`}
        description={batchStatus.failed === 0
          ? '每个板块按板块代码、股票代码确定性选一股，日期、成本与分钟修正参数完全一致；结果只进入当前浏览器会话对比，不等于全板块或全股票稳健性证据。'
          : `${batchStatus.failed} 个板块请求失败，失败结果未补造；当前覆盖必须按成功行计数，不能声称完成全板块检验。`}
      />}
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
      {sessionRuns.length > 0 && <Card type="inner" title={`本次会话个股回测对比（${sessionRuns.length}/15，覆盖 ${sessionSectorCount} 个板块）`} extra={<JsonExportButton filename="qtrade-single-stock-five-minute-session-comparison.json" value={sessionRuns} label="导出本次会话对比" size="small" />}>
        <Space orientation="vertical" style={{ width: '100%' }}>
          <Typography.Text type="secondary">仅保留当前页面会话最近 15 个不同结果哈希，便于各选一股覆盖签名数据的 15 个板块；覆盖数只是当前会话去重统计。刷新后清空，不写服务端、不构成正式实验或晋级证据。</Typography.Text>
          <Table<SingleStockFiveMinuteBacktest>
            rowKey="preview_hash"
            size="small"
            dataSource={sessionRuns}
            pagination={false}
            columns={[
              { title: '个股', render: (_: unknown, row) => `${row.symbol} ${row.instrument.name}` },
              { title: '板块', render: (_: unknown, row) => row.instrument.sector_name },
              { title: '结果哈希', render: (_: unknown, row) => <Typography.Text code>{row.preview_hash.slice(0, 12)}…</Typography.Text> },
              { title: '区间', render: (_: unknown, row) => `${row.parameters.start} → ${row.parameters.end}` },
              { title: '短/长窗', render: (_: unknown, row) => `${row.parameters.short_window}/${row.parameters.long_window}` },
              { title: '成本 bps', render: (_: unknown, row) => row.parameters.cost_bps },
              { title: '累计收益', render: (_: unknown, row) => formatPercent(row.metrics.overlay.total_return) },
              { title: '年化收益', render: (_: unknown, row) => formatPercent(row.metrics.overlay.annualized_return) },
              { title: 'Sharpe', render: (_: unknown, row) => formatNumber(row.metrics.overlay.sharpe) },
              { title: '最大回撤', render: (_: unknown, row) => formatPercent(row.metrics.overlay.maximum_drawdown) },
              { title: '主动超额', render: (_: unknown, row) => formatPercent(row.metrics.excess_return) },
              { title: '信息比率', render: (_: unknown, row) => formatNumber(row.metrics.information_ratio) },
              { title: '平均敞口', render: (_: unknown, row) => formatPercent(row.execution.mean_exposure) },
              { title: '换手', render: (_: unknown, row) => formatNumber(row.execution.turnover) },
              { title: '操作', render: (_: unknown, row) => <Space size={4}>
                <Button type="link" size="small" onClick={() => {
                  form.setFieldsValue(row.parameters)
                  setResult(null)
                  setError(null)
                  setBatchStatus(null)
                  setBatchProgress(0)
                }}>载入该次参数</Button>
                <JsonExportButton filename={`qtrade-${row.symbol}-five-minute-${row.preview_hash.slice(0, 12)}.json`} value={row} label="导出该次" size="small" type="link" />
              </Space> },
            ]}
            scroll={{ x: 1820 }}
          />
        </Space>
      </Card>}
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

function formatDraftNumber(value: string | number) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? String(parsed) : '—'
}

function formatDraftPercent(value: string | number) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `${(parsed * 100).toFixed(2)}%` : '—'
}
