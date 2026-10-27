import { Alert, Button, Card, Form, InputNumber, Select, Space, Tabs, Typography } from 'antd'
import { createContext, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { loadCurrentDatasets, loadCurrentSeries, runSingleStockFiveMinuteBacktest } from '../api/client'
import type { CurrentDataset, CurrentDatasets, HistoricalBarSeries, MinuteBarSeries, PlatformSnapshot, SingleStockFiveMinuteBacktest } from '../types'
import { MarketHistoryChart } from '../charts/MarketHistoryChart'
import { MinuteHistoryChart } from '../charts/MinuteHistoryChart'
import { EquityChart } from '../charts/EquityChart'
import { LegacyBacktestArchive } from './LegacyBacktestArchive'
import { JsonExportButton } from './JsonExportButton'
import { PublicResearchObservations } from './PublicResearchObservations'

const CurrentContext = createContext<{ data: CurrentDatasets | null; error: string | null }>({ data: null, error: null })

export function CurrentDataProvider({ children }: { children: ReactNode }) {
  const [data, setData] = useState<CurrentDatasets | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let active = true
    let inFlight = false
    const refresh = async () => {
      if (inFlight) return
      inFlight = true
      try {
        const value = await loadCurrentDatasets()
        if (!value.daily?.data_version || !value.minute?.data_version) throw new Error('当前数据目录格式无效')
        if (active) { setData(value); setError(null) }
      } catch (reason) {
        if (active) { setData(null); setError(reason instanceof Error ? reason.message : '当前数据不可用') }
      } finally { inFlight = false }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 60_000)
    return () => { active = false; window.clearInterval(timer) }
  }, [])
  return <CurrentContext.Provider value={{ data, error }}>{children}</CurrentContext.Provider>
}

export function CurrentDataStatus({ archive = false }: { archive?: boolean }) {
  const { data, error } = useContext(CurrentContext)
  if (!data) return <Alert showIcon type={error ? 'error' : 'info'} title={error ? `当前数据通道不可用：${error}` : '正在核对当前数据版本…'} description="当前工作区不会用 8 月档案替代缺失的新数据。" />
  const fresh = data.daily.freshness === 'FRESH' && data.minute.freshness === 'FRESH' && (!data.intraday || data.intraday.freshness === 'FRESH')
  return <Alert showIcon type={fresh ? 'success' : 'warning'} title={`当前数据：日线 ${data.daily.minimum_latest_day} · 五分钟 ${data.minute.minimum_latest_day} · ${fresh ? '符合交易日历' : '数据滞后'}`} description={`最近应有完整交易日 ${data.daily.expected_complete_day}；网页每 60 秒核对版本。行情与历史工作区的当前数据回测工具共用输入；日终历史与盘中观测分别显示。${archive ? '固定实验保留原日期；Qlib冻结数据和实验状态在策略研究、实验中心分别显示。' : ''}`} />
}

export function CurrentWorkspace({ mode }: { mode: 'market' | 'backtest'; platform: PlatformSnapshot | null }) {
  const { data, error } = useContext(CurrentContext)
  if (!data) return <Alert type={error ? 'error' : 'info'} showIcon title={error ?? '正在加载当前研究数据…'} />
  const identity = `${data.daily.checksum}:${data.minute.checksum}`
  return <Space orientation="vertical" style={{ width: '100%' }}>
    <Typography.Text type="secondary">旧范围参考：{data.daily.universe.symbols.length} ETF 日线 / {data.minute.universe.symbols.length} 股票五分钟，保留此范围自身的数据截止与版本。当前统一目标池行情请返回“统一池当前行情”；扩容库存与此旧网页范围分别列示。</Typography.Text>
    {data.expansion && <Alert type="info" showIcon title={`集群扩容：日频已校验 ${data.expansion.daily.completed} / ${data.expansion.daily.candidate_count ?? '—'} 候选，已选 ${data.expansion.daily.selected ?? '待排名'} / 1000；分钟 ${data.expansion.minute.completed ?? 0} / 400`} description={`日频状态 ${data.expansion.daily.state}；分钟状态 ${data.expansion.minute.state}；持久盘余量 ${data.expansion.free_gib} GiB。完整十年历史仅对上市时间足够的股票成立。`} />}
    {mode === 'market' ? <CurrentCharts key={identity} daily={data.daily} minute={data.minute} intraday={data.intraday} /> : <>
      <LegacyBacktestArchive dataset={data.daily} />
      <CurrentMinuteLab key={data.minute.checksum} dataset={data.minute} />
    </>}
    {mode === 'market' && <PublicResearchObservations />}
  </Space>
}

