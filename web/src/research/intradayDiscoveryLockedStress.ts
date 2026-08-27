import type { IntradayFactorDiscovery, IntradayPerformanceMetrics } from '../types'
import { canonicalJson } from './strategyIdentity'

const COSTS = ['0', '5', '10', '20'] as const

export function matchesIntradayDiscoveryLockedStress(
  response: IntradayFactorDiscovery,
): boolean {
  const selectedId = response.selected.candidate_id
  const testStart = response.split.test_start
  const testEnd = response.split.test_end
  const testSessions = response.split.test_session_count
  const signalCount = response.test_validation.signal_count
  return matchesCostStress(
    response,
    selectedId,
    testStart,
    testEnd,
    testSessions,
    signalCount,
  ) && matchesCrossSectionStress(
    response,
    selectedId,
    testStart,
    testEnd,
    testSessions,
    signalCount,
  )
}

function matchesCostStress(
  response: IntradayFactorDiscovery,
  selectedId: string,
  testStart: string,
  testEnd: string,
  testSessions: number,
  signalCount: number,
): boolean {
  const stress = response.locked_test_cost_stress
  const rowKeys = Object.keys(stress.rows).sort((left, right) => Number(left) - Number(right))
  if (!sameStrings(stress.costs_bps, COSTS) || !sameStrings(rowKeys, COSTS)) return false
  const rows = COSTS.map((cost) => stress.rows[cost])
  const totalReturns = rows.map((row) => Number(row.strategy_metrics.total_return))
  const transactionCosts = rows.map((row) => Number(row.strategy_metrics.transaction_costs))
  const oneBenchmark = canonicalJson(rows[0].benchmark_metrics)
  const baseRow = COSTS.includes(response.parameters.cost_bps as typeof COSTS[number])
    ? stress.rows[response.parameters.cost_bps]
    : null
  const criteria = {
    zero_cost_locked_test_total_return_positive: totalReturns[0] > 0,
    all_fixed_cost_locked_test_total_returns_positive: totalReturns.every((value) => value > 0),
    twenty_bps_locked_test_excess_return_positive: Number(rows[3].strategy_metrics.excess_return) > 0,
    locked_test_total_return_non_increasing_with_cost: totalReturns.every((value, index) => (
      index === 0 || totalReturns[index - 1] >= value
    )),
  }
  return matchesLockedIdentity(stress, selectedId, testStart, testEnd)
    && stress.cost_rows_used_for_selection === false
    && stress.signal_count === signalCount
    && rows.every((row) => (
      matchesPerformanceMetrics(row.strategy_metrics, testSessions)
      && matchesPerformanceMetrics(row.benchmark_metrics, testSessions)
      && canonicalJson(row.benchmark_metrics) === oneBenchmark
    ))
    && transactionCosts.every((value, index) => value >= 0 && (
      index === 0 || transactionCosts[index - 1] <= value
    ))
    && (baseRow === null || (
      canonicalJson(baseRow.strategy_metrics) === canonicalJson(response.test_validation.strategy_metrics)
      && canonicalJson(baseRow.benchmark_metrics) === canonicalJson(response.test_validation.benchmark_metrics)
    ))
    && canonicalJson(stress.criteria) === canonicalJson(criteria)
    && stress.passed === Object.values(criteria).every(Boolean)
}

