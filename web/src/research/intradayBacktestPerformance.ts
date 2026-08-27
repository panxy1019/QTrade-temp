import type {
  FiveMinuteResearchReport,
  IntradayFactorBacktest,
  IntradayFactorBacktestRequest,
  IntradayPerformanceMetrics,
} from '../types'

type VerifiedFiveMinuteDataset = NonNullable<FiveMinuteResearchReport['minute_dataset']>

const METRIC_TOLERANCE = 1e-9

export function matchesIntradayBacktestPerformance(
  response: IntradayFactorBacktest,
  request: IntradayFactorBacktestRequest,
  dataset: VerifiedFiveMinuteDataset,
): boolean {
  const startIndex = dataset.trading_days.indexOf(request.start)
  const endIndex = dataset.trading_days.indexOf(request.end)
  if (startIndex < 0 || endIndex < startIndex || endIndex + 1 >= dataset.trading_days.length) return false

  const selectedDays = dataset.trading_days.slice(startIndex, endIndex + 1)
  const expectedExitCompletionDay = dataset.trading_days[endIndex + 1]
  const expectedObservationCount = selectedDays.length * dataset.symbol_count
  const expectedSourceBarCount = (selectedDays.length + 1)
    * dataset.symbol_count
    * dataset.session_bar_count_per_symbol

  if (!sameStrings(response.selected_trading_days, selectedDays)
    || response.exit_completion_day !== expectedExitCompletionDay
    || response.selected_session_count !== selectedDays.length
    || response.source_bar_count !== expectedSourceBarCount
    || response.event_summary.observation_count !== expectedObservationCount
    || response.backtest.curve.length !== selectedDays.length) return false

  let strategyEquity = 1
  let benchmarkEquity = 1
  let strategyPeak = 1
  let benchmarkPeak = 1
  let strategyMaximumDrawdown = 0
  let benchmarkMaximumDrawdown = 0
  let strategyWins = 0
  let benchmarkWins = 0
  let activeDays = 0
  let signalCount = 0

  for (const [index, point] of response.backtest.curve.entries()) {
    const strategyReturn = finiteNumber(point.strategy_return)
    const benchmarkReturn = finiteNumber(point.benchmark_return)
    const reportedStrategyEquity = finiteNumber(point.strategy_equity)
    const reportedBenchmarkEquity = finiteNumber(point.benchmark_equity)
    const reportedExcessEquity = finiteNumber(point.excess_equity)
    if (point.trading_day !== selectedDays[index]
      || !Number.isInteger(point.signal_count)
      || point.signal_count < 0
      || point.signal_count > dataset.symbol_count
      || point.eligible_symbol_count !== dataset.symbol_count
      || strategyReturn === null
      || benchmarkReturn === null
      || strategyReturn <= -1
      || benchmarkReturn <= -1
      || reportedStrategyEquity === null
      || reportedBenchmarkEquity === null
      || reportedExcessEquity === null) return false

    strategyEquity *= 1 + strategyReturn
    benchmarkEquity *= 1 + benchmarkReturn
    if (!approximatelyEqual(reportedStrategyEquity, strategyEquity)
      || !approximatelyEqual(reportedBenchmarkEquity, benchmarkEquity)
      || !approximatelyEqual(reportedExcessEquity, strategyEquity - benchmarkEquity)) return false

    strategyPeak = Math.max(strategyPeak, strategyEquity)
    benchmarkPeak = Math.max(benchmarkPeak, benchmarkEquity)
    strategyMaximumDrawdown = Math.min(strategyMaximumDrawdown, strategyEquity / strategyPeak - 1)
    benchmarkMaximumDrawdown = Math.min(benchmarkMaximumDrawdown, benchmarkEquity / benchmarkPeak - 1)
    strategyWins += Number(strategyReturn > 0)
    benchmarkWins += Number(benchmarkReturn > 0)
    activeDays += Number(point.signal_count > 0)
    signalCount += point.signal_count
  }

  if (signalCount !== response.event_summary.signal_count) return false

  return matchesCoreMetrics(response.backtest.strategy_metrics, {
    totalReturn: strategyEquity - 1,
    benchmarkReturn: benchmarkEquity - 1,
    excessReturn: strategyEquity - benchmarkEquity,
    maximumDrawdown: strategyMaximumDrawdown,
    winRate: strategyWins / selectedDays.length,
    periods: selectedDays.length,
    turnover: activeDays * 2,
    exposure: activeDays / selectedDays.length,
    trades: signalCount,
  }) && matchesCoreMetrics(response.backtest.benchmark_metrics, {
    totalReturn: benchmarkEquity - 1,
    benchmarkReturn: benchmarkEquity - 1,
    excessReturn: 0,
    maximumDrawdown: benchmarkMaximumDrawdown,
    winRate: benchmarkWins / selectedDays.length,
    periods: selectedDays.length,
    turnover: selectedDays.length * 2,
    exposure: 1,
    trades: expectedObservationCount,
  })
}

