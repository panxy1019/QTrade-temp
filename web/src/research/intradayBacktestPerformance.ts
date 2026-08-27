import type {
  FiveMinuteResearchReport,
  IntradayFactorBacktest,
  IntradayFactorBacktestRequest,
  IntradayFactorGroup,
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
  ) && matchesGroupBreakdown(response, dataset, selectedDays.length)
}

interface ParsedGroup {
  id: string
  observationCount: number
  signalCount: number
  meanSignalForwardReturn: number
  signalHitRate: number
}

function matchesGroupBreakdown(
  response: IntradayFactorBacktest,
  dataset: VerifiedFiveMinuteDataset,
  sessionCount: number,
): boolean {
  if (!Array.isArray(response.backtest.sectors) || !Array.isArray(response.backtest.symbols)) return false

  const symbols = [...dataset.eligible_symbols].sort()
  if (symbols.length !== dataset.symbol_count || new Set(symbols).size !== symbols.length) return false
  if (!Array.isArray(dataset.instruments) || dataset.instruments.length !== symbols.length) return false

  const instruments = new Map<string, VerifiedFiveMinuteDataset['instruments'][number]>()
  for (const instrument of dataset.instruments) {
    if (instruments.has(instrument.symbol)) return false
    instruments.set(instrument.symbol, instrument)
  }

  const sectorMembers = new Map<string, { name: string; symbols: string[] }>()
  for (const symbol of symbols) {
    const instrument = instruments.get(symbol)
    if (!instrument || instrument.instrument_type !== 'STOCK'
      || instrument.sector_code === null || instrument.sector_name === null) return false
    const sector = sectorMembers.get(instrument.sector_code)
    if (sector && sector.name !== instrument.sector_name) return false
    if (sector) sector.symbols.push(symbol)
    else sectorMembers.set(instrument.sector_code, { name: instrument.sector_name, symbols: [symbol] })
  }
  const sectors = [...sectorMembers.entries()].sort(([leftId, left], [rightId, right]) => (
    leftId.localeCompare(rightId) || left.name.localeCompare(right.name)
  ))
  if (sectors.length !== dataset.sector_count
    || new Set(sectors.map(([, sector]) => sector.name)).size !== sectors.length
    || response.backtest.symbols.length !== symbols.length
    || response.backtest.sectors.length !== sectors.length) return false

  const parsedSymbols: ParsedGroup[] = []
  for (const [index, symbol] of symbols.entries()) {
    const parsed = parseGroup(
      response.backtest.symbols[index],
      symbol,
      symbol,
      sessionCount,
      sessionCount,
    )
    if (!parsed) return false
    parsedSymbols.push(parsed)
  }

  const parsedSectors: ParsedGroup[] = []
  for (const [index, [sectorId, sector]] of sectors.entries()) {
    const parsed = parseGroup(
      response.backtest.sectors[index],
      sectorId,
      sector.name,
      sessionCount * sector.symbols.length,
      sessionCount,
    )
    if (!parsed) return false
    const members = parsedSymbols.filter((row) => sector.symbols.includes(row.id))
    const aggregate = aggregateGroups(members)
    if (members.length !== sector.symbols.length
      || parsed.signalCount !== aggregate.signalCount
      || !approximatelyEqual(parsed.meanSignalForwardReturn, aggregate.meanSignalForwardReturn)
      || !approximatelyEqual(parsed.signalHitRate, aggregate.signalHitRate)) return false
    parsedSectors.push(parsed)
  }

  const symbolAggregate = aggregateGroups(parsedSymbols)
  const sectorAggregate = aggregateGroups(parsedSectors)
  const eventSignalRate = finiteNumber(response.event_summary.signal_rate)
  const eventMean = finiteNumber(response.event_summary.mean_signal_forward_return)
  const eventHitRate = finiteNumber(response.event_summary.signal_hit_rate)
  const expectedSignalRate = symbolAggregate.observationCount === 0
    ? 0
    : symbolAggregate.signalCount / symbolAggregate.observationCount
  return eventSignalRate !== null
    && eventMean !== null
    && eventHitRate !== null
    && response.event_summary.observation_count === symbolAggregate.observationCount
    && response.event_summary.signal_count === symbolAggregate.signalCount
    && response.event_summary.sector_count === parsedSectors.length
    && response.event_summary.positive_sector_count
      === parsedSectors.filter((row) => row.meanSignalForwardReturn > 0).length
    && sameAggregate(symbolAggregate, sectorAggregate)
    && approximatelyEqual(eventSignalRate, expectedSignalRate)
    && approximatelyEqual(eventMean, symbolAggregate.meanSignalForwardReturn)
    && approximatelyEqual(eventHitRate, symbolAggregate.signalHitRate)
}

function parseGroup(
  group: IntradayFactorGroup | undefined,
  expectedId: string,
  expectedName: string,
  expectedObservationCount: number,
  sessionCount: number,
): ParsedGroup | null {
  if (!group || group.group_id !== expectedId || group.group_name !== expectedName
    || !Number.isInteger(group.observation_count)
    || group.observation_count !== expectedObservationCount
    || !Number.isInteger(group.signal_count)
    || group.signal_count < 0
    || group.signal_count > group.observation_count) return null

  const meanSignalForwardReturn = finiteNumber(group.mean_signal_forward_return)
  const signalHitRate = finiteNumber(group.signal_hit_rate)
  if (meanSignalForwardReturn === null || signalHitRate === null
    || signalHitRate < 0 || signalHitRate > 1
    || (group.signal_count === 0
      && (!approximatelyEqual(meanSignalForwardReturn, 0) || !approximatelyEqual(signalHitRate, 0)))
    || !approximatelyEqual(signalHitRate * group.signal_count, Math.round(signalHitRate * group.signal_count))
    || !matchesGroupMetrics(group.strategy_metrics, sessionCount, group.signal_count, false)
    || !matchesGroupMetrics(group.benchmark_metrics, sessionCount, group.observation_count, true)) return null

  const strategyBenchmarkReturn = finiteNumber(group.strategy_metrics.benchmark_return)
  const benchmarkTotalReturn = finiteNumber(group.benchmark_metrics.total_return)
  if (strategyBenchmarkReturn === null || benchmarkTotalReturn === null
    || !approximatelyEqual(strategyBenchmarkReturn, benchmarkTotalReturn)) return null
  return {
    id: group.group_id,
    observationCount: group.observation_count,
    signalCount: group.signal_count,
    meanSignalForwardReturn,
    signalHitRate,
  }
}

