import type { LocalIntradayFactorCandidate } from './localIntradayFactorCandidates'
import type {
  FiveMinuteResearchReport,
  IntradayFactorBacktest,
  IntradayFactorBacktestRequest,
  IntradayFactorCorrelation,
  IntradayOpeningDirection,
} from '../types'

type Dataset = Pick<
  NonNullable<FiveMinuteResearchReport['minute_dataset']>,
  | 'provider'
  | 'provider_version'
  | 'data_version'
  | 'checksum'
  | 'evidence_sha256'
  | 'symbol_count'
  | 'sector_count'
>

export interface IntradayFactorStrategyLineage {
  schema_version: 1
  classification: 'BROWSER_SESSION_FACTOR_TO_STRATEGY_LINEAGE_NOT_IMMUTABLE'
  data_identity: {
    provider: string
    provider_version: string | null
    data_version: string
    dataset_checksum: string
    evidence_sha256: string
    symbol_count: number
    sector_count: number
    point_in_time: false
  }
  factor_evidence: {
    candidate_key: string
    candidate_name: string
    candidate_expression: string
    rule_id: string
    factor_id: string
    correlation_preview_hash: string
    window: { start: string; end: string }
    directions: [IntradayOpeningDirection, IntradayOpeningDirection, IntradayOpeningDirection]
    combination: 'ALL' | 'ANY'
    minimum_body_return_bps: string
    observation_count: number
    signal_count: number
    clustered_signal_session_count: number
    mean_session_conditional_excess_return: string
    newey_west_t_statistic: string
    raw_p_value: string
    holm_adjusted_p_value: string
    effectiveness_passed: true
    redundant: false
  }
  strategy_preview: {
    request: IntradayFactorBacktestRequest
    preview_hash: string
    strategy_id: string
    behavior_spec_hash: string
    registration_status: 'AD_HOC_UNREGISTERED'
    promotion: 'NOT_ELIGIBLE'
    paper_readiness: 'NOT_ELIGIBLE'
    live_readiness: 'DISABLED_REQUIRES_SEPARATE_AUTHORIZATION'
  }
  interpretation: {
    same_historical_window: true
    entry_factor_identity_match: true
    exit_rule_added_during_strategy_construction: true
    exit_rule_validated_by_factor_correlation: false
    strategy_returns_validated_by_factor_correlation: false
    independent_evidence: false
  }
  immutable_persistence: false
  factor_registry_write: false
  strategy_registry_write: false
  paper_runtime_start: false
  live_runtime_start: false
  state_mutation: false
  external_order_transmission: false
}

