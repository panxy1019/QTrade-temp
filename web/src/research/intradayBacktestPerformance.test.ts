import { describe, expect, it } from 'vitest'
import type { FiveMinuteResearchReport, IntradayFactorBacktest, IntradayFactorBacktestRequest } from '../types'
import { matchesIntradayBacktestPerformance } from './intradayBacktestPerformance'

const request: IntradayFactorBacktestRequest = {
  factor: 'opening_three_ten_minute_bullish',
  start: '2026-07-28',
  end: '2026-07-29',
  cost_bps: '5',
  minimum_body_return_bps: '0',
  directions: ['BULLISH', 'BULLISH', 'BULLISH'],
  combination: 'ALL',
}

const dataset = {
  data_version: 'baostock-five-minute-long-v1',
  checksum: 'dataset-checksum',
  symbol_count: 2,
  sector_count: 2,
  session_bar_count_per_symbol: 48,
  trading_days: ['2026-07-28', '2026-07-29', '2026-07-30'],
} as NonNullable<FiveMinuteResearchReport['minute_dataset']>

const strategyReturns = [-0.05, 0.09421052631578947]
const benchmarkReturns = [-0.02, 0.03040816326530612]
const baseCostBps = 5

function average(values: number[]): number {
  return values.reduce((total, value) => total + value, 0) / values.length
}

function standardDeviation(values: number[]): number {
  const mean = average(values)
  return Math.sqrt(average(values.map((value) => (value - mean) ** 2)))
}

function removeCost(value: number): number {
  return (1 + value) / (1 - baseCostBps / 10_000) ** 2 - 1
}

function applyCost(value: number, costBps: number): number {
  return (1 + value) * (1 - costBps / 10_000) ** 2 - 1
}

function transactionCosts(gross: number[], net: number[]): number {
  return gross.reduce((total, value, index) => total + value - net[index], 0)
}

function performanceMetrics(
  returns: number[],
  benchmarks: number[],
  turnover: number,
  exposure: number,
  trades: number,
  costs: number,
) {
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
  const annualizedVolatility = standardDeviation(returns) * Math.sqrt(252)
  const annualizedAverageReturn = average(returns) * 252
  const downsideDeviation = Math.sqrt(average(returns.map((value) => Math.min(0, value) ** 2))) * Math.sqrt(252)
  const activeReturns = returns.map((value, index) => value - benchmarks[index])
  const trackingError = standardDeviation(activeReturns) * Math.sqrt(252)
  return {
    total_return: String(equity - 1), annualized_return: String(annualizedReturn),
    annualized_volatility: String(annualizedVolatility),
    sharpe: String(annualizedVolatility === 0 ? 0 : annualizedAverageReturn / annualizedVolatility),
    sortino: String(downsideDeviation === 0 ? 0 : annualizedAverageReturn / downsideDeviation),
    maximum_drawdown: String(maximumDrawdown),
    calmar: String(maximumDrawdown === 0 ? 0 : annualizedReturn / Math.abs(maximumDrawdown)),
    win_rate: String(returns.filter((value) => value > 0).length / returns.length),
    benchmark_return: String(benchmarkEquity - 1), excess_return: String(equity - benchmarkEquity),
    information_ratio: String(trackingError === 0 ? 0 : average(activeReturns) * 252 / trackingError),
    number_of_periods: returns.length, turnover: String(turnover), profit_loss_ratio: '1',
    exposure: String(exposure), number_of_trades: trades, transaction_costs: String(costs),
  }
}

const grossStrategyReturns = strategyReturns.map(removeCost)
const grossBenchmarkReturns = benchmarkReturns.map(removeCost)
const strategyMetrics = performanceMetrics(
  strategyReturns,
  benchmarkReturns,
  4,
  1,
  3,
  transactionCosts(grossStrategyReturns, strategyReturns),
)
const benchmarkMetrics = performanceMetrics(
  benchmarkReturns,
  benchmarkReturns,
  4,
  1,
  4,
  transactionCosts(grossBenchmarkReturns, benchmarkReturns),
)
const costStress = Object.fromEntries([0, 5, 10, 20].map((costBps) => {
  const stressedStrategy = grossStrategyReturns.map((value) => applyCost(value, costBps))
  const stressedBenchmark = grossBenchmarkReturns.map((value) => applyCost(value, costBps))
  return [String(costBps), {
    strategy_metrics: performanceMetrics(
      stressedStrategy,
      stressedBenchmark,
      4,
      1,
      3,
      transactionCosts(grossStrategyReturns, stressedStrategy),
    ),
    benchmark_metrics: performanceMetrics(
      stressedBenchmark,
      stressedBenchmark,
      4,
      1,
      4,
      transactionCosts(grossBenchmarkReturns, stressedBenchmark),
    ),
  }]
}))

