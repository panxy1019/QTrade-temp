import type { IntradayFactorDiscovery, IntradayPerformanceMetrics } from '../types'
import { canonicalJson } from './strategyIdentity'

const REGIME_IDS = ['PREVIOUS_SESSION_NEGATIVE', 'PREVIOUS_SESSION_NON_NEGATIVE']
const DELAYS = [
  { bars: 1, time: '10:05' },
  { bars: 2, time: '10:10' },
  { bars: 3, time: '10:15' },
]
const PARTICIPATION_RATES = ['0.01', '0.05', '0.10']

export function matchesIntradayDiscoveryOperationalStress(
  response: IntradayFactorDiscovery,
): boolean {
  const selectedId = response.selected.candidate_id
  const testStart = response.split.test_start
  const testEnd = response.split.test_end
  const testSessions = response.split.test_session_count
  const signalCount = response.test_validation.signal_count
  const signalSessions = response.test_validation.signal_session_count
  return matchesTestValidation(response, testSessions)
    && matchesRegimeStress(response, selectedId, testStart, testEnd, testSessions, signalCount)
    && matchesPlaceboAudit(response, selectedId, testStart, testEnd, signalSessions)
    && matchesExecutionDelay(
      response,
      selectedId,
      testStart,
      testEnd,
      testSessions,
      signalCount,
      signalSessions,
    )
    && matchesLiquidityCapacity(
      response,
      selectedId,
      testStart,
      testEnd,
      signalCount,
      signalSessions,
    )
    && matchesDiscoveryGate(response)
}

function matchesTestValidation(response: IntradayFactorDiscovery, testSessions: number): boolean {
  const validation = response.test_validation
  const criteria = {
    minimum_250_test_observations: validation.observation_count >= 250,
    minimum_twenty_test_signals: validation.signal_count >= 20,
    minimum_ten_test_signal_sessions: validation.signal_session_count >= 10,
    positive_test_session_conditional_excess: Number(validation.mean_session_conditional_excess_return) > 0,
    test_newey_west_t_statistic_above_1_96: Number(validation.newey_west_t_statistic) > 1.96,
    test_strategy_total_return_positive: Number(validation.strategy_metrics.total_return) > 0,
    test_strategy_excess_return_positive: Number(validation.strategy_metrics.excess_return) > 0,
  }
  return validation.observation_count === testSessions * response.symbol_count
    && boundedInteger(validation.signal_count, 0, validation.observation_count)
    && boundedInteger(validation.signal_session_count, 0, testSessions)
    && [
      validation.mean_session_conditional_excess_return,
      validation.newey_west_standard_error,
      validation.newey_west_t_statistic,
      validation.confidence_interval_95_lower,
      validation.confidence_interval_95_upper,
    ].every(finiteDecimal)
    && boundedInteger(validation.newey_west_lag, 0, Math.max(0, validation.signal_session_count - 1))
    && matchesPerformanceMetrics(validation.strategy_metrics, testSessions)
    && matchesPerformanceMetrics(validation.benchmark_metrics, testSessions)
    && canonicalJson(validation.criteria) === canonicalJson(criteria)
    && validation.passed === Object.values(criteria).every(Boolean)
}