interface CoreMetricExpectation {
  totalReturn: number
  benchmarkReturn: number
  excessReturn: number
  maximumDrawdown: number
  winRate: number
  periods: number
  turnover: number
  exposure: number
  trades: number
}

function matchesCoreMetrics(
  metrics: IntradayPerformanceMetrics,
  expected: CoreMetricExpectation,
): boolean {
  const totalReturn = finiteNumber(metrics.total_return)
  const annualizedReturn = finiteNumber(metrics.annualized_return)
  const annualizedVolatility = finiteNumber(metrics.annualized_volatility)
  const sharpe = finiteNumber(metrics.sharpe)
  const sortino = finiteNumber(metrics.sortino)
  const maximumDrawdown = finiteNumber(metrics.maximum_drawdown)
  const calmar = finiteNumber(metrics.calmar)
  const winRate = finiteNumber(metrics.win_rate)
  const benchmarkReturn = finiteNumber(metrics.benchmark_return)
  const excessReturn = finiteNumber(metrics.excess_return)
  const informationRatio = finiteNumber(metrics.information_ratio)
  const turnover = finiteNumber(metrics.turnover)
  const profitLossRatio = finiteNumber(metrics.profit_loss_ratio)
  const exposure = finiteNumber(metrics.exposure)
  const transactionCosts = finiteNumber(metrics.transaction_costs)

  return totalReturn !== null
    && annualizedReturn !== null
    && annualizedVolatility !== null
    && annualizedVolatility >= 0
    && sharpe !== null
    && sortino !== null
    && maximumDrawdown !== null
    && maximumDrawdown <= 0
    && calmar !== null
    && winRate !== null
    && winRate >= 0
    && winRate <= 1
    && benchmarkReturn !== null
    && excessReturn !== null
    && informationRatio !== null
    && turnover !== null
    && turnover >= 0
    && profitLossRatio !== null
    && profitLossRatio >= 0
    && exposure !== null
    && exposure >= 0
    && exposure <= 1
    && transactionCosts !== null
    && transactionCosts >= 0
    && Number.isInteger(metrics.number_of_periods)
    && Number.isInteger(metrics.number_of_trades)
    && approximatelyEqual(totalReturn, expected.totalReturn)
    && approximatelyEqual(benchmarkReturn, expected.benchmarkReturn)
    && approximatelyEqual(excessReturn, expected.excessReturn)
    && approximatelyEqual(maximumDrawdown, expected.maximumDrawdown)
    && approximatelyEqual(winRate, expected.winRate)
    && metrics.number_of_periods === expected.periods
    && approximatelyEqual(turnover, expected.turnover)
    && approximatelyEqual(exposure, expected.exposure)
    && metrics.number_of_trades === expected.trades
}

function finiteNumber(value: string): number | null {
  if (typeof value !== 'string' || value.trim() === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function approximatelyEqual(left: number, right: number): boolean {
  return Math.abs(left - right) <= METRIC_TOLERANCE * Math.max(1, Math.abs(left), Math.abs(right))
}

function sameStrings(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index])
}