const response = {
  selected_trading_days: ['2026-07-28', '2026-07-29'],
  exit_completion_day: '2026-07-30',
  selected_session_count: 2,
  source_bar_count: 288,
  event_summary: { observation_count: 4, signal_count: 3 },
  backtest: {
    strategy_metrics: strategyMetrics,
    benchmark_metrics: benchmarkMetrics,
    cost_stress: costStress,
    curve: [
      { trading_day: '2026-07-28', signal_count: 1, eligible_symbol_count: 2, strategy_return: '-0.05', benchmark_return: '-0.02', strategy_equity: '0.95', benchmark_equity: '0.98', excess_equity: '-0.03' },
      { trading_day: '2026-07-29', signal_count: 2, eligible_symbol_count: 2, strategy_return: '0.09421052631578947', benchmark_return: '0.03040816326530612', strategy_equity: '1.0395', benchmark_equity: '1.0098', excess_equity: '0.0297' },
    ],
  },
} as unknown as IntradayFactorBacktest

describe('intraday backtest performance response', () => {
  it('accepts an internally reproducible curve and core metrics', () => {
    expect(matchesIntradayBacktestPerformance(response, request, dataset)).toBe(true)
  })

  it('rejects selected-session and source-bar drift', () => {
    expect(matchesIntradayBacktestPerformance({ ...response, selected_session_count: 1 }, request, dataset)).toBe(false)
    expect(matchesIntradayBacktestPerformance({ ...response, source_bar_count: 192 }, request, dataset)).toBe(false)
  })

  it('rejects forged daily equity, signal totals and cumulative metrics', () => {
    expect(matchesIntradayBacktestPerformance({
      ...response,
      backtest: {
        ...response.backtest,
        curve: response.backtest.curve.map((point, index) => index === 1 ? { ...point, strategy_equity: '9' } : point),
      },
    }, request, dataset)).toBe(false)
    expect(matchesIntradayBacktestPerformance({
      ...response,
      event_summary: { ...response.event_summary, signal_count: 4 },
    }, request, dataset)).toBe(false)
    expect(matchesIntradayBacktestPerformance({
      ...response,
      backtest: {
        ...response.backtest,
        strategy_metrics: { ...response.backtest.strategy_metrics, excess_return: '0.5' },
      },
    }, request, dataset)).toBe(false)
  })

  it('rejects forged drawdown, win rate, exposure and trade counts', () => {
    for (const strategy_metrics of [
      { ...response.backtest.strategy_metrics, maximum_drawdown: '-0.01' },
      { ...response.backtest.strategy_metrics, win_rate: '1' },
      { ...response.backtest.strategy_metrics, exposure: '0.5' },
      { ...response.backtest.strategy_metrics, number_of_trades: 4 },
    ]) {
      expect(matchesIntradayBacktestPerformance({
        ...response,
        backtest: { ...response.backtest, strategy_metrics },
      }, request, dataset)).toBe(false)
    }
  })

  it('rejects forged annualization and risk ratios', () => {
    for (const field of [
      'annualized_return',
      'annualized_volatility',
      'sharpe',
      'sortino',
      'calmar',
      'information_ratio',
    ] as const) {
      expect(matchesIntradayBacktestPerformance({
        ...response,
        backtest: {
          ...response.backtest,
          strategy_metrics: { ...response.backtest.strategy_metrics, [field]: '999' },
        },
      }, request, dataset)).toBe(false)
    }
  })

  it('rejects missing or unexpected fixed cost rows', () => {
    const missing = Object.fromEntries(Object.entries(response.backtest.cost_stress).filter(([cost]) => cost !== '20'))
    expect(matchesIntradayBacktestPerformance({
      ...response,
      backtest: { ...response.backtest, cost_stress: missing },
    }, request, dataset)).toBe(false)
    expect(matchesIntradayBacktestPerformance({
      ...response,
      backtest: {
        ...response.backtest,
        cost_stress: { ...response.backtest.cost_stress, '40': response.backtest.cost_stress['20'] },
      },
    }, request, dataset)).toBe(false)
  })

  it('rejects forged base and stressed transaction costs', () => {
    expect(matchesIntradayBacktestPerformance({
      ...response,
      backtest: {
        ...response.backtest,
        strategy_metrics: { ...response.backtest.strategy_metrics, transaction_costs: '999' },
      },
    }, request, dataset)).toBe(false)
    for (const cost of ['0', '5', '10', '20']) {
      const row = response.backtest.cost_stress[cost]
      expect(matchesIntradayBacktestPerformance({
        ...response,
        backtest: {
          ...response.backtest,
          cost_stress: {
            ...response.backtest.cost_stress,
            [cost]: {
              ...row,
              strategy_metrics: { ...row.strategy_metrics, transaction_costs: '999' },
            },
          },
        },
      }, request, dataset)).toBe(false)
    }
  })

  it('rejects a strategy return on a day without signals', () => {
    expect(matchesIntradayBacktestPerformance({
      ...response,
      backtest: {
        ...response.backtest,
        curve: response.backtest.curve.map((point, index) => index === 0
          ? { ...point, signal_count: 0 }
          : point),
      },
    }, request, dataset)).toBe(false)
  })
})
