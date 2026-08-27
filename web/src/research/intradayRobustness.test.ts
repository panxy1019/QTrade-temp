import { describe, expect, it } from 'vitest'
import type { IntradayFactorBacktest, IntradayPerformanceMetrics } from '../types'
import { matchesIntradayRobustness } from './intradayRobustness'

const DAYS = Array.from({ length: 20 }, (_, index) => `2026-08-${String(index + 1).padStart(2, '0')}`)
const COST_BPS = 5
const STRATEGY_RETURNS = [0.01, -0.004, 0.003, 0.001, 0.002, -0.002, 0.004, 0.001, -0.003, 0.002]
const BENCHMARK_RETURNS = [0.001, -0.002, 0.001, 0, 0.001, -0.001, 0.001, 0, -0.001, 0.001]
const SIGNALS = [5, 4, 5, 3, 4, 5, 4, 5, 3, 4]

const average = (values: readonly number[]) => (
  values.reduce((total, value) => total + value, 0) / values.length
)
const populationStandardDeviation = (values: readonly number[]) => {
  const mean = average(values)
  return Math.sqrt(average(values.map((value) => (value - mean) ** 2)))
}
const removeCost = (value: number) => (1 + value) / (1 - COST_BPS / 10_000) ** 2 - 1
const transactionCosts = (gross: readonly number[], net: readonly number[]) => (
  gross.reduce((total, value, index) => total + value - net[index], 0)
)

function performanceMetrics(
  returns: readonly number[],
  benchmarks: readonly number[],
  turnover: number,
  exposure: number,
  trades: number,
  costs: number,
): IntradayPerformanceMetrics {
  let equity = 1
  let benchmarkEquity = 1
  let peak = 1
  let maximumDrawdown = 0
  for (const [index, value] of returns.entries()) {
    equity *= 1 + value
    benchmarkEquity *= 1 + benchmarks[index]
    peak = Math.max(peak, equity)
    maximumDrawdown = Math.min(maximumDrawdown, equity / peak - 1)
  }
  const annualizedReturn = equity ** (252 / returns.length) - 1
  const annualizedVolatility = populationStandardDeviation(returns) * Math.sqrt(252)
  const annualizedAverageReturn = average(returns) * 252
  const downsideDeviation = Math.sqrt(
    average(returns.map((value) => Math.min(0, value) ** 2)),
  ) * Math.sqrt(252)
  const activeReturns = returns.map((value, index) => value - benchmarks[index])
  const trackingError = populationStandardDeviation(activeReturns) * Math.sqrt(252)
  return {
    total_return: String(equity - 1),
    annualized_return: String(annualizedReturn),
    annualized_volatility: String(annualizedVolatility),
    sharpe: String(annualizedVolatility === 0 ? 0 : annualizedAverageReturn / annualizedVolatility),
    sortino: String(downsideDeviation === 0 ? 0 : annualizedAverageReturn / downsideDeviation),
    maximum_drawdown: String(maximumDrawdown),
    calmar: String(maximumDrawdown === 0 ? 0 : annualizedReturn / Math.abs(maximumDrawdown)),
    win_rate: String(returns.filter((value) => value > 0).length / returns.length),
    benchmark_return: String(benchmarkEquity - 1),
    excess_return: String(equity - benchmarkEquity),
    information_ratio: String(
      trackingError === 0 ? 0 : average(activeReturns) * 252 / trackingError,
    ),
    number_of_periods: returns.length,
    turnover: String(turnover),
    profit_loss_ratio: '1',
    exposure: String(exposure),
    number_of_trades: trades,
    transaction_costs: String(costs),
  }
}

function evaluatedMetrics(
  returns: readonly number[],
  benchmarks: readonly number[],
  signals: readonly number[],
  trades: number,
): IntradayPerformanceMetrics {
  const activeDays = signals.filter((value) => value > 0).length
  const gross = returns.map((value, index) => signals[index] > 0 ? removeCost(value) : 0)
  return performanceMetrics(
    returns,
    benchmarks,
    activeDays * 2,
    activeDays / returns.length,
    trades,
    transactionCosts(gross, returns),
  )
}

function benchmarkMetrics(returns: readonly number[], symbolCount: number): IntradayPerformanceMetrics {
  const gross = returns.map(removeCost)
  return performanceMetrics(
    returns,
    returns,
    returns.length * 2,
    1,
    returns.length * symbolCount,
    transactionCosts(gross, returns),
  )
}

