import type { IntradayFactorBacktest } from '../types'

const TOLERANCE = 1e-9

export function matchesIntradayEventSummary(response: IntradayFactorBacktest): boolean {
  const summary = response.event_summary
  const observationCount = response.selected_trading_days.length * response.symbol_count
  const signalRate = finiteNumber(summary.signal_rate)
  const meanSignalReturn = finiteNumber(summary.mean_signal_forward_return)
  const meanUniverseReturn = finiteNumber(summary.mean_universe_forward_return)
  const conditionalExcess = finiteNumber(summary.conditional_excess_return)
  const signalHitRate = finiteNumber(summary.signal_hit_rate)
  const meanSessionExcess = finiteNumber(summary.mean_session_conditional_excess_return)
  const standardError = finiteNumber(summary.newey_west_standard_error)
  const tStatistic = finiteNumber(summary.newey_west_t_statistic)
  const confidenceLower = finiteNumber(summary.confidence_interval_95_lower)
  const confidenceUpper = finiteNumber(summary.confidence_interval_95_upper)
  const signalSessionCount = response.backtest.curve.filter((point) => point.signal_count > 0).length

  if ([
    signalRate,
    meanSignalReturn,
    meanUniverseReturn,
    conditionalExcess,
    signalHitRate,
    meanSessionExcess,
    standardError,
    tStatistic,
    confidenceLower,
    confidenceUpper,
  ].some((value) => value === null)
    || !Number.isInteger(summary.observation_count)
    || summary.observation_count !== observationCount
    || !Number.isInteger(summary.signal_count)
    || summary.signal_count < 0 || summary.signal_count > observationCount
    || !Number.isInteger(summary.positive_sector_count)
    || summary.positive_sector_count < 0
    || summary.positive_sector_count > response.sector_count
    || summary.sector_count !== response.sector_count
    || !Number.isInteger(summary.clustered_signal_session_count)
    || summary.clustered_signal_session_count !== signalSessionCount
    || summary.clustered_signal_session_count > summary.signal_count
    || !Number.isInteger(summary.newey_west_lag)
    || (signalHitRate as number) < 0 || (signalHitRate as number) > 1
    || (standardError as number) < 0
    || !approximatelyEqual(
      signalRate as number,
      observationCount === 0 ? 0 : summary.signal_count / observationCount,
    )
    || !approximatelyEqual(
      conditionalExcess as number,
      (meanSignalReturn as number) - (meanUniverseReturn as number),
    )
    || !approximatelyEqual(
      (signalHitRate as number) * summary.signal_count,
      Math.round((signalHitRate as number) * summary.signal_count),
    )) return false

  const expectedLag = summary.clustered_signal_session_count < 2
    ? 0
    : Math.min(
      summary.clustered_signal_session_count - 1,
      Math.floor(4 * (summary.clustered_signal_session_count / 100) ** (2 / 9)),
    )
  const expectedT = (standardError as number) === 0
    ? 0
    : (meanSessionExcess as number) / (standardError as number)
  const margin = 1.96 * (standardError as number)
  return summary.newey_west_lag === expectedLag
    && approximatelyEqual(tStatistic as number, expectedT)
    && approximatelyEqual(
      confidenceLower as number,
      (meanSessionExcess as number) - margin,
    )
    && approximatelyEqual(
      confidenceUpper as number,
      (meanSessionExcess as number) + margin,
    )
    && (summary.signal_count !== 0 || [
      meanSignalReturn,
      signalHitRate,
      meanSessionExcess,
      standardError,
      tStatistic,
      confidenceLower,
      confidenceUpper,
    ].every((value) => approximatelyEqual(value as number, 0)))
}

function finiteNumber(value: unknown): number | null {
  if (typeof value !== 'string' || value.trim() === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function approximatelyEqual(left: number, right: number): boolean {
  return Math.abs(left - right) <= TOLERANCE * Math.max(1, Math.abs(left), Math.abs(right))
}
