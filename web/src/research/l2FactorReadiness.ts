import type { IntradayFactorBacktest } from '../types'

type L2FactorReadiness = IntradayFactorBacktest['l2_factor_readiness']

export const EXPECTED_UNAVAILABLE_L2_FACTOR_READINESS: L2FactorReadiness = {
  classification: 'READ_ONLY_L2_FACTOR_DATA_CONTRACT',
  factor_id: 'order-book-buying-intensity-v1',
  formula: '(bid_size_1+...+bid_size_5)/(ask_size_1+...+ask_size_5)',
  buying_hypothesis: {
    condition: 'buying_intensity > 1',
    interpretation: 'BUY_SIDE_QUANTITY_EXCEEDS_SELL_SIDE_QUANTITY',
    validation_status: 'UNTESTED_NO_AUTHORIZED_L2_DATA',
  },
  sell_condition_status: 'NOT_DEFINED_REQUIRES_SEPARATE_RESEARCH',
  required_data: 'AUTHORIZED_L2_FIVE_LEVEL_ORDER_BOOK',
  normalized_input_contract: {
    level_count: 5,
    bid_price_fields: ['bid_price_1', 'bid_price_2', 'bid_price_3', 'bid_price_4', 'bid_price_5'],
    bid_size_fields: ['bid_size_1', 'bid_size_2', 'bid_size_3', 'bid_size_4', 'bid_size_5'],
    ask_price_fields: ['ask_price_1', 'ask_price_2', 'ask_price_3', 'ask_price_4', 'ask_price_5'],
    ask_size_fields: ['ask_size_1', 'ask_size_2', 'ask_size_3', 'ask_size_4', 'ask_size_5'],
    size_unit: 'SHARES',
    required_identity_fields: ['symbol', 'exchange', 'provider_event_at', 'received_at', 'sequence'],
    allowed_session: 'CONTINUOUS_AUCTION',
  },
  invalid_snapshot_policy: {
    missing_or_non_finite_value: 'REJECT',
    negative_size: 'REJECT',
    zero_total_ask_size: 'REJECT_FACTOR_UNDEFINED',
    non_monotonic_or_crossed_book: 'REJECT',
    stale_snapshot: 'REJECT',
    future_snapshot: 'REJECT',
  },
  causal_contract: {
    decision_clock: 'FINALIZED_MINUTE_CORRECTION',
    snapshot_must_be_known_before_decision: true,
    same_symbol_snapshot: true,
    same_provider_snapshot: true,
    future_book_used: false,
    freshness_limit: 'REQUIRES_PROVIDER_CERTIFICATION',
  },
  required_gates: [
    { gate_id: 'authorized_l2_provider_selected', passed: false, reason: 'NO_AUTHORIZED_L2_PROVIDER' },
    { gate_id: 'five_level_schema_certified', passed: false, reason: 'PROVIDER_SCHEMA_NOT_CERTIFIED' },
    { gate_id: 'size_unit_normalized_to_shares', passed: false, reason: 'PROVIDER_SIZE_UNIT_NOT_CERTIFIED' },
    { gate_id: 'event_time_and_sequence_available', passed: false, reason: 'PROVIDER_CLOCK_NOT_CERTIFIED' },
    { gate_id: 'freshness_threshold_certified', passed: false, reason: 'PROVIDER_FRESHNESS_NOT_CERTIFIED' },
    { gate_id: 'recorded_replay_reconciliation_passed', passed: false, reason: 'NO_AUTHORIZED_RECORDED_L2_SESSION' },
  ],
  passed_gate_count: 0,
  required_gate_count: 6,
  readiness: 'UNCONFIGURED',
  reason: 'NO_AUTHORIZED_L2_PROVIDER',
  factor_effectiveness_tested: false,
  correlation_tested: false,
  strategy_backtested: false,
  derived_from_ohlcv: false,
  state_mutation: false,
  external_order_transmission: false,
}

export function matchesUnavailableL2FactorReadiness(value: unknown): value is L2FactorReadiness {
  if (!sameContract(value, EXPECTED_UNAVAILABLE_L2_FACTOR_READINESS)) return false
  const readiness = value as L2FactorReadiness
  return readiness.required_gate_count === readiness.required_gates.length
    && readiness.passed_gate_count === readiness.required_gates.filter((gate) => gate.passed).length
}

function sameContract(actual: unknown, expected: unknown): boolean {
  if (Array.isArray(expected)) {
    return Array.isArray(actual)
      && actual.length === expected.length
      && expected.every((item, index) => sameContract(actual[index], item))
  }
  if (isRecord(expected)) {
    if (!isRecord(actual)) return false
    const expectedKeys = Object.keys(expected)
    const actualKeys = Object.keys(actual)
    return actualKeys.length === expectedKeys.length
      && expectedKeys.every((key) => Object.hasOwn(actual, key) && sameContract(actual[key], expected[key]))
  }
  return Object.is(actual, expected)
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === 'object' && value !== null && !Array.isArray(value)
}
