import { Alert, Button, Card, Empty, Input, Popconfirm, Select, Space, Table, Tabs, Tag, Typography } from 'antd'
import type { ColumnsType } from 'antd/es/table'
import { useEffect, useRef, useState } from 'react'
import type { CausalPreviewTemplateId, HistoricalBarSeries, IntradayDirectionalBreadth, MinuteBarSeries, OrderRow, PlatformSnapshot, PositionRow, StrategyTemplate } from '../types'
import { StatusTag } from '../components/StatusTag'
import { BacktestLab } from '../components/BacktestLab'
import { IntradayFactorLongWindowEvidence, IntradayFactorResearchLab } from '../components/IntradayFactorResearchLab'
import { SingleStockFiveMinuteBacktestLab } from '../components/SingleStockFiveMinuteBacktestLab'
import { EquityChart } from '../charts/EquityChart'
import { MarketHistoryChart } from '../charts/MarketHistoryChart'
import { MinuteHistoryChart } from '../charts/MinuteHistoryChart'
import { MinuteBacktestChart } from '../charts/MinuteBacktestChart'
import { ReservedTailChart } from '../charts/ReservedTailChart'
import { formatChinaMinuteTimestamp } from '../charts/formatChinaMinuteTimestamp'
import { loadFiveMinuteBarSeries, loadHistoricalBarSeries, loadLongFiveMinuteBarSeries, loadMinuteBarSeries } from '../api/client'
import { cancelPaperOrder, controlRuntime, prometheusMetricsUrl } from '../api/client'
import type { RuntimeControlAction } from '../api/client'
import { JsonExportButton } from '../components/JsonExportButton'
import { FactorEvidenceCatalog } from '../components/FactorEvidenceCatalog'
import { PublicResearchObservations } from '../components/PublicResearchObservations'
import { QlibBaseline } from '../components/QlibBaseline'
import { CurrentWorkspace } from '../components/CurrentWorkspace'
import { formatBasisPoints, formatDirectionalDelta } from '../research/intradayDirectional'

const waiting = <Alert showIcon type="info" title="等待实时数据授权" description="适配器与回放链路已准备；当前未配置任何供应商令牌或真实连接。" />
const minuteReasonLabels: Record<string, string> = {
  INTRADAY_DRAWDOWN_LIMIT: '日内回撤限制',
  MINUTE_TREND_WEAK: '分钟趋势弱',
  MINUTE_VOLATILITY_LIMIT: '分钟波动限制',
  RECOVERY_COOLDOWN: '恢复冷却',
}
const reservedTailGateLabels: Record<string, string> = {
  CANDIDATE_BEATS_COMPARATOR_AT_5_BPS: '5 bps 战胜原低暴露对照',
  CANDIDATE_DRAWDOWN_NOT_WORSE_THAN_BASELINE_AT_5_BPS: '5 bps 回撤不劣于全暴露基准',
  MEAN_EXPOSURE_AT_LEAST_85_PERCENT: '平均暴露至少 85%',
  TURNOVER_AT_MOST_HALF_COMPARATOR: '换手不超过对照的一半',
  POSITIVE_AT_ZERO_AND_FIVE_BPS: '0/5 bps 均为正收益',
  LEAVE_ONE_SECTOR_OUT_MAJORITY: '15 个整行业剔除中至少胜 12 个',
}
const forwardObservationGateLabels: Record<string, string> = {
  OVERLAY_DRAWDOWN_NOT_WORSE_AT_5_BPS: '5 bps 回撤不劣于基准',
  RETURN_SHORTFALL_WITHIN_50_BPS_AT_5_BPS: '5 bps 收益落后不超过 50 bps',
  MEAN_EXPOSURE_AT_LEAST_90_PERCENT: '平均敞口至少 90%',
  MEAN_TURNOVER_PER_SYMBOL_AT_MOST_FIVE: '前 20 日每标的累计换手不超过 5',
  LEAVE_ONE_SECTOR_OUT_STABILITY: '15 个整行业剔除中至少 12 个稳定',
}
type SeriesLoadError = { key: string; message: string }

export function MarketPage({ data }: { data: PlatformSnapshot | null }) {
  return <Page title="行情中心" subtitle="当前数据与历史证据"><Tabs defaultActiveKey="current" items={[
    { key: 'current', label: '当前行情', children: <CurrentWorkspace mode="market" platform={data} /> },
    { key: 'archive', label: '历史档案（原始日期）', children: <><Alert showIcon type="warning" title="以下为冻结历史档案，不代表当前行情" /><HistoricalMarketPage data={data} /></> },
  ]} /></Page>
}

