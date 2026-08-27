import type {
  FiveMinuteResearchReport,
  IntradayFactorBacktest,
  IntradayFactorBacktestRequest,
  IntradayPerformanceMetrics,
} from '../types'

type VerifiedFiveMinuteDataset = NonNullable<FiveMinuteResearchReport['minute_dataset']>

const METRIC_TOLERANCE = 1e-9
const TRADING_PERIODS_PER_YEAR = 252
const COST_STRESS_BPS = [0, 5, 10, 20] as const

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
  const baseCostBps = finiteNumber(request.cost_bps)

  if (baseCostBps === null
    || baseCostBps < 0
    || baseCostBps > 100
    || !sameStrings(response.selected_trading_days, selectedDays)
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
  const strategyReturns: number[] = []
  const benchmarkReturns: number[] = []
  const strategyActive: boolean[] = []

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
      || (point.signal_count === 0 && !approximatelyEqual(strategyReturn, 0))
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
    strategyReturns.push(strategyReturn)
    benchmarkReturns.push(benchmarkReturn)
    strategyActive.push(point.signal_count > 0)
  }

  if (signalCount !== response.event_summary.signal_count) return false

  const grossStrategyReturns = strategyReturns.map((value, index) => (
    strategyActive[index] ? removeRoundTripCost(value, baseCostBps) : 0
  ))
  const grossBenchmarkReturns = benchmarkReturns.map((value) => removeRoundTripCost(value, baseCostBps))
  const strategyTransactionCosts = totalTransactionCosts(grossStrategyReturns, strategyReturns)
  const benchmarkTransactionCosts = totalTransactionCosts(grossBenchmarkReturns, benchmarkReturns)
  const strategyRiskMetrics = calculateRiskMetrics(
    strategyReturns,
    benchmarkReturns,
    strategyEquity,
    strategyMaximumDrawdown,
  )
  const benchmarkRiskMetrics = calculateRiskMetrics(
    benchmarkReturns,
    benchmarkReturns,
    benchmarkEquity,
    benchmarkMaximumDrawdown,
  )
  return matchesCoreMetrics(response.backtest.strategy_metrics, {
    ...strategyRiskMetrics,
    totalReturn: strategyEquity - 1,
    benchmarkReturn: benchmarkEquity - 1,
    excessReturn: strategyEquity - benchmarkEquity,
    maximumDrawdown: strategyMaximumDrawdown,
    winRate: strategyWins / selectedDays.length,
    periods: selectedDays.length,
    turnover: activeDays * 2,
    exposure: activeDays / selectedDays.length,
    trades: signalCount,
    transactionCosts: strategyTransactionCosts,
  }) && matchesCoreMetrics(response.backtest.benchmark_metrics, {
    ...benchmarkRiskMetrics,
    totalReturn: benchmarkEquity - 1,
    benchmarkReturn: benchmarkEquity - 1,
    excessReturn: 0,
    maximumDrawdown: benchmarkMaximumDrawdown,
    winRate: benchmarkWins / selectedDays.length,
    periods: selectedDays.length,
    turnover: selectedDays.length * 2,
    exposure: 1,
    trades: expectedObservationCount,
    transactionCosts: benchmarkTransactionCosts,
  }) && matchesCostStress(
    response.backtest.cost_stress,
    grossStrategyReturns,
    grossBenchmarkReturns,
    strategyActive,
    signalCount,
    expectedObservationCount,
  )
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
  annualizedReturn: number
  annualizedVolatility: number
  sharpe: number
  sortino: number
  calmar: number
  informationRatio: number
  transactionCosts: number
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
    && approximatelyEqual(annualizedReturn, expected.annualizedReturn)
    && approximatelyEqual(annualizedVolatility, expected.annualizedVolatility)
    && approximatelyEqual(sharpe, expected.sharpe)
    && approximatelyEqual(sortino, expected.sortino)
    && approximatelyEqual(benchmarkReturn, expected.benchmarkReturn)
    && approximatelyEqual(excessReturn, expected.excessReturn)
    && approximatelyEqual(maximumDrawdown, expected.maximumDrawdown)
    && approximatelyEqual(calmar, expected.calmar)
    && approximatelyEqual(winRate, expected.winRate)
    && approximatelyEqual(informationRatio, expected.informationRatio)
    && metrics.number_of_periods === expected.periods
    && approximatelyEqual(turnover, expected.turnover)
    && approximatelyEqual(exposure, expected.exposure)
    && metrics.number_of_trades === expected.trades
    && approximatelyEqual(transactionCosts, expected.transactionCosts)
}