function matchesCrossSectionStress(
  response: IntradayFactorDiscovery,
  selectedId: string,
  testStart: string,
  testEnd: string,
  testSessions: number,
  signalCount: number,
): boolean {
  const sectorStress = response.locked_test_sector_stress
  const symbolStress = response.locked_test_symbol_stress
  if (!matchesLockedIdentity(sectorStress, selectedId, testStart, testEnd)
    || !matchesLockedIdentity(symbolStress, selectedId, testStart, testEnd)
    || sectorStress.sector_rows_used_for_selection !== false
    || symbolStress.symbol_rows_used_for_selection !== false) return false
  if (sectorStress.sector_only.length !== response.sector_count
    || sectorStress.leave_one_sector_out.length !== response.sector_count
    || symbolStress.symbols.length !== response.symbol_count) return false

  const sectors = new Map<string, typeof sectorStress.sector_only[number]>()
  const excluded = new Map<string, typeof sectorStress.leave_one_sector_out[number]>()
  const symbols = new Set<string>()
  const sectorSignalCounts = new Map<string, number>()
  const sectorSymbolCounts = new Map<string, number>()
  for (const row of sectorStress.sector_only) {
    if (sectors.has(row.sector_code) || row.sector_code.length === 0 || row.sector_name.length === 0) return false
    if (row.observation_count !== testSessions * (response.symbol_count / response.sector_count)
      || !boundedInteger(row.signal_count, 0, row.observation_count)
      || !finiteDecimal(row.mean_signal_forward_return)
      || !validHitRate(row.signal_hit_rate, row.signal_count)
      || !matchesPerformanceMetrics(row.strategy_metrics, testSessions)
      || !matchesPerformanceMetrics(row.benchmark_metrics, testSessions)) return false
    sectors.set(row.sector_code, row)
  }
  for (const row of sectorStress.leave_one_sector_out) {
    const sector = sectors.get(row.excluded_sector_code)
    if (sector === undefined || excluded.has(row.excluded_sector_code)
      || row.excluded_sector_name !== sector.sector_name
      || row.observation_count !== testSessions * (
        response.symbol_count - response.symbol_count / response.sector_count
      )
      || !boundedInteger(row.signal_count, 0, row.observation_count)
      || !matchesPerformanceMetrics(row.strategy_metrics, testSessions)
      || !matchesPerformanceMetrics(row.benchmark_metrics, testSessions)) return false
    excluded.set(row.excluded_sector_code, row)
  }
  for (const row of symbolStress.symbols) {
    const sector = sectors.get(row.sector_code)
    if (symbols.has(row.symbol) || !/^\d{6}$/.test(row.symbol)
      || sector === undefined || row.sector_name !== sector.sector_name
      || row.observation_count !== testSessions
      || !boundedInteger(row.signal_count, 0, testSessions)
      || !matchesPerformanceMetrics(row.strategy_metrics, testSessions)
      || !matchesPerformanceMetrics(row.benchmark_metrics, testSessions)) return false
    symbols.add(row.symbol)
    sectorSignalCounts.set(row.sector_code, (sectorSignalCounts.get(row.sector_code) ?? 0) + row.signal_count)
    sectorSymbolCounts.set(row.sector_code, (sectorSymbolCounts.get(row.sector_code) ?? 0) + 1)
  }
  const expectedPerSector = response.symbol_count / response.sector_count
  if (![...sectors].every(([sectorCode, sector]) => (
    sectorSymbolCounts.get(sectorCode) === expectedPerSector
    && sectorSignalCounts.get(sectorCode) === sector.signal_count
    && excluded.get(sectorCode)?.signal_count === signalCount - sector.signal_count
  ))) return false
  if (sum(sectorStress.sector_only.map((row) => row.signal_count)) !== signalCount
    || sum(symbolStress.symbols.map((row) => row.signal_count)) !== signalCount) return false

  const positiveSectorCount = sectorStress.sector_only.filter(
    (row) => Number(row.strategy_metrics.total_return) > 0,
  ).length
  const positiveExclusionCount = sectorStress.leave_one_sector_out.filter(
    (row) => Number(row.strategy_metrics.total_return) > 0,
  ).length
  const positiveSectorRatio = positiveSectorCount / response.sector_count
  const positiveExclusionRatio = positiveExclusionCount / response.sector_count
  const sectorCriteria = {
    every_locked_test_sector_has_signal: sectorStress.sector_only.every((row) => row.signal_count > 0),
    positive_locked_test_sector_ratio_at_least_0_60: positiveSectorRatio >= 0.60,
    positive_leave_one_sector_out_ratio_at_least_0_80: positiveExclusionRatio >= 0.80,
    worst_leave_one_sector_out_total_return_non_negative: Math.min(
      ...sectorStress.leave_one_sector_out.map((row) => Number(row.strategy_metrics.total_return)),
    ) >= 0,
  }
  const orderedSymbols = [...symbolStress.symbols].sort((left, right) => (
    Math.abs(Number(right.strategy_metrics.total_return))
      - Math.abs(Number(left.strategy_metrics.total_return))
    || left.symbol.localeCompare(right.symbol)
  ))
  const orderedReturns = symbolStress.symbols
    .map((row) => Number(row.strategy_metrics.total_return))
    .sort((left, right) => left - right)
  const middle = Math.floor(orderedReturns.length / 2)
  const median = orderedReturns.length % 2 === 1
    ? orderedReturns[middle]
    : (orderedReturns[middle - 1] + orderedReturns[middle]) / 2
  const absoluteTotal = sum(orderedReturns.map(Math.abs))
  const topFiveShare = absoluteTotal === 0
    ? 0
    : sum(orderedSymbols.slice(0, 5).map((row) => Math.abs(Number(row.strategy_metrics.total_return))))
      / absoluteTotal
  const positiveSymbolCount = orderedReturns.filter((value) => value > 0).length
  const positiveSymbolRatio = positiveSymbolCount / response.symbol_count
  const symbolCriteria = {
    every_locked_test_symbol_has_signal: symbolStress.symbols.every((row) => row.signal_count > 0),
    positive_locked_test_symbol_ratio_at_least_0_60: positiveSymbolRatio >= 0.60,
    median_locked_test_symbol_total_return_positive: median > 0,
    top_five_absolute_symbol_return_share_below_0_30: topFiveShare < 0.30,
  }
  return sectorStress.positive_sector_count === positiveSectorCount
    && approximatelyEqual(Number(sectorStress.positive_sector_ratio), positiveSectorRatio)
    && sectorStress.positive_exclusion_count === positiveExclusionCount
    && approximatelyEqual(Number(sectorStress.positive_exclusion_ratio), positiveExclusionRatio)
    && canonicalJson(sectorStress.criteria) === canonicalJson(sectorCriteria)
    && sectorStress.passed === Object.values(sectorCriteria).every(Boolean)
    && symbolStress.positive_symbol_count === positiveSymbolCount
    && approximatelyEqual(Number(symbolStress.positive_symbol_ratio), positiveSymbolRatio)
    && approximatelyEqual(Number(symbolStress.median_symbol_total_return), median)
    && approximatelyEqual(Number(symbolStress.top_five_absolute_return_share), topFiveShare)
    && sameStrings(
      symbolStress.top_five_absolute_return_symbols,
      orderedSymbols.slice(0, 5).map((row) => row.symbol),
    )
    && canonicalJson(symbolStress.criteria) === canonicalJson(symbolCriteria)
    && symbolStress.passed === Object.values(symbolCriteria).every(Boolean)
}