export function HistoricalMarketPage({ data }: { data: PlatformSnapshot | null }) {
  const historical = data?.market?.historical
  const minuteDataset = data?.minuteResearch.minute_dataset
  const fiveMinuteDataset = data?.fiveMinuteResearch?.minute_dataset
  const longFiveMinuteDataset = data?.longFiveMinuteResearch?.minute_dataset
  const intraday = data?.intradayObservation
  const breadth = data?.intradayBreadth
  const directionalBreadth = breadth?.directional_breadth
  const continuity = data?.intradayContinuity
  const latestContinuity = continuity?.checkpoints.at(-1)
  const previousDirectionalBySequence = new Map<number, IntradayDirectionalBreadth>()
  let previousDirectional: IntradayDirectionalBreadth | undefined
  for (const checkpoint of continuity?.checkpoints ?? []) {
    const currentDirectional = checkpoint.breadth.directional_breadth
    if (!currentDirectional) continue
    if (previousDirectional) previousDirectionalBySequence.set(checkpoint.sequence, previousDirectional)
    previousDirectional = currentDirectional
  }
  const [activeMarketTab, setActiveMarketTab] = useState('daily')
  const [query, setQuery] = useState('')
  const [selectedSymbol, setSelectedSymbol] = useState<string | null>(null)
  const [selectedMinuteSymbol, setSelectedMinuteSymbol] = useState<string | null>(null)
  const [selectedFiveMinuteSymbol, setSelectedFiveMinuteSymbol] = useState<string | null>(null)
  const [selectedLongFiveMinuteSymbol, setSelectedLongFiveMinuteSymbol] = useState<string | null>(null)
  const [selectedLongFiveMinuteDay, setSelectedLongFiveMinuteDay] = useState<string | null>(null)
  const [selectedIntradaySymbol, setSelectedIntradaySymbol] = useState<string | null>(null)
  const [series, setSeries] = useState<HistoricalBarSeries | null>(null)
  const [minuteSeries, setMinuteSeries] = useState<MinuteBarSeries | null>(null)
  const [fiveMinuteSeries, setFiveMinuteSeries] = useState<MinuteBarSeries | null>(null)
  const [longFiveMinuteSeries, setLongFiveMinuteSeries] = useState<MinuteBarSeries | null>(null)
  const [dailySeriesError, setDailySeriesError] = useState<SeriesLoadError | null>(null)
  const [minuteSeriesError, setMinuteSeriesError] = useState<SeriesLoadError | null>(null)
  const [fiveMinuteSeriesError, setFiveMinuteSeriesError] = useState<SeriesLoadError | null>(null)
  const [longFiveMinuteSeriesError, setLongFiveMinuteSeriesError] = useState<SeriesLoadError | null>(null)
  const bars = (data?.historicalBars ?? []).filter((row) => row.symbol.includes(query.trim()))
  const activeSymbol = selectedSymbol ?? data?.historicalBars?.[0]?.symbol ?? null
  const minuteInstruments = (minuteDataset?.instruments ?? []).filter((row) => `${row.symbol} ${row.name} ${row.sector_name ?? ''}`.includes(query.trim()))
  const defaultMinuteSymbol = minuteDataset?.eligible_symbols.includes('510300')
    ? '510300'
    : minuteDataset?.eligible_symbols[0] ?? null
  const activeMinuteSymbol = selectedMinuteSymbol ?? defaultMinuteSymbol
  const fiveMinuteInstruments = (fiveMinuteDataset?.instruments ?? []).filter((row) => `${row.symbol} ${row.name} ${row.sector_name ?? ''}`.includes(query.trim()))
  const defaultFiveMinuteSymbol = fiveMinuteDataset?.eligible_symbols.includes('510300')
    ? '510300'
    : fiveMinuteDataset?.eligible_symbols[0] ?? null
  const activeFiveMinuteSymbol = selectedFiveMinuteSymbol ?? defaultFiveMinuteSymbol
  const longFiveMinuteInstruments = (longFiveMinuteDataset?.instruments ?? []).filter((row) => `${row.symbol} ${row.name} ${row.sector_name ?? ''}`.includes(query.trim()))
  const activeLongFiveMinuteSymbol = selectedLongFiveMinuteSymbol ?? longFiveMinuteDataset?.eligible_symbols[0] ?? null
  const activeLongFiveMinuteDay = selectedLongFiveMinuteDay ?? longFiveMinuteDataset?.trading_days.at(-1) ?? null
  const intradayObservations = (intraday?.observations ?? []).filter((row) => `${row.symbol} ${row.name} ${row.sector_name}`.includes(query.trim()))
  const breadthObservations = (breadth?.observations ?? []).filter((row) => `${row.symbol} ${row.name} ${row.sector_name}`.includes(query.trim()))
  const activeIntradaySymbol = selectedIntradaySymbol ?? intraday?.observations[0]?.symbol ?? null
  const activeIntradayObservation = intraday?.observations.find((row) => row.symbol === activeIntradaySymbol) ?? null
  const displayedIntradaySeries: MinuteBarSeries | null = activeIntradayObservation?.bars.length ? {
    symbol: activeIntradayObservation.symbol,
    data_version: `sina-intraday-observation-${intraday?.completed_at ?? 'unknown'}`,
    dataset_checksum: intraday?.evidence_sha256 ?? null,
    observation_type: 'HISTORICAL_MINUTE_BAR_SERIES',
    interval: '1m',
    selected_trading_day: activeIntradayObservation.bars[0]?.trading_day ?? null,
    total_count: activeIntradayObservation.bars.length,
    truncated: false,
    bars: activeIntradayObservation.bars.map((bar) => ({
      ...bar,
      data_version: `sina-intraday-observation-${intraday?.completed_at ?? 'unknown'}`,
      observation_type: 'HISTORICAL_MINUTE_BAR',
    })),
  } : null
  const datasetIdentity = historical ? `${historical.data_version}:${historical.checksum}` : null
  const minuteDatasetIdentity = minuteDataset ? `${minuteDataset.data_version}:${minuteDataset.checksum}` : null
  const fiveMinuteDatasetIdentity = fiveMinuteDataset ? `${fiveMinuteDataset.data_version}:${fiveMinuteDataset.checksum}` : null
  const longFiveMinuteDatasetIdentity = longFiveMinuteDataset ? `${longFiveMinuteDataset.data_version}:${longFiveMinuteDataset.checksum}` : null
  const displayedSeries = datasetIdentity
    && series?.symbol === activeSymbol
    && `${series.data_version}:${series.dataset_checksum}` === datasetIdentity ? series : null
  const displayedMinuteSeries = minuteDatasetIdentity
    && minuteSeries?.symbol === activeMinuteSymbol
    && `${minuteSeries.data_version}:${minuteSeries.dataset_checksum}` === minuteDatasetIdentity ? minuteSeries : null
  const displayedFiveMinuteSeries = fiveMinuteDatasetIdentity
    && fiveMinuteSeries?.symbol === activeFiveMinuteSymbol
    && `${fiveMinuteSeries.data_version}:${fiveMinuteSeries.dataset_checksum}` === fiveMinuteDatasetIdentity ? fiveMinuteSeries : null
  const displayedLongFiveMinuteSeries = longFiveMinuteDatasetIdentity
    && longFiveMinuteSeries?.symbol === activeLongFiveMinuteSymbol
    && longFiveMinuteSeries.selected_trading_day === activeLongFiveMinuteDay
    && `${longFiveMinuteSeries.data_version}:${longFiveMinuteSeries.dataset_checksum}` === longFiveMinuteDatasetIdentity ? longFiveMinuteSeries : null
  const dailySeriesRequestKey = activeSymbol && datasetIdentity ? `${datasetIdentity}:${activeSymbol}` : null
  const minuteSeriesRequestKey = activeMinuteSymbol && minuteDatasetIdentity ? `${minuteDatasetIdentity}:${activeMinuteSymbol}` : null
  const fiveMinuteSeriesRequestKey = activeFiveMinuteSymbol && fiveMinuteDatasetIdentity ? `${fiveMinuteDatasetIdentity}:${activeFiveMinuteSymbol}` : null
  const longFiveMinuteSeriesRequestKey = activeLongFiveMinuteSymbol && activeLongFiveMinuteDay && longFiveMinuteDatasetIdentity ? `${longFiveMinuteDatasetIdentity}:${activeLongFiveMinuteSymbol}:${activeLongFiveMinuteDay}` : null
  const dailySeriesErrorMessage = dailySeriesError?.key === dailySeriesRequestKey ? dailySeriesError.message : null
  const minuteSeriesErrorMessage = minuteSeriesError?.key === minuteSeriesRequestKey ? minuteSeriesError.message : null
  const fiveMinuteSeriesErrorMessage = fiveMinuteSeriesError?.key === fiveMinuteSeriesRequestKey ? fiveMinuteSeriesError.message : null
  const longFiveMinuteSeriesErrorMessage = longFiveMinuteSeriesError?.key === longFiveMinuteSeriesRequestKey ? longFiveMinuteSeriesError.message : null
  const dailySeriesLoading = Boolean(activeMarketTab === 'daily' && dailySeriesRequestKey && !displayedSeries && !dailySeriesErrorMessage)
  const minuteSeriesLoading = Boolean(activeMarketTab === 'minute' && minuteSeriesRequestKey && !displayedMinuteSeries && !minuteSeriesErrorMessage)
  const fiveMinuteSeriesLoading = Boolean(activeMarketTab === 'five-minute' && fiveMinuteSeriesRequestKey && !displayedFiveMinuteSeries && !fiveMinuteSeriesErrorMessage)
  const longFiveMinuteSeriesLoading = Boolean(activeMarketTab === 'long-five-minute' && longFiveMinuteSeriesRequestKey && !displayedLongFiveMinuteSeries && !longFiveMinuteSeriesErrorMessage)
  useEffect(() => {
    let active = true
    if (activeMarketTab !== 'daily' || !activeSymbol || !datasetIdentity || !dailySeriesRequestKey) {
      return () => { active = false }
    }
    const requestKey = dailySeriesRequestKey
    void loadHistoricalBarSeries(activeSymbol).then((result) => {
      if (active) {
        setSeries(result)
        setDailySeriesError(null)
      }
    }).catch(() => {
      if (active) {
        setSeries(null)
        setDailySeriesError({ key: requestKey, message: '历史 K 线加载失败；未使用缓存或合成数据替代。' })
      }
    })
    return () => { active = false }
  }, [activeMarketTab, activeSymbol, datasetIdentity, dailySeriesRequestKey])
  useEffect(() => {
    let active = true
    if (activeMarketTab !== 'minute' || !activeMinuteSymbol || !minuteDatasetIdentity || !minuteSeriesRequestKey) {
      return () => { active = false }
    }
    const requestKey = minuteSeriesRequestKey
    void loadMinuteBarSeries(activeMinuteSymbol).then((result) => {
      if (active) {
        setMinuteSeries(result)
        setMinuteSeriesError(null)
      }
    }).catch(() => {
      if (active) {
        setMinuteSeries(null)
        setMinuteSeriesError({ key: requestKey, message: '历史 1 分钟线加载失败；未使用缓存、插值或合成数据替代。' })
      }
    })
    return () => { active = false }
  }, [activeMarketTab, activeMinuteSymbol, minuteDatasetIdentity, minuteSeriesRequestKey])
  useEffect(() => {
    let active = true
    if (activeMarketTab !== 'five-minute' || !activeFiveMinuteSymbol || !fiveMinuteDatasetIdentity || !fiveMinuteSeriesRequestKey) {
      return () => { active = false }
    }
    const requestKey = fiveMinuteSeriesRequestKey
    void loadFiveMinuteBarSeries(activeFiveMinuteSymbol).then((result) => {
      if (active) {
        setFiveMinuteSeries(result)
        setFiveMinuteSeriesError(null)
      }
    }).catch(() => {
      if (active) {
        setFiveMinuteSeries(null)
        setFiveMinuteSeriesError({ key: requestKey, message: '历史 5 分钟线加载失败；未使用 1 分钟降采样、缓存或合成数据替代。' })
      }
    })
    return () => { active = false }
  }, [activeMarketTab, activeFiveMinuteSymbol, fiveMinuteDatasetIdentity, fiveMinuteSeriesRequestKey])
  useEffect(() => {
    let active = true
    if (activeMarketTab !== 'long-five-minute' || !activeLongFiveMinuteSymbol || !activeLongFiveMinuteDay || !longFiveMinuteDatasetIdentity || !longFiveMinuteSeriesRequestKey) {
      return () => { active = false }
    }
    const requestKey = longFiveMinuteSeriesRequestKey
    void loadLongFiveMinuteBarSeries(activeLongFiveMinuteSymbol, activeLongFiveMinuteDay).then((result) => {
      if (active) {
        setLongFiveMinuteSeries(result)
        setLongFiveMinuteSeriesError(null)
      }
    }).catch(() => {
      if (active) {
        setLongFiveMinuteSeries(null)
        setLongFiveMinuteSeriesError({ key: requestKey, message: '长历史 5 分钟线加载失败；未使用缓存、插值或合成数据替代。' })
      }
    })
    return () => { active = false }
  }, [activeMarketTab, activeLongFiveMinuteSymbol, activeLongFiveMinuteDay, longFiveMinuteDatasetIdentity, longFiveMinuteSeriesRequestKey])
  const columns = [
    { title: '代码', dataIndex: 'symbol', fixed: 'left' as const },
    { title: '交易所', dataIndex: 'exchange' },
    { title: '交易日', dataIndex: 'trading_day' },
    { title: '开盘', dataIndex: 'open' },
    { title: '最高', dataIndex: 'high' },
    { title: '最低', dataIndex: 'low' },
    { title: '收盘', dataIndex: 'close' },
    { title: '成交量', dataIndex: 'volume', render: (value: string) => formatInteger(value) },
    { title: '数据性质', render: () => <Tag color="blue">历史日线收盘</Tag> },
  ]
  const minuteColumns = [
    { title: '代码', dataIndex: 'symbol', fixed: 'left' as const },
    { title: '名称', dataIndex: 'name' },
    { title: '类型', dataIndex: 'instrument_type', render: (value: string) => <Tag color={value === 'ETF' ? 'blue' : 'purple'}>{value}</Tag> },
    { title: '板块', dataIndex: 'sector_name', render: (value: string | null) => value ?? 'ETF' },
    { title: '完整交易日', render: () => String(minuteDataset?.session_count ?? 0) },
    { title: '每日日内点', render: () => String(minuteDataset?.session_bar_count_per_symbol ?? 0) },
  ]
  const fiveMinuteColumns = [
    { title: '代码', dataIndex: 'symbol', fixed: 'left' as const },
    { title: '名称', dataIndex: 'name' },
    { title: '类型', dataIndex: 'instrument_type', render: (value: string) => <Tag color={value === 'ETF' ? 'blue' : 'purple'}>{value}</Tag> },
    { title: '板块', dataIndex: 'sector_name', render: (value: string | null) => value ?? 'ETF' },
    { title: '完整交易日', render: () => String(fiveMinuteDataset?.session_count ?? 0) },
    { title: '每日日内点', render: () => String(fiveMinuteDataset?.session_bar_count_per_symbol ?? 0) },
  ]
  const longFiveMinuteColumns = [
    { title: '代码', dataIndex: 'symbol', fixed: 'left' as const },
    { title: '名称', dataIndex: 'name' },
    { title: '板块', dataIndex: 'sector_name' },
    { title: '完整交易日', render: () => String(longFiveMinuteDataset?.session_count ?? 0) },
    { title: '每日日内点', render: () => String(longFiveMinuteDataset?.session_bar_count_per_symbol ?? 0) },
  ]
  const intradayColumns = [
    { title: '代码', dataIndex: 'symbol', fixed: 'left' as const },
    { title: '名称', dataIndex: 'name' },
    { title: '板块', dataIndex: 'sector_name' },
    { title: '同日已结束分钟', dataIndex: 'same_day_finalized_bar_count' },
    { title: '最新已结束 Bar', dataIndex: 'latest_finalized_timestamp', render: (value: string) => formatChinaMinuteTimestamp(value) },
  ]
  const breadthColumns = [
    { title: '代码', dataIndex: 'symbol', fixed: 'left' as const },
    { title: '名称', dataIndex: 'name' },
    { title: '板块', dataIndex: 'sector_name' },
    { title: '同日已结束分钟', dataIndex: 'same_day_finalized_bar_count' },
    { title: '最新同日 Bar', dataIndex: 'latest_same_day_finalized_timestamp', render: (value: string | null) => value ? formatChinaMinuteTimestamp(value) : '尚无' },
    { title: '最新 Bar 年龄（秒）', dataIndex: 'latest_same_day_finalized_age_seconds', render: (value: number | null) => value ?? '—' },
    { title: '开盘至今', dataIndex: 'session_return', render: renderSignedReturn },
    { title: '最新分钟（bp）', dataIndex: 'latest_minute_return', render: renderSignedBasisPoints },
  ]
  const sectorBreadthColumns = [
    { title: '板块', dataIndex: 'sector_name', fixed: 'left' as const },
    { title: '相对开盘上涨', render: (_: unknown, row: NonNullable<typeof breadth>['sector_summaries'][number]) => `${row.directional_breadth.above_open_count}/${row.directional_breadth.eligible_symbol_count}` },
    { title: '最新分钟上涨', render: (_: unknown, row: NonNullable<typeof breadth>['sector_summaries'][number]) => `${row.directional_breadth.latest_minute_up_count}/${row.directional_breadth.latest_minute_eligible_symbol_count}` },
    { title: '等权开盘至今', render: (_: unknown, row: NonNullable<typeof breadth>['sector_summaries'][number]) => renderSignedReturn(row.directional_breadth.equal_weight_session_return) },
    { title: '等权最新分钟', render: (_: unknown, row: NonNullable<typeof breadth>['sector_summaries'][number]) => renderSignedBasisPoints(row.directional_breadth.equal_weight_latest_minute_return) },
  ]
  const continuityColumns = [
    { title: '序号', dataIndex: 'sequence', width: 72 },
    { title: '检查点时间', dataIndex: 'captured_at', render: (value: string) => formatChinaMinuteTimestamp(value) },
    { title: '代表股共同分钟', render: (_: unknown, row: NonNullable<typeof latestContinuity>) => `${row.detail.synchronized_finalized_timestamp_count} · ${row.detail.latest_synchronized_finalized_timestamp ? formatChinaMinuteTimestamp(row.detail.latest_synchronized_finalized_timestamp) : '尚无'}` },
    { title: '75 股票共同分钟', render: (_: unknown, row: NonNullable<typeof latestContinuity>) => `${row.breadth.synchronized_finalized_timestamp_count} · ${row.breadth.latest_synchronized_finalized_timestamp ? formatChinaMinuteTimestamp(row.breadth.latest_synchronized_finalized_timestamp) : '尚无'}` },
    { title: '相对开盘', render: (_: unknown, row: NonNullable<typeof latestContinuity>) => row.breadth.directional_breadth ? `${row.breadth.directional_breadth.above_open_count}/${row.breadth.directional_breadth.eligible_symbol_count} · ${formatPercent(row.breadth.directional_breadth.equal_weight_session_return ?? undefined)}` : '未记录' },
    { title: '最新分钟', render: (_: unknown, row: NonNullable<typeof latestContinuity>) => row.breadth.directional_breadth ? `${row.breadth.directional_breadth.latest_minute_up_count}/${row.breadth.directional_breadth.latest_minute_eligible_symbol_count} · ${formatBasisPoints(row.breadth.directional_breadth.equal_weight_latest_minute_return ?? undefined)}` : '未记录' },
    { title: '相对上一方向点', render: (_: unknown, row: NonNullable<typeof latestContinuity>) => formatDirectionalDelta(row.breadth.directional_breadth, previousDirectionalBySequence.get(row.sequence)) },
    { title: '相对前次', dataIndex: 'monotonic_vs_previous', render: (value: boolean) => <Tag color={value ? 'green' : 'red'}>{value ? '单调推进' : '检测到回退'}</Tag> },
  ]
  return <Page title="行情中心" subtitle="校验通过的历史日线、1 分钟与原生 5 分钟分时线">
    <Space orientation="vertical" style={{ width: '100%' }}>
      <PublicResearchObservations />
      {waiting}
      <Alert showIcon type="warning" title="冻结历史研究数据（不随观测快照更新）" description="历史日线与历史分时来自 checksum 绑定的 Parquet；当日分钟页是签名点时观测。两者都没有 Bid/Ask、授权实时价格或实盘权限，不参与 REAL_DATA_PAPER。" />
      <Input.Search placeholder="搜索代码、名称或板块" value={query} onChange={(event) => setQuery(event.target.value)} allowClear />
      <Card><Tabs activeKey={activeMarketTab} onChange={setActiveMarketTab} items={[
        {
          key: 'daily', label: `历史日线 · ${historical?.bar_count ?? 0}`,
          children: <Space orientation="vertical" style={{ width: '100%' }}>
            {dailySeriesErrorMessage && <Alert showIcon type="error" title={dailySeriesErrorMessage} />}
            <Alert showIcon type={historical ? 'success' : 'warning'} title={historical ? `已验证：${historical.provider} ${historical.provider_version}` : '尚无校验通过的历史日线'} description={historical ? `${historical.observed_start} 至 ${historical.observed_end}；当前样本池非 PIT。` : '需要 checksum 与质量状态同时通过。'} />
            <Card title={`${activeSymbol ?? '—'} 历史日 K 与成交量`}><MarketHistoryChart series={displayedSeries} loading={dailySeriesLoading} /><Typography.Text type="secondary">{displayedSeries?.bars.length ? `${displayedSeries.bars.length}/${displayedSeries.total_count} 个完整交易日；版本 ${displayedSeries.data_version}。` : dailySeriesLoading ? '正在校验并加载所选 ETF 的历史日线。' : '点击下方 ETF 行切换标的。'}</Typography.Text></Card>
            <Table rowKey="symbol" size="small" columns={columns} dataSource={bars} pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 920 }} onRow={(row) => ({ onClick: () => setSelectedSymbol(row.symbol), style: { cursor: 'pointer' } })} rowClassName={(row) => row.symbol === activeSymbol ? 'selected-market-row' : ''} locale={{ emptyText: <Empty description="暂无校验通过的历史日线" /> }} />
          </Space>,
        },
        {
          key: 'intraday-observation', label: `当日分钟观测 · ${intraday?.same_day_symbol_count ?? 0}/${intraday?.symbol_count ?? 0}`,
          children: <Space orientation="vertical" style={{ width: '100%' }}>
            <Alert showIcon type="warning" title={`免费历史源当日观测：${intraday?.session_phase ?? 'UNAVAILABLE'}`} description={`完成时间 ${intraday?.completed_at ? formatChinaMinuteTimestamp(intraday.completed_at) : '—'}（中国市场时钟）；${intraday?.same_day_symbol_count ?? 0}/${intraday?.symbol_count ?? 0} 个行业代表股出现同日已结束分钟 Bar，共同时间点 ${intraday?.synchronized_finalized_timestamp_count ?? 0} 个，最新 ${intraday?.latest_synchronized_finalized_timestamp ? formatChinaMinuteTimestamp(intraday.latest_synchronized_finalized_timestamp) : '尚无'}。代表股请求窗口 ${intraday?.source_request_window_seconds ?? '—'} 秒，最新同日 Bar 最大年龄 ${intraday?.maximum_same_day_latest_age_seconds ?? '尚无'} 秒；额外等待 ${intraday?.finalization_guard_seconds ?? 0} 秒后才纳入。这不是授权实时行情，不进入 PAPER 或策略状态。`} />
            <Card title={`盘中连续性 · ${continuity?.checkpoint_count ?? 0} 个签名检查点`}>
              <Space orientation="vertical" style={{ width: '100%' }}>
                <Alert showIcon type={continuity?.all_checkpoints_monotonic ? 'success' : 'warning'} title={continuity?.all_checkpoints_monotonic ? '已验证单调推进' : '尚无连续性结论或检测到回退'} description={`最新检查点 ${latestContinuity?.captured_at ? formatChinaMinuteTimestamp(latestContinuity.captured_at) : '—'}；每个检查点同时绑定 15 只代表股详细证据和 75 股票宽度证据。${continuity?.directional_breadth_first_sequence ? `方向序列自检查点 ${continuity.directional_breadth_first_sequence} 开始签名保留；` : ''}时间或共同分钟回退会被保留并标红，不会自动修正或用于 PAPER。`} />
                <Table rowKey="checkpoint_sha256" size="small" columns={continuityColumns} dataSource={continuity?.checkpoints ?? []} pagination={false} scroll={{ x: 1150 }} locale={{ emptyText: <Empty description="暂无校验通过的盘中连续性检查点" /> }} />
              </Space>
            </Card>
            <Card title={`全股票宽度观测 · ${breadth?.same_day_symbol_count ?? 0}/${breadth?.symbol_count ?? 0}`}>
              <Space orientation="vertical" style={{ width: '100%' }}>
                <Alert showIcon type={breadth?.all_symbols_same_day_coverage ? 'success' : 'warning'} title={`${breadth?.sector_count ?? 0} 板块 × 每板块 ${breadth?.symbols_per_sector ?? 0} 只股票`} description={`全股票完成时间 ${breadth?.completed_at ? formatChinaMinuteTimestamp(breadth.completed_at) : '—'}；跨股票共同已结束分钟 ${breadth?.synchronized_finalized_timestamp_count ?? 0} 个，最新 ${breadth?.latest_synchronized_finalized_timestamp ? formatChinaMinuteTimestamp(breadth.latest_synchronized_finalized_timestamp) : '尚无'}。75 股票请求窗口 ${breadth?.source_request_window_seconds ?? '—'} 秒，最大最新 Bar 年龄 ${breadth?.maximum_same_day_latest_age_seconds ?? '尚无'} 秒。后端从签名的逐股票时间集合重新计算共同覆盖；该宽度审计不进入策略或 PAPER。`} />
                <Alert showIcon type="info" title="盘中横截面方向因子 · 仅观察" description={`相对开盘上涨 ${directionalBreadth?.above_open_count ?? 0}/${directionalBreadth?.eligible_symbol_count ?? 0}、下跌 ${directionalBreadth?.below_open_count ?? 0}；最新已结束分钟上涨 ${directionalBreadth?.latest_minute_up_count ?? 0}/${directionalBreadth?.latest_minute_eligible_symbol_count ?? 0}、下跌 ${directionalBreadth?.latest_minute_down_count ?? 0}。75 股等权开盘至今 ${formatPercent(directionalBreadth?.equal_weight_session_return ?? undefined)}，等权最新分钟 ${formatBasisPoints(directionalBreadth?.equal_weight_latest_minute_return ?? undefined)}。这些值由签名的逐股首开、前收和最新收盘字段复算，只作方向广度观测，不进入策略调参或 PAPER。`} />
                <Table rowKey="sector_code" size="small" columns={sectorBreadthColumns} dataSource={breadth?.sector_summaries ?? []} pagination={false} scroll={{ x: 820 }} locale={{ emptyText: <Empty description="暂无校验通过的板块方向广度" /> }} />
                <Table rowKey="symbol" size="small" columns={breadthColumns} dataSource={breadthObservations} pagination={{ pageSize: 15, showSizeChanger: false }} scroll={{ x: 1120 }} locale={{ emptyText: <Empty description="暂无校验通过的全股票分钟宽度观测" /> }} />
              </Space>
            </Card>
            <Card title={`${activeIntradaySymbol ?? '—'} 当日已结束 1 分钟观测`}><MinuteHistoryChart series={displayedIntradaySeries} /><Typography.Text type="secondary">{displayedIntradaySeries?.bars.length ? `${displayedIntradaySeries.bars.length} 根同日已结束 Bar；观测签名 ${intraday?.evidence_sha256 ?? '—'}。` : '当前阶段尚无同日已结束分钟 Bar。'} 免费历史接口只作点时观测，不宣称流式、低延迟或实时授权。</Typography.Text></Card>
            <Table rowKey="symbol" size="small" columns={intradayColumns} dataSource={intradayObservations} pagination={{ pageSize: 15, showSizeChanger: false }} scroll={{ x: 900 }} onRow={(row) => ({ onClick: () => setSelectedIntradaySymbol(row.symbol), style: { cursor: 'pointer' } })} rowClassName={(row) => row.symbol === activeIntradaySymbol ? 'selected-market-row' : ''} locale={{ emptyText: <Empty description="暂无校验通过的当日分钟观测" /> }} />
          </Space>,
        },
        {
          key: 'minute', label: `历史分时 · ${minuteDataset?.bar_count ?? 0}`,
          children: <Space orientation="vertical" style={{ width: '100%' }}>
            {minuteSeriesErrorMessage && <Alert showIcon type="error" title={minuteSeriesErrorMessage} />}
            <Alert showIcon type={minuteDataset ? 'success' : 'warning'} title={minuteDataset ? `已验证 1 分钟数据：${minuteDataset.symbol_count} 标的 × ${minuteDataset.session_count} 日` : '尚无校验通过的历史分时数据'} description={minuteDataset ? `${minuteDataset.observed_start} 至 ${minuteDataset.observed_end}；${minuteDataset.etf_count} ETF + ${minuteDataset.stock_count} 行业股，覆盖 ${minuteDataset.sector_count} 个行业。来源原生每个完整日 238 点，不合成 14:58/14:59。` : '签名清单、Parquet checksum 或完整交易日网格未通过。'} />
            <Card title={`${activeMinuteSymbol ?? '—'} 已验证历史分时线`}><MinuteHistoryChart series={displayedMinuteSeries} loading={minuteSeriesLoading} /><Typography.Text type="secondary">{displayedMinuteSeries?.bars.length ? `${displayedMinuteSeries.bars.length}/${displayedMinuteSeries.total_count} 根 1 分钟 Bar；版本 ${displayedMinuteSeries.data_version}。` : minuteSeriesLoading ? '正在校验并加载所选标的的历史 1 分钟线。' : '点击下方 ETF 或股票切换标的。'} 这是最近历史分时，不是盘中实时跟踪。</Typography.Text></Card>
            <Table rowKey="symbol" size="small" columns={minuteColumns} dataSource={minuteInstruments} pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 900 }} onRow={(row) => ({ onClick: () => setSelectedMinuteSymbol(row.symbol), style: { cursor: 'pointer' } })} rowClassName={(row) => row.symbol === activeMinuteSymbol ? 'selected-market-row' : ''} locale={{ emptyText: <Empty description="暂无校验通过的历史分时标的" /> }} />
          </Space>,
        },
        {
          key: 'five-minute', label: `原生 5 分钟 · ${fiveMinuteDataset?.bar_count ?? 0}`,
          children: <Space orientation="vertical" style={{ width: '100%' }}>
            {fiveMinuteSeriesErrorMessage && <Alert showIcon type="error" title={fiveMinuteSeriesErrorMessage} />}
            <Alert showIcon type={fiveMinuteDataset ? 'success' : 'warning'} title={fiveMinuteDataset ? `已验证原生 5 分钟数据：${fiveMinuteDataset.symbol_count} 标的 × ${fiveMinuteDataset.session_count} 日` : '尚无校验通过的原生 5 分钟数据'} description={fiveMinuteDataset ? `${fiveMinuteDataset.observed_start} 至 ${fiveMinuteDataset.observed_end}；${fiveMinuteDataset.etf_count} ETF + ${fiveMinuteDataset.stock_count} 行业股，覆盖 ${fiveMinuteDataset.sector_count} 个行业。每个完整交易日严格为供应商原生 48 点。` : '签名清单、Parquet checksum 或原生 5 分钟交易日网格未通过。'} />
            <Card title={`${activeFiveMinuteSymbol ?? '—'} 已验证原生 5 分钟分时线`}><MinuteHistoryChart series={displayedFiveMinuteSeries} loading={fiveMinuteSeriesLoading} /><Typography.Text type="secondary">{displayedFiveMinuteSeries?.bars.length ? `${displayedFiveMinuteSeries.bars.length}/${displayedFiveMinuteSeries.total_count} 根原生 5 分钟 Bar；版本 ${displayedFiveMinuteSeries.data_version}。` : fiveMinuteSeriesLoading ? '正在校验并加载所选标的的原生 5 分钟线。' : '点击下方 ETF 或股票切换标的。'} 这不是 1 分钟线降采样，也不是盘中实时跟踪。</Typography.Text></Card>
            <Table rowKey="symbol" size="small" columns={fiveMinuteColumns} dataSource={fiveMinuteInstruments} pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 900 }} onRow={(row) => ({ onClick: () => setSelectedFiveMinuteSymbol(row.symbol), style: { cursor: 'pointer' } })} rowClassName={(row) => row.symbol === activeFiveMinuteSymbol ? 'selected-market-row' : ''} locale={{ emptyText: <Empty description="暂无校验通过的原生 5 分钟标的" /> }} />
          </Space>,
        },
        {
          key: 'long-five-minute', label: `长历史 5 分钟 · ${longFiveMinuteDataset?.bar_count ?? 0}`,
          children: <Space orientation="vertical" style={{ width: '100%' }}>
            {longFiveMinuteSeriesErrorMessage && <Alert showIcon type="error" title={longFiveMinuteSeriesErrorMessage} />}
            <Alert showIcon type={longFiveMinuteDataset ? 'success' : 'warning'} title={longFiveMinuteDataset ? `已验证长历史原生 5 分钟数据：${longFiveMinuteDataset.symbol_count} 只股票 × ${longFiveMinuteDataset.session_count} 日` : '尚无校验通过的长历史 5 分钟数据'} description={longFiveMinuteDataset ? `${longFiveMinuteDataset.observed_start} 至 ${longFiveMinuteDataset.observed_end}；覆盖 ${longFiveMinuteDataset.sector_count} 个行业，每日严格 48 点。当前前复权数据可能含未来复权知识、股票池非 PIT，只供研究查看。` : '签名清单、Parquet checksum 或完整交易日网格未通过。'} />
            <Space wrap>
              <Typography.Text>交易日</Typography.Text>
              <Select aria-label="长历史交易日" showSearch value={activeLongFiveMinuteDay} onChange={setSelectedLongFiveMinuteDay} options={[...(longFiveMinuteDataset?.trading_days ?? [])].reverse().map((value) => ({ value, label: value }))} style={{ width: 180 }} />
              <Tag color="orange">QFQ · 非 PIT</Tag>
            </Space>
            <Card title={`${activeLongFiveMinuteSymbol ?? '—'} · ${activeLongFiveMinuteDay ?? '—'} 原生 5 分钟分时线`}><MinuteHistoryChart series={displayedLongFiveMinuteSeries} loading={longFiveMinuteSeriesLoading} /><Typography.Text type="secondary">{displayedLongFiveMinuteSeries?.bars.length ? `${displayedLongFiveMinuteSeries.bars.length}/${displayedLongFiveMinuteSeries.total_count} 根原生 5 分钟 Bar；版本 ${displayedLongFiveMinuteSeries.data_version}。` : longFiveMinuteSeriesLoading ? '正在校验并加载所选股票与交易日的原生 5 分钟线。' : '选择交易日并点击下方股票切换标的。'} 每次只返回所选交易日 48 点；保留尾部不会进入策略调参。</Typography.Text></Card>
            <Table rowKey="symbol" size="small" columns={longFiveMinuteColumns} dataSource={longFiveMinuteInstruments} pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 820 }} onRow={(row) => ({ onClick: () => setSelectedLongFiveMinuteSymbol(row.symbol), style: { cursor: 'pointer' } })} rowClassName={(row) => row.symbol === activeLongFiveMinuteSymbol ? 'selected-market-row' : ''} locale={{ emptyText: <Empty description="暂无校验通过的长历史 5 分钟股票" /> }} />
          </Space>,
        },
      ]} /></Card>
    </Space>
  </Page>
}

