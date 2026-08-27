import type { IntradayFactorBacktest } from '../types'

const BLOCK_COUNT = 4
const CONTRIBUTION_SHARE_THRESHOLD = 0.6
const TOLERANCE = 1e-9
const CRITERIA = [
  'minimum_four_contiguous_blocks',
  'every_block_has_signal_session',
  'positive_block_ratio_at_least_75pct',
  'maximum_absolute_block_contribution_share_below_0_60',
] as const

export function matchesIntradayStability(response: IntradayFactorBacktest): boolean {
  const stability = response.stability_validation
  const days = response.selected_trading_days
  if (stability.classification !== 'CONTIGUOUS_SESSION_BLOCK_FACTOR_STABILITY_DIAGNOSTIC'
    || days.length < BLOCK_COUNT
    || !Array.isArray(stability.blocks)
    || stability.blocks.length !== BLOCK_COUNT
    || stability.block_count !== BLOCK_COUNT) return false

  const baseSize = Math.floor(days.length / BLOCK_COUNT)
  const largerBlockCount = days.length % BLOCK_COUNT
  const blockSizes = Array.from(
    { length: BLOCK_COUNT },
    (_, index) => baseSize + Number(index < largerBlockCount),
  )
  let offset = 0
  let observationCount = 0
  let signalCount = 0
  let signalSessionCount = 0
  const means: number[] = []
  for (const [index, block] of stability.blocks.entries()) {
    const size = blockSizes[index]
    const blockDays = days.slice(offset, offset + size)
    const curve = response.backtest.curve.slice(offset, offset + size)
    const mean = finiteNumber(block.mean_session_conditional_excess_return)
    const expectedSignalCount = curve.reduce((total, point) => total + point.signal_count, 0)
    const expectedSignalSessionCount = curve.filter((point) => point.signal_count > 0).length
    if (mean === null
      || block.block_id !== `TS-${String(index + 1).padStart(2, '0')}`
      || block.start !== blockDays[0]
      || block.end !== blockDays[blockDays.length - 1]
      || block.session_count !== size
      || block.observation_count !== size * response.symbol_count
      || block.signal_count !== expectedSignalCount
      || block.signal_session_count !== expectedSignalSessionCount
      || (expectedSignalSessionCount === 0 && !approximatelyEqual(mean, 0))) return false
    observationCount += block.observation_count
    signalCount += block.signal_count
    signalSessionCount += block.signal_session_count
    means.push(mean)
    offset += size
  }

  const eventMean = finiteNumber(response.event_summary.mean_session_conditional_excess_return)
  const reportedWorst = finiteNumber(stability.worst_block_mean_conditional_excess_return)
  const reportedMaximumShare = finiteNumber(stability.maximum_absolute_block_contribution_share)
  const reportedThreshold = finiteNumber(stability.contribution_share_threshold)
  if (eventMean === null || reportedWorst === null || reportedMaximumShare === null
    || reportedThreshold === null || reportedThreshold !== CONTRIBUTION_SHARE_THRESHOLD) return false

  const weightedMean = signalSessionCount === 0 ? 0 : stability.blocks.reduce(
    (total, block, index) => total + means[index] * block.signal_session_count,
    0,
  ) / signalSessionCount
  const positiveBlockCount = means.filter((value) => value > 0).length
  const absoluteSum = means.reduce((total, value) => total + Math.abs(value), 0)
  const maximumShare = absoluteSum === 0
    ? 0
    : Math.max(...means.map(Math.abs)) / absoluteSum
  const expectedCriteria: Record<string, boolean> = {
    minimum_four_contiguous_blocks: true,
    every_block_has_signal_session: stability.blocks.every((block) => block.signal_session_count > 0),
    positive_block_ratio_at_least_75pct: positiveBlockCount * 4 >= BLOCK_COUNT * 3,
    maximum_absolute_block_contribution_share_below_0_60: maximumShare < CONTRIBUTION_SHARE_THRESHOLD,
  }
  const criteriaKeys = Object.keys(stability.criteria)
  return observationCount === response.event_summary.observation_count
    && signalCount === response.event_summary.signal_count
    && signalSessionCount === response.event_summary.clustered_signal_session_count
    && approximatelyEqual(weightedMean, eventMean)
    && stability.positive_block_count === positiveBlockCount
    && approximatelyEqual(reportedWorst, Math.min(...means))
    && approximatelyEqual(reportedMaximumShare, maximumShare)
    && sameStrings(criteriaKeys, CRITERIA)
    && criteriaKeys.every((key) => stability.criteria[key] === expectedCriteria[key])
    && stability.passed === Object.values(expectedCriteria).every(Boolean)
}

function finiteNumber(value: unknown): number | null {
  if (typeof value !== 'string' || value.trim() === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function approximatelyEqual(left: number, right: number): boolean {
  return Math.abs(left - right) <= TOLERANCE * Math.max(1, Math.abs(left), Math.abs(right))
}

function sameStrings(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index])
}
