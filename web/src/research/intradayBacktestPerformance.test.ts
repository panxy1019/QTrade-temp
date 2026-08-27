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

const strategyMetrics = {
  total_return: '0.0395', annualized_return: '130.79113618644902', annualized_volatility: '1.1446355672079438',
  sharpe: '4.866637448089611840466200867', sortino: '9.925238541758241668642152972',
  maximum_drawdown: '-0.05', calmar: '2615.8227237289804', win_rate: '0.5',
  benchmark_return: '0.0098', excess_return: '0.0297', information_ratio: '5.720494246595854461766838963',
  number_of_periods: 2, turnover: '4', profit_loss_ratio: '1', exposure: '1',
  number_of_trades: 3, transaction_costs: '0.001',
}

const benchmarkMetrics = {
  ...strategyMetrics,
  total_return: '0.0098', annualized_return: '2.4170879842503443', annualized_volatility: '0.4001023921426248',
  sharpe: '3.277732393464633605411150166', sortino: '5.841567144657273488834262994',
  maximum_drawdown: '-0.02', calmar: '120.854399212517215', win_rate: '0.5',
  benchmark_return: '0.0098', excess_return: '0', information_ratio: '0', number_of_trades: 4,
}

const response = {
  selected_trading_days: ['2026-07-28', '2026-07-29'],
  exit_completion_day: '2026-07-30',
  selected_session_count: 2,
  source_bar_count: 288,
  event_summary: { observation_count: 4, signal_count: 3 },
  backtest: {
    strategy_metrics: strategyMetrics,
    benchmark_metrics: benchmarkMetrics,
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
})