export function StrategyPage({ data }: { data: PlatformSnapshot | null }) {
  const [query, setQuery] = useState('')
  const [scenario, setScenario] = useState<string | undefined>()
  const rows = (data?.strategies ?? []).filter((row) => {
    const text = query.trim().toLowerCase()
    return (!text || `${row.name} ${row.family} ${row.strategy_id}`.toLowerCase().includes(text))
      && (!scenario || row.scenario_type === scenario)
  })
  const scenarios = [...new Set((data?.strategies ?? []).map((row) => row.scenario_type))].map((value) => ({ value, label: value }))
  const columns = [
    { title: '策略名称', dataIndex: 'name' }, { title: 'Record 哈希', dataIndex: 'version' },
    { title: 'Family', dataIndex: 'family' }, { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color="blue">{value}</Tag> },
    { title: '角色', dataIndex: 'role' },
    { title: '证据', dataIndex: 'evidence_status', render: () => <Tag color="orange">已验证·不可晋级</Tag> },
    { title: 'Sharpe', render: (_: unknown, row: { metrics: Record<string, string> }) => row.metrics.sharpe ?? '—' },
    { title: '最大回撤', render: (_: unknown, row: { metrics: Record<string, string> }) => row.metrics.maximum_drawdown ?? '—' },
  ]
  const templateCatalog = data?.strategyTemplates
  const templateColumns: ColumnsType<StrategyTemplate> = [
    {
      title: '模板',
      dataIndex: 'name',
      render: (value: string, row) => <Space orientation="vertical" size={0}><Typography.Text strong>{value}</Typography.Text><Typography.Text type="secondary">{row.description}</Typography.Text></Space>,
    },
    { title: '角色', dataIndex: 'role', render: (value: StrategyTemplate['role']) => <Tag color={value === 'BENCHMARK_ONLY' ? 'default' : 'blue'}>{value === 'BENCHMARK_ONLY' ? '比较基准' : '主动基线'}</Tag> },
    { title: '范围', render: (_, row) => `${row.universe_scope} · ${row.data_interval}` },
    { title: '开仓条件', dataIndex: 'entry_rule' },
    { title: '平仓条件', dataIndex: 'exit_rule' },
    { title: '验证状态', render: () => <Tag color="orange">已实现·未生产验证</Tag> },
    {
      title: '因果回测',
      render: (_, row) => row.causal_preview_supported
        ? <Button size="small" onClick={() => { window.location.hash = `#/backtest?template=${encodeURIComponent(row.template_id)}` }}>载入因果回测</Button>
        : <Tag>尚未接入该实验室</Tag>,
    },
  ]
  return <Page title="策略中心" subtitle="从已实现基线模板到不可变实验候选">
    <Space orientation="vertical" style={{ width: '100%' }} size="large">
      <Card title={`已实现基线策略模板 · ${templateCatalog?.template_count ?? 0}`}>
        <Space orientation="vertical" style={{ width: '100%' }}>
          <Alert showIcon type="info" title={`${templateCatalog?.active_template_count ?? 0} 个主动模板 + ${templateCatalog?.benchmark_template_count ?? 0} 个比较基准`} description="这些是代码中已有的策略结构，不代表成熟盈利或生产验证。选择模板不会创建实验、注册策略或启动模拟盘；没有任意代码执行入口。" />
          <Typography.Text strong>下一步：冻结 ExperimentRunSpec 后运行因果回测；通过有效性、相关性、稳健性和成本检验后，才可单独申请 PAPER/SHADOW。</Typography.Text>
          <Table rowKey="template_id" columns={templateColumns} dataSource={templateCatalog?.templates ?? []} pagination={false} scroll={{ x: 1300 }} expandable={{ expandedRowRender: (row) => <Descriptions values={[["实现", row.implementation_ref], ["默认参数（未冻结）", Object.entries(row.parameter_defaults).map(([key, value]) => `${key}=${value}`).join('，')], ["所需输入", row.required_inputs.join('，')], ["已知限制", row.known_limitations.join('；')]]} /> }} locale={{ emptyText: '基线模板目录暂不可用' }} />
        </Space>
      </Card>
      <Card title="已验证实验候选">
        <Space orientation="vertical" style={{ width: '100%' }}>
          <Alert showIcon type="warning" title="实验候选，不是已注册或已批准策略" description="每行均通过 comparison、record、result 与 artifact 哈希校验；spec_hash 仅校验格式，当前产物未携带足够字段供 API 独立复算。当前样本池非 PIT，状态 NOT_ELIGIBLE、角色 NONE，也没有已注册谱系，不是 Champion 或运行中 PAPER 策略。" />
          <Space wrap><Input.Search aria-label="搜索策略候选" placeholder="搜索名称、Family 或 ID" allowClear value={query} onChange={(event) => setQuery(event.target.value)} style={{ width: 320 }} /><Select aria-label="筛选策略场景" allowClear placeholder="全部场景" value={scenario} onChange={setScenario} options={scenarios} style={{ width: 180 }} /><JsonExportButton filename="qtrade-strategy-candidates.json" value={rows} /></Space>
          <Table rowKey="strategy_id" columns={columns} dataSource={rows} pagination={{ pageSize: 8, showSizeChanger: false }} scroll={{ x: 1100 }} expandable={{ expandedRowRender: (row) => <Descriptions values={[["策略 ID", row.strategy_id], ["数据版本", row.data_version], ["Git Commit", row.git_commit], ["Record 哈希", row.version], ["场景类型", row.scenario_type], ["谱系状态", row.lineage_status]]} /> }} locale={{ emptyText: '暂无 record/result/artifact 验证通过的实验候选' }} />
        </Space>
      </Card>
    </Space>
  </Page>
}