function matchesRegimeStress(
  response: IntradayFactorDiscovery,
  selectedId: string,
  testStart: string,
  testEnd: string,
  testSessions: number,
  signalCount: number,
): boolean {
  const stress = response.locked_test_regime_stress
  if (!matchesIdentity(stress, selectedId, testStart, testEnd)
    || stress.regime_rows_used_for_selection !== false
    || stress.regime_definition !== 'PREVIOUS_SESSION_EQUAL_WEIGHT_UNIVERSE_GROSS_RETURN_SIGN'
    || stress.regime_result_known_at !== 'CURRENT_SESSION_09_35'
    || stress.current_signal_known_at !== 'CURRENT_SESSION_10_00'
    || stress.regimes.length !== 2) return false
  const rowsMatch = stress.regimes.every((row, index) => {
    const expectedId = REGIME_IDS[index]
    const minimum = Number(row.previous_session_mean_universe_return_minimum)
    const maximum = Number(row.previous_session_mean_universe_return_maximum)
    return row.regime_id === expectedId
      && row.regime_name === (index === 0 ? '上一交易日等权收益为负' : '上一交易日等权收益非负')
      && boundedInteger(row.session_count, 1, testSessions)
      && row.observation_count === row.session_count * response.symbol_count
      && boundedInteger(row.signal_count, 0, row.observation_count)
      && Number.isFinite(minimum)
      && Number.isFinite(maximum)
      && minimum <= maximum
      && (index === 0 ? maximum < 0 : minimum >= 0)
      && row.known_before_signal === true
      && matchesPerformanceMetrics(row.strategy_metrics, row.session_count)
      && matchesPerformanceMetrics(row.benchmark_metrics, row.session_count)
  })
  const criteria = {
    both_previous_session_direction_regimes_present: sameStrings(
      stress.regimes.map((row) => row.regime_id),
      REGIME_IDS,
    ),
    minimum_five_sessions_per_previous_session_regime: stress.regimes.every((row) => row.session_count >= 5),
    every_previous_session_regime_known_before_signal: stress.regimes.every((row) => row.known_before_signal),
    every_previous_session_regime_has_signal: stress.regimes.every((row) => row.signal_count > 0),
    all_previous_session_regime_total_returns_positive: stress.regimes.every(
      (row) => Number(row.strategy_metrics.total_return) > 0,
    ),
    all_previous_session_regime_excess_returns_positive: stress.regimes.every(
      (row) => Number(row.strategy_metrics.excess_return) > 0,
    ),
  }
  return rowsMatch
    && sum(stress.regimes.map((row) => row.session_count)) === testSessions
    && sum(stress.regimes.map((row) => row.observation_count)) === response.test_validation.observation_count
    && sum(stress.regimes.map((row) => row.signal_count)) === signalCount
    && canonicalJson(stress.criteria) === canonicalJson(criteria)
    && stress.passed === Object.values(criteria).every(Boolean)
}

function matchesPlaceboAudit(
  response: IntradayFactorDiscovery,
  selectedId: string,
  testStart: string,
  testEnd: string,
  signalSessions: number,
): boolean {
  const audit = response.locked_test_placebo_audit
  const observed = Number(audit.observed_mean_session_conditional_excess_return)
  const p95 = Number(audit.placebo_p95_session_conditional_excess_return)
  const pValue = (audit.placebo_at_least_observed_count + 1) / (audit.replications + 1)
  const criteria = {
    minimum_ten_locked_test_signal_sessions_for_placebo: signalSessions >= 10,
    placebo_preserves_every_session_signal_count: audit.preserves_daily_signal_counts,
    observed_conditional_excess_positive: observed > 0,
    empirical_one_sided_placebo_p_value_at_most_0_05: pValue <= 0.05,
    observed_conditional_excess_above_placebo_p95: observed > p95,
  }
  return matchesIdentity(audit, selectedId, testStart, testEnd)
    && audit.placebo_rows_used_for_selection === false
    && audit.method === 'SESSION_PRESERVING_CROSS_SECTIONAL_PERMUTATION'
    && audit.replications === 499
    && audit.seed === 20260827
    && audit.preserves_daily_signal_counts === true
    && audit.observed_mean_session_conditional_excess_return
      === response.test_validation.mean_session_conditional_excess_return
    && [
      audit.observed_mean_session_conditional_excess_return,
      audit.placebo_mean_session_conditional_excess_return,
      audit.placebo_median_session_conditional_excess_return,
      audit.placebo_p95_session_conditional_excess_return,
    ].every(finiteDecimal)
    && boundedInteger(audit.placebo_at_least_observed_count, 0, audit.replications)
    && approximatelyEqual(Number(audit.empirical_one_sided_p_value), pValue)
    && canonicalJson(audit.criteria) === canonicalJson(criteria)
    && audit.passed === Object.values(criteria).every(Boolean)
}

