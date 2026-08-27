import type { IntradayFactorDiscovery, IntradayFactorDiscoveryRequest, IntradayOpeningDirection } from '../types'
import { matchesResponsePreviewHash } from './intradayPreviewHash'
import { canonicalJson } from './strategyIdentity'

interface VerifiedFiveMinuteDataset {
  data_version: string
  checksum: string
  symbol_count: number
  sector_count: number
  session_bar_count_per_symbol: number
  trading_days: string[]
}

const DIRECTIONS: IntradayOpeningDirection[] = ['BULLISH', 'BEARISH', 'IGNORE']
const THRESHOLDS = ['0', '5', '10', '20']
const DIRECTION_CODES: Record<IntradayOpeningDirection, string> = {
  BULLISH: 'U',
  BEARISH: 'D',
  IGNORE: 'X',
}
const OPENING_LABELS = ['0930_0940', '0940_0950', '0950_1000']

export async function matchesIntradayDiscoveryResponse(
  response: IntradayFactorDiscovery,
  request: IntradayFactorDiscoveryRequest,
  dataset: VerifiedFiveMinuteDataset,
): Promise<boolean> {
  try {
    return await matchesIntradayDiscoveryResponseUnchecked(response, request, dataset)
  } catch {
    return false
  }
}

async function matchesIntradayDiscoveryResponseUnchecked(
  response: IntradayFactorDiscovery,
  request: IntradayFactorDiscoveryRequest,
  dataset: VerifiedFiveMinuteDataset,
): Promise<boolean> {
  const startIndex = dataset.trading_days.indexOf(request.start)
  const endIndex = dataset.trading_days.indexOf(request.end)
  if (startIndex < 0 || endIndex < startIndex || endIndex + 1 >= dataset.trading_days.length) return false
  const selectedDays = dataset.trading_days.slice(startIndex, endIndex + 1)
  const trainingCount = Math.floor(selectedDays.length / 2)
  const testCount = selectedDays.length - trainingCount
  const selectedCandidate = response.candidates.find(
    (candidate) => candidate.candidate_id === response.selected.candidate_id,
  )
  const candidateIds = response.candidates.map((candidate) => candidate.candidate_id)
  const selectedByTraining = response.candidates.reduce((selected, candidate) => (
    compareTrainingCandidates(candidate, selected) < 0 ? candidate : selected
  ))
  const expectedSourceBarCount = (selectedDays.length + 1)
    * dataset.symbol_count
    * dataset.session_bar_count_per_symbol
  const contract = response.candidate_contract
  const split = response.split
  const candidatesMatch = response.candidates.length === 104
    && new Set(candidateIds).size === response.candidates.length
    && response.candidates.every((candidate) => (
      candidate.candidate_id.length > 0
      && candidate.combination === 'ALL'
      && candidate.directions.length === 3
      && candidate.directions.every((direction) => DIRECTIONS.includes(direction))
      && candidate.directions.some((direction) => direction !== 'IGNORE')
      && THRESHOLDS.includes(candidate.minimum_body_return_bps)
      && candidate.candidate_id === expectedCandidateId(
        candidate.directions,
        candidate.minimum_body_return_bps,
      )
      && candidate.factor_expression === expectedFactorExpression(candidate.directions)
      && candidate.training_observation_count === trainingCount * dataset.symbol_count
      && boundedInteger(candidate.training_signal_count, 0, candidate.training_observation_count)
      && boundedInteger(candidate.training_signal_session_count, 0, trainingCount)
      && finiteDecimal(candidate.training_mean_session_conditional_excess_return)
      && finiteDecimal(candidate.training_newey_west_t_statistic)
      && matchesTrainingGate(candidate)
    ))
  const selectedMatches = selectedCandidate !== undefined
    && selectedCandidate.candidate_id === selectedByTraining.candidate_id
    && sameStrings(response.selected.directions, selectedCandidate.directions)
    && response.selected.combination === selectedCandidate.combination
    && response.selected.minimum_body_return_bps === selectedCandidate.minimum_body_return_bps
    && response.selected.factor_expression === selectedCandidate.factor_expression
    && response.selected.training_gate_passed === selectedCandidate.training_gate_passed
    && response.selected.training_signal_count === selectedCandidate.training_signal_count
    && response.selected.training_signal_session_count === selectedCandidate.training_signal_session_count
    && response.selected.training_mean_session_conditional_excess_return
      === selectedCandidate.training_mean_session_conditional_excess_return
    && response.selected.training_newey_west_t_statistic
      === selectedCandidate.training_newey_west_t_statistic
    && canonicalJson(response.selected.training_strategy_metrics)
      === canonicalJson(selectedCandidate.training_strategy_metrics)
    && response.selected.selection_rule
      === 'TRAINING_GATE_THEN_NEWEY_WEST_T_THEN_EFFECT_THEN_LOWER_THRESHOLD_THEN_ID'

  return response.classification === 'BOUNDED_INTRADAY_FACTOR_DISCOVERY_REUSED_HISTORY_NOT_INDEPENDENT'
    && response.parameters.start === request.start
    && response.parameters.end === request.end
    && response.parameters.cost_bps === request.cost_bps
    && selectedDays.length === 40
    && sameStrings(contract.direction_values, DIRECTIONS)
    && contract.active_slot_requirement === 'AT_LEAST_ONE'
    && contract.combination === 'ALL'
    && sameStrings(contract.thresholds_bps, THRESHOLDS)
    && contract.candidate_count === 104
    && contract.arbitrary_code_execution === false
    && contract.future_features_used === false
    && split.training_start === selectedDays[0]
    && split.training_end === selectedDays[trainingCount - 1]
    && split.training_session_count === trainingCount
    && split.test_start === selectedDays[trainingCount]
    && split.test_end === selectedDays[selectedDays.length - 1]
    && split.test_session_count === testCount
    && split.selection_known_at === `${split.test_start}T09:35:00+08:00`
    && split.first_test_signal_known_at === `${split.test_start}T10:00:00+08:00`
    && split.selection_uses_test_returns === false
    && split.chronological_disjoint === true
    && candidatesMatch
    && selectedMatches
    && response.data_version === dataset.data_version
    && response.dataset_checksum === dataset.checksum
    && response.data_interval === '5m'
    && sameStrings(response.selected_trading_days, selectedDays)
    && response.exit_completion_day === dataset.trading_days[endIndex + 1]
    && response.source_bar_count === expectedSourceBarCount
    && response.symbol_count === dataset.symbol_count
    && response.sector_count === dataset.sector_count
    && response.point_in_time === false
    && response.future_adjustment_knowledge_possible === true
    && response.realtime_readiness === false
    && response.independent_confirmation === false
    && response.promotion === 'NOT_ELIGIBLE'
    && response.paper_readiness === 'NOT_ELIGIBLE'
    && response.live_readiness === 'DISABLED_REQUIRES_SEPARATE_AUTHORIZATION'
    && response.state_mutation === false
    && response.external_order_transmission === false
    && await matchesResponsePreviewHash(response)
}