export function ResearchPage({ data }: { data: PlatformSnapshot | null }) {
  const report = data?.experiments
  const walkForward = data?.walkForward
  const robustness = data?.robustness
  const factorSearch = data?.factorSearch
  const robustFactorSearch = data?.robustFactorSearch
  const stockTransfer = data?.stockSectorTransfer
  const developmentSearch = data?.developmentPolicySearch
  const forwardObservation = data?.forwardObservation
  const forwardSourceCoverage = data?.forwardSourceCoverage
  const forwardSourceCoverageHistory = data?.forwardSourceCoverageHistory
  const longFiveMinuteDataset = data?.longFiveMinuteResearch?.minute_dataset ?? null
  const intradayFactorRobustness = data?.intradayFactorRobustness
  const forwardSourceIncompleteRows = forwardSourceCoverage?.coverage.filter((row) => !row.ready) ?? []
  const forwardSourceCoverageCheckpoints = forwardSourceCoverageHistory?.checkpoints ?? []
  const forwardGateRows = forwardObservation?.gates.length
    ? forwardObservation.gates
    : Object.keys(forwardObservationGateLabels).map((gate_id) => ({ gate_id, passed: null }))
  const aggregate = walkForward?.result.aggregate_metrics
  const factorMetrics = factorSearch?.test.selected_metrics
  const benchmarkMetrics = factorSearch?.test.buy_hold_metrics
  const robustCandidateId = robustFactorSearch?.selection?.robust_candidate_id
  const robustSummary = robustFactorSearch?.candidate_summaries.find(
    (item) => item.candidate_id === robustCandidateId,
  )
  const robustStress = robustFactorSearch?.stress_tests.evaluations.find(
    (item) => item.candidate_id === robustCandidateId,
  )
  const doubledCost = robustStress?.cost_stresses.find(
    (item) => item.scenario_id === 'double_cost',
  )
  const robustBootstrap = robustFactorSearch?.bootstrap.evaluations.find(
    (item) => item.candidate_id === robustCandidateId,
  )
  const pollutedAudit = robustFactorSearch?.polluted_audit
  const transferFull = stockTransfer?.formal_period?.full_metrics
  const transferDouble = stockTransfer?.formal_period?.doubled_cost_metrics
  const transferAudit = stockTransfer?.polluted_audit
  const weakestSector = [...(stockTransfer?.sector_only ?? [])].sort(
    (left, right) => Number(left.metrics.total_return) - Number(right.metrics.total_return),
  )[0]
  const factorDiagnostics = factorSearch?.factor_diagnostics ?? []
  const validationRows = (factorSearch?.validation.scores ?? []).map(([candidate_id, sharpe]) => ({ candidate_id, sharpe }))
  const robustRows = robustFactorSearch?.candidate_summaries ?? []
  const yearlyRows = stockTransfer?.yearly ?? []
  const sectorRows = stockTransfer?.sector_only ?? []
  const leaveOneRows = stockTransfer?.leave_one_sector_out ?? []
  return <Page title="策略研究" subtitle="实验候选、独立样本外证据与晋级边界">
    <QlibBaseline />
    <FactorEvidenceCatalog factorSearch={factorSearch} intradayReport={intradayFactorRobustness} />
    {intradayFactorRobustness && <IntradayFactorLongWindowEvidence report={intradayFactorRobustness} />}
    <IntradayFactorResearchLab key={longFiveMinuteDataset ? `intraday-factor:${longFiveMinuteDataset.data_version}:${longFiveMinuteDataset.checksum}` : 'no-intraday-factor-dataset'} dataset={longFiveMinuteDataset} />
    <Alert showIcon type="warning" title={`晋级结论：${report?.promotion ?? '未评估'}`} description={(report?.promotion_reasons ?? ['缺少可验证研究产物']).join(' / ')} />
    <JsonExportButton filename="qtrade-verified-research-evidence.json" value={{ intradayFactorRobustness, factorSearch, robustFactorSearch, stockTransfer, developmentSearch, forwardObservation, forwardSourceCoverage, forwardSourceCoverageHistory, walkForward, robustness }} label="导出全部研究证据" />
    <Card><Tabs items={[
      { key: 'registry', label: '实验候选', children: <Space orientation="vertical"><Typography.Text>record/result/artifact 验证通过：{report?.experiments.length ?? 0} 个不可变实验记录。</Typography.Text><Typography.Text type="secondary">spec_hash 未独立复算；这些记录尚未注册为 StrategyVersion，也不存在可展示的 Champion、Challenger 或演化血缘。</Typography.Text></Space> },
      { key: 'factor-search', label: '因子搜索', children: <Space orientation="vertical" style={{ width: '100%' }}><Typography.Text>发现期通过：{factorSearch?.factor_selection?.passing_factor_ids.join('、') || '无'}；验证候选：{factorSearch?.validation.candidate_count ?? 0} 个。</Typography.Text><Typography.Text>验证胜者：{factorSearch?.validation.selected_candidate_id ?? '—'}；确认段收益 {formatPercent(String(factorMetrics?.total_return ?? '0'))}，Sharpe {String(factorMetrics?.sharpe ?? '—')}，最大回撤 {formatPercent(String(factorMetrics?.maximum_drawdown ?? '0'))}。</Typography.Text><Typography.Text>同期 Buy-and-Hold：{formatPercent(String(benchmarkMetrics?.total_return ?? '0'))}；主动超额：{formatPercent(factorSearch?.test.terminal_excess_return ?? '0')}。</Typography.Text><Table rowKey="factor_id" size="small" pagination={false} dataSource={factorDiagnostics} columns={[{ title: '因子', dataIndex: 'factor_id' }, { title: '周期', dataIndex: 'periods' }, { title: '最少标的', dataIndex: 'instruments_minimum' }, { title: 'IC', render: (_: unknown, row: typeof factorDiagnostics[number]) => formatNumber(String(row.metrics.ic ?? '')) }, { title: 'Rank IC', render: (_: unknown, row: typeof factorDiagnostics[number]) => formatNumber(String(row.metrics.rank_ic ?? '')) }, { title: 'ICIR', render: (_: unknown, row: typeof factorDiagnostics[number]) => formatNumber(String(row.metrics.icir ?? '')) }, { title: '多空收益差', render: (_: unknown, row: typeof factorDiagnostics[number]) => formatPercent(String(row.metrics.long_short_spread ?? '')) }]} scroll={{ x: 900 }} locale={{ emptyText: '暂无已验证因子诊断' }} /><Table rowKey="candidate_id" size="small" dataSource={validationRows} pagination={{ pageSize: 8, showSizeChanger: false }} columns={[{ title: '验证候选', dataIndex: 'candidate_id' }, { title: '验证 Sharpe', dataIndex: 'sharpe', render: (value: string) => formatNumber(value) }, { title: '是否选中', render: (_: unknown, row: typeof validationRows[number]) => row.candidate_id === factorSearch?.validation.selected_candidate_id ? <Tag color="blue">LOCKED</Tag> : '—' }]} /><Typography.Text type="secondary">因子 Rank IC 仅用于发现期筛选；参数只按验证期选择。确认期曾被项目历史实验查看，不是全新盲测，结果固定为 {factorSearch?.promotion ?? '未评估'}，不能接入 PAPER。</Typography.Text></Space> },
      { key: 'factor-robustness', label: '因子鲁棒性', children: <Space orientation="vertical" style={{ width: '100%' }}>
        <Typography.Text>冻结候选：{robustFactorSearch?.selection?.candidate_count ?? 0} 个；季度窗口：{robustFactorSearch?.selection?.window_ids.length ?? 0} 个；季度硬门槛合格：{robustFactorSearch?.selection?.window_gate_eligible_candidate_ids.length ?? 0} 个。</Typography.Text>
        <Typography.Text>鲁棒候选：{robustCandidateId ?? '无'}；正收益窗口 {formatPercent(robustSummary?.positive_window_ratio ?? '0')}；中位 Sharpe {formatNumber(robustSummary?.median_window_sharpe)}；最差 Sharpe {formatNumber(robustSummary?.worst_window_sharpe)}。</Typography.Text>
        <Typography.Text>双倍成本收益 {formatPercent(String(doubledCost?.metrics.total_return ?? '0'))}；邻参正收益比例 {formatPercent(robustStress?.parameter_neighbors.positive_ratio ?? '0')}；股票池子样本正收益比例 {formatPercent(robustStress?.universe_subsets.positive_ratio ?? '0')}；Bootstrap 正收益概率 {formatPercent(robustBootstrap?.probability_positive ?? '0')}。</Typography.Text>
        <Typography.Text>2026 污染审计：收益 {formatPercent(String(pollutedAudit?.selected_metrics?.total_return ?? '0'))}；主动超额 {formatPercent(pollutedAudit?.terminal_excess_return ?? '0')}；最大回撤 {formatPercent(String(pollutedAudit?.selected_metrics?.maximum_drawdown ?? '0'))}。</Typography.Text>
        <Table rowKey="candidate_id" size="small" dataSource={robustRows} pagination={{ pageSize: 8, showSizeChanger: false }} columns={[{ title: '排名', dataIndex: 'selection_rank' }, { title: '候选', dataIndex: 'candidate_id' }, { title: '正收益窗口', dataIndex: 'positive_window_ratio', render: (value: string) => formatPercent(value) }, { title: '中位 Sharpe', dataIndex: 'median_window_sharpe', render: (value: string) => formatNumber(value) }, { title: '最差 Sharpe', dataIndex: 'worst_window_sharpe', render: (value: string) => formatNumber(value) }, { title: '全期收益', dataIndex: 'full_period_return', render: (value: string) => formatPercent(value) }, { title: '结论', render: (_: unknown, row: typeof robustRows[number]) => row.candidate_id === robustCandidateId ? <Tag color="blue">ROBUST</Tag> : '未过完整门槛' }]} scroll={{ x: 980 }} />
        <Typography.Text type="secondary">通过的是复用 2024–2025 数据的预声明研究鲁棒门槛，不是新的独立样本外确认。2026 只做污染审计；当前结论仍为 {robustFactorSearch?.promotion ?? '未评估'}，不能接入 PAPER。</Typography.Text>
      </Space> },
      { key: 'stock-sector-transfer', label: '多板块个股迁移', children: <Space orientation="vertical" style={{ width: '100%' }}>
        <Typography.Text>数据：{stockTransfer?.data?.sector_count ?? 0} 个板块 × 每板块 {stockTransfer?.data?.stocks_per_sector ?? 0} 只，共 {stockTransfer?.data?.symbol_count ?? 0} 只股票、{stockTransfer?.data?.bar_count ?? 0} 根日线；统一 {stockTransfer?.data?.adjustment?.toUpperCase() ?? '—'}。</Typography.Text>
        <Typography.Text>锁定策略：{stockTransfer?.locked_strategy?.candidate_id ?? '—'}；2019–2025 正收益年份 {stockTransfer?.formal_period?.positive_year_count ?? 0}/7，单板块 {stockTransfer?.formal_period?.positive_sector_only_count ?? 0}/15，留一板块 {stockTransfer?.formal_period?.positive_leave_one_sector_out_count ?? 0}/15。</Typography.Text>
        <Typography.Text>全期收益 {formatPercent(String(transferFull?.total_return ?? '0'))}，年化 {formatPercent(String(transferFull?.annualized_return ?? '0'))}，Sharpe {formatNumber(String(transferFull?.sharpe ?? '0'))}，最大回撤 {formatPercent(String(transferFull?.maximum_drawdown ?? '0'))}；双倍成本收益 {formatPercent(String(transferDouble?.total_return ?? '0'))}。</Typography.Text>
        <Typography.Text>最弱单板块：{weakestSector?.sector_name ?? '—'}，收益 {formatPercent(String(weakestSector?.metrics.total_return ?? '0'))}；Bootstrap 正收益概率 {formatPercent(stockTransfer?.bootstrap.probability_positive ?? '0')}，5% 分位 {formatPercent(stockTransfer?.bootstrap.p05_total_return ?? '0')}。</Typography.Text>
        <Typography.Text>2026 污染审计：策略 {formatPercent(String(transferAudit?.strategy_metrics?.total_return ?? '0'))}，等权 {formatPercent(String(transferAudit?.equal_weight_metrics?.total_return ?? '0'))}，超额 {formatPercent(transferAudit?.terminal_excess_return ?? '0')}。</Typography.Text>
        <Table rowKey="year" size="small" pagination={false} dataSource={yearlyRows} columns={[{ title: '年度', dataIndex: 'year' }, { title: '收益', render: (_: unknown, row: typeof yearlyRows[number]) => formatPercent(String(row.metrics.total_return ?? '')) }, { title: 'Sharpe', render: (_: unknown, row: typeof yearlyRows[number]) => formatNumber(String(row.metrics.sharpe ?? '')) }, { title: '最大回撤', render: (_: unknown, row: typeof yearlyRows[number]) => formatPercent(String(row.metrics.maximum_drawdown ?? '')) }, { title: '交易数', render: (_: unknown, row: typeof yearlyRows[number]) => String(row.metrics.number_of_trades ?? '—') }]} />
        <Table rowKey="sector_code" size="small" dataSource={sectorRows} pagination={{ pageSize: 8, showSizeChanger: false }} columns={[{ title: '板块', dataIndex: 'sector_name' }, { title: '代码', dataIndex: 'sector_code' }, { title: '股票数', dataIndex: 'symbols', render: (value: string[]) => value.length }, { title: '收益', render: (_: unknown, row: typeof sectorRows[number]) => formatPercent(String(row.metrics.total_return ?? '')) }, { title: 'Sharpe', render: (_: unknown, row: typeof sectorRows[number]) => formatNumber(String(row.metrics.sharpe ?? '')) }, { title: '最大回撤', render: (_: unknown, row: typeof sectorRows[number]) => formatPercent(String(row.metrics.maximum_drawdown ?? '')) }]} />
        <Table rowKey="excluded_sector_code" size="small" dataSource={leaveOneRows} pagination={{ pageSize: 8, showSizeChanger: false }} columns={[{ title: '剔除板块', dataIndex: 'excluded_sector_name' }, { title: '剔除代码', dataIndex: 'excluded_sector_code' }, { title: '收益', render: (_: unknown, row: typeof leaveOneRows[number]) => formatPercent(String(row.metrics.total_return ?? '')) }, { title: 'Sharpe', render: (_: unknown, row: typeof leaveOneRows[number]) => formatNumber(String(row.metrics.sharpe ?? '')) }, { title: '最大回撤', render: (_: unknown, row: typeof leaveOneRows[number]) => formatPercent(String(row.metrics.maximum_drawdown ?? '')) }]} />
        <Typography.Text type="secondary">研究硬门：{stockTransfer?.transfer_gate?.passed ? '通过' : '未通过'}；但股票池是 2026 当前成分股向历史回投、后复权也不是时点一致数据，且未模拟涨跌停成交，结论固定为 {stockTransfer?.promotion ?? '未评估'}，不能接入 PAPER。</Typography.Text>
      </Space> },
      { key: 'development-policy-search', label: '开发区分钟策略', children: <Space orientation="vertical" style={{ width: '100%' }}>
        <Alert showIcon type="warning" title={`回顾性开发搜索：${developmentSearch?.promotion ?? '未评估'}`} description={`只使用 ${developmentSearch?.development_contract?.session_count ?? 0} 个开发会话、${developmentSearch?.development_contract?.processed_bar_count ?? 0} 根 5 分钟 Bar；保留尾部 ${developmentSearch?.reserved_tail?.session_count ?? 0} 日未进入策略引擎或候选指标。此前尾部结果已经可见，因此这不是盲测或晋级证据。`} />
        <Typography.Text>观测领先者：{developmentSearch?.observed_leader?.policy_id ?? '—'}；在 {developmentSearch?.future_observation_lock?.locked_at ? formatChinaMinuteTimestamp(developmentSearch.future_observation_lock.locked_at) : '—'}（中国市场时钟）锁定，至少等待 {developmentSearch?.future_observation_lock?.minimum_complete_future_sessions ?? 0} 个完整未来会话。当天未完成分钟不进入指标，锁定期间不改参数。</Typography.Text>
        <Table rowKey="policy_id" size="small" pagination={false} dataSource={developmentSearch?.candidates ?? []} columns={[{ title: '候选', dataIndex: 'policy_id' }, { title: '策略收益', render: (_: unknown, row: NonNullable<typeof developmentSearch>['candidates'][number]) => formatPercent(String(row.overlay.total_return)) }, { title: '对基准差', dataIndex: 'return_delta_vs_base', render: (value: number) => formatPercent(String(value)) }, { title: '回撤改善', dataIndex: 'drawdown_improvement', render: (value: number) => formatPercent(String(value)) }, { title: '平均敞口', render: (_: unknown, row: NonNullable<typeof developmentSearch>['candidates'][number]) => formatPercent(String(row.execution.mean_exposure)) }, { title: '每标的换手', render: (_: unknown, row: NonNullable<typeof developmentSearch>['candidates'][number]) => formatNumber(String(row.execution.mean_turnover_per_symbol)) }, { title: '状态', render: (_: unknown, row: NonNullable<typeof developmentSearch>['candidates'][number]) => row.policy_id === developmentSearch?.observed_leader?.policy_id ? <Tag color="blue">FUTURE LOCK</Tag> : '开发候选' }]} scroll={{ x: 1050 }} />
        <Typography.Text type="secondary">信号在 5 分钟 Bar 收盘观察、下一 Bar 开盘生效；只允许保持或降低日级目标，不发送外部订单，也不证明 1 分钟实时就绪。</Typography.Text>
      </Space> },
      { key: 'forward-observation', label: '未来完整日观测', children: <Space orientation="vertical" style={{ width: '100%' }}>
        <Alert showIcon type={forwardSourceCoverage?.ready_for_formal_collection ? 'success' : 'warning'} title={`BaoStock 来源覆盖：${forwardSourceCoverage?.ready_symbol_count ?? 0}/${forwardSourceCoverage?.required_symbol_count ?? 75} 可正式采集`} description={`目标日 ${forwardSourceCoverage?.target_day ?? '—'}；${forwardSourceCoverage?.incomplete_symbol_count ?? 75} 只仍未形成完整目标日网格。该签名诊断不保存新 Bar、不进入 0/20，也不授权 PAPER、LIVE 或外部下单。`} />
        <Typography.Text>来源版本 {forwardSourceCoverage?.provider_version ?? '—'}；目标日完整网格 {forwardSourceCoverage?.target_complete_symbol_count ?? 0}/{forwardSourceCoverage?.required_symbol_count ?? 75}；生成于 {forwardSourceCoverage?.generated_at ? formatChinaMinuteTimestamp(forwardSourceCoverage.generated_at) : '—'}。</Typography.Text>
        <Alert showIcon type={forwardSourceCoverageHistory?.all_checkpoints_monotonic ? 'success' : 'warning'} title={`来源发布序列：${forwardSourceCoverageHistory?.checkpoint_count ?? 0} 个检查点`} description={`${forwardSourceCoverageCheckpoints.map((checkpoint) => checkpoint.ready_symbol_count).join(' → ') || '暂无'} READY；${forwardSourceCoverageHistory?.all_checkpoints_monotonic ? '覆盖度未发生回退' : '检测到覆盖度回退，需人工复核'}。每个点均绑定并重验完整 75 股不可变归档；该链不进入正式前向指标。`} />
        <Table rowKey="checkpoint_sha256" size="small" pagination={false} dataSource={forwardSourceCoverageCheckpoints} columns={[{ title: '发布时间', dataIndex: 'generated_at', render: (value: string) => formatChinaMinuteTimestamp(value) }, { title: 'READY', dataIndex: 'ready_symbol_count', render: (value: number) => `${value}/75` }, { title: '未完整', dataIndex: 'incomplete_symbol_count' }, { title: '较上次', dataIndex: 'ready_symbol_count_delta', render: (value: number | null) => value === null ? '基线' : `${value >= 0 ? '+' : ''}${value}` }, { title: '单调', dataIndex: 'monotonic_vs_previous', render: (value: boolean) => value ? <Tag color="green">是</Tag> : <Tag color="red">否</Tag> }]} locale={{ emptyText: '暂无签名来源覆盖发布序列' }} />
        <Table rowKey="symbol" size="small" dataSource={forwardSourceIncompleteRows} pagination={{ pageSize: 8, showSizeChanger: false }} columns={[{ title: '未完整股票', dataIndex: 'symbol' }, { title: '目标 Bar', dataIndex: 'target_bar_count', render: (value: number) => `${value}/${forwardSourceCoverage?.required_bars_per_symbol_session ?? 48}` }, { title: '锚点一致', dataIndex: 'anchor_exact_match', render: (value: boolean) => value ? <Tag color="green">是</Tag> : <Tag color="red">否</Tag> }, { title: '诊断原因', dataIndex: 'reasons', render: (value: string[]) => value.join(' / ') || '—' }]} locale={{ emptyText: '75 只股票均已形成完整来源网格' }} />
        <Alert showIcon type="info" title={`观测进度：${forwardObservation?.future_session_count ?? 0}/${forwardObservation?.minimum_complete_future_sessions ?? 20} 个完整交易日`} description={`当前状态 ${forwardObservation?.observation_status ?? 'UNAVAILABLE'}；只接受 ${forwardObservation?.required_sector_count ?? 15} 板块、${forwardObservation?.required_symbol_count ?? 75} 股、每股 ${forwardObservation?.required_bars_per_symbol_session ?? 48} 根原生 5 分钟 Bar 的收盘后完整会话。半日、停牌零值占位或锚点修订均拒绝。`} />
        <Typography.Text>锁定策略：{forwardObservation?.policy_id ?? '—'}；最早合格日 {forwardObservation?.first_eligible_trading_day ?? '—'}；预注册时间 {forwardObservation?.created_at ? formatChinaMinuteTimestamp(forwardObservation.created_at) : '—'}。</Typography.Text>
        <Table rowKey="gate_id" size="small" pagination={false} dataSource={forwardGateRows} columns={[{ title: '20 日后硬门', dataIndex: 'gate_id', render: (value: string) => forwardObservationGateLabels[value] ?? value }, { title: '状态', dataIndex: 'passed', render: (value: boolean | null) => value === null ? <Tag>等待样本</Tag> : value ? <Tag color="green">通过</Tag> : <Tag color="red">未通过</Tag> }]} />
        <Typography.Text type="secondary">开发策略在开盘前已经锁定；具体评估合同在首日开盘后、目标会话抓取前固定，因此不宣称完全盲预注册。暖启动的 368 日收益不进入未来指标；即使 20 日硬门全部通过，也不会自动授权 PAPER、LIVE 或外部下单。</Typography.Text>
      </Space> },
      { key: 'walk-forward', label: '滚动样本外', children: <Space orientation="vertical"><Typography.Text>独立测试折数：{walkForward?.result.folds.length ?? 0}；正收益折比例：{walkForward?.result.fold_pass_ratio ?? '—'}</Typography.Text><Typography.Text>聚合 Sharpe：{String(aggregate?.sharpe ?? '—')}；聚合收益：{String(aggregate?.total_return ?? '—')}</Typography.Text><Typography.Text type="secondary">仅验证期选择候选；完整折区间互不重叠。结论：{walkForward?.promotion ?? '未评估'}。</Typography.Text></Space> },
      { key: 'robustness', label: '稳健性诊断', children: <Space orientation="vertical"><Typography.Text>场景数：{robustness?.result.scenario_metrics.length ?? 0}；最差 Sharpe：{robustness?.result.worst_sharpe ?? '—'}</Typography.Text><Typography.Text>离散度：{robustness?.result.dispersion ?? '—'}；存在负 Sharpe 场景：{robustness?.result.any_negative_sharpe ? '是' : '否'}</Typography.Text><Typography.Text type="secondary">这是复用样本的拒绝后诊断，不是新的独立测试证据。结论：{robustness?.promotion ?? '未评估'}。</Typography.Text></Space> },
      { key: 'lineage', label: '演化树', children: <Empty description="当前实验尚未注册策略血缘" /> },
      { key: 'rejected', label: '未晋级', children: <Typography.Text type="secondary">当前样本池存在幸存者偏差，且缺少 PAPER/SHADOW 证据。</Typography.Text> },
    ]} /></Card>
  </Page>
}