function matchesLockedIdentity(
  stress: {
    selected_candidate_id: string
    test_start: string
    test_end: string
    candidate_reselected: false
  },
  selectedId: string,
  testStart: string,
  testEnd: string,
): boolean {
  return stress.selected_candidate_id === selectedId
    && stress.test_start === testStart
    && stress.test_end === testEnd
    && stress.candidate_reselected === false
}

function matchesPerformanceMetrics(metrics: IntradayPerformanceMetrics, periods: number): boolean {
  const decimals = [
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
  ]
  return decimals.every(finiteDecimal)
    && metrics.number_of_periods === periods
    && boundedInteger(metrics.number_of_trades, 0, Number.MAX_SAFE_INTEGER)
}

function validHitRate(value: string, signalCount: number): boolean {
  const rate = Number(value)
  return Number.isFinite(rate) && rate >= 0 && rate <= 1
    && (signalCount > 0 || rate === 0)
    && approximatelyEqual(rate * signalCount, Math.round(rate * signalCount))
}

function finiteDecimal(value: string): boolean {
  return value.trim().length > 0 && Number.isFinite(Number(value))
}

function boundedInteger(value: number, minimum: number, maximum: number): boolean {
  return Number.isInteger(value) && value >= minimum && value <= maximum
}

function sum(values: readonly number[]): number {
  return values.reduce((total, value) => total + value, 0)
}

function approximatelyEqual(left: number, right: number): boolean {
  if (!Number.isFinite(left) || !Number.isFinite(right)) return false
  return Math.abs(left - right) <= Math.max(1e-12, Math.abs(right) * 1e-9)
}

function sameStrings(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index])
}