function expectedCandidateId(
  directions: readonly IntradayOpeningDirection[],
  threshold: string,
): string {
  return `DIR-${directions.map((direction) => DIRECTION_CODES[direction]).join('')}-T${threshold.padStart(4, '0')}`
}

function expectedFactorExpression(directions: readonly IntradayOpeningDirection[]): string {
  return directions.flatMap((direction, index) => (
    direction === 'IGNORE' ? [] : [`${direction.toLowerCase()}_10m_${OPENING_LABELS[index]}`]
  )).join(' AND ')
}

function compareTrainingCandidates(
  left: IntradayFactorDiscovery['candidates'][number],
  right: IntradayFactorDiscovery['candidates'][number],
): number {
  if (left.training_gate_passed !== right.training_gate_passed) {
    return left.training_gate_passed ? -1 : 1
  }
  const statistic = Number(right.training_newey_west_t_statistic)
    - Number(left.training_newey_west_t_statistic)
  if (statistic !== 0) return statistic
  const effect = Number(right.training_mean_session_conditional_excess_return)
    - Number(left.training_mean_session_conditional_excess_return)
  if (effect !== 0) return effect
  const threshold = Number(left.minimum_body_return_bps) - Number(right.minimum_body_return_bps)
  return threshold !== 0 ? threshold : left.candidate_id.localeCompare(right.candidate_id)
}

function matchesTrainingGate(
  candidate: IntradayFactorDiscovery['candidates'][number],
): boolean {
  const expected = {
    minimum_250_training_observations: candidate.training_observation_count >= 250,
    minimum_twenty_training_signals: candidate.training_signal_count >= 20,
    minimum_ten_training_signal_sessions: candidate.training_signal_session_count >= 10,
    positive_training_session_conditional_excess: (
      Number(candidate.training_mean_session_conditional_excess_return) > 0
    ),
    training_newey_west_t_statistic_above_1_96: (
      Number(candidate.training_newey_west_t_statistic) > 1.96
    ),
  }
  return canonicalJson(candidate.training_gate_criteria) === canonicalJson(expected)
    && candidate.training_gate_passed === Object.values(expected).every(Boolean)
}

function finiteDecimal(value: string): boolean {
  return value.trim().length > 0 && Number.isFinite(Number(value))
}

function boundedInteger(value: number, minimum: number, maximum: number): boolean {
  return Number.isInteger(value) && value >= minimum && value <= maximum
}

function sameStrings(
  left: readonly string[],
  right: readonly string[],
): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index])
}