export function ExperimentPage({ data }: { data: PlatformSnapshot | null }) {
  const [query, setQuery] = useState('')
  const [scenario, setScenario] = useState<string | undefined>()
  const experiments = data?.experiments.experiments ?? []
  const rows = experiments.filter((row) => {
    const text = query.trim().toLowerCase()
    return (!text || `${row.experiment_id} ${row.model ?? ''}`.toLowerCase().includes(text))
      && (!scenario || row.scenario_type === scenario)
  })
  const scenarios = [...new Set(experiments.map((row) => row.scenario_type))].map((value) => ({ value, label: value }))
  const columns = [
    { title: '实验ID', dataIndex: 'experiment_id' },
    { title: '模型', dataIndex: 'model' },
    { title: '类型', dataIndex: 'scenario_type' },
    { title: '参数', dataIndex: 'parameters', render: (value: Record<string, string>) => Object.entries(value ?? {}).map(([key, item]) => `${key}=${item}`).join(', ') || '基准' },
    { title: '样本划分', dataIndex: 'split', render: (value: { test_start: string; test_end: string }) => value ? `${value.test_start} 至 ${value.test_end}` : '—' },
    { title: 'Universe', dataIndex: 'universe_size' },
    { title: '测试期收益', render: (_: unknown, row: { metrics: Record<string, string> }) => row.metrics.total_return ?? '—' },
    { title: 'Sharpe', render: (_: unknown, row: { metrics: Record<string, string> }) => row.metrics.sharpe ?? '—' },
    { title: '最大回撤', render: (_: unknown, row: { metrics: Record<string, string> }) => row.metrics.maximum_drawdown ?? '—' },
  ]
  return <Page title="实验中心" subtitle="真实 AKShare 历史数据上的可复现实验"><PublicResearchObservations mode="experiments" /><Alert showIcon type="warning" title={data?.experiments.classification ?? '尚无验证产物'} description={`数据版本：${data?.experiments.data_version ?? '—'}；每行已验证 record/result/artifact 哈希，但当前结论仍为 ${data?.experiments.promotion ?? '未评估'}。`} /><Space wrap><Input.Search aria-label="搜索实验" placeholder="搜索实验 ID 或模型" allowClear value={query} onChange={(event) => setQuery(event.target.value)} style={{ width: 320 }} /><Select aria-label="筛选实验类型" allowClear placeholder="全部类型" value={scenario} onChange={setScenario} options={scenarios} style={{ width: 180 }} /><JsonExportButton filename="qtrade-experiments.json" value={rows} /></Space><Table rowKey="experiment_id" columns={columns} dataSource={rows} pagination={{ pageSize: 8, showSizeChanger: false }} scroll={{ x: 1400 }} expandable={{ expandedRowRender: (row) => <Descriptions values={[["Spec Hash", row.spec_hash], ["Result Hash", row.result_hash], ["Record Hash", row.record_hash ?? '—'], ["数据版本", row.data_version ?? data?.experiments.data_version ?? '—'], ["Git Commit", row.git_commit ?? data?.experiments.git_commit ?? '—'], ["交易成本", row.transaction_cost ?? '—'], ["参数", Object.entries(row.parameters ?? {}).map(([key, value]) => `${key}=${value}`).join('，') || '基准']]} /> }} locale={{ emptyText: '尚未运行验证实验' }} /></Page>
}

export function BacktestPage({ data }: { data: PlatformSnapshot | null }) {
  return <Page title="回测分析" subtitle="使用当前数据重新计算"><Tabs defaultActiveKey="current" items={[
    { key: 'current', label: '当前数据回测', children: <CurrentWorkspace mode="backtest" platform={data} /> },
    { key: 'archive', label: '历史实验与旧版回测', children: <><Alert showIcon type="warning" title="历史实验结果保留原始数据区间；不会随行情自动改写" /><HistoricalBacktestPage data={data} /></> },
  ]} /></Page>
}

