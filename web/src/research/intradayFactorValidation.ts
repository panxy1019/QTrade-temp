import type { IntradayFactorBacktest } from '../types'

const TOLERANCE = 1e-9

export function matchesIntradayFactorValidation(response: IntradayFactorBacktest): boolean {
  const validation = response.factor_validation
  const event = response.event_summary
  const ic = finiteNumber(validation.ic)
  const rankIc = finiteNumber(validation.rank_ic)
  const icir = finiteNumber(validation.icir)
  const turnover = finiteNumber(validation.factor_turnover)
  const spread = finiteNumber(validation.long_short_spread)
  const signalMean = finiteNumber(event.mean_signal_forward_return)
  const universeMean = finiteNumber(event.mean_universe_forward_return)
  const conditionalExcess = finiteNumber(event.conditional_excess_return)
  if (!Array.isArray(validation.quantile_returns)) return false
  const quantileReturns = validation.quantile_returns.map(finiteNumber)
  if ([
    ic, rankIc, icir, turnover, spread,
    signalMean, universeMean, conditionalExcess,
    ...quantileReturns,
  ].some((value) => value === null)
    || validation.quantile_returns.length !== 2
    || !Number.isInteger(validation.number_of_periods)
    || !Number.isInteger(validation.minimum_instruments)
    || validation.number_of_periods !== response.selected_trading_days.length
    || validation.minimum_instruments !== response.symbol_count
    || (ic as number) < -1 || (ic as number) > 1
    || (rankIc as number) < -1 || (rankIc as number) > 1
    || (turnover as number) < 0 || (turnover as number) > 1
    || event.observation_count <= 0
    || event.signal_count < 0
    || event.signal_count > event.observation_count) return false

  const nonSignalCount = event.observation_count - event.signal_count
  const nonSignalMean = nonSignalCount === 0
    ? 0
    : ((universeMean as number) * event.observation_count
      - (signalMean as number) * event.signal_count) / nonSignalCount
  return approximatelyEqual(quantileReturns[0] as number, nonSignalMean)
    && approximatelyEqual(quantileReturns[1] as number, signalMean as number)
    && approximatelyEqual(spread as number, (signalMean as number) - nonSignalMean)
    && approximatelyEqual(
      conditionalExcess as number,
      (signalMean as number) - (universeMean as number),
    )
}

function finiteNumber(value: unknown): number | null {
  if (typeof value !== 'string' || value.trim() === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function approximatelyEqual(left: number, right: number): boolean {
  return Math.abs(left - right) <= TOLERANCE * Math.max(1, Math.abs(left), Math.abs(right))
}
