import type { IntradayFactorBacktest } from '../types'
import { canonicalJson, sha256Hex } from './strategyIdentity'

export async function matchesIntradayPreviewHash(
  response: IntradayFactorBacktest,
): Promise<boolean> {
  if (!/^[a-f0-9]{64}$/.test(response.preview_hash)) return false
  const unsigned = { ...response } as Record<string, unknown>
  delete unsigned.preview_hash
  const digest = await sha256Hex(canonicalJson(unsigned))
  return digest !== null && digest === response.preview_hash
}