export function HistoricalBacktestPage({ data }: { data: PlatformSnapshot | null }) {
  const analytics = data?.backtestAnalytics
  const minuteResearch = data?.minuteResearch
  const minuteSelected = minuteResearch?.selected
  const fiveMinuteResearch = data?.fiveMinuteResearch
  const fiveMinuteAggregate = fiveMinuteResearch?.aggregate
  const longFiveMinuteResearch = data?.longFiveMinuteResearch
  const longFiveMinuteAggregate = longFiveMinuteResearch?.aggregate
  const longExecution = longFiveMinuteAggregate?.execution
  const reservedTail = data?.reservedTailDiagnostic
  const reservedFiveBps = reservedTail?.five_bps
  const reservedPassedGateCount = reservedTail?.gates.filter((gate) => gate.passed).length ?? 0
  const longReasonSummary = Object.entries(longExecution?.reason_counts ?? {})
    .sort((left, right) => right[1] - left[1])
    .map(([reason, count]) => `${minuteReasonLabels[reason] ?? reason} ${count.toLocaleString('zh-CN')}`)
    .join('；')
  const leader = analytics?.leader
  const benchmark = analytics?.benchmark
  const requestedTemplate = new URLSearchParams(window.location.hash.split('?')[1] ?? '').get('template')
  const initialTemplateId = data?.strategyTemplates.templates.some((template) => (
    template.causal_preview_supported && template.template_id === requestedTemplate
  )) ? requestedTemplate as CausalPreviewTemplateId : null
  const walkForward = data?.walkForward
  const aggregate = walkForward?.result.aggregate_metrics
  const folds = walkForward?.result.folds ?? []
  const hasWalkForwardEvidence = folds.length > 0
  const positiveFolds = folds.filter((fold) => Number(fold.test_metrics.total_return) > 0).length
  const foldColumns = [
    { title: '折', dataIndex: 'fold', width: 56 },
    { title: '验证期选中候选', dataIndex: 'selected_candidate_id' },
    { title: '独立测试窗', render: (_: unknown, row: typeof folds[number]) => `${row.split.test_start} 至 ${row.split.test_end}` },
    { title: '测试收益', render: (_: unknown, row: typeof folds[number]) => formatPercent(String(row.test_metrics.total_return)) },
    { title: '测试 Sharpe', render: (_: unknown, row: typeof folds[number]) => formatNumber(String(row.test_metrics.sharpe)) },
    { title: '最大回撤', render: (_: unknown, row: typeof folds[number]) => formatPercent(String(row.test_metrics.maximum_drawdown)) },
    { title: '测试期交易', render: (_: unknown, row: typeof folds[number]) => String(row.test_metrics.number_of_trades ?? '—') },
  ]
  const tradeColumns = [
    { title: '交易日', dataIndex: 'trading_day' }, { title: '证券', dataIndex: 'symbol' },
    { title: '方向', dataIndex: 'side', render: (value: string) => <Tag color={value === 'BUY' ? 'red' : 'green'}>{value === 'BUY' ? '买入' : '卖出'}</Tag> },
    { title: '模拟价格', dataIndex: 'price' }, { title: '数量', dataIndex: 'quantity' },
    { title: '模拟名义金额', dataIndex: 'notional', render: (value: string) => formatInteger(value) },
    { title: '模拟交易成本', dataIndex: 'cost', render: (value: string) => formatNumber(value) },
  ]
  const positionColumns = [{ title: '证券', dataIndex: 'symbol' }, { title: '期末数量', dataIndex: 'quantity' }]
  const monthlyColumns = [{ title: '月份', dataIndex: 'month' }, { title: '月收益', dataIndex: 'return', render: (value: string) => formatPercent(value) }]
  return <Page title="回测分析" subtitle="资金曲线、回撤、敞口、换手与成本">
    <SingleStockFiveMinuteBacktestLab key={longFiveMinuteResearch?.minute_dataset ? `single-stock:${longFiveMinuteResearch.minute_dataset.data_version}:${longFiveMinuteResearch.minute_dataset.checksum}` : 'no-five-minute-dataset'} dataset={longFiveMinuteResearch?.minute_dataset ?? null} />
    <BacktestLab key={data?.market.historical ? `${data.market.historical.data_version}:${data.market.historical.checksum}:${initialTemplateId ?? 'no-template'}` : 'no-dataset'} dataset={data?.market.historical ?? null} templates={data?.strategyTemplates.templates ?? []} initialTemplateId={initialTemplateId} gitCommit={data?.deployment.git_commit_sha ?? null} />
    <Alert showIcon type="warning" title={`分钟修正锁定测试：${minuteResearch?.promotion ?? '未评估'}`} description={minuteSelected ? `${minuteSelected.policy_id} 在后两日、${minuteSelected.cost_bps} bps 下：基准 ${formatPercent(String(minuteSelected.test_base.total_return))}，分钟修正 ${formatPercent(String(minuteSelected.test_overlay.total_return))}，相对改善 ${formatPercent(String(minuteSelected.test_excess_return))}；两者绝对收益均为负。` : '暂无签名与 artifact 哈希均验证通过的分钟回测。'} />
    <Card title="分钟级因果修正：前 3 日选择、后 2 日锁定验证" extra={<JsonExportButton filename="qtrade-minute-overlay-evidence.json" value={minuteResearch ?? {}} />}>
      <MinuteBacktestChart report={minuteResearch ?? null} />
      {minuteSelected && <Descriptions values={[
        ['策略', minuteSelected.policy_id],
        ['标的 / 板块', `${minuteResearch?.minute_dataset?.symbol_count ?? 0} / ${minuteResearch?.minute_dataset?.sector_count ?? 0}`],
        ['测试日', minuteResearch?.split?.test_days.join('、') ?? '—'],
        ['基准收益', formatPercent(String(minuteSelected.test_base.total_return))],
        ['修正后收益', formatPercent(String(minuteSelected.test_overlay.total_return))],
        ['基准最大回撤', formatPercent(String(minuteSelected.test_base.maximum_drawdown))],
        ['修正后最大回撤', formatPercent(String(minuteSelected.test_overlay.maximum_drawdown))],
        ['平均换手', formatNumber(String(minuteSelected.mean_turnover))],
        ['时序', '本分钟收盘决策，下一分钟开盘生效'],
        ['最大暴露', formatPercent(String(minuteResearch?.causal_contract?.maximum_exposure ?? 0))],
        ['外部订单', 'DISABLED'],
        ['结论', '降低风险，但未证明盈利'],
      ]} />}
      <Table rowKey="trading_day" size="small" pagination={false} dataSource={minuteResearch?.daily ?? []} columns={[
        { title: '交易日', dataIndex: 'trading_day' },
        { title: '分区', dataIndex: 'partition', render: (value: string) => <Tag color={value === 'TEST' ? 'orange' : 'blue'}>{value}</Tag> },
        { title: '基准收益', render: (_: unknown, row: NonNullable<typeof minuteResearch>['daily'][number]) => formatPercent(String(row.base.total_return)) },
        { title: '修正后收益', render: (_: unknown, row: NonNullable<typeof minuteResearch>['daily'][number]) => formatPercent(String(row.overlay.total_return)) },
        { title: '修正后回撤', render: (_: unknown, row: NonNullable<typeof minuteResearch>['daily'][number]) => formatPercent(String(row.overlay.maximum_drawdown)) },
      ]} />
      <Table rowKey="cost_bps" size="small" pagination={false} dataSource={minuteResearch?.cost_stress ?? []} columns={[
        { title: '单边成本 bps', dataIndex: 'cost_bps' },
        { title: '基准收益', render: (_: unknown, row: NonNullable<typeof minuteResearch>['cost_stress'][number]) => formatPercent(String(row.base.total_return)) },
        { title: '修正后收益', render: (_: unknown, row: NonNullable<typeof minuteResearch>['cost_stress'][number]) => formatPercent(String(row.overlay.total_return)) },
        { title: '相对基准', render: (_: unknown, row: NonNullable<typeof minuteResearch>['cost_stress'][number]) => formatPercent(String(row.overlay.total_return - row.base.total_return)) },
        { title: '最大回撤', render: (_: unknown, row: NonNullable<typeof minuteResearch>['cost_stress'][number]) => formatPercent(String(row.overlay.maximum_drawdown)) },
      ]} />
      <Table rowKey="group" size="small" pagination={false} dataSource={minuteResearch?.subgroups ?? []} columns={[
        { title: '样本组', dataIndex: 'group' },
        { title: '标的数', dataIndex: 'symbol_count' },
        { title: '基准收益', render: (_: unknown, row: NonNullable<typeof minuteResearch>['subgroups'][number]) => formatPercent(String(row.base.total_return)) },
        { title: '修正后收益', render: (_: unknown, row: NonNullable<typeof minuteResearch>['subgroups'][number]) => formatPercent(String(row.overlay.total_return)) },
        { title: '修正后回撤', render: (_: unknown, row: NonNullable<typeof minuteResearch>['subgroups'][number]) => formatPercent(String(row.overlay.maximum_drawdown)) },
      ]} />
      <Table rowKey="excluded_sector_code" size="small" pagination={{ pageSize: 8, showSizeChanger: false }} dataSource={minuteResearch?.leave_one_sector_out ?? []} columns={[
        { title: '整行业剔除', dataIndex: 'excluded_sector_name' },
        { title: '剔除股票数', dataIndex: 'excluded_symbol_count' },
        { title: '保留标的数', dataIndex: 'retained_symbol_count' },
        { title: '修正后收益', render: (_: unknown, row: NonNullable<typeof minuteResearch>['leave_one_sector_out'][number]) => formatPercent(String(row.overlay.total_return)) },
        { title: '相对全样本', render: (_: unknown, row: NonNullable<typeof minuteResearch>['leave_one_sector_out'][number]) => formatPercent(String(row.return_delta_vs_all)) },
        { title: '修正后回撤', render: (_: unknown, row: NonNullable<typeof minuteResearch>['leave_one_sector_out'][number]) => formatPercent(String(row.overlay.maximum_drawdown)) },
      ]} />
      <Typography.Text type="secondary">7 个冻结参数只在前三日排序；后两日不回填选择。0/5/10 bps 相对基准改善，20 bps 优势消失；只有五个最近交易日、当前非 PIT 样本池且没有 PAPER/SHADOW，会永久保持 NOT_ELIGIBLE。</Typography.Text>
    </Card>
    <Alert
      showIcon
      type="error"
      title={fiveMinuteAggregate ? `跨时间尺度否证：${fiveMinuteAggregate.positive_test_fold_count}/${fiveMinuteResearch?.folds.length ?? 0} 折绝对正收益` : '暂无已验证的长窗口 5 分钟滚动样本外证据'}
      description={fiveMinuteAggregate ? `40 个交易日、101 个标的的原生 5 分钟数据上，${fiveMinuteAggregate.cost_bps} bps 聚合基准 ${formatPercent(String(fiveMinuteAggregate.base.total_return))}，分钟修正 ${formatPercent(String(fiveMinuteAggregate.overlay.total_return))}，相对基准 ${formatPercent(String(fiveMinuteAggregate.excess_return))}。短窗口 1 分钟结果未跨时间尺度复现，策略不能晋级。` : '报告或其签名 artifacts 未通过校验，不能据此声称成功或失败。'}
    />
    <Card title="40 日原生 5 分钟滚动样本外：如实展示失败" extra={<JsonExportButton filename="qtrade-five-minute-walk-forward-evidence.json" value={fiveMinuteResearch ?? {}} />}>
      <MinuteBacktestChart report={fiveMinuteResearch ?? null} />
      {fiveMinuteAggregate && <Descriptions values={[
        ['标的 / 板块', `${fiveMinuteResearch?.minute_dataset?.symbol_count ?? 0} / ${fiveMinuteResearch?.minute_dataset?.sector_count ?? 0}`],
        ['ETF / 股票', `${fiveMinuteResearch?.minute_dataset?.etf_count ?? 0} / ${fiveMinuteResearch?.minute_dataset?.stock_count ?? 0}`],
        ['数据范围', `${fiveMinuteResearch?.minute_dataset?.trading_days[0] ?? '—'} 至 ${fiveMinuteResearch?.minute_dataset?.trading_days.at(-1) ?? '—'}`],
        ['滚动切分', `${fiveMinuteResearch?.walk_forward?.initial_train_sessions ?? 0} 日初始训练；${fiveMinuteResearch?.walk_forward?.fold_count ?? 0} 折 × ${fiveMinuteResearch?.walk_forward?.test_sessions_per_fold ?? 0} 日独立测试`],
        ['基准收益', formatPercent(String(fiveMinuteAggregate.base.total_return))],
        ['修正后收益', formatPercent(String(fiveMinuteAggregate.overlay.total_return))],
        ['相对基准', formatPercent(String(fiveMinuteAggregate.excess_return))],
        ['修正后最大回撤', formatPercent(String(fiveMinuteAggregate.overlay.maximum_drawdown))],
        ['策略序列', fiveMinuteResearch?.walk_forward?.selected_policy_sequence.join(' → ') ?? '—'],
        ['时序', '本 5 分钟收盘决策，下一 5 分钟开盘生效'],
        ['候选状态', '全部候选因果预热'],
        ['外部订单', 'DISABLED'],
      ]} />}
      <Table rowKey="fold" size="small" pagination={false} dataSource={fiveMinuteResearch?.folds ?? []} columns={[
        { title: '折', dataIndex: 'fold', width: 56 },
        { title: '冻结策略', dataIndex: 'selected_policy_id' },
        { title: '独立测试窗', render: (_: unknown, row: NonNullable<typeof fiveMinuteResearch>['folds'][number]) => `${row.test_days[0]} 至 ${row.test_days.at(-1)}` },
        { title: '基准收益', render: (_: unknown, row: NonNullable<typeof fiveMinuteResearch>['folds'][number]) => formatPercent(String(row.test_base.total_return)) },
        { title: '修正后收益', render: (_: unknown, row: NonNullable<typeof fiveMinuteResearch>['folds'][number]) => formatPercent(String(row.test_overlay.total_return)) },
        { title: '相对基准', render: (_: unknown, row: NonNullable<typeof fiveMinuteResearch>['folds'][number]) => formatPercent(String(row.test_excess_return)) },
      ]} />
      <Table rowKey="cost_bps" size="small" pagination={false} dataSource={fiveMinuteResearch?.cost_stress ?? []} columns={[
        { title: '单边成本 bps', dataIndex: 'cost_bps' },
        { title: '基准收益', render: (_: unknown, row: NonNullable<typeof fiveMinuteResearch>['cost_stress'][number]) => formatPercent(String(row.base.total_return)) },
        { title: '修正后收益', render: (_: unknown, row: NonNullable<typeof fiveMinuteResearch>['cost_stress'][number]) => formatPercent(String(row.overlay.total_return)) },
        { title: '相对基准', render: (_: unknown, row: NonNullable<typeof fiveMinuteResearch>['cost_stress'][number]) => formatPercent(String(row.excess_return)) },
        { title: '修正后回撤', render: (_: unknown, row: NonNullable<typeof fiveMinuteResearch>['cost_stress'][number]) => formatPercent(String(row.overlay.maximum_drawdown)) },
      ]} />
      <Table rowKey="group" size="small" pagination={false} dataSource={fiveMinuteResearch?.subgroups ?? []} columns={[
        { title: '样本组', dataIndex: 'group' },
        { title: '标的数', dataIndex: 'symbol_count' },
        { title: '基准收益', render: (_: unknown, row: NonNullable<typeof fiveMinuteResearch>['subgroups'][number]) => formatPercent(String(row.base.total_return)) },
        { title: '修正后收益', render: (_: unknown, row: NonNullable<typeof fiveMinuteResearch>['subgroups'][number]) => formatPercent(String(row.overlay.total_return)) },
        { title: '修正后回撤', render: (_: unknown, row: NonNullable<typeof fiveMinuteResearch>['subgroups'][number]) => formatPercent(String(row.overlay.maximum_drawdown)) },
      ]} />
      <Typography.Text type="secondary">这是供应商原生 5 分钟线的跨尺度压力测试，不是把 1 分钟线降采样，也不证明 1 分钟实盘可用。24 个测试日互不重叠；0 bps 起已跑输基准，ETF、股票及 15 个整板块剔除样本均未得到可晋级的绝对收益，因此保持 {fiveMinuteResearch?.promotion ?? 'NOT_EVALUATED'}。</Typography.Text>
    </Card>
    <Alert
      showIcon
      type="error"
      title={longFiveMinuteAggregate ? `长历史复核：${longFiveMinuteAggregate.positive_test_fold_count}/${longFiveMinuteResearch?.folds.length ?? 0} 折绝对正收益，仍未战胜基准` : '暂无已验证的长历史 5 分钟复核'}
      description={longFiveMinuteAggregate ? `BaoStock 75 只股票、368 个共同完整交易日；预先锁定前 360 日，其中 240 日为六折独立测试。${longFiveMinuteAggregate.cost_bps} bps 下基准 ${formatPercent(String(longFiveMinuteAggregate.base.total_return))}，分钟修正 ${formatPercent(String(longFiveMinuteAggregate.overlay.total_return))}，相对基准 ${formatPercent(String(longFiveMinuteAggregate.excess_return))}。${longExecution ? `平均暴露仅 ${formatPercent(String(longExecution.mean_exposure))}，${formatPercent(String(longExecution.reduced_exposure_ratio))} 的标的-Bar 处于减仓，低暴露与高换手共同解释收益损失。` : '回撤虽降低，但收益损失过大。'}` : '报告、数据集 checksum 或任一签名 artifact 未通过校验，保持失败关闭。'}
    />
    <Card title="368 日原生 5 分钟长历史复核：保留否证，不追涨调参" extra={<JsonExportButton filename="qtrade-long-five-minute-walk-forward-evidence.json" value={longFiveMinuteResearch ?? {}} />}>
      <MinuteBacktestChart report={longFiveMinuteResearch ?? null} />
      {longFiveMinuteAggregate && <Descriptions values={[
        ['来源', `${longFiveMinuteResearch?.minute_dataset?.provider ?? '—'} ${longFiveMinuteResearch?.minute_dataset?.provider_version ?? ''}`],
        ['标的 / 板块', `${longFiveMinuteResearch?.minute_dataset?.symbol_count ?? 0} / ${longFiveMinuteResearch?.minute_dataset?.sector_count ?? 0}`],
        ['数据范围', `${longFiveMinuteResearch?.minute_dataset?.trading_days[0] ?? '—'} 至 ${longFiveMinuteResearch?.minute_dataset?.trading_days.at(-1) ?? '—'}`],
        ['滚动切分', `${longFiveMinuteResearch?.walk_forward?.initial_train_sessions ?? 0} 日初始训练；${longFiveMinuteResearch?.walk_forward?.fold_count ?? 0} 折 × ${longFiveMinuteResearch?.walk_forward?.test_sessions_per_fold ?? 0} 日独立测试；尾部 ${longFiveMinuteResearch?.walk_forward?.unused_trailing_sessions ?? 0} 日未进入回测（该六折流程），随后仅进入一次预注册诊断`],
        ['基准收益', formatPercent(String(longFiveMinuteAggregate.base.total_return))],
        ['修正后收益', formatPercent(String(longFiveMinuteAggregate.overlay.total_return))],
        ['相对基准', formatPercent(String(longFiveMinuteAggregate.excess_return))],
        ['基准 / 修正回撤', `${formatPercent(String(longFiveMinuteAggregate.base.maximum_drawdown))} / ${formatPercent(String(longFiveMinuteAggregate.overlay.maximum_drawdown))}`],
        ['平均暴露', longExecution ? formatPercent(String(longExecution.mean_exposure)) : '—'],
        ['减仓标的-Bar', longExecution ? formatPercent(String(longExecution.reduced_exposure_ratio)) : '—'],
        ['平均换手 / 标的', longExecution ? formatNumber(String(longExecution.mean_turnover_per_symbol)) : '—'],
        ['累计成本扣减 / 标的', longExecution ? `${formatPercent(String(longExecution.mean_transaction_cost_per_symbol))} 初始权益单位` : '—'],
        ['复权边界', longFiveMinuteResearch?.minute_dataset?.future_adjustment_knowledge_possible ? '前复权，可能含未来复权知识；非 PIT' : '—'],
        ['外部订单', 'DISABLED'],
      ]} />}
      <Table rowKey="fold" size="small" pagination={false} dataSource={longFiveMinuteResearch?.folds ?? []} columns={[
        { title: '折', dataIndex: 'fold', width: 56 },
        { title: '冻结策略', dataIndex: 'selected_policy_id' },
        { title: '独立测试窗', render: (_: unknown, row: NonNullable<typeof longFiveMinuteResearch>['folds'][number]) => `${row.test_days[0]} 至 ${row.test_days.at(-1)}` },
        { title: '基准收益', render: (_: unknown, row: NonNullable<typeof longFiveMinuteResearch>['folds'][number]) => formatPercent(String(row.test_base.total_return)) },
        { title: '修正后收益', render: (_: unknown, row: NonNullable<typeof longFiveMinuteResearch>['folds'][number]) => formatPercent(String(row.test_overlay.total_return)) },
        { title: '相对基准', render: (_: unknown, row: NonNullable<typeof longFiveMinuteResearch>['folds'][number]) => formatPercent(String(row.test_excess_return)) },
        { title: '平均暴露', render: (_: unknown, row: NonNullable<typeof longFiveMinuteResearch>['folds'][number]) => row.execution ? formatPercent(String(row.execution.mean_exposure)) : '—' },
        { title: '减仓 Bar', render: (_: unknown, row: NonNullable<typeof longFiveMinuteResearch>['folds'][number]) => row.execution ? formatPercent(String(row.execution.reduced_exposure_ratio)) : '—' },
        { title: '换手 / 标的', render: (_: unknown, row: NonNullable<typeof longFiveMinuteResearch>['folds'][number]) => row.execution ? formatNumber(String(row.execution.mean_turnover_per_symbol)) : '—' },
        { title: '成本扣减 / 标的', render: (_: unknown, row: NonNullable<typeof longFiveMinuteResearch>['folds'][number]) => row.execution ? formatPercent(String(row.execution.mean_transaction_cost_per_symbol)) : '—' },
      ]} scroll={{ x: 1380 }} />
      <Table rowKey="cost_bps" size="small" pagination={false} dataSource={longFiveMinuteResearch?.cost_stress ?? []} columns={[
        { title: '单边成本 bps', dataIndex: 'cost_bps' },
        { title: '基准收益', render: (_: unknown, row: NonNullable<typeof longFiveMinuteResearch>['cost_stress'][number]) => formatPercent(String(row.base.total_return)) },
        { title: '修正后收益', render: (_: unknown, row: NonNullable<typeof longFiveMinuteResearch>['cost_stress'][number]) => formatPercent(String(row.overlay.total_return)) },
        { title: '相对基准', render: (_: unknown, row: NonNullable<typeof longFiveMinuteResearch>['cost_stress'][number]) => formatPercent(String(row.excess_return)) },
      ]} />
      {longReasonSummary && <Typography.Text>独立测试段重叠触发计数：{longReasonSummary}。同一标的-Bar 可以同时命中多个风险原因。</Typography.Text>}
      <Typography.Text type="secondary">这批数据只用于历史研究：当前成分股样本池非 PIT，前复权可能使用未来复权知识，免费历史源不等于获授权的实时源。即使 0 bps，修正层仍落后基准，因此保持 {longFiveMinuteResearch?.promotion ?? 'NOT_EVALUATED'}，不启动 PAPER/LIVE。</Typography.Text>
    </Card>
    <Alert
      showIcon
      type="error"
      title={reservedFiveBps ? `预注册尾部诊断被拒绝：仅通过 ${reservedPassedGateCount}/${reservedTail?.gates.length ?? 0} 个门槛` : '暂无校验通过的预注册尾部诊断'}
      description={reservedFiveBps ? `冻结候选在 5 bps 下收益 ${formatPercent(String(reservedFiveBps.candidate.total_return))}，低于原低暴露对照 ${formatPercent(String(reservedFiveBps.comparator.total_return))}；平均暴露 ${formatPercent(String(reservedFiveBps.candidate_execution.mean_exposure))} 也未达到 85%。结论固定为 ${reservedTail?.promotion ?? 'NOT_EVALUATED'}。` : '预注册签名、数据身份、前置长历史报告、明细 artifact 或门槛重算任一失败时均拒绝展示。'}
    />
    <Card title="保留尾部 8 日一次性诊断：失败后不二次调参" extra={<JsonExportButton filename="qtrade-reserved-tail-diagnostic-evidence.json" value={reservedTail ?? {}} />}>
      <ReservedTailChart report={reservedTail ?? null} />
      {reservedFiveBps && <Descriptions values={[
        ['候选 / 对照', `${reservedTail?.candidate?.policy_id ?? '—'} / ${reservedTail?.comparator?.policy_id ?? '—'}`],
        ['因果预热 / 计分', `${reservedTail?.warmup_session_count ?? 0} 日 / ${reservedTail?.test_session_count ?? 0} 日`],
        ['一次性计分窗', `${reservedTail?.test_days?.[0] ?? '—'} 至 ${reservedTail?.test_days?.at(-1) ?? '—'}`],
        ['5 bps 基准收益', formatPercent(String(reservedFiveBps.base.total_return))],
        ['5 bps 候选 / 对照', `${formatPercent(String(reservedFiveBps.candidate.total_return))} / ${formatPercent(String(reservedFiveBps.comparator.total_return))}`],
        ['基准 / 候选 / 对照回撤', `${formatPercent(String(reservedFiveBps.base.maximum_drawdown))} / ${formatPercent(String(reservedFiveBps.candidate.maximum_drawdown))} / ${formatPercent(String(reservedFiveBps.comparator.maximum_drawdown))}`],
        ['候选 / 对照平均暴露', `${formatPercent(String(reservedFiveBps.candidate_execution.mean_exposure))} / ${formatPercent(String(reservedFiveBps.comparator_execution.mean_exposure))}`],
        ['候选 / 对照换手（每标的）', `${formatNumber(String(reservedFiveBps.candidate_execution.mean_turnover_per_symbol))} / ${formatNumber(String(reservedFiveBps.comparator_execution.mean_turnover_per_symbol))}`],
        ['候选 / 对照成本扣减（每标的）', `${formatPercent(String(reservedFiveBps.candidate_execution.mean_transaction_cost_per_symbol))} / ${formatPercent(String(reservedFiveBps.comparator_execution.mean_transaction_cost_per_symbol))}`],
        ['整行业剔除胜数', `${reservedTail?.sector_win_count ?? 0} / ${reservedTail?.leave_one_sector_out.length ?? 0}`],
        ['外部订单', 'DISABLED'],
      ]} />}
      <Table rowKey="gate_id" size="small" pagination={false} dataSource={reservedTail?.gates ?? []} columns={[
        { title: '预注册门槛', render: (_: unknown, row: NonNullable<typeof reservedTail>['gates'][number]) => reservedTailGateLabels[row.gate_id] ?? row.gate_id },
        { title: '结果', render: (_: unknown, row: NonNullable<typeof reservedTail>['gates'][number]) => <Tag color={row.passed ? 'success' : 'error'}>{row.passed ? 'PASS' : 'FAIL'}</Tag> },
      ]} />
      <Table rowKey="cost_bps" size="small" pagination={false} dataSource={reservedTail?.cost_stress ?? []} columns={[
        { title: '单边成本 bps', dataIndex: 'cost_bps' },
        { title: '基准收益', render: (_: unknown, row: NonNullable<typeof reservedTail>['cost_stress'][number]) => formatPercent(String(row.base.total_return)) },
        { title: '冻结候选', render: (_: unknown, row: NonNullable<typeof reservedTail>['cost_stress'][number]) => formatPercent(String(row.candidate.total_return)) },
        { title: '原低暴露对照', render: (_: unknown, row: NonNullable<typeof reservedTail>['cost_stress'][number]) => formatPercent(String(row.comparator.total_return)) },
      ]} />
      <Table rowKey="trading_day" size="small" pagination={false} dataSource={reservedTail?.daily ?? []} columns={[
        { title: '尾部交易日', dataIndex: 'trading_day' },
        { title: '基准收益', render: (_: unknown, row: NonNullable<typeof reservedTail>['daily'][number]) => formatPercent(String(row.base.total_return)) },
        { title: '冻结候选', render: (_: unknown, row: NonNullable<typeof reservedTail>['daily'][number]) => formatPercent(String(row.candidate.total_return)) },
        { title: '原低暴露对照', render: (_: unknown, row: NonNullable<typeof reservedTail>['daily'][number]) => formatPercent(String(row.comparator.total_return)) },
      ]} />
      <Typography.Text type="secondary">这 8 日价格曾在分时线前端 QA 中被部分查看，因此不是全盲独立测试；参数、门槛和“失败后不使用同一尾部二次调参”规则均在策略引擎读取尾部前锁定。结果不能覆盖前述长历史否证，也不授权 PAPER/LIVE。</Typography.Text>
    </Card>
    <Alert showIcon type="warning" title="探索性领先者，不是 Champion" description={leader ? `${leader.experiment_id}：测试窗 ${analytics?.split?.test_start ?? '—'} 至 ${analytics?.split?.test_end ?? '—'}，收益 ${formatPercent(leader.metrics.total_return)}，Sharpe ${formatNumber(leader.metrics.sharpe)}，最大回撤 ${formatPercent(leader.metrics.maximum_drawdown)}` : '暂无已验证结果'} />
    <Card title="已验证全期策略、基准、超额收益与回撤"><EquityChart dark analytics={analytics} />{leader && <Descriptions values={[["测试窗平均敞口", formatPercent(leader.metrics.test_exposure)], ["测试窗换手", formatNumber(leader.metrics.test_turnover)], ["测试窗交易成本", formatPercent(leader.metrics.test_transaction_cost)], ["Buy-and-Hold 测试收益", benchmark ? formatPercent(benchmark.metrics.total_return) : '—']]} />}<Typography.Text type="secondary">图为完整模拟期；Buy-and-Hold 仅在记录、资金曲线、回撤 checksum 全部通过且交易日逐点对齐时展示。测试窗指标在上方单列；当前样本池非 PIT，且缺少 PAPER/SHADOW 观察，禁止晋级。</Typography.Text></Card>
    <Alert showIcon type={hasWalkForwardEvidence ? 'error' : 'info'} title={hasWalkForwardEvidence ? `三折独立样本外：${positiveFolds}/${folds.length} 折正收益` : '暂无已验证滚动样本外证据'} description={hasWalkForwardEvidence ? `聚合收益 ${formatPercent(String(aggregate?.total_return ?? ''))}，聚合 Sharpe ${formatNumber(String(aggregate?.sharpe ?? ''))}，最大回撤 ${formatPercent(String(aggregate?.maximum_drawdown ?? ''))}。这组预先分折证据优先于上方事后挑选的探索性领先者。` : '滚动样本外报告缺失或未通过校验；不能据此声称策略通过或失败。'} />
    <Card title="验证期选择后的独立测试结果"><Table rowKey="fold" size="small" columns={foldColumns} dataSource={folds} pagination={false} scroll={{ x: 920 }} locale={{ emptyText: '暂无已验证滚动样本外结果' }} />{hasWalkForwardEvidence && <Typography.Text type="secondary">候选只按验证期 Sharpe 选择，选择前不可读取对应测试窗；三个测试区间互不重叠。当前结论：{walkForward?.promotion ?? '未评估'}，不得因探索性领先者的正收益覆盖样本外败绩。</Typography.Text>}</Card>
    <Card title="已验证月度收益"><Table rowKey="month" size="small" columns={monthlyColumns} dataSource={analytics?.monthly_returns ?? []} pagination={{ pageSize: 12, showSizeChanger: false }} locale={{ emptyText: '暂无已验证月度收益' }} /><Typography.Text type="secondary">月末对上月末计算；首月相对完整模拟期首个净值点。</Typography.Text></Card>
    <Card title="研究回测模拟成交"><Alert showIcon type="info" title="以下成交均为历史回测模拟成交，不是市场真实成交" /><Table rowKey={(row) => `${row.trading_day}-${row.symbol}-${row.side}-${row.quantity}`} size="small" columns={tradeColumns} dataSource={analytics?.trades ?? []} pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 900 }} locale={{ emptyText: '暂无已验证模拟成交' }} /></Card>
    <Card title="研究回测期末持仓"><Table rowKey="symbol" size="small" columns={positionColumns} dataSource={analytics?.positions ?? []} pagination={false} locale={{ emptyText: '期末无持仓或暂无已验证持仓产物' }} /></Card>
    <Card title="研究回测风险与运行契约"><Descriptions values={analytics?.risk_summary && analytics?.run_metadata ? [["全期最大回撤", formatPercent(analytics.risk_summary.maximum_drawdown)], ["全期换手", formatNumber(analytics.risk_summary.turnover)], ["模拟交易成本", formatNumber(analytics.risk_summary.transaction_costs)], ["因果时序", "收盘决策，下一开盘执行"], ["模拟会话数", String(analytics.run_metadata.session_count)]] : []} /><Typography.Text type="secondary">这是可从资金曲线与模拟成交独立重算的历史研究风险摘要，以及 checksum 绑定的运行契约；不是当前 PAPER 账户风险。未提供逐日证据的引擎自报字段不会展示为已验证。</Typography.Text></Card>
  </Page>
}

