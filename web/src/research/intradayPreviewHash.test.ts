import { describe, expect, it } from 'vitest'
import type { IntradayFactorBacktest } from '../types'
import { matchesIntradayPreviewHash } from './intradayPreviewHash'
import { canonicalJson, sha256Hex } from './strategyIdentity'

async function fixture(): Promise<IntradayFactorBacktest> {
  const unsigned = {
    classification: 'AD_HOC_INTRADAY_FACTOR_RESEARCH_CURRENT_NON_PIT',
    factor_definition: { name: '09:30 起三根十分钟阳线', directions: ['BULLISH', 'BULLISH', 'BULLISH'] },
    event_summary: { signal_count: 192, newey_west_t_statistic: '0.6674200684352877' },
    backtest: { curve: [{ trading_day: '2026-08-01', strategy_return: '-0.01' }] },
    research_gate: { passed: false, criteria: { minimum_signal_count: true } },
    promotion: 'NOT_ELIGIBLE',
    state_mutation: false,
    external_order_transmission: false,
  }
  const digest = await sha256Hex(canonicalJson(unsigned))
  if (digest === null) throw new Error('Web Crypto SHA-256 unavailable')
  return { ...unsigned, preview_hash: digest } as unknown as IntradayFactorBacktest
}

describe('intraday preview response hash', () => {
  it('accepts the SHA-256 of the full unsigned response with Unicode content', async () => {
    expect(await matchesIntradayPreviewHash(await fixture())).toBe(true)
  })

  it('rejects a well-formed hash after any nested evidence drift', async () => {
    const drifted = await fixture()
    drifted.research_gate.criteria.minimum_signal_count = false
    expect(await matchesIntradayPreviewHash(drifted)).toBe(false)
  })

  it('rejects missing, malformed and unrelated digests', async () => {
    for (const previewHash of ['', 'not-a-hash', '0'.repeat(64)]) {
      const drifted = await fixture()
      drifted.preview_hash = previewHash
      expect(await matchesIntradayPreviewHash(drifted)).toBe(false)
    }
  })
})
