import { canonicalJson, sha256Hex } from './strategyIdentity'

interface PreviewHashedResponse {
  preview_hash: string
}

export async function matchesResponsePreviewHash(
  response: PreviewHashedResponse,
): Promise<boolean> {
  if (!/^[a-f0-9]{64}$/.test(response.preview_hash)) return false
  const unsigned = { ...response } as Record<string, unknown>
  delete unsigned.preview_hash
  const digest = await sha256Hex(canonicalJson(unsigned))
  return digest !== null && digest === response.preview_hash
}

export const matchesIntradayPreviewHash = matchesResponsePreviewHash
