import { useEffect, useState } from 'react'
import { Alert, Button, Card, Collapse, Descriptions, Empty, Input, Select, Space, Tag, Typography } from 'antd'
import { loadUnifiedBars, loadUnifiedSecurities, loadUnifiedSummary } from '../api/client'
import { MarketHistoryChart } from '../charts/MarketHistoryChart'
import { MinuteHistoryChart } from '../charts/MinuteHistoryChart'
import { ageSeconds, chinaTime, validateUnifiedCatalog, validateUnifiedSeries } from '../research/unifiedMarket'
import type { UnifiedBarSeries, UnifiedSecurity, UnifiedSummary } from '../types'

const states: Record<string, string> = { AVAILABLE: '已取得完整窗口', FRESH: '当前时间齐备', PARTIAL: '仅部分行情', NO_DATA: '尚无行情',
  QUARANTINED: '行情被隔离', OUTSIDE_WINDOW: '超出已发布窗口', STALE_PUBLICATION: '发布已过期' }
const freshnessLabels: Record<string, string> = { FRESH: '时间齐备', PARTIAL: '未追齐当前时点', NO_DATA: '尚无完整数据',
  QUARANTINED: '数据隔离', STALE_PUBLICATION: '发布过期' }
const limitationLabels: Record<string, string> = {
  CURRENT_OBSERVED_UNIVERSE_NOT_HISTORICAL_PIT: '当前观测池存在幸存者偏差，历史时点成员资格未认证',
  VENDOR_NATIVE_PRICE_ADJUSTMENT_NOT_CERTIFIED: '使用供应商原生价格，复权口径未独立认证',
  NO_EXECUTION_OR_PAPER_ADMISSION: '行情展示不代表通过模拟交易或执行准入',
  HISTORICAL_AVAILABLE_AT_UNKNOWN: '历史可得时点未知，不能作为无前视证据',
  SOURCE_NATIVE_NOT_CROSS_VENDOR_RECONCILED: '来源尚未完成独立供应商对账',
  BOUNDED_RECENT_INCREMENTAL_WINDOW_NOT_COMPLETE_HISTORY: '仅发布近期有界窗口，长期历史需到数据中心核对',
  MISSING_BARS_NOT_FILLED_OR_INFERRED_AS_SUSPENSION: '缺失行情不补造，也不据此推断证券停牌',
}