function fixture(): IntradayFactorBacktest {
  let strategyEquity = 1
  let benchmarkEquity = 1
  const curve = STRATEGY_RETURNS.map((strategyReturn, index) => {
    strategyEquity *= 1 + strategyReturn
    benchmarkEquity *= 1 + BENCHMARK_RETURNS[index]
    return {
      trading_day: DAYS[index + 10],
      signal_count: SIGNALS[index],
      eligible_symbol_count: 10,
      strategy_return: String(strategyReturn),
      benchmark_return: String(BENCHMARK_RETURNS[index]),
      strategy_equity: String(strategyEquity),
      benchmark_equity: String(benchmarkEquity),
      excess_equity: String(strategyEquity - benchmarkEquity),
    }
  })
  const stitchedStrategy = evaluatedMetrics(
    STRATEGY_RETURNS,
    BENCHMARK_RETURNS,
    SIGNALS,
    SIGNALS.reduce((total, value) => total + value, 0),
  )
  const stitchedBenchmark = benchmarkMetrics(BENCHMARK_RETURNS, 10)
  const fold = (index: number) => {
    const offset = index * 5
    const strategy = STRATEGY_RETURNS.slice(offset, offset + 5)
    const benchmark = BENCHMARK_RETURNS.slice(offset, offset + 5)
    const signals = SIGNALS.slice(offset, offset + 5)
    const signalCount = signals.reduce((total, value) => total + value, 0)
    const trainingCount = 10 + offset
    return {
      fold_id: `WF-${String(index + 1).padStart(2, '0')}`,
      training_start: DAYS[0],
      training_end: DAYS[trainingCount - 1],
      test_start: DAYS[10 + offset],
      test_end: DAYS[14 + offset],
      training_session_count: trainingCount,
      test_session_count: 5,
      selected_minimum_body_return_bps: '5',
      selection_known_at: `${DAYS[10 + offset]}T09:35:00+08:00`,
      training_signal_count: 30 + offset,
      test_signal_count: signalCount,
      training_strategy_metrics: performanceMetrics(
        Array.from({ length: trainingCount }, () => 0),
        Array.from({ length: trainingCount }, () => 0),
        0,
        0,
        30 + offset,
        0,
      ),
      test_strategy_metrics: evaluatedMetrics(strategy, benchmark, signals, signalCount),
      test_benchmark_metrics: benchmarkMetrics(benchmark, 10),
    }
  }
  const thresholdTotals = [0.02, 0.01, -0.01, -0.02]
  const thresholdSignals = [50, 40, 30, 20]
  const thresholdSensitivity = thresholdTotals.map((total, index) => ({
    minimum_body_return_bps: String([0, 5, 10, 20][index]),
    signal_count: thresholdSignals[index],
    signal_rate: String(thresholdSignals[index] / 100),
    strategy_metrics: {
      ...stitchedStrategy,
      total_return: String(total),
      number_of_trades: thresholdSignals[index],
    },
    benchmark_metrics: structuredClone(stitchedBenchmark),
  }))
  const positiveFolds = [fold(0), fold(1)].filter(
    (row) => Number(row.test_strategy_metrics.total_return) > 0,
  ).length
  const criteria = {
    minimum_two_disjoint_test_folds: true,
    every_test_fold_has_signal: true,
    causal_selection_known_before_test_signal: true,
    stitched_oos_total_return_positive: Number(stitchedStrategy.total_return) > 0,
    stitched_oos_excess_return_positive: Number(stitchedStrategy.excess_return) > 0,
    positive_fold_ratio_at_least_60pct: positiveFolds * 5 >= 6,
    threshold_sensitivity_majority_positive: true,
    worst_threshold_total_return_non_negative: false,
  }
  return {
    parameters: { cost_bps: String(COST_BPS) },
    selected_trading_days: DAYS,
    symbol_count: 10,
    robustness_validation: {
      classification: 'REUSED_CHRONOLOGICAL_WALK_FORWARD_DIAGNOSTIC_NOT_INDEPENDENT',
      candidate_thresholds_bps: ['0', '5', '10', '20'],
      initial_training_session_count: 10,
      stitched_test_session_count: 10,
      folds: [fold(0), fold(1)],
      stitched_strategy_metrics: stitchedStrategy,
      stitched_benchmark_metrics: stitchedBenchmark,
      stitched_curve: curve,
      threshold_sensitivity: thresholdSensitivity,
      criteria,
      passed: false,
    },
  } as unknown as IntradayFactorBacktest
}

describe('intraday walk-forward robustness response', () => {
  it('accepts an exactly recomputed rolling out-of-sample result', () => {
    expect(matchesIntradayRobustness(fixture())).toBe(true)
  })

  it('rejects forged fold metrics, curve equity and temporal boundaries', () => {
    for (const mutate of [
      (value: IntradayFactorBacktest) => { value.robustness_validation.folds[0].test_strategy_metrics.total_return = '0.5' },
      (value: IntradayFactorBacktest) => { value.robustness_validation.stitched_curve[2].strategy_equity = '9' },
      (value: IntradayFactorBacktest) => { value.robustness_validation.folds[1].training_end = DAYS[8] },
      (value: IntradayFactorBacktest) => { value.robustness_validation.folds[0].selection_known_at = `${DAYS[10]}T10:00:00+08:00` },
    ]) {
      const drifted = fixture()
      mutate(drifted)
      expect(matchesIntradayRobustness(drifted)).toBe(false)
    }
  })

  it('rejects forged threshold sensitivity, benchmark binding and gate flags', () => {
    for (const mutate of [
      (value: IntradayFactorBacktest) => { value.robustness_validation.threshold_sensitivity[1].signal_count = 60 },
      (value: IntradayFactorBacktest) => { value.robustness_validation.threshold_sensitivity[0].benchmark_metrics.total_return = '0.2' },
      (value: IntradayFactorBacktest) => { value.robustness_validation.criteria.worst_threshold_total_return_non_negative = true },
      (value: IntradayFactorBacktest) => { value.robustness_validation.passed = true },
    ]) {
      const drifted = fixture()
      mutate(drifted)
      expect(matchesIntradayRobustness(drifted)).toBe(false)
    }
  })
})
