import type { UnifiedBarSeries, UnifiedSecurities, UnifiedSummary } from '../types'

export function chinaTime(value: string | null | undefined): string {
  if (!value) return '未取得'
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value
  if (!Number.isFinite(Date.parse(value))) return '时间无效'
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit',
    day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hour12: false }).format(new Date(value))
}

export function ageSeconds(value: string | undefined, now: number): number | null {
  const age = value ? (now - Date.parse(value)) / 1000 : NaN
  return Number.isFinite(age) && age >= 0 ? Math.floor(age) : null
}

export function validateUnifiedCatalog(summary: UnifiedSummary, result: UnifiedSecurities): void {
  if (summary.live_trading_enabled !== false || result.live_trading_enabled !== false
    || summary.master.universe_version !== result.universe_version
    || summary.evidence_sha256 !== result.catalog_sha256
    || !Array.isArray(result.rows) || result.rows.length > 25
    || result.rows.some(row => !row.in_target || !/^\d{6}$/.test(row.symbol))) {
    throw new Error('统一证券池或目录身份变化，请刷新重试')
  }
}

export function validateUnifiedSeries(value: UnifiedBarSeries, selection: {
  symbol: string; instrument_id: string; exchange: string; security_type: string
  frequency: 'day' | '5min'; universe_version: string; start?: string; end?: string
}, now = Date.now()): UnifiedBarSeries {
  const decimal = (v: unknown, positive = false) => typeof v === 'string' && v.length <= 80
    && /^[+-]?(\d+(\.\d*)?|\.\d+)([eE][+-]?\d+)?$/.test(v)
    && Number.isFinite(Number(v)) && (positive ? Number(v) > 0 : Number(v) >= 0)
  const day = (v: string) => /^\d{4}-\d{2}-\d{2}$/.test(v) && Number.isFinite(Date.parse(v))
    && new Date(`${v}T00:00:00Z`).toISOString().slice(0, 10) === v
  const time = (v: string) => typeof v === 'string'
    && /^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d{1,6})?(Z|[+-]\d{2}:\d{2})$/.test(v)
    && day(v.slice(0, 10)) && Number(v.slice(11, 13)) < 24 && Number(v.slice(14, 16)) < 60
    && Number(v.slice(17, 19)) < 60 && Number.isFinite(Date.parse(v))
  const sessionClock = (v: string, tradingDay: string) => {
    if (!time(v)) return false
    const parts = new Intl.DateTimeFormat('en-CA', { timeZone: 'Asia/Shanghai', year: 'numeric', month: '2-digit',
      day: '2-digit', hour: '2-digit', minute: '2-digit', second: '2-digit', hourCycle: 'h23' }).formatToParts(new Date(v))
    const part = (kind: Intl.DateTimeFormatPartTypes) => parts.find(row => row.type === kind)?.value ?? ''
    const minute = Number(part('hour')) * 60 + Number(part('minute'))
    const fraction = v.match(/\.(\d+)/)?.[1] ?? '0'
    return `${part('year')}-${part('month')}-${part('day')}` === tradingDay && Number(part('second')) === 0
      && Number(fraction) === 0 && (selection.frequency === 'day' ? minute === 15 * 60
        : minute % 5 === 0 && ((minute >= 9 * 60 + 35 && minute <= 11 * 60 + 30)
          || (minute >= 13 * 60 + 5 && minute <= 15 * 60)))
  }
  if (value.schema_version !== 'qtrade.unified-market-series.v1' || value.publication_available !== true
    || value.live_trading_enabled !== false || value.external_order_transmission !== false
    || value.symbol !== selection.symbol || value.instrument_id !== selection.instrument_id
    || value.security_type !== selection.security_type
    || value.frequency !== selection.frequency || value.universe_version !== selection.universe_version
    || value.version !== value.index_id || !/^[a-f0-9]{64}$/.test(value.index_id)
    || value.price_basis !== 'SINA_NATIVE_ADJUSTMENT_UNCERTIFIED'
    || !['AVAILABLE', 'PARTIAL', 'NO_DATA', 'QUARANTINED', 'OUTSIDE_WINDOW'].includes(value.state)
    || !['FRESH', 'PARTIAL', 'NO_DATA', 'QUARANTINED', 'STALE_PUBLICATION'].includes(value.freshness)
    || !time(value.published_at) || !time(value.checked_at) || !time(value.expected_end)
    || !Number.isFinite(now) || Date.parse(value.checked_at) > now
    || Date.parse(value.published_at) > Date.parse(value.checked_at)
    || Date.parse(value.expected_end) > Date.parse(value.checked_at)
    || !day(value.window_start) || !day(value.window_end)
    || value.window_start > value.window_end
    || (value.actual_end !== null && (!time(value.actual_end) || Date.parse(value.actual_end) > Date.parse(value.expected_end)))
    || (value.manifest_sha256 !== null && !/^[a-f0-9]{64}$/.test(value.manifest_sha256))
    || !Array.isArray(value.bars) || value.bars.length > 2000 || value.count !== value.bars.length
    || !Number.isInteger(value.total_count) || value.total_count < value.count
    || value.truncated !== (value.total_count > value.count)
    || (['AVAILABLE', 'PARTIAL'].includes(value.state) !== (value.bars.length > 0))
    || (value.freshness === 'QUARANTINED' && value.state !== 'QUARANTINED')
    || (value.state === 'QUARANTINED' && !['QUARANTINED', 'STALE_PUBLICATION'].includes(value.freshness))
    || (value.bars.length > 0 && (value.manifest_sha256 === null || value.available_start === null
      || value.available_end === null || !time(value.available_start) || !time(value.available_end)
      || Date.parse(value.available_start) > Date.parse(value.available_end)
      || value.actual_end === null || Date.parse(value.actual_end) !== Date.parse(value.available_end)
      || Date.parse(value.available_end) > Date.parse(value.published_at)))
    || !Array.isArray(value.gaps?.missing_timestamps) || value.gaps.missing_timestamps.length > 2000
    || !value.gaps.missing_timestamps.every(time) || typeof value.gaps.requested_outside_window !== 'boolean'
    || !Array.isArray(value.limitations) || value.limitations.length > 32) {
    throw new Error('统一行情身份、版本或范围校验失败')
  }
  let previous = -Infinity
  for (const bar of value.bars) {
    const timestamp = Date.parse(bar.timestamp)
    if (bar.symbol !== selection.symbol || bar.exchange !== selection.exchange || bar.frequency !== selection.frequency
      || !sessionClock(bar.timestamp, bar.trading_day)
      || timestamp <= previous || !value.actual_end || timestamp > Date.parse(value.actual_end)
      || timestamp > Date.parse(value.published_at) || timestamp < Date.parse(value.available_start!)
      || timestamp > Date.parse(value.available_end!)
      || !day(bar.trading_day)
      || bar.trading_day < (selection.start ?? value.window_start) || bar.trading_day > (selection.end ?? value.window_end)
      || timestamp > Date.parse(value.expected_end) || bar.price_basis !== value.price_basis
      || bar.historical_available_at !== null
      || ![bar.open, bar.high, bar.low, bar.close].every(v => decimal(v, true)) || !decimal(bar.volume)
      || ![bar.open, bar.close].every(v => Number(v) >= Number(bar.low) && Number(v) <= Number(bar.high))
      || (bar.amount !== null && !decimal(bar.amount)) || (bar.vwap !== null && !decimal(bar.vwap, true))) {
      throw new Error('统一行情行内容、时钟或价格基础不匹配')
    }
    previous = timestamp
  }
  return value
}