export function PaperPage({ data, publicNode = false, onRefresh }: { data: PlatformSnapshot | null; publicNode?: boolean; onRefresh?: () => Promise<void> }) {
  const [token, setToken] = useState('')
  const [reason, setReason] = useState('manual web control')
  const [runningAction, setRunningAction] = useState<RuntimeControlAction | null>(null)
  const [controlStatus, setControlStatus] = useState<string | null>(null)
  const perform = async (action: RuntimeControlAction) => {
    if (!token.trim()) {
      setControlStatus('请输入本地控制令牌；令牌不会保存到浏览器存储。')
      return
    }
    setRunningAction(action)
    try {
      const result = await controlRuntime(action, token.trim(), reason.trim() || 'manual web control')
      setControlStatus(`${action.toUpperCase()} 已执行：${result.risk_state}`)
      await onRefresh?.()
    } catch (error) {
      setControlStatus(error instanceof Error ? error.message : '控制操作失败')
    } finally {
      setRunningAction(null)
    }
  }
  return <Page title="模拟交易" subtitle="真实内核、模拟成交"><Space orientation="vertical" style={{ width: '100%' }}><Alert type="warning" showIcon title="仅模拟成交，不发送真实订单" description="账户现金、订单与成交来自当前持久化 PAPER 运行时；没有实时市价时不计算或展示虚构权益。" /><Descriptions values={[["现金", data?.account.cash ?? '—'], ["账户权益", data?.account.equity ?? '实时市价不可用'], ["持仓数", String(data?.positions.length ?? 0)], ["订单数", String(data?.orders.length ?? 0)], ["模拟成交数", String(data?.trades.length ?? 0)], ["已实现盈亏", data?.pnl.realized ?? '—']]} />{publicNode ? <Alert showIcon type="info" title="公开节点只读" description="Vercel 不持有 SQLite 状态或控制令牌；运行控制只在 loopback 本地安全核心显示。" /> : <Card title="本地 PAPER/SHADOW 运行控制"><Space orientation="vertical" style={{ width: '100%' }}><Alert showIcon type="warning" title="控制令牌只驻留当前页面内存" description="不写入 localStorage、日志或仓库。恢复和启动仍需后端对账证明；停机与熔断需要二次确认。" /><Space wrap><Input.Password aria-label="本地控制令牌" value={token} onChange={(event) => setToken(event.target.value)} placeholder="QTRADE_CONTROL_TOKEN" style={{ width: 280 }} /><Input aria-label="控制操作原因" value={reason} onChange={(event) => setReason(event.target.value)} maxLength={200} style={{ width: 320 }} /></Space><Space wrap><Button loading={runningAction === 'recover'} onClick={() => { void perform('recover') }}>恢复并对账</Button><Button type="primary" loading={runningAction === 'start'} onClick={() => { void perform('start') }}>启动</Button><Button loading={runningAction === 'pause'} onClick={() => { void perform('pause') }}>暂停</Button><Button loading={runningAction === 'resume'} onClick={() => { void perform('resume') }}>继续</Button><Popconfirm title="确认停止当前 PAPER/SHADOW 运行时？" onConfirm={() => { void perform('stop') }}><Button danger loading={runningAction === 'stop'}>停止</Button></Popconfirm><Popconfirm title="确认触发安全熔断？恢复必须重新对账。" onConfirm={() => { void perform('kill') }}><Button danger type="primary" loading={runningAction === 'kill'}>安全熔断</Button></Popconfirm></Space>{controlStatus && <Alert showIcon type="info" title={controlStatus} />}</Space></Card>}<Table<OrderRow> rowKey="internal_order_id" dataSource={data?.orders ?? []} columns={[{ title: '内部委托ID', dataIndex: 'internal_order_id' }, { title: '策略', dataIndex: 'strategy_id' }, { title: '证券', dataIndex: 'symbol' }, { title: '方向', dataIndex: 'side' }, { title: '限价', dataIndex: 'price' }, { title: '状态', dataIndex: 'status' }, { title: '数量', dataIndex: 'quantity' }, { title: '已成交', dataIndex: 'filled_quantity' }]} pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 980 }} locale={{ emptyText: '当前 PAPER 运行时暂无委托' }} /></Space></Page>
}

export function PositionsPage({ data }: { data: PlatformSnapshot | null }) {
  const [query, setQuery] = useState('')
  const columns: ColumnsType<PositionRow> = [{ title: '证券', dataIndex: 'symbol' }, { title: '数量', dataIndex: 'quantity' }, { title: '可卖数量', dataIndex: 'sellable_quantity', render: (value: number | null) => value ?? '不可验证' }, { title: '平均成本', dataIndex: 'average_cost' }, { title: '成本口径持仓额', dataIndex: 'cost_basis_value' }, { title: '已实现盈亏', dataIndex: 'realized_pnl' }, { title: '实时估值', render: () => <Tag color="orange">市价不可用</Tag> }, { title: '可卖性日期', dataIndex: 'as_of_date' }]
  const rows = (data?.positions ?? []).filter((row) => row.symbol.includes(query.trim()))
  return <Page title="持仓" subtitle="持久化 PAPER 数量、成本与日期有效可卖性"><Alert showIcon type="warning" title="当前没有可信实时市价" description="市值、浮动盈亏和持仓权重保持 unavailable；不会用历史收盘价冒充当前估值。可卖数量仅在运行时 SellabilityLedger 可用时展示。" /><Space wrap><Input.Search aria-label="搜索持仓证券" placeholder="搜索证券代码" allowClear value={query} onChange={(event) => setQuery(event.target.value)} style={{ width: 280 }} /><JsonExportButton filename="qtrade-paper-positions.json" value={rows} /></Space><Table rowKey="symbol" columns={columns} dataSource={rows} scroll={{ x: 1000 }} locale={{ emptyText: '当前 PAPER 账户无持仓' }} /></Page>
}

export function OrdersPage({ data, publicNode = false, onRefresh }: { data: PlatformSnapshot | null; publicNode?: boolean; onRefresh?: () => Promise<void> }) {
  const [query, setQuery] = useState('')
  const [tradeToken, setTradeToken] = useState('')
  const [cancelStatus, setCancelStatus] = useState<string | null>(null)
  const [cancelling, setCancelling] = useState<string | null>(null)
  const cancelKeys = useRef<Record<string, string>>({})
  const text = query.trim().toLowerCase()
  const orders = (data?.orders ?? []).filter((row) => !text || `${row.internal_order_id} ${row.strategy_id} ${row.portfolio_id} ${row.symbol} ${row.status}`.toLowerCase().includes(text))
  const trades = (data?.trades ?? []).filter((row) => !text || `${row.trade_id} ${row.strategy_id ?? ''} ${row.portfolio_id ?? ''} ${row.symbol}`.toLowerCase().includes(text))
  const cancel = async (order: OrderRow) => {
    if (!tradeToken.trim()) {
      setCancelStatus('请输入本地交易敏感令牌；令牌不会保存到浏览器存储。')
      return
    }
    const key = cancelKeys.current[order.internal_order_id]
      ?? `web-cancel-${order.internal_order_id}-${crypto.randomUUID()}`
    cancelKeys.current[order.internal_order_id] = key
    setCancelling(order.internal_order_id)
    try {
      const result = await cancelPaperOrder(order.internal_order_id, key, tradeToken.trim())
      setCancelStatus(`${result.internal_order_id}：${result.status}`)
      await onRefresh?.()
    } catch (error) {
      setCancelStatus(error instanceof Error ? error.message : '撤单失败')
    } finally {
      setCancelling(null)
    }
  }
  const terminal = new Set(['FILLED', 'CANCELLED', 'REJECTED'])
  const orderColumns = [{ title: '内部委托ID', dataIndex: 'internal_order_id' }, { title: '策略', dataIndex: 'strategy_id' }, { title: '组合', dataIndex: 'portfolio_id' }, { title: '证券', dataIndex: 'symbol' }, { title: '方向', dataIndex: 'side' }, { title: '限价', dataIndex: 'price' }, { title: '数量', dataIndex: 'quantity' }, { title: '成交数量', dataIndex: 'filled_quantity' }, { title: '状态', dataIndex: 'status' }, { title: '更新时间', dataIndex: 'updated_at' }, ...(!publicNode ? [{ title: '操作', render: (_: unknown, row: OrderRow) => terminal.has(row.status) ? '—' : <Popconfirm title="确认撤销这笔 PAPER 委托？" onConfirm={() => { void cancel(row) }}><Button type="link" danger loading={cancelling === row.internal_order_id}>撤单</Button></Popconfirm> }] : [])]
  return <Page title="委托与成交" subtitle="持久化 PAPER 订单状态机与模拟成交"><Space orientation="vertical" style={{ width: '100%' }}><Alert showIcon type="info" title="以下成交均为 PAPER 模拟成交，不是交易所或券商回报" /><Space wrap><Input.Search aria-label="搜索委托成交" placeholder="搜索订单、策略、组合或证券" allowClear value={query} onChange={(event) => setQuery(event.target.value)} style={{ width: 340 }} /><JsonExportButton filename="qtrade-paper-orders-trades.json" value={{ orders, trades }} /></Space>{!publicNode && <Card size="small" title="本地 PAPER 撤单授权"><Space wrap><Input.Password aria-label="本地交易敏感令牌" value={tradeToken} onChange={(event) => setTradeToken(event.target.value)} placeholder="QTRADE_TRADE_TOKEN" style={{ width: 280 }} /><Typography.Text type="secondary">仅驻留当前页面内存；撤单使用每笔订单固定的幂等键。</Typography.Text></Space>{cancelStatus && <Alert showIcon type="info" title={cancelStatus} style={{ marginTop: 12 }} />}</Card>}<Card title="委托"><Table<OrderRow> rowKey="internal_order_id" dataSource={orders} columns={orderColumns} pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 1400 }} locale={{ emptyText: '当前 PAPER 运行时暂无委托' }} /></Card><Card title="模拟成交"><Table rowKey="trade_id" dataSource={trades} columns={[{ title: '成交ID', dataIndex: 'trade_id' }, { title: '策略', dataIndex: 'strategy_id' }, { title: '证券', dataIndex: 'symbol' }, { title: '方向', dataIndex: 'side' }, { title: '价格', dataIndex: 'price' }, { title: '数量', dataIndex: 'quantity' }, { title: '费用', dataIndex: 'fee' }, { title: '时间', dataIndex: 'timestamp' }, { title: '性质', render: () => <Tag color="orange">PAPER 模拟</Tag> }]} pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 1200 }} locale={{ emptyText: '当前持久化运行时暂无已应用模拟成交' }} /></Card></Space></Page>
}

export function RiskPage({ data }: { data: PlatformSnapshot | null }) {
  const risk = data?.risk
  const state = risk?.state ?? 'HALTED'
  const paperFailure = data?.availability?.failures.paper ?? null
  return <Page title="风控中心" subtitle="可从 PAPER 账本与订单重算的风险摘要">{paperFailure && <Alert showIcon type="error" title="PAPER 原子快照读取失败" description={paperFailure} />}<Alert type={state === 'HALTED' ? 'error' : 'success'} showIcon title={<Space>当前风险状态 <StatusTag value={state} prominent /></Space>} description="敞口采用持仓成本加未成交 BUY 限价名义额，不是实时市值风险；没有可信实时市价时，回撤保持 unavailable。" /><Card className="risk-grid"><Descriptions values={[['Kill State', state], ['Gateway', risk?.gateway_connected ? 'CONNECTED' : 'DISCONNECTED'], ['Reconciliation', risk?.reconciliation_status ?? 'UNKNOWN'], ['成本口径持仓额', risk?.position_cost_basis ?? '—'], ['未成交买单名义额', risk?.open_buy_notional ?? '—'], ['成本口径总敞口', risk?.gross_exposure ?? '—'], ['最大持仓集中度', risk?.largest_position_concentration === null || risk?.largest_position_concentration === undefined ? '无持仓' : formatPercent(risk.largest_position_concentration)], ['未完成订单', String(risk?.open_order_count ?? 0)], ['已实现盈亏', risk?.realized_pnl ?? '—'], ['实时回撤', '市价不可用'], ['分钟层权限', '只减仓，不加仓'], ['外部订单传输', 'DISABLED']]}/></Card><Alert showIcon type="info" title="运行边界" description="所有 PAPER 目标仍经过 Portfolio → Risk → OMS → PaperGateway；策略与分钟层都不能接触外部网关。" /></Page>
}

