import type { IntradayDirectionalBreadth } from '../types'

export function formatBasisPoints(value?: string) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return '—'
  const basisPoints = parsed * 10_000
  return `${basisPoints > 0 ? '+' : ''}${basisPoints.toFixed(2)} bp`
}

export function formatDirectionalDelta(
  current?: IntradayDirectionalBreadth,
  previous?: IntradayDirectionalBreadth,
) {
  if (!current) return '未记录'
  if (!previous) return '首次记录'
  const participationDelta = current.above_open_count - previous.above_open_count
  const returnDelta = Number(current.equal_weight_session_return) - Number(previous.equal_weight_session_return)
  if (!Number.isFinite(returnDelta)) return '—'
  return `${participationDelta > 0 ? '+' : ''}${participationDelta} 股 · ${formatBasisPoints(String(returnDelta))}`
}
