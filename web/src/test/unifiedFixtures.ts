import type { UnifiedBarSeries, UnifiedSecurities, UnifiedSecurity, UnifiedSummary } from '../types'

export const universeVersion = 'a'.repeat(64)
export const publicationVersion = 'b'.repeat(64)
export const catalogHash = 'c'.repeat(64)
export const security: UnifiedSecurity = {
  instrument_id: 'SZSE.000063', symbol: '000063', exchange: 'SZSE', name: '中兴通讯', security_type: 'STOCK',
  status: 'ACTIVE_OBSERVED', listing_date: '1997-11-18', delisting_date: null, available_at: null,
  in_target: true, legacy_sources: [], issues: [], source: [],
  frequencies: {
    day: { planned: true, state: 'PLANNED', issues: [], observed_start: null, observed_end: null, bar_count: 0 },
    '5min': { planned: true, state: 'PLANNED', issues: [], observed_start: null, observed_end: null, bar_count: 0 },
  },
  incremental: {
    day: { planned: true, state: 'CONTENT_COVERAGE_VERIFIED', issues: [], observed_start: '2026-10-26', observed_end: '2026-10-26', bar_count: 1, last_success_at: '2026-10-26T08:00:00Z' },
    '5min': { planned: true, state: 'CONTENT_COVERAGE_VERIFIED', issues: [], observed_start: '2026-10-27T01:35:00Z', observed_end: '2026-10-27T01:40:00Z', bar_count: 2 },
  },
}
export function unifiedSummary(): UnifiedSummary {
  return {
    schema_version: 'qtrade.unified-data.v1', built_at: new Date().toISOString(), snapshot_age_seconds: 0,
    live_trading_enabled: false, evidence_sha256: catalogHash,
    incremental_freshness: {
      day: { status: 'PARTIAL', ready_symbols: 1497, required_symbols: 1500, expected_end: '2026-10-26T07:00:00Z' },
      '5min': { status: 'PARTIAL', ready_symbols: 0, required_symbols: 1500, expected_end: '2026-10-27T01:50:00Z' },
    },
    master: { master_count: 5500, target_count: 1500, target_actual: 1500, legacy_union_count: 1032,
      legacy_active_retained: 1032, top_up_count: 468, target_by_type: { STOCK: 1470, ETF: 30 },
      vendor_master_observed_at: '2026-10-25T00:00:00Z', universe_version: universeVersion,
      frequency_counts: { day: { PLANNED: 1500 }, '5min': { PLANNED: 1500 } },
      collection: { state: 'RUNNING', updated_at: '2026-10-27T01:50:00Z', attempted_symbols: 1500,
        data_ready_events: 1000, elapsed_seconds: 20, stage_counts: {}, task_counts: {} } },
  }
}
export function unifiedSecurities(): UnifiedSecurities {
  return { universe_version: universeVersion, catalog_sha256: catalogHash, built_at: new Date().toISOString(),
    total: 1500, rows: [structuredClone(security)], live_trading_enabled: false }
}
export function unifiedBars(frequency: 'day' | '5min' = 'day', symbol = '000063'): UnifiedBarSeries {
  const timestamp = frequency === 'day' ? '2026-10-26T07:00:00Z' : '2026-10-27T01:40:00Z'
  return { schema_version: 'qtrade.unified-market-series.v1', publication_available: true,
    live_trading_enabled: false, external_order_transmission: false, state: 'AVAILABLE', symbol,
    instrument_id: symbol === '000063' ? security.instrument_id : `SZSE.${symbol}`, security_type: 'STOCK',
    frequency, universe_version: universeVersion, index_id: publicationVersion, version: publicationVersion,
    manifest_sha256: 'd'.repeat(64), published_at: new Date().toISOString(), checked_at: new Date().toISOString(),
    window_start: '2026-10-01', window_end: '2026-10-27', available_start: timestamp, available_end: timestamp,
    actual_end: timestamp, expected_end: timestamp, price_basis: 'SINA_NATIVE_ADJUSTMENT_UNCERTIFIED', freshness: 'FRESH',
    gaps: { missing_timestamps: [], requested_outside_window: false }, limitations: ['历史可得性未认证'],
    count: 1, total_count: 1, truncated: false,
    bars: [{ symbol, exchange: 'SZSE', frequency, timestamp, trading_day: timestamp.slice(0, 10),
      open: '10', high: '12', low: '9', close: '11', volume: '100', amount: null, vwap: null,
      amount_basis: 'UNAVAILABLE', price_basis: 'SINA_NATIVE_ADJUSTMENT_UNCERTIFIED', historical_available_at: null }],
  }
}
