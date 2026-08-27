import type { IntradayFactorBacktest, IntradayPerformanceMetrics } from '../types'
import { matchesIntradayMetricsFromReturns } from './intradayBacktestPerformance'

const THRESHOLDS = ['0', '5', '10', '20'] as const
const CRITERIA = [
  'minimum_two_disjoint_test_folds',
  'every_test_fold_has_signal',
  'causal_selection_known_before_test_signal',
  'stitched_oos_total_return_positive',
  'stitched_oos_excess_return_positive',
  'positive_fold_ratio_at_least_60pct',
  'threshold_sensitivity_majority_positive',
  'worst_threshold_total_return_non_negative',
] as const
const TOLERANCE = 1e-9

export function matchesIntradayRobustness(response: IntradayFactorBacktest): boolean {
  const robustness = response.robustness_validation
  const days = response.selected_trading_days
  const initialTrainingCount = Math.floor(days.length / 2)
  const remainingCount = days.length - initialTrainingCount
  const foldCount = Math.min(6, Math.max(1, Math.floor(remainingCount / 5)))
  const baseFoldSize = Math.floor(remainingCount / foldCount)
  const largerFoldCount = remainingCount % foldCount
  const foldSizes = Array.from(
    { length: foldCount },
    (_, index) => baseFoldSize + Number(index < largerFoldCount),
  )
  if (robustness.classification !== 'REUSED_CHRONOLOGICAL_WALK_FORWARD_DIAGNOSTIC_NOT_INDEPENDENT'
    || !sameStrings(robustness.candidate_thresholds_bps, THRESHOLDS)
    || robustness.initial_training_session_count !== initialTrainingCount
    || robustness.stitched_test_session_count !== remainingCount
    || !Array.isArray(robustness.folds)
    || robustness.folds.length !== foldCount
    || !Array.isArray(robustness.stitched_curve)
    || robustness.stitched_curve.length !== remainingCount
    || !Array.isArray(robustness.threshold_sensitivity)
    || robustness.threshold_sensitivity.length !== THRESHOLDS.length) return false

  const testDays = days.slice(initialTrainingCount)
  const costBps = finiteNumber(response.parameters.cost_bps)
  if (costBps === null || costBps < 0 || costBps > 100) return false
  let testOffset = 0
  for (const [index, fold] of robustness.folds.entries()) {
    const foldSize = foldSizes[index]
    const trainingCount = initialTrainingCount + testOffset
    const foldDays = testDays.slice(testOffset, testOffset + foldSize)
    const curve = robustness.stitched_curve.slice(testOffset, testOffset + foldSize)
    const expectedTestSignals = curve.reduce((total, point) => total + point.signal_count, 0)
    const foldStrategyReturns = curve.map((point) => Number(point.strategy_return))
    const foldBenchmarkReturns = curve.map((point) => Number(point.benchmark_return))
    const foldActiveDays = curve.filter((point) => point.signal_count > 0).length
    const foldGrossStrategyReturns = foldStrategyReturns.map((value, curveIndex) => (
      curve[curveIndex].signal_count > 0 ? removeRoundTripCost(value, costBps) : 0
    ))
    const foldGrossBenchmarkReturns = foldBenchmarkReturns.map(
      (value) => removeRoundTripCost(value, costBps),
    )
    if (fold.fold_id !== `WF-${String(index + 1).padStart(2, '0')}`
      || fold.training_start !== days[0]
      || fold.training_end !== days[trainingCount - 1]
      || fold.test_start !== foldDays[0]
      || fold.test_end !== foldDays[foldDays.length - 1]
      || fold.training_session_count !== trainingCount
      || fold.test_session_count !== foldSize
      || !THRESHOLDS.includes(fold.selected_minimum_body_return_bps as typeof THRESHOLDS[number])
      || fold.selection_known_at !== `${fold.test_start}T09:35:00+08:00`
      || !Number.isInteger(fold.training_signal_count)
      || fold.training_signal_count < 0
      || fold.training_signal_count > trainingCount * response.symbol_count
      || fold.test_signal_count !== expectedTestSignals
      || !matchesMetricCounts(
        fold.training_strategy_metrics,
        trainingCount,
        fold.training_signal_count,
      )
      || !matchesIntradayMetricsFromReturns(
        fold.test_strategy_metrics,
        foldStrategyReturns,
        foldBenchmarkReturns,
        foldActiveDays * 2,
        foldActiveDays / foldSize,
        fold.test_signal_count,
        totalTransactionCosts(foldGrossStrategyReturns, foldStrategyReturns),
      )
      || !matchesIntradayMetricsFromReturns(
        fold.test_benchmark_metrics,
        foldBenchmarkReturns,
        foldBenchmarkReturns,
        foldSize * 2,
        1,
        foldSize * response.symbol_count,
        totalTransactionCosts(foldGrossBenchmarkReturns, foldBenchmarkReturns),
      )
      || !matchesBenchmarkBinding(fold.test_strategy_metrics, fold.test_benchmark_metrics)) return false
    testOffset += foldSize
  }

  const strategyReturns: number[] = []
  const benchmarkReturns: number[] = []
  const strategyActive: boolean[] = []
  let strategyEquity = 1
  let benchmarkEquity = 1
  let signalCount = 0
  for (const [index, point] of robustness.stitched_curve.entries()) {
    const strategyReturn = finiteNumber(point.strategy_return)
    const benchmarkReturn = finiteNumber(point.benchmark_return)
    const reportedStrategyEquity = finiteNumber(point.strategy_equity)
    const reportedBenchmarkEquity = finiteNumber(point.benchmark_equity)
    const reportedExcessEquity = finiteNumber(point.excess_equity)
    if (point.trading_day !== testDays[index]
      || !Number.isInteger(point.signal_count)
      || point.signal_count < 0 || point.signal_count > response.symbol_count
      || point.eligible_symbol_count !== response.symbol_count
      || strategyReturn === null || strategyReturn <= -1
      || benchmarkReturn === null || benchmarkReturn <= -1
      || reportedStrategyEquity === null
      || reportedBenchmarkEquity === null
      || reportedExcessEquity === null
      || (point.signal_count === 0 && !approximatelyEqual(strategyReturn, 0))) return false
    strategyEquity *= 1 + strategyReturn
    benchmarkEquity *= 1 + benchmarkReturn
    if (!approximatelyEqual(reportedStrategyEquity, strategyEquity)
      || !approximatelyEqual(reportedBenchmarkEquity, benchmarkEquity)
      || !approximatelyEqual(reportedExcessEquity, strategyEquity - benchmarkEquity)) return false
    signalCount += point.signal_count
    strategyReturns.push(strategyReturn)
    benchmarkReturns.push(benchmarkReturn)
    strategyActive.push(point.signal_count > 0)
  }

  const grossStrategyReturns = strategyReturns.map((value, index) => (
    strategyActive[index] ? removeRoundTripCost(value, costBps) : 0
  ))
  const grossBenchmarkReturns = benchmarkReturns.map((value) => removeRoundTripCost(value, costBps))
  const activeDays = strategyActive.filter(Boolean).length
  if (!matchesIntradayMetricsFromReturns(
    robustness.stitched_strategy_metrics,
    strategyReturns,
    benchmarkReturns,
    activeDays * 2,
    activeDays / remainingCount,
    signalCount,
    totalTransactionCosts(grossStrategyReturns, strategyReturns),
  ) || !matchesIntradayMetricsFromReturns(
    robustness.stitched_benchmark_metrics,
    benchmarkReturns,
    benchmarkReturns,
    remainingCount * 2,
    1,
    remainingCount * response.symbol_count,
    totalTransactionCosts(grossBenchmarkReturns, benchmarkReturns),
  )) return false

  const observationCount = remainingCount * response.symbol_count
  let previousSignalCount = observationCount + 1
  for (const [index, row] of robustness.threshold_sensitivity.entries()) {
    const signalRate = finiteNumber(row.signal_rate)
    if (row.minimum_body_return_bps !== THRESHOLDS[index]
      || !Number.isInteger(row.signal_count)
      || row.signal_count < 0 || row.signal_count > observationCount
      || row.signal_count > previousSignalCount
      || signalRate === null
      || !approximatelyEqual(signalRate, row.signal_count / observationCount)
      || !matchesMetricCounts(row.strategy_metrics, remainingCount, row.signal_count)
      || !matchesMetricCounts(row.benchmark_metrics, remainingCount, observationCount)
      || !matchesBenchmarkBinding(row.strategy_metrics, row.benchmark_metrics)
      || !sameMetrics(row.benchmark_metrics, robustness.stitched_benchmark_metrics)) return false
    previousSignalCount = row.signal_count
  }

  const foldTotals = robustness.folds.map((fold) => finiteNumber(fold.test_strategy_metrics.total_return))
  const thresholdTotals = robustness.threshold_sensitivity.map((row) => finiteNumber(row.strategy_metrics.total_return))
  const stitchedTotal = finiteNumber(robustness.stitched_strategy_metrics.total_return)
  const stitchedExcess = finiteNumber(robustness.stitched_strategy_metrics.excess_return)
  if ([...foldTotals, ...thresholdTotals, stitchedTotal, stitchedExcess].some((value) => value === null)) return false
  const positiveFoldCount = foldTotals.filter((value) => (value as number) > 0).length
  const positiveThresholdCount = thresholdTotals.filter((value) => (value as number) > 0).length
  const expectedCriteria: Record<string, boolean> = {
    minimum_two_disjoint_test_folds: foldCount >= 2,
    every_test_fold_has_signal: robustness.folds.every((fold) => fold.test_signal_count > 0),
    causal_selection_known_before_test_signal: true,
    stitched_oos_total_return_positive: (stitchedTotal as number) > 0,
    stitched_oos_excess_return_positive: (stitchedExcess as number) > 0,
    positive_fold_ratio_at_least_60pct: positiveFoldCount * 5 >= foldCount * 3,
    threshold_sensitivity_majority_positive: positiveThresholdCount * 2 >= THRESHOLDS.length,
    worst_threshold_total_return_non_negative: Math.min(...thresholdTotals as number[]) >= 0,
  }
  const keys = Object.keys(robustness.criteria)
  return sameStrings(keys, CRITERIA)
    && keys.every((key) => robustness.criteria[key] === expectedCriteria[key])
    && robustness.passed === Object.values(expectedCriteria).every(Boolean)
}