function matchesExecutionDelay(
  response: IntradayFactorDiscovery,
  selectedId: string,
  testStart: string,
  testEnd: string,
  testSessions: number,
  signalCount: number,
  signalSessions: number,
): boolean {
  const stress = response.locked_test_execution_delay_stress
  const rowsMatch = stress.rows.length === DELAYS.length && stress.rows.every((row, index) => (
    row.delay_bars_after_signal === DELAYS[index].bars
    && row.entry_source_bar_time === DELAYS[index].time
    && row.signal_count === signalCount
    && row.signal_session_count === signalSessions
    && finiteDecimal(row.mean_session_conditional_excess_return)
    && matchesPerformanceMetrics(row.strategy_metrics, testSessions)
    && matchesPerformanceMetrics(row.benchmark_metrics, testSessions)
  ))
  const criteria = {
    three_predeclared_execution_delays_present: rowsMatch,
    every_execution_delay_preserves_locked_signal_identity: stress.rows.every(
      (row) => row.signal_count === signalCount && row.signal_session_count === signalSessions,
    ),
    all_execution_delay_total_returns_positive: stress.rows.every(
      (row) => Number(row.strategy_metrics.total_return) > 0,
    ),
    all_execution_delay_excess_returns_positive: stress.rows.every(
      (row) => Number(row.strategy_metrics.excess_return) > 0,
    ),
  }
  return matchesIdentity(stress, selectedId, testStart, testEnd)
    && stress.cost_bps === response.parameters.cost_bps
    && stress.delay_rows_used_for_selection === false
    && stress.signal_known_at === 'CURRENT_SESSION_10_00'
    && stress.source_bar_timestamp_convention === 'BAR_END_TIME'
    && rowsMatch
    && canonicalJson(stress.rows[0].strategy_metrics)
      === canonicalJson(response.test_validation.strategy_metrics)
    && canonicalJson(stress.rows[0].benchmark_metrics)
      === canonicalJson(response.test_validation.benchmark_metrics)
    && canonicalJson(stress.criteria) === canonicalJson(criteria)
    && stress.passed === Object.values(criteria).every(Boolean)
}

function matchesLiquidityCapacity(
  response: IntradayFactorDiscovery,
  selectedId: string,
  testStart: string,
  testEnd: string,
  signalCount: number,
  signalSessions: number,
): boolean {
  const stress = response.locked_test_liquidity_capacity_stress
  const rowsMatch = stress.rows.length === PARTICIPATION_RATES.length && stress.rows.every((row, index) => (
    row.participation_rate === PARTICIPATION_RATES[index]
    && row.signal_count === signalCount
    && row.signal_session_count === signalSessions
    && [
      row.minimum_round_lot_share_capacity,
      row.lower_median_round_lot_share_capacity,
      row.maximum_round_lot_share_capacity,
    ].every((value) => boundedInteger(value, 0, Number.MAX_SAFE_INTEGER) && value % 100 === 0)
    && row.minimum_round_lot_share_capacity <= row.lower_median_round_lot_share_capacity
    && row.lower_median_round_lot_share_capacity <= row.maximum_round_lot_share_capacity
    && boundedInteger(row.zero_capacity_signal_count, 0, signalCount)
    && boundedInteger(row.one_lot_eligible_signal_count, 0, signalCount)
    && row.zero_capacity_signal_count + row.one_lot_eligible_signal_count === signalCount
    && approximatelyEqual(
      Number(row.one_lot_eligible_signal_ratio),
      signalCount === 0 ? 0 : row.one_lot_eligible_signal_count / signalCount,
    )
  ))
  const criteria = {
    three_predeclared_participation_rates_present: rowsMatch,
    all_capacity_uses_completed_signal_bar_volume: stress.volume_source_bar_completed_before_entry,
    every_participation_row_preserves_locked_signal_identity: stress.rows.every(
      (row) => row.signal_count === signalCount && row.signal_session_count === signalSessions,
    ),
    every_locked_signal_supports_one_round_lot_at_1pct: rowsMatch
      && stress.rows[0].zero_capacity_signal_count === 0
      && stress.rows[0].one_lot_eligible_signal_count === signalCount,
    exact_provider_rmb_amount_available: false,
  }
  return matchesIdentity(stress, selectedId, testStart, testEnd)
    && stress.capacity_rows_used_for_selection === false
    && stress.volume_source_bar_time === '10:00'
    && stress.volume_source_interval === '09:55-10:00'
    && stress.volume_source_bar_completed_before_entry === true
    && stress.volume_unit === 'SHARES'
    && stress.order_lot_size_shares === 100
    && stress.source_amount_available === false
    && stress.qfq_price_volume_used_as_actual_notional === false
    && stress.rmb_notional_capacity_status === 'UNAVAILABLE_QFQ_OHLCV_WITHOUT_PROVIDER_AMOUNT'
    && rowsMatch
    && stress.rows.every((row, index) => index === 0 || (
      row.minimum_round_lot_share_capacity >= stress.rows[index - 1].minimum_round_lot_share_capacity
      && row.lower_median_round_lot_share_capacity >= stress.rows[index - 1].lower_median_round_lot_share_capacity
      && row.maximum_round_lot_share_capacity >= stress.rows[index - 1].maximum_round_lot_share_capacity
      && row.one_lot_eligible_signal_count >= stress.rows[index - 1].one_lot_eligible_signal_count
    ))
    && canonicalJson(stress.criteria) === canonicalJson(criteria)
    && stress.passed === Object.values(criteria).every(Boolean)
}