export function DataCenterPage({ data }: { data: PlatformSnapshot | null }) {
  const reportCount = data?.reports.length ?? 0
  const reporting = data?.reporting
  const historical = data?.market?.historical
  const publicAudit = data?.publicMinuteAudit
  const evidence = [
    { id: 'market', name: 'ETF 历史日线', status: historical ? 'VERIFIED' : 'UNAVAILABLE', count: historical?.bar_count ?? 0, identity: historical ? `${historical.data_version} · ${historical.checksum}` : '—' },
    { id: 'minute-history', name: '多资产历史分时线', status: data?.minuteResearch.minute_dataset ? 'VERIFIED' : 'UNAVAILABLE', count: data?.minuteResearch.minute_dataset?.bar_count ?? 0, identity: data?.minuteResearch.minute_dataset ? `${data.minuteResearch.minute_dataset.data_version} · ${data.minuteResearch.minute_dataset.checksum}` : '—' },
    { id: 'minute-backtest', name: '分钟因果修正回测', status: data?.minuteResearch.selected ? 'VERIFIED' : 'UNAVAILABLE', count: data?.minuteResearch.candidates.length ?? 0, identity: data?.minuteResearch.promotion ?? '—' },
    { id: 'five-minute-history', name: '40 日原生 5 分钟分时线', status: data?.fiveMinuteResearch?.minute_dataset ? 'VERIFIED' : 'UNAVAILABLE', count: data?.fiveMinuteResearch?.minute_dataset?.bar_count ?? 0, identity: data?.fiveMinuteResearch?.minute_dataset ? `${data.fiveMinuteResearch.minute_dataset.data_version} · ${data.fiveMinuteResearch.minute_dataset.checksum}` : '—' },
    { id: 'five-minute-walk-forward', name: '5 分钟滚动样本外否证', status: data?.fiveMinuteResearch?.folds.length ? 'VERIFIED' : 'UNAVAILABLE', count: data?.fiveMinuteResearch?.folds.length ?? 0, identity: data?.fiveMinuteResearch?.promotion ?? '—' },
    { id: 'long-five-minute-history', name: '368 日 BaoStock 原生 5 分钟数据', status: data?.longFiveMinuteResearch?.minute_dataset ? 'VERIFIED' : 'UNAVAILABLE', count: data?.longFiveMinuteResearch?.minute_dataset?.bar_count ?? 0, identity: data?.longFiveMinuteResearch?.minute_dataset ? `${data.longFiveMinuteResearch.minute_dataset.data_version} · ${data.longFiveMinuteResearch.minute_dataset.checksum}` : '—' },
    { id: 'long-five-minute-walk-forward', name: '240 日长历史滚动样本外否证', status: data?.longFiveMinuteResearch?.folds.length ? 'VERIFIED' : 'UNAVAILABLE', count: data?.longFiveMinuteResearch?.folds.length ?? 0, identity: data?.longFiveMinuteResearch?.promotion ?? '—' },
    { id: 'intraday-factor-long-window', name: '120 日三连阳因子稳健性否证', status: data?.intradayFactorRobustness?.report_id ? 'VERIFIED' : 'UNAVAILABLE', count: data?.intradayFactorRobustness?.summary?.fold_count ?? 0, identity: `${data?.intradayFactorRobustness?.result_preview_hash ?? '—'} · ${data?.intradayFactorRobustness?.promotion ?? '—'}` },
    { id: 'reserved-tail-diagnostic', name: '尾部 8 日预注册一次性诊断', status: data?.reservedTailDiagnostic?.five_bps ? 'VERIFIED' : 'UNAVAILABLE', count: data?.reservedTailDiagnostic?.gates.length ?? 0, identity: data?.reservedTailDiagnostic?.promotion ?? '—' },
    { id: 'development-policy-search', name: '360 日开发区分钟策略搜索', status: data?.developmentPolicySearch?.candidate_count === 4 ? 'VERIFIED' : 'UNAVAILABLE', count: data?.developmentPolicySearch?.candidate_count ?? 0, identity: `${data?.developmentPolicySearch?.observed_leader?.policy_id ?? '—'} · ${data?.developmentPolicySearch?.promotion ?? '—'}` },
    { id: 'forward-source-coverage', name: '75 股票 BaoStock 前向来源覆盖', status: data?.forwardSourceCoverage?.classification === 'SOURCE_COVERAGE_DIAGNOSTIC_NOT_FORWARD_EVIDENCE' ? 'VERIFIED' : 'UNAVAILABLE', count: data?.forwardSourceCoverage?.ready_symbol_count ?? 0, identity: `${data?.forwardSourceCoverage?.ready_symbol_count ?? 0}/${data?.forwardSourceCoverage?.required_symbol_count ?? 75} READY · ${data?.forwardSourceCoverage?.status ?? 'UNAVAILABLE'}` },
    { id: 'forward-source-coverage-history', name: 'BaoStock 来源覆盖发布序列', status: data?.forwardSourceCoverageHistory?.classification === 'SIGNED_SOURCE_COVERAGE_HISTORY_NOT_FORWARD_EVIDENCE' ? 'VERIFIED' : 'UNAVAILABLE', count: data?.forwardSourceCoverageHistory?.checkpoint_count ?? 0, identity: `${data?.forwardSourceCoverageHistory?.all_checkpoints_monotonic ? 'MONOTONIC' : 'REVIEW'} · ${(data?.forwardSourceCoverageHistory?.checkpoints ?? []).map((checkpoint) => checkpoint.ready_symbol_count).join(' → ') || '—'} READY` },
    { id: 'public-minute-audit', name: '公网长历史分时可用性审计', status: publicAudit?.all_valid ? 'VERIFIED' : 'UNAVAILABLE', count: (publicAudit?.first_round?.request_count ?? 0) + (publicAudit?.repeat_round?.request_count ?? 0), identity: publicAudit?.dataset_checksum ?? '—' },
    { id: 'intraday-observation', name: '15 行业代表股当日分钟点时观测', status: data?.intradayObservation?.classification?.startsWith('FREE_HISTORICAL_SOURCE_') ? 'VERIFIED' : 'UNAVAILABLE', count: data?.intradayObservation?.same_day_symbol_count ?? 0, identity: `${data?.intradayObservation?.session_phase ?? 'UNAVAILABLE'} · ${data?.intradayObservation?.evidence_sha256 ?? '—'}` },
    { id: 'intraday-breadth', name: '75 股票全板块当日分钟宽度', status: data?.intradayBreadth?.classification?.startsWith('FREE_HISTORICAL_SOURCE_') ? 'VERIFIED' : 'UNAVAILABLE', count: data?.intradayBreadth?.same_day_symbol_count ?? 0, identity: `${data?.intradayBreadth?.session_phase ?? 'UNAVAILABLE'} · ${data?.intradayBreadth?.evidence_sha256 ?? '—'}` },
    { id: 'intraday-continuity', name: '当日分钟多检查点连续性', status: data?.intradayContinuity?.classification?.startsWith('SIGNED_INTRADAY_SOURCE_') ? 'VERIFIED' : 'UNAVAILABLE', count: data?.intradayContinuity?.checkpoint_count ?? 0, identity: `${data?.intradayContinuity?.all_checkpoints_monotonic ? 'MONOTONIC' : 'REVIEW'} · ${data?.intradayContinuity?.evidence_sha256 ?? '—'}` },
    { id: 'experiments', name: '基线实验与 artifacts', status: data?.experiments.experiments.length ? 'VERIFIED' : 'UNAVAILABLE', count: data?.experiments.experiments.length ?? 0, identity: data?.experiments.data_version ?? '—' },
    { id: 'walk-forward', name: '滚动样本外', status: data?.walkForward.result.folds.length ? 'VERIFIED' : 'UNAVAILABLE', count: data?.walkForward.result.folds.length ?? 0, identity: data?.walkForward.data_version ?? '—' },
    { id: 'robustness', name: '拒绝后稳健性', status: data?.robustness.result.scenario_metrics.length ? 'VERIFIED' : 'UNAVAILABLE', count: data?.robustness.result.scenario_metrics.length ?? 0, identity: data?.robustness.promotion ?? '—' },
    { id: 'factor-search', name: '因子搜索', status: data?.factorSearch.factor_diagnostics.length ? 'VERIFIED' : 'UNAVAILABLE', count: data?.factorSearch.factor_diagnostics.length ?? 0, identity: data?.factorSearch.promotion ?? '—' },
    { id: 'factor-robustness', name: '因子重复鲁棒性', status: data?.robustFactorSearch.candidate_summaries.length ? 'VERIFIED' : 'UNAVAILABLE', count: data?.robustFactorSearch.candidate_summaries.length ?? 0, identity: data?.robustFactorSearch.promotion ?? '—' },
    { id: 'stock-sector', name: '75 股票多板块迁移', status: data?.stockSectorTransfer.data?.symbol_count === 75 ? 'VERIFIED' : 'UNAVAILABLE', count: data?.stockSectorTransfer.data?.symbol_count ?? 0, identity: data?.stockSectorTransfer.promotion ?? '—' },
    { id: 'minute', name: '分钟前向会话', status: data?.minuteOverlay.readiness ?? 'UNAVAILABLE', count: data?.minuteOverlay.ready_symbol_count ?? 0, identity: (data?.minuteOverlay.reasons ?? []).join(' / ') || '—' },
  ]
  const reportingConfigured = reporting?.readiness === 'CONFIGURED'
  const reportingIdentity = reportingConfigured
    ? `${reporting?.data_version} · ${(reporting?.strategy_versions ?? []).join('、')}`
    : '需要在本地进程启动时绑定数据版本、策略版本和已验证开市日；网页调用方不能提供这些身份。'
  return <Page title="数据中心" subtitle="供应商、质量、存储与回放"><Space orientation="vertical" style={{ width: '100%' }}>{waiting}<Descriptions values={[['Provider', historical ? `${historical.provider} ${historical.provider_version}` : '未配置'], ['Snapshot 连接', data?.market.provider_connected ? 'CONNECTED' : 'DISCONNECTED'], ['Snapshot 就绪', data?.market.snapshot_readiness ? 'YES' : 'NO'], ['数据版本', historical?.data_version ?? '—'], ['质量状态', historical?.quality_status ?? '等待数据'], ['历史 Bar', String(historical?.bar_count ?? 0)], ['PIT 样本池', historical?.universe.point_in_time ? 'YES' : 'NO'], ['Replay', '可用'], ['日终自动报告', reportingConfigured ? reporting?.state ?? 'CONFIGURED' : 'UNCONFIGURED'], ['已验证每日报告', String(reportCount)]]}/><Alert showIcon type={reportingConfigured ? (reporting?.state === 'FAILED' ? 'error' : 'success') : 'info'} title={`PAPER 日终自动报告：${reportingConfigured ? reporting?.state : 'UNCONFIGURED'}`} description={`${reportingIdentity}${reportingConfigured ? `；Asia/Shanghai ${reporting?.cutoff} 后，仅在配置内的 SSE 开市日幂等生成。` : ''} 不触发任何外部订单。`} /><Alert showIcon type={publicAudit?.all_valid ? 'success' : 'warning'} title={publicAudit?.all_valid ? `公网分时可用性审计：${(publicAudit.first_round?.request_count ?? 0) + (publicAudit.repeat_round?.request_count ?? 0)} 次请求全部通过` : '暂无校验通过的公网分时可用性审计'} description={publicAudit?.all_valid ? `${publicAudit.sector_count ?? 0} 个行业 × ${publicAudit.sampled_trading_days?.length ?? 0} 个历史日期；强制未缓存中位 ${formatNumber(String(publicAudit.first_round?.latency_seconds.median))} 秒、P95 ${formatNumber(String(publicAudit.first_round?.latency_seconds.p95))} 秒，CDN 命中中位 ${formatNumber(String(publicAudit.repeat_round?.latency_seconds.median))} 秒、P95 ${formatNumber(String(publicAudit.repeat_round?.latency_seconds.p95))} 秒；公网写操作 ${publicAudit.public_write_boundary?.request_count ?? 0}/${publicAudit.public_write_boundary?.request_count ?? 0} 已阻断。审计时间 ${publicAudit.generated_at ?? '—'}；这是可访问性证据，不是实时行情授权。` : '签名、数据 checksum、15 行业抽样、48 点完整会话、Asia/Shanghai 交易时钟、CDN 重复命中或公网只读边界任一未通过。'} /><Card title="校验通过的证据库存" extra={<JsonExportButton filename="qtrade-evidence-inventory.json" value={evidence} />}><Table rowKey="id" size="small" pagination={false} dataSource={evidence} columns={[{ title: '证据链', dataIndex: 'name' }, { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color={value === 'VERIFIED' ? 'green' : 'orange'}>{value}</Tag> }, { title: '条目', dataIndex: 'count' }, { title: '版本 / checksum / 阻塞原因', dataIndex: 'identity' }]} scroll={{ x: 980 }} /></Card><Card title="PAPER 每日报告历史"><Table rowKey="report_id" dataSource={data?.reports ?? []} columns={[{ title: '交易日', dataIndex: 'trading_day' }, { title: '报告ID', dataIndex: 'report_id' }, { title: '数据版本', dataIndex: 'data_version' }, { title: '策略版本', dataIndex: 'strategy_versions', render: (versions: string[]) => versions.join('、') }, { title: '生成时间', dataIndex: 'generated_at' }, { title: '完整性', render: () => <Tag color="green">SHA-256 已验证</Tag> }]} pagination={{ pageSize: 10, showSizeChanger: false }} scroll={{ x: 1050 }} locale={{ emptyText: reportingConfigured ? '等待首份日终报告生成并通过哈希与 schema 校验' : '暂无经哈希与 schema 校验的 PAPER 每日报告；自动日终链路尚未绑定可信运行时身份' }} /></Card></Space></Page>
}

export function MonitorPage({ data }: { data: PlatformSnapshot | null }) {
  const [auditQuery, setAuditQuery] = useState("");
  const [auditSeverity, setAuditSeverity] = useState<string | undefined>();
  const [auditEventType, setAuditEventType] = useState<string | undefined>();
  const entries = Object.entries(data?.metrics ?? {});
  const audit = data?.audit ?? [];
  const auditAvailable = data?.audit != null;
  const alerts = audit.filter(
    (event) => event.severity === "ERROR" || event.severity === "CRITICAL",
  );
  const eventTypes = [...new Set(audit.map((event) => event.event_type))]
    .sort()
    .map((value) => ({ value, label: value }));
  const normalizedAuditQuery = auditQuery.trim().toLowerCase();
  const filteredAudit = audit.filter((event) =>
    (!auditSeverity || event.severity === auditSeverity)
    && (!auditEventType || event.event_type === auditEventType)
    && (!normalizedAuditQuery || `${event.event_type} ${event.message} ${event.order_id ?? ""}`.toLowerCase().includes(normalizedAuditQuery))
  );
  const minute = data?.minuteOverlay;
  const minuteRows = minute?.symbols ?? [];
  return (
    <Page title="系统监控" subtitle="服务健康、延迟、队列与持久化">
      <PublicResearchObservations mode="monitor" />
      <Space orientation="vertical" style={{ width: "100%" }}>
        <Card extra={<Button href={prometheusMetricsUrl} target="_blank" rel="noreferrer">Prometheus 指标</Button>}>
          <Descriptions
            values={[
              ["Service Health", data?.health.healthy ? "HEALTHY" : "DEGRADED"],
              ["Deployment", data?.deployment.status ?? "UNAVAILABLE"],
              ["Git Commit", data?.deployment.git_commit_sha?.slice(0, 12) ?? "—"],
              ["Git Branch", data?.deployment.git_commit_ref ?? "—"],
              ["Vercel Environment", data?.deployment.target_environment ?? "—"],
              ["Event Queue", String(data?.metrics.event_queue_depth ?? 0)],
              ["Market Data Lag", String(data?.metrics.market_data_lag_seconds ?? "—")],
              ["Order Rate", String(data?.metrics.orders_per_minute ?? 0)],
              ["Reject Rate", String(data?.metrics.reject_rate ?? 0)],
              ["Fill Rate", String(data?.metrics.fill_rate ?? 0)],
              ["Order Latency", String(data?.metrics.last_order_latency_seconds ?? "—")],
              ["Gateway", data?.metrics.gateway_connected ? "CONNECTED" : "DISCONNECTED"],
              ["Reconciliation", String(data?.metrics.reconciliation_status ?? "UNKNOWN")],
              ["Monitoring", data?.metrics.monitoring_degraded ? "DEGRADED" : "NORMAL"],
              ["Runtime Owner", "SQLite 单进程租约"],
              ["指标数量", String(entries.length)],
              [
                "持久化告警",
                auditAvailable ? String(alerts.length) : "不可用",
              ],
            ]}
          />
        </Card>
        <Alert
          showIcon
          type={minute?.readiness === "READY" ? "success" : "warning"}
          title={`分钟修正：${minute?.readiness ?? "UNAVAILABLE"}`}
          description={
            minute?.readiness === "READY"
              ? `${minute.ready_symbol_count}/${minute.symbol_count} 个标的已完成 1 分钟预热，只允许降低日线基础仓位。`
              : "尚未接入授权实时 Tick 或分钟状态未完全预热；不会产生分钟 PAPER 目标。"
          }
        />
        <Card title="分钟级时间序列修正">
          <Descriptions
            values={[
              ["行情 Provider", minute?.provider ?? "未配置"],
              ["分钟周期", minute?.bar_interval ?? "1m"],
              [
                "Provider 连接",
                minute?.provider_connected ? "CONNECTED" : "DISCONNECTED",
              ],
              ["监控标的", String(minute?.symbol_count ?? 0)],
              ["已就绪标的", String(minute?.ready_symbol_count ?? 0)],
              ["仓位权限", "只减仓，不加仓"],
              ["执行边界", "封闭分钟线后的下一 Tick"],
              [
                "实盘订单",
                minute?.external_order_transmission ? "允许" : "禁止",
              ],
              ["晋级状态", minute?.promotion ?? "NOT_ELIGIBLE"],
            ]}
          />
          <Table
            rowKey="symbol"
            size="small"
            dataSource={minuteRows}
            columns={[
              { title: "标的", dataIndex: "symbol" },
              {
                title: "就绪",
                dataIndex: "ready",
                render: (value: boolean) => (value ? "READY" : "WARMING"),
              },
              { title: "基础目标", dataIndex: "base_target_quantity" },
              { title: "修正目标", dataIndex: "corrected_target_quantity" },
              { title: "缩放倍数", dataIndex: "multiplier" },
              { title: "分钟波动", dataIndex: "minute_volatility", render: (value: string | null) => value ?? "—" },
              { title: "日内回撤", dataIndex: "intraday_drawdown", render: (value: string | null) => value ?? "—" },
              { title: "冷却 Bars", dataIndex: "cooldown_bars_remaining", render: (value: number | null) => value ?? "—" },
              {
                title: "封闭分钟",
                dataIndex: "last_bar_timestamp",
                render: (value: string | null) => value ?? "—",
              },
              { title: "原因", dataIndex: "reasons", render: (values: string[]) => values.join(" / ") || "—" },
            ]}
            pagination={{ pageSize: 10, showSizeChanger: false }}
            scroll={{ x: 920 }}
            locale={{ emptyText: "暂无已授权的分钟监控标的" }}
          />
          <Typography.Text type="secondary">
            分钟层不在盘中重搜因子参数；目标版本按交易日冻结，弱趋势、分钟波动、日内回撤及恢复冷却只负责收缩风险。
          </Typography.Text>
        </Card>
        {!auditAvailable && (
          <Alert
            showIcon
            type="error"
            title="持久化审计历史不可用"
            description="其他运行时快照仍可查看，但不能据此声称当前没有告警。"
          />
        )}
        <Card title="最近持久化审计与告警">
          <Space wrap style={{ marginBottom: 16 }}>
            <Input.Search
              aria-label="搜索审计事件"
              placeholder="搜索事件、消息或订单 ID"
              allowClear
              value={auditQuery}
              onChange={(event) => setAuditQuery(event.target.value)}
              style={{ width: 320 }}
            />
            <Select
              aria-label="筛选审计级别"
              allowClear
              placeholder="全部级别"
              value={auditSeverity}
              onChange={setAuditSeverity}
              options={["INFO", "WARNING", "ERROR", "CRITICAL"].map((value) => ({ value, label: value }))}
              style={{ width: 150 }}
            />
            <Select
              aria-label="筛选事件类型"
              allowClear
              placeholder="全部事件"
              value={auditEventType}
              onChange={setAuditEventType}
              options={eventTypes}
              style={{ width: 210 }}
            />
            <Tag color="blue">显示 {filteredAudit.length}/{audit.length}</Tag>
            <JsonExportButton
              filename="qtrade-filtered-audit.json"
              value={auditAvailable ? filteredAudit : null}
              label="导出当前审计视图"
            />
          </Space>
          <Table
            rowKey={(row) =>
              `${row.timestamp}-${row.event_type}-${row.order_id ?? ""}`
            }
            size="small"
            dataSource={filteredAudit}
            columns={[
              { title: "时间", dataIndex: "timestamp" },
              { title: "级别", dataIndex: "severity" },
              { title: "事件", dataIndex: "event_type" },
              {
                title: "订单",
                dataIndex: "order_id",
                render: (value: string | null) => value ?? "—",
              },
              { title: "消息", dataIndex: "message" },
            ]}
            pagination={{ pageSize: 10, showSizeChanger: false }}
            scroll={{ x: 900 }}
            locale={{
              emptyText: auditAvailable
                ? "暂无持久化审计事件"
                : "审计历史不可用",
            }}
          />
          <Typography.Text type="secondary">
            最多显示最近 500 条严格校验的 SQLite 审计记录；ERROR/CRITICAL
            计入告警。SQLite 表不是哈希链或 WORM 存储。
          </Typography.Text>
        </Card>
      </Space>
    </Page>
  );
}

function Descriptions({ values }: { values: [string, string][] }) {
  return <div className="description-grid">{values.map(([label, value]) => <div key={label}><span>{label}</span><strong>{value}</strong></div>)}</div>
}

function Page({ title, subtitle, children }: { title: string; subtitle: string; children: React.ReactNode }) {
  return <Space orientation="vertical" size={18} style={{ width: '100%' }}><div><Typography.Title level={2}>{title}</Typography.Title><Typography.Text type="secondary">{subtitle}</Typography.Text></div>{children}</Space>
}

function formatNumber(value?: string) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toFixed(3) : '—'
}

function formatPercent(value?: string) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `${(parsed * 100).toFixed(2)}%` : '—'
}

function renderSignedReturn(value: string | null) {
  if (value === null) return '—'
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return '—'
  return <Tag color={parsed > 0 ? 'red' : parsed < 0 ? 'green' : 'default'}>{formatPercent(value)}</Tag>
}

function renderSignedBasisPoints(value: string | null) {
  if (value === null) return '—'
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return '—'
  return <Tag color={parsed > 0 ? 'red' : parsed < 0 ? 'green' : 'default'}>{formatBasisPoints(value)}</Tag>
}

function formatInteger(value?: string) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? Math.round(parsed).toLocaleString('zh-CN') : '—'
}