export function buildIntradayFactorStrategyLineage({
  dataset,
  candidate,
  ruleId,
  correlation,
  request,
  backtest,
}: {
  dataset: Dataset
  candidate: LocalIntradayFactorCandidate
  ruleId: string
  correlation: IntradayFactorCorrelation
  request: IntradayFactorBacktestRequest
  backtest: IntradayFactorBacktest
}): IntradayFactorStrategyLineage | null {
  const ruleIndex = correlation.rules.findIndex((rule) => rule.rule_id === ruleId)
  const rule = correlation.rules[ruleIndex]
  const requestedRule = correlation.parameters.rules[ruleIndex]
  const effectiveness = correlation.effectiveness.find((row) => row.rule_id === ruleId)
  const redundant = correlation.pairs.some((pair) => (
    pair.redundant && (pair.left_rule_id === ruleId || pair.right_rule_id === ruleId)
  ))
  const strategyIdentity = backtest.strategy_identity

  if (ruleIndex < 0 || !rule || !requestedRule || !effectiveness?.passed || redundant) return null
  if (candidate.data_version !== dataset.data_version
    || candidate.dataset_checksum !== dataset.checksum
    || correlation.data_version !== dataset.data_version
    || correlation.dataset_checksum !== dataset.checksum
    || backtest.data_version !== dataset.data_version
    || backtest.dataset_checksum !== dataset.checksum) return null
  if (correlation.classification !== 'AD_HOC_INTRADAY_FACTOR_CORRELATION_CURRENT_NON_PIT'
    || correlation.parameters.start !== request.start
    || correlation.parameters.end !== request.end
    || correlation.data_interval !== '5m'
    || correlation.symbol_count !== dataset.symbol_count
    || correlation.sector_count !== dataset.sector_count
    || correlation.point_in_time !== false
    || correlation.future_adjustment_knowledge_possible !== true
    || correlation.realtime_readiness !== false
    || correlation.promotion !== 'NOT_ELIGIBLE'
    || correlation.paper_readiness !== 'NOT_ELIGIBLE'
    || correlation.live_readiness !== 'DISABLED_REQUIRES_SEPARATE_AUTHORIZATION'
    || correlation.state_mutation !== false
    || correlation.external_order_transmission !== false
    || !isSha256(correlation.preview_hash)) return null
  if (request.factor !== 'custom_opening_ten_minute_direction_rule'
    || effectiveness.minimum_body_return_bps !== candidate.minimum_body_return_bps
    || !sameEntryRule(candidate, request)
    || !sameEntryRule(candidate, requestedRule)
    || !sameEntryRule(candidate, rule)
    || !sameBacktestRequest(backtest.parameters, request)) return null
  if (!isSha256(backtest.preview_hash)
    || !isSha256(strategyIdentity.spec_hash)
    || strategyIdentity.strategy_id !== `adhoc-intraday-${strategyIdentity.spec_hash.slice(0, 16)}`
    || strategyIdentity.registration_status !== 'AD_HOC_UNREGISTERED'
    || backtest.promotion !== 'NOT_ELIGIBLE'
    || backtest.paper_readiness !== 'NOT_ELIGIBLE'
    || backtest.live_readiness !== 'DISABLED_REQUIRES_SEPARATE_AUTHORIZATION'
    || backtest.state_mutation !== false
    || backtest.external_order_transmission !== false) return null

  return {
    schema_version: 1,
    classification: 'BROWSER_SESSION_FACTOR_TO_STRATEGY_LINEAGE_NOT_IMMUTABLE',
    data_identity: {
      provider: dataset.provider,
      provider_version: dataset.provider_version ?? null,
      data_version: dataset.data_version,
      dataset_checksum: dataset.checksum,
      evidence_sha256: dataset.evidence_sha256,
      symbol_count: dataset.symbol_count,
      sector_count: dataset.sector_count,
      point_in_time: false,
    },
    factor_evidence: {
      candidate_key: candidate.draft_key,
      candidate_name: candidate.name,
      candidate_expression: candidate.expression,
      rule_id: ruleId,
      factor_id: rule.factor_id,
      correlation_preview_hash: correlation.preview_hash,
      window: { start: request.start, end: request.end },
      directions: [...candidate.directions],
      combination: candidate.combination,
      minimum_body_return_bps: candidate.minimum_body_return_bps,
      observation_count: effectiveness.observation_count,
      signal_count: effectiveness.signal_count,
      clustered_signal_session_count: effectiveness.clustered_signal_session_count,
      mean_session_conditional_excess_return: effectiveness.mean_session_conditional_excess_return,
      newey_west_t_statistic: effectiveness.newey_west_t_statistic,
      raw_p_value: effectiveness.raw_p_value,
      holm_adjusted_p_value: effectiveness.holm_adjusted_p_value,
      effectiveness_passed: true,
      redundant: false,
    },
    strategy_preview: {
      request: { ...request, directions: [...request.directions] },
      preview_hash: backtest.preview_hash,
      strategy_id: strategyIdentity.strategy_id,
      behavior_spec_hash: strategyIdentity.spec_hash,
      registration_status: 'AD_HOC_UNREGISTERED',
      promotion: 'NOT_ELIGIBLE',
      paper_readiness: 'NOT_ELIGIBLE',
      live_readiness: 'DISABLED_REQUIRES_SEPARATE_AUTHORIZATION',
    },
    interpretation: {
      same_historical_window: true,
      entry_factor_identity_match: true,
      exit_rule_added_during_strategy_construction: true,
      exit_rule_validated_by_factor_correlation: false,
      strategy_returns_validated_by_factor_correlation: false,
      independent_evidence: false,
    },
    immutable_persistence: false,
    factor_registry_write: false,
    strategy_registry_write: false,
    paper_runtime_start: false,
    live_runtime_start: false,
    state_mutation: false,
    external_order_transmission: false,
  }
}

function sameEntryRule(
  candidate: LocalIntradayFactorCandidate,
  rule: Pick<IntradayFactorBacktestRequest, 'directions' | 'combination' | 'minimum_body_return_bps'>,
): boolean {
  return candidate.combination === rule.combination
    && candidate.minimum_body_return_bps === rule.minimum_body_return_bps
    && sameDirections(candidate.directions, rule.directions)
}

function sameBacktestRequest(
  left: IntradayFactorBacktestRequest,
  right: IntradayFactorBacktestRequest,
): boolean {
  return left.factor === right.factor
    && left.start === right.start
    && left.end === right.end
    && left.cost_bps === right.cost_bps
    && left.minimum_body_return_bps === right.minimum_body_return_bps
    && left.combination === right.combination
    && sameDirections(left.directions, right.directions)
    && (left.exit_rule ?? 'NEXT_SESSION_OPEN') === (right.exit_rule ?? 'NEXT_SESSION_OPEN')
    && (left.exit_minimum_body_return_bps ?? '0') === (right.exit_minimum_body_return_bps ?? '0')
}

function sameDirections(
  left: readonly IntradayOpeningDirection[],
  right: readonly IntradayOpeningDirection[],
): boolean {
  return left.length === right.length
    && left.every((direction, index) => direction === right[index])
}

function isSha256(value: string): boolean {
  return /^[a-f0-9]{64}$/.test(value)
}