function matchesDiscoveryGate(response: IntradayFactorDiscovery): boolean {
  const gate = response.discovery_gate
  const criteria = {
    chronological_locked_test_passed: response.test_validation.passed,
    selection_bias_audit_passed: response.overfitting_audit.passed,
    locked_test_cost_stress_passed: response.locked_test_cost_stress.passed,
    locked_test_sector_stress_passed: response.locked_test_sector_stress.passed,
    locked_test_symbol_stress_passed: response.locked_test_symbol_stress.passed,
    locked_test_regime_stress_passed: response.locked_test_regime_stress.passed,
    locked_test_placebo_audit_passed: response.locked_test_placebo_audit.passed,
    locked_test_execution_delay_stress_passed: response.locked_test_execution_delay_stress.passed,
    locked_test_liquidity_capacity_stress_passed: response.locked_test_liquidity_capacity_stress.passed,
  }
  return canonicalJson(gate.criteria) === canonicalJson(criteria)
    && gate.passed === Object.values(criteria).every(Boolean)
    && gate.promotion_authority === false
}

function matchesIdentity(
  stress: { selected_candidate_id: string; test_start: string; test_end: string; candidate_reselected: false },
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
  return [
    metrics.total_return, metrics.annualized_return, metrics.annualized_volatility,
    metrics.sharpe, metrics.sortino, metrics.maximum_drawdown, metrics.calmar,
    metrics.win_rate, metrics.benchmark_return, metrics.excess_return,
    metrics.information_ratio, metrics.turnover, metrics.profit_loss_ratio,
    metrics.exposure, metrics.transaction_costs,
  ].every(finiteDecimal)
    && metrics.number_of_periods === periods
    && boundedInteger(metrics.number_of_trades, 0, Number.MAX_SAFE_INTEGER)
}

function finiteDecimal(value: string): boolean {
  return value.trim().length > 0 && Number.isFinite(Number(value))
}

function boundedInteger(value: number, minimum: number, maximum: number): boolean {
  return Number.isInteger(value) && value >= minimum && value <= maximum
}

function approximatelyEqual(left: number, right: number): boolean {
  if (!Number.isFinite(left) || !Number.isFinite(right)) return false
  return Math.abs(left - right) <= Math.max(1e-12, Math.abs(right) * 1e-9)
}

function sameStrings(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index])
}

function sum(values: readonly number[]): number {
  return values.reduce((total, value) => total + value, 0)
}
