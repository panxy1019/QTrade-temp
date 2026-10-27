import { describe, expect, it } from 'vitest'
import { unifiedBars, unifiedSecurities, unifiedSummary, security, universeVersion } from '../test/unifiedFixtures'
import { validateUnifiedCatalog, validateUnifiedSeries } from './unifiedMarket'

const selection = { symbol: security.symbol, instrument_id: security.instrument_id, exchange: security.exchange,
  security_type: security.security_type, frequency: 'day' as const, universe_version: universeVersion }
const actualNow = Date.parse('2026-10-27T10:00:00Z')
function checkedSeries(frequency: 'day' | '5min' = 'day') {
  return { ...unifiedBars(frequency), published_at: '2026-10-27T09:59:00Z', checked_at: '2026-10-27T10:00:00Z' }
}
describe('unified chart admission', () => {
  it('retains no-data, partial gaps and null amount explicitly', () => {
    const value = unifiedBars()
    value.state = 'PARTIAL'; value.freshness = 'PARTIAL'; value.gaps.missing_timestamps = ['2026-10-23T07:00:00Z']
    expect(validateUnifiedSeries(value, selection).bars[0].amount).toBeNull()
    expect(validateUnifiedSeries({ ...value, state: 'NO_DATA', bars: [], count: 0, total_count: 0, actual_end: null }, selection).bars).toEqual([])
  })
  it.each(['version', 'universe_version', 'symbol', 'frequency'] as const)('rejects %s drift', key => {
    expect(() => validateUnifiedSeries({ ...unifiedBars(), [key]: 'wrong' }, selection)).toThrow(/身份/)
  })
  it.each(['NaN', 'Infinity', '-1', '0'])('rejects impossible closing price %s', close => {
    const value = unifiedBars(); value.bars[0].close = close
    expect(() => validateUnifiedSeries(value, selection)).toThrow(/行内容/)
  })
  it('rejects unordered bars and completion-clock violations', () => {
    const value = unifiedBars(); value.bars.push(value.bars[0]); value.count = 2; value.total_count = 2
    expect(() => validateUnifiedSeries(value, selection)).toThrow(/时钟/)
    value.bars.pop(); value.count = 1; value.total_count = 1; value.expected_end = '2026-10-23T07:00:00Z'
    expect(() => validateUnifiedSeries(value, selection)).toThrow(/范围/)
  })
  it('rejects bars outside the requested date window instead of silently widening it', () => {
    expect(() => validateUnifiedSeries(unifiedBars(), { ...selection, end: '2026-10-23' })).toThrow(/行内容/)
  })
  it('rejects catalog snapshots from different signed inventories', () => {
    expect(() => validateUnifiedCatalog(unifiedSummary(), { ...unifiedSecurities(), catalog_sha256: 'd'.repeat(64) })).toThrow(/身份/)
  })
  it('rejects a future-clock payload against the actual clock by default', () => {
    const value = unifiedBars()
    const tomorrow = new Date(Date.now() + 86_400_000).toISOString()
    value.checked_at = tomorrow; value.expected_end = tomorrow; value.actual_end = tomorrow
    value.available_end = tomorrow; value.bars[0].timestamp = tomorrow
    expect(() => validateUnifiedSeries(value, selection)).toThrow(/范围/)
  })
  it('binds controlled test clocks without using a publication clock as now', () => {
    const value = checkedSeries()
    expect(validateUnifiedSeries(value, selection, actualNow)).toBe(value)
    expect(() => validateUnifiedSeries(value, selection, actualNow - 1)).toThrow(/范围/)
    value.expected_end = '2026-10-27T10:00:01Z'
    expect(() => validateUnifiedSeries(value, selection, actualNow)).toThrow(/范围/)
  })
  it.each(['published_at', 'checked_at', 'expected_end', 'available_start', 'available_end'] as const)(
    'rejects timezone-free %s', key => {
      const value = checkedSeries(); value[key] = '2026-10-27T09:59:00'
      expect(() => validateUnifiedSeries(value, selection, actualNow)).toThrow(/范围/)
    })
  it('rejects timezone-free bars and a mismatched Shanghai trading day', () => {
    const value = checkedSeries()
    value.bars[0].timestamp = '2026-10-26T15:00:00'
    expect(() => validateUnifiedSeries(value, selection, actualNow)).toThrow(/行内容/)
    value.bars[0].timestamp = '2026-10-26T07:00:00Z'; value.bars[0].trading_day = '2026-10-25'
    expect(() => validateUnifiedSeries(value, selection, actualNow)).toThrow(/行内容/)
  })
  it('rejects bars that did not exist when the index was published', () => {
    const value = checkedSeries()
    value.published_at = '2026-10-26T06:59:59Z'
    expect(() => validateUnifiedSeries(value, selection, actualNow)).toThrow(/范围/)
  })
  it.each(['2026-10-27T01:31:00Z', '2026-10-27T04:00:00Z', '2026-10-27T05:00:00Z', '2026-10-27T01:40:01Z'])(
    'rejects a five-minute bar outside completed session clock %s', timestamp => {
      const value = checkedSeries('5min')
      value.bars[0].timestamp = timestamp; value.actual_end = timestamp; value.available_end = timestamp
      value.available_start = timestamp; value.expected_end = timestamp
      expect(() => validateUnifiedSeries(value, { ...selection, frequency: '5min' }, actualNow)).toThrow(/行内容/)
    })
  it('accepts offset-aware Shanghai session clocks and rejects a daily non-close timestamp', () => {
    const value = checkedSeries()
    value.bars[0].timestamp = '2026-10-26T15:00:00+08:00'
    expect(validateUnifiedSeries(value, selection, actualNow)).toBe(value)
    value.bars[0].timestamp = '2026-10-26T14:55:00+08:00'
    expect(() => validateUnifiedSeries(value, selection, actualNow)).toThrow(/行内容/)
  })
  it('binds security type and exchange to the selected catalog member', () => {
    const value = checkedSeries(); value.security_type = 'ETF'
    expect(() => validateUnifiedSeries(value, selection, actualNow)).toThrow(/身份/)
    value.security_type = 'STOCK'; value.bars[0].exchange = 'SSE'
    expect(() => validateUnifiedSeries(value, selection, actualNow)).toThrow(/行内容/)
  })
  it('requires a source manifest and available bounds for nonempty bars', () => {
    const value = checkedSeries(); value.manifest_sha256 = null
    expect(() => validateUnifiedSeries(value, selection, actualNow)).toThrow(/范围/)
    value.manifest_sha256 = 'd'.repeat(64); value.available_start = null
    expect(() => validateUnifiedSeries(value, selection, actualNow)).toThrow(/范围/)
    value.available_start = '2026-10-26T07:01:00Z'
    expect(() => validateUnifiedSeries(value, selection, actualNow)).toThrow(/范围/)
  })
  it('rejects inconsistent isolation states while retaining valid stale history', () => {
    const value = checkedSeries(); value.freshness = 'QUARANTINED'
    expect(() => validateUnifiedSeries(value, selection, actualNow)).toThrow(/范围/)
    value.state = 'QUARANTINED'
    expect(() => validateUnifiedSeries(value, selection, actualNow)).toThrow(/范围/)
    value.bars = []; value.count = 0; value.total_count = 0
    value.freshness = 'STALE_PUBLICATION'
    expect(validateUnifiedSeries(value, selection, actualNow)).toBe(value)
  })
})