export function UnifiedMarket() {
  const [frequency, setFrequency] = useState<'day' | '5min'>('day')
  const [query, setQuery] = useState('')
  const [summary, setSummary] = useState<UnifiedSummary | null>(null)
  const [rows, setRows] = useState<UnifiedSecurity[]>([])
  const [selection, setSelection] = useState<{ row: UnifiedSecurity; universe: string } | null>(null)
  const [series, setSeries] = useState<UnifiedBarSeries | null>(null)
  const [catalogError, setCatalogError] = useState<string | null>(null)
  const [seriesError, setSeriesError] = useState<string | null>(null)
  const [start, setStart] = useState('')
  const [end, setEnd] = useState('')
  const [range, setRange] = useState<{ start?: string; end?: string }>({})
  const [refresh, setRefresh] = useState(0)
  const [loading, setLoading] = useState(true)
  const [now, setNow] = useState(Date.now)

  useEffect(() => {
    let active = true
    let request: AbortController | null = null
    let inFlight = false
    const load = async () => {
      if (inFlight) return
      inFlight = true
      request = new AbortController()
      try {
        const [info, result] = await Promise.all([loadUnifiedSummary({ signal: request.signal }),
          loadUnifiedSecurities({ query, frequency, quality: '', coverage: 'incremental', target_only: true, offset: 0 }, { signal: request.signal })])
        validateUnifiedCatalog(info, result)
        if (active) {
          setSummary(info); setRows(result.rows); setCatalogError(null)
          setSelection(previous => {
            const row = result.rows.find(row => row.symbol === previous?.row.symbol) ?? result.rows[0]
            return row ? { row, universe: result.universe_version } : null
          })
        }
      } catch (reason) {
        request?.abort()
        if (active) { setSummary(null); setRows([]); setSelection(null); setCatalogError(reason instanceof Error ? reason.message : '证券池读取失败') }
      } finally { inFlight = false }
    }
    const debounce = window.setTimeout(() => void load(), 250)
    const timer = window.setInterval(() => void load(), 60_000)
    return () => { active = false; request?.abort(); window.clearTimeout(debounce); window.clearInterval(timer) }
  }, [query, frequency, refresh])

  const symbol = selection?.row.symbol
  const instrument = selection?.row.instrument_id
  const exchange = selection?.row.exchange
  const securityType = selection?.row.security_type
  const universe = selection?.universe
  useEffect(() => {
    let active = true
    let controller: AbortController | null = null
    let inFlight = false
    const load = async () => {
      if (inFlight) return
      if (!symbol || !instrument || !exchange || !securityType || !universe) { setSeries(null); setLoading(false); return }
      inFlight = true
      controller = new AbortController()
      setSeries(null); setSeriesError(null); setLoading(true)
      try {
        const value = await loadUnifiedBars({ symbol, frequency, ...range, limit: 2000 }, { signal: controller.signal })
        validateUnifiedSeries(value, { symbol, instrument_id: instrument, exchange, security_type: securityType,
          frequency, universe_version: universe, ...range })
        if (active) { setSeries(value); setNow(Date.now()) }
      } catch (reason) {
        if (active) setSeriesError(reason instanceof Error ? reason.message : '行情读取失败')
      } finally { inFlight = false; if (active) setLoading(false) }
    }
    void load()
    const timer = window.setInterval(() => void load(), 60_000)
    return () => { active = false; controller?.abort(); window.clearInterval(timer) }
  }, [symbol, instrument, exchange, securityType, universe, frequency, range, refresh])
  useEffect(() => { const timer = window.setInterval(() => setNow(Date.now()), 1000); return () => window.clearInterval(timer) }, [])
  const publishedAge = ageSeconds(series?.published_at, now)
  const matching = series && series.symbol === symbol && series.frequency === frequency && series.universe_version === universe
  const fresh = matching && publishedAge !== null && publishedAge <= 180 && series.freshness !== 'STALE_PUBLICATION'
  const drawable = !!matching && ['AVAILABLE', 'PARTIAL'].includes(series.state) && series.bars.length > 0
  const current = selection?.row.incremental?.[frequency]
  const delay = series?.actual_end ? Math.max(0, Math.round((Date.parse(series.expected_end) - Date.parse(series.actual_end)) / 60000)) : null
  return <Space orientation="vertical" style={{ width: '100%' }}>
    <Card title="统一目标池行情" extra={<Button onClick={() => setRefresh(n => n + 1)}>刷新统一行情</Button>}>
      <Typography.Paragraph>当前目标 {summary?.master.target_actual ?? '未取得'} / {summary?.master.target_count ?? '未取得'} 只证券。搜索覆盖当前目标池；每次最多展示 25 个搜索结果。日线保留最近 20 个交易日，五分钟保留最近 3 个交易日，未完成 Bar 不展示。</Typography.Paragraph>
      <Space wrap>
        <Input.Search aria-label="搜索行情证券" placeholder="代码或名称" allowClear onSearch={setQuery} style={{ width: 220 }} />
        <Select aria-label="行情证券" value={symbol} placeholder="选择证券" style={{ minWidth: 250 }}
          options={rows.map(row => ({ value: row.symbol, label: `${row.symbol} ${row.name} · ${row.security_type}` }))}
          onChange={value => { const row = rows.find(row => row.symbol === value); if (row && summary) setSelection({ row, universe: summary.master.universe_version }) }} />
        <Select aria-label="行情频率" value={frequency} options={[{ value: 'day', label: '日线' }, { value: '5min', label: '五分钟' }]} onChange={setFrequency} />
        <Input aria-label="行情开始日期" type="date" value={start} onChange={event => setStart(event.target.value)} style={{ width: 160 }} />
        <Input aria-label="行情结束日期" type="date" value={end} onChange={event => setEnd(event.target.value)} style={{ width: 160 }} />
        <Button disabled={!!start && !!end && start > end} onClick={() => setRange({ start: start || undefined, end: end || undefined })}>应用日期范围</Button>
        <Button onClick={() => { setStart(''); setEnd(''); setRange({}) }}>完整发布窗口</Button>
      </Space>
      {start && end && start > end && <Alert type="error" title="开始日期不能晚于结束日期" />}
      {catalogError && <Alert type="error" showIcon title={`统一证券池不可用：${catalogError}`} />}
      {!catalogError && summary && rows.length === 0 && <Empty description="当前目标池没有匹配证券；不会回退至旧数据池" />}
      {seriesError && <Alert type="error" showIcon title={`统一行情不可用：${seriesError}`} description="版本变化或接口缺失时清空图表，请刷新重试。" />}
    </Card>
    {series && <Card title={`${selection?.row.name ?? series.symbol} · ${frequency === 'day' ? '日线 K 线' : '五分钟收盘与成交量'}`}>
      <Space wrap><Tag color={drawable && series.state === 'AVAILABLE' ? 'blue' : 'orange'}>查询窗口：{states[series.state]}</Tag><Tag color={fresh && series.freshness === 'FRESH' ? 'blue' : 'orange'}>整个数据集：{fresh ? freshnessLabels[series.freshness] : '发布过期或时钟无效'}</Tag><Tag>新浪原生价格 · 复权未认证</Tag></Space>
      <Descriptions size="small" column={{ xs: 1, md: 2 }} items={[
        { key: 'actual', label: '整个数据集实际截止（上海）', children: chinaTime(series.actual_end) },
        { key: 'selected', label: '所选范围最后一根（上海）', children: chinaTime(series.bars.at(-1)?.timestamp) },
        { key: 'expected', label: '当前应覆盖至（上海）', children: chinaTime(series.expected_end) },
        { key: 'delay', label: '行情落后应覆盖时点', children: delay == null ? '尚无可计算值' : `${delay} 分钟（不是网络耗时）` },
        { key: 'collection', label: '最近成功采集（上海）', children: chinaTime(current?.last_success_at) },
        { key: 'publication', label: '行情发布（上海）', children: `${chinaTime(series.published_at)} · ${publishedAge ?? '未知'} 秒前` },
        { key: 'window', label: '可查询日期窗口', children: `${series.window_start} 至 ${series.window_end}` },
        { key: 'count', label: '当前显示 / 范围内记录', children: `${series.count} / ${series.total_count}${series.truncated ? '；仅显示最新记录' : ''}` },
        { key: 'gap', label: '请求窗口缺口', children: `${series.gaps.missing_timestamps.length} 个时间点${series.gaps.requested_outside_window ? '；部分日期超出发布窗口' : ''}` },
        { key: 'universe', label: '证券池版本', children: <Typography.Text copyable>{series.universe_version}</Typography.Text> },
        { key: 'version', label: '行情发布版本', children: <Typography.Text copyable>{series.index_id}</Typography.Text> },
      ]} />
      {!fresh && <Alert type="warning" showIcon title="行情发布已过期或时钟无效；以下仅为已验证的历史记录"
        description="保留实际行情与原始截止时间，不代表当前时间齐备，也不能据此准入研究执行。" />}
      {(!drawable || series.state === 'PARTIAL' || series.gaps.requested_outside_window || series.gaps.missing_timestamps.length > 0) && <Alert type="warning" showIcon
        title={drawable ? '只展示实际取得的已完成行情，不填补缺口' : '当前没有可展示的有效行情'}
        description={series.gaps.missing_timestamps.slice(0, 10).map(chinaTime).join('、') || '请核对发布状态与查询日期范围。'} />}
      {drawable ? frequency === 'day' ? <MarketHistoryChart series={series} /> : <MinuteHistoryChart series={{ interval: '5m', bars: series.bars }} /> : <Empty description="无有效行情图表" />}
      <Typography.Paragraph type="secondary">价格复权与历史可得性未认证；近期图表不能替代长期历史核验，也不授予交易准入。</Typography.Paragraph>
      <Collapse size="small" items={[{ key: 'source', label: '展开来源与限制明细', children: <>
        <Typography.Paragraph copyable>行情条目摘要：{series.manifest_sha256 ?? '无已验行情条目'}</Typography.Paragraph>
        {series.limitations.map(item => <Typography.Paragraph key={item}>{limitationLabels[item] ?? '来源报告的额外限制，保留原始代码供核对'}<br /><Typography.Text code>{item}</Typography.Text></Typography.Paragraph>)}
      </> }]} />
    </Card>}
    {!series && loading && <Typography.Text role="status">正在读取统一行情…</Typography.Text>}
    <Typography.Text type="secondary">成交额与 VWAP 未取得时保持空值。当前证券池有幸存者偏差，历史可得时点和复权未认证；这些行情不能直接证明策略可执行。旧 30 ETF / 75 股图表在“旧范围参考”中单独保留。</Typography.Text>
  </Space>
}