function matchesCostStress(
  costStress: IntradayFactorBacktest['backtest']['cost_stress'],
  grossStrategyReturns: readonly number[],
  grossBenchmarkReturns: readonly number[],
  strategyActive: readonly boolean[],
  signalCount: number,
  observationCount: number,
): boolean {
  if (!costStress || typeof costStress !== 'object' || Array.isArray(costStress)) return false
  const keys = Object.keys(costStress).sort((left, right) => Number(left) - Number(right))
  if (!sameStrings(keys, COST_STRESS_BPS.map(String))) return false

  const activeDays = strategyActive.filter(Boolean).length
  return COST_STRESS_BPS.every((costBps) => {
    const strategyReturns = grossStrategyReturns.map((value, index) => (
      strategyActive[index] ? applyRoundTripCost(value, costBps) : 0
    ))
    const benchmarkReturns = grossBenchmarkReturns.map((value) => applyRoundTripCost(value, costBps))
    const strategyMetrics = calculateMetricExpectation(
      strategyReturns,
      benchmarkReturns,
      activeDays * 2,
      activeDays / strategyReturns.length,
      signalCount,
      totalTransactionCosts(grossStrategyReturns, strategyReturns),
    )
    const benchmarkMetrics = calculateMetricExpectation(
      benchmarkReturns,
      benchmarkReturns,
      benchmarkReturns.length * 2,
      1,
      observationCount,
      totalTransactionCosts(grossBenchmarkReturns, benchmarkReturns),
    )
    const row = costStress[String(costBps)]
    return row !== undefined
      && matchesCoreMetrics(row.strategy_metrics, strategyMetrics)
      && matchesCoreMetrics(row.benchmark_metrics, benchmarkMetrics)
  })
}

function calculateMetricExpectation(
  returns: readonly number[],
  benchmarkReturns: readonly number[],
  turnover: number,
  exposure: number,
  trades: number,
  transactionCosts: number,
): CoreMetricExpectation {
  let equity = 1
  let benchmarkEquity = 1
  let peak = 1
  let maximumDrawdown = 0
  let wins = 0
  for (const [index, value] of returns.entries()) {
    equity *= 1 + value
    benchmarkEquity *= 1 + benchmarkReturns[index]
    peak = Math.max(peak, equity)
    maximumDrawdown = Math.min(maximumDrawdown, equity / peak - 1)
    wins += Number(value > 0)
  }
  return {
    ...calculateRiskMetrics(returns, benchmarkReturns, equity, maximumDrawdown),
    totalReturn: equity - 1,
    benchmarkReturn: benchmarkEquity - 1,
    excessReturn: equity - benchmarkEquity,
    maximumDrawdown,
    winRate: wins / returns.length,
    periods: returns.length,
    turnover,
    exposure,
    trades,
    transactionCosts,
  }
}

function removeRoundTripCost(netReturn: number, costBps: number): number {
  const retainedPerSide = 1 - costBps / 10_000
  return (1 + netReturn) / retainedPerSide ** 2 - 1
}

function applyRoundTripCost(grossReturn: number, costBps: number): number {
  const retainedPerSide = 1 - costBps / 10_000
  return (1 + grossReturn) * retainedPerSide ** 2 - 1
}

function totalTransactionCosts(
  grossReturns: readonly number[],
  netReturns: readonly number[],
): number {
  return grossReturns.reduce((total, value, index) => total + value - netReturns[index], 0)
}

function calculateRiskMetrics(
  returns: readonly number[],
  benchmarkReturns: readonly number[],
  finalEquity: number,
  maximumDrawdown: number,
): Pick<CoreMetricExpectation,
  'annualizedReturn' | 'annualizedVolatility' | 'sharpe' | 'sortino' | 'calmar' | 'informationRatio'> {
  const annualizedReturn = finalEquity ** (TRADING_PERIODS_PER_YEAR / returns.length) - 1
  const annualizedVolatility = populationStandardDeviation(returns)
    * Math.sqrt(TRADING_PERIODS_PER_YEAR)
  const annualizedAverageReturn = average(returns) * TRADING_PERIODS_PER_YEAR
  const sharpe = annualizedVolatility === 0 ? 0 : annualizedAverageReturn / annualizedVolatility
  const downsideDeviation = Math.sqrt(average(returns.map((value) => Math.min(0, value) ** 2)))
    * Math.sqrt(TRADING_PERIODS_PER_YEAR)
  const sortino = downsideDeviation === 0 ? 0 : annualizedAverageReturn / downsideDeviation
  const calmar = maximumDrawdown === 0 ? 0 : annualizedReturn / Math.abs(maximumDrawdown)
  const activeReturns = returns.map((value, index) => value - benchmarkReturns[index])
  const trackingError = populationStandardDeviation(activeReturns)
    * Math.sqrt(TRADING_PERIODS_PER_YEAR)
  const informationRatio = trackingError === 0
    ? 0
    : average(activeReturns) * TRADING_PERIODS_PER_YEAR / trackingError
  return { annualizedReturn, annualizedVolatility, sharpe, sortino, calmar, informationRatio }
}

function average(values: readonly number[]): number {
  return values.reduce((total, value) => total + value, 0) / values.length
}

function populationStandardDeviation(values: readonly number[]): number {
  const mean = average(values)
  return Math.sqrt(average(values.map((value) => (value - mean) ** 2)))
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