function CurrentCharts({ daily, minute, intraday }: { daily: CurrentDataset; minute: CurrentDataset; intraday?: CurrentDataset }) {
  const [kind, setKind] = useState<'daily' | 'minute' | 'intraday'>('daily')
  const [symbols, setSymbols] = useState({ daily: daily.universe.symbols[0], minute: minute.universe.symbols[0], intraday: minute.universe.symbols[0] })
  const [day, setDay] = useState(minute.trading_days.at(-1)!)
  const [series, setSeries] = useState<HistoricalBarSeries | MinuteBarSeries | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const dataset = kind === 'daily' ? daily : kind === 'intraday' ? intraday! : minute
  const symbol = symbols[kind]
  useEffect(() => {
    let active = true
    void loadCurrentSeries(kind, symbol, dataset.data_version, kind === 'minute' ? day : undefined).then((value) => {
      if (!active) return
      if (value.data_version !== dataset.data_version || value.dataset_checksum !== dataset.checksum || value.symbol !== symbol) throw new Error('图表数据版本不一致')
      setSeries(value); setError(null); setLoading(false)
    }).catch((reason: unknown) => { if (active) { setSeries(null); setError(String(reason)); setLoading(false) } })
    return () => { active = false }
  }, [kind, symbol, day, dataset.data_version, dataset.checksum])
  const change = () => { setLoading(true); setSeries(null); setError(null) }
  return <Card title={`当前${kind === 'daily' ? '日线' : '五分钟'}图表 · 截至 ${dataset.minimum_latest_day}`}>
    <Tabs activeKey={kind} onChange={(key) => { change(); setKind(key as 'daily' | 'minute' | 'intraday') }} items={[{ key: 'daily', label: '当前日线' }, { key: 'minute', label: '已完成交易日五分钟' }, ...(intraday ? [{ key: 'intraday', label: '盘中五分钟观测（15 分钟采集）' }] : [])]} />
    <Space wrap><Select aria-label="当前行情证券" showSearch optionFilterProp="label" value={symbol} style={{ width: 250 }} onChange={(value) => { change(); setSymbols({ ...symbols, [kind]: value }) }} options={dataset.instruments.map((r) => ({ value: r.symbol, label: `${r.symbol} ${r.name}` }))} />
      {kind === 'minute' && <Select aria-label="当前五分钟交易日" value={day} style={{ width: 160 }} onChange={(value) => { change(); setDay(value) }} options={[...minute.trading_days].reverse().map((value) => ({ value, label: value }))} />}</Space>
    <Typography.Paragraph type="secondary">{dataset.observed_start.slice(0, 10)} 至 {dataset.observed_end.slice(0, 10)} · {dataset.bar_count.toLocaleString()} 根 · {dataset.data_version}；最新源时钟 {dataset.observed_end}</Typography.Paragraph>
    {error && <Alert type="error" showIcon title={error} />}
    {kind === 'daily' ? <MarketHistoryChart loading={loading || series?.data_version !== dataset.data_version} series={series?.data_version === dataset.data_version ? series as HistoricalBarSeries : null} /> : <MinuteHistoryChart loading={loading || (!error && series?.data_version !== dataset.data_version)} series={series?.data_version === dataset.data_version ? series as MinuteBarSeries : null} />}
  </Card>
}

function CurrentMinuteLab({ dataset }: { dataset: CurrentDataset }) {
  const [result, setResult] = useState<SingleStockFiveMinuteBacktest | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const defaults = { symbol: dataset.universe.symbols[0], start: dataset.trading_days.at(-20) ?? dataset.trading_days[0], end: dataset.trading_days.at(-1)!, cost_bps: 5 }
  const submit = async (values: typeof defaults) => {
    setRunning(true); setResult(null); setError(null)
    try {
      const value = await runSingleStockFiveMinuteBacktest({ ...values, cost_bps: String(values.cost_bps), short_window: 12, long_window: 48, volatility_window: 24, maximum_minute_volatility: '0.02', maximum_intraday_drawdown: '0.04', reduction_multiplier: '0.98', cooldown_bars: 6 }, dataset.data_version)
      if (value.data_version !== dataset.data_version || value.dataset_checksum !== dataset.checksum) throw new Error('数据版本已变化，请刷新重试')
      setResult(value)
    } catch (reason) { setError(String(reason)) } finally { setRunning(false) }
  }
  return <Card title={`当前五分钟因果回测 · 可用至 ${dataset.minimum_latest_day}`}>
    <Alert type="warning" showIcon title="近期完整会话研究预览" description={`当前 ${dataset.trading_days.length} 个共同完整交易日；信号在收盘后观察，下一根开盘执行，仅减仓。此来源复权口径未独立认证，与历史 BaoStock 前复权库分开保存；结果不可晋级实盘。`} />
    <Form layout="inline" initialValues={defaults} onFinish={(values: typeof defaults) => void submit(values)} onValuesChange={() => { setResult(null); setError(null) }}>
      <Form.Item name="symbol" label="股票"><Select style={{ width: 210 }} showSearch optionFilterProp="label" options={dataset.instruments.map((r) => ({ value: r.symbol, label: `${r.symbol} ${r.name}` }))} /></Form.Item>
      {(['start', 'end'] as const).map((key) => <Form.Item key={key} name={key} label={key === 'start' ? '开始' : '结束'}><Select style={{ width: 145 }} options={dataset.trading_days.map((d) => ({ value: d, label: d }))} /></Form.Item>)}
      <Form.Item name="cost_bps" label="成本 bps"><InputNumber min={0} max={100} /></Form.Item><Button htmlType="submit" type="primary" loading={running}>运行当前五分钟回测</Button>
    </Form>
    {error && <Alert type="error" showIcon title={error} />}
    {result && <><Typography.Paragraph>实际计算区间 {result.selected_trading_days[0]} 至 {result.selected_trading_days.at(-1)}；策略收益 {(Number(result.metrics.overlay.total_return) * 100).toFixed(2)}%，基准 {(Number(result.metrics.base.total_return) * 100).toFixed(2)}%。</Typography.Paragraph><EquityChart dark analytics={result} /><JsonExportButton filename="current-minute-backtest.json" value={result} /></>}
  </Card>
}