function matchesGroupMetrics(
  metrics: IntradayPerformanceMetrics | null | undefined,
  sessionCount: number,
  expectedTrades: number,
  benchmark: boolean,
): boolean {
  if (!metrics) return false
  const values = {
    totalReturn: finiteNumber(metrics.total_return),
    annualizedReturn: finiteNumber(metrics.annualized_return),
    annualizedVolatility: finiteNumber(metrics.annualized_volatility),
    sharpe: finiteNumber(metrics.sharpe),
    sortino: finiteNumber(metrics.sortino),
    maximumDrawdown: finiteNumber(metrics.maximum_drawdown),
    calmar: finiteNumber(metrics.calmar),
    winRate: finiteNumber(metrics.win_rate),
    benchmarkReturn: finiteNumber(metrics.benchmark_return),
    excessReturn: finiteNumber(metrics.excess_return),
    informationRatio: finiteNumber(metrics.information_ratio),
    turnover: finiteNumber(metrics.turnover),
    profitLossRatio: finiteNumber(metrics.profit_loss_ratio),
    exposure: finiteNumber(metrics.exposure),
    transactionCosts: finiteNumber(metrics.transaction_costs),
  }
  if (Object.values(values).some((value) => value === null)) return false
  const value = values as Record<keyof typeof values, number>
  const expectedAnnualizedReturn = (1 + value.totalReturn) ** (TRADING_PERIODS_PER_YEAR / sessionCount) - 1
  const expectedCalmar = value.maximumDrawdown === 0
    ? 0
    : value.annualizedReturn / Math.abs(value.maximumDrawdown)
  if (value.totalReturn <= -1
    || value.benchmarkReturn <= -1
    || value.annualizedVolatility < 0
    || value.maximumDrawdown < -1
    || value.maximumDrawdown > 0
    || value.winRate < 0 || value.winRate > 1
    || value.turnover < 0 || value.turnover > sessionCount * 2
    || value.profitLossRatio < 0
    || value.exposure < 0 || value.exposure > 1
    || value.transactionCosts < 0
    || metrics.number_of_periods !== sessionCount
    || metrics.number_of_trades !== expectedTrades
    || !approximatelyEqual(value.annualizedReturn, expectedAnnualizedReturn)
    || !approximatelyEqual(value.calmar, expectedCalmar)
    || !approximatelyEqual(value.excessReturn, value.totalReturn - value.benchmarkReturn)) return false

  if (benchmark) {
    return approximatelyEqual(value.totalReturn, value.benchmarkReturn)
      && approximatelyEqual(value.excessReturn, 0)
      && approximatelyEqual(value.informationRatio, 0)
      && approximatelyEqual(value.turnover, sessionCount * 2)
      && approximatelyEqual(value.exposure, 1)
  }
  if (!approximatelyEqual(value.turnover, value.exposure * sessionCount * 2)) return false
  return expectedTrades !== 0 || [
    value.totalReturn,
    value.annualizedReturn,
    value.annualizedVolatility,
    value.sharpe,
    value.sortino,
    value.maximumDrawdown,
    value.calmar,
    value.winRate,
    value.turnover,
    value.profitLossRatio,
    value.exposure,
    value.transactionCosts,
  ].every((metric) => approximatelyEqual(metric, 0))
}

function aggregateGroups(groups: readonly ParsedGroup[]) {
  const observationCount = groups.reduce((total, row) => total + row.observationCount, 0)
  const signalCount = groups.reduce((total, row) => total + row.signalCount, 0)
  return {
    observationCount,
    signalCount,
    meanSignalForwardReturn: signalCount === 0 ? 0 : groups.reduce(
      (total, row) => total + row.meanSignalForwardReturn * row.signalCount,
      0,
    ) / signalCount,
    signalHitRate: signalCount === 0 ? 0 : groups.reduce(
      (total, row) => total + row.signalHitRate * row.signalCount,
      0,
    ) / signalCount,
  }
}

function sameAggregate(
  left: ReturnType<typeof aggregateGroups>,
  right: ReturnType<typeof aggregateGroups>,
): boolean {
  return left.observationCount === right.observationCount
    && left.signalCount === right.signalCount
    && approximatelyEqual(left.meanSignalForwardReturn, right.meanSignalForwardReturn)
    && approximatelyEqual(left.signalHitRate, right.signalHitRate)
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

export function matchesIntradayMetricsFromReturns(
  metrics: IntradayPerformanceMetrics,
  returns: readonly number[],
  benchmarkReturns: readonly number[],
  turnover: number,
  exposure: number,
  trades: number,
  transactionCosts: number,
): boolean {
  return returns.length > 0
    && returns.length === benchmarkReturns.length
    && matchesCoreMetrics(metrics, calculateMetricExpectation(
      returns,
      benchmarkReturns,
      turnover,
      exposure,
      trades,
      transactionCosts,
    ))
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