function matchesMetricCounts(
  metrics: IntradayPerformanceMetrics,
  periods: number,
  trades: number,
): boolean {
  const fields = [
    metrics.total_return,
    metrics.annualized_return,
    metrics.annualized_volatility,
    metrics.sharpe,
    metrics.sortino,
    metrics.maximum_drawdown,
    metrics.calmar,
    metrics.win_rate,
    metrics.benchmark_return,
    metrics.excess_return,
    metrics.information_ratio,
    metrics.turnover,
    metrics.profit_loss_ratio,
    metrics.exposure,
    metrics.transaction_costs,
  ].map(finiteNumber)
  return fields.every((value) => value !== null)
    && metrics.number_of_periods === periods
    && metrics.number_of_trades === trades
}

function matchesBenchmarkBinding(
  strategy: IntradayPerformanceMetrics,
  benchmark: IntradayPerformanceMetrics,
): boolean {
  const strategyBenchmark = finiteNumber(strategy.benchmark_return)
  const benchmarkTotal = finiteNumber(benchmark.total_return)
  const benchmarkBenchmark = finiteNumber(benchmark.benchmark_return)
  const benchmarkExcess = finiteNumber(benchmark.excess_return)
  return strategyBenchmark !== null
    && benchmarkTotal !== null
    && benchmarkBenchmark !== null
    && benchmarkExcess !== null
    && approximatelyEqual(strategyBenchmark, benchmarkTotal)
    && approximatelyEqual(benchmarkTotal, benchmarkBenchmark)
    && approximatelyEqual(benchmarkExcess, 0)
}

function sameMetrics(
  left: IntradayPerformanceMetrics,
  right: IntradayPerformanceMetrics,
): boolean {
  const leftValues = metricValues(left)
  const rightValues = metricValues(right)
  return leftValues !== null
    && rightValues !== null
    && leftValues.every((value, index) => approximatelyEqual(value, rightValues[index]))
    && left.number_of_periods === right.number_of_periods
    && left.number_of_trades === right.number_of_trades
}

function metricValues(metrics: IntradayPerformanceMetrics): number[] | null {
  const values = [
    metrics.total_return,
    metrics.annualized_return,
    metrics.annualized_volatility,
    metrics.sharpe,
    metrics.sortino,
    metrics.maximum_drawdown,
    metrics.calmar,
    metrics.win_rate,
    metrics.benchmark_return,
    metrics.excess_return,
    metrics.information_ratio,
    metrics.turnover,
    metrics.profit_loss_ratio,
    metrics.exposure,
    metrics.transaction_costs,
  ].map(finiteNumber)
  return values.some((value) => value === null) ? null : values as number[]
}

function removeRoundTripCost(netReturn: number, costBps: number): number {
  const retainedPerSide = 1 - costBps / 10_000
  return (1 + netReturn) / retainedPerSide ** 2 - 1
}

function totalTransactionCosts(gross: readonly number[], net: readonly number[]): number {
  return gross.reduce((total, value, index) => total + value - net[index], 0)
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
