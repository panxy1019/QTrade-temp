import { describe, expect, it } from 'vitest'
import {
  matchesBlockedStrategyRegistrationPreflight,
  type BlockedStrategyRegistrationContext,
} from './strategyRegistrationPreflight'

const context: BlockedStrategyRegistrationContext = {
  dataVersion: 'baostock-five-minute-long-v1',
  datasetChecksum: 'long-five-minute-checksum',
  strategyId: `adhoc-intraday-${'e'.repeat(16)}`,
  strategySpecHash: 'e'.repeat(64),
  factorValidationPassed: false,
  correlationValidationPassed: true,
  robustnessValidationPassed: false,
  backtestPassed: false,
}

function contract() {
  return {
    classification: 'READ_ONLY_STRATEGY_REGISTRY_ADMISSION_PREFLIGHT',
    decision: 'BLOCKED',
    current_candidate: {
      strategy_id: `adhoc-intraday-${'e'.repeat(16)}`,
      behavior_spec_hash: 'e'.repeat(64),
      behavior_spec_hash_is_complete_registry_spec_hash: false,
      registration_status: 'AD_HOC_UNREGISTERED',
      registry_spec_hash_status: 'NOT_COMPUTABLE_REQUIRED_FIELDS_MISSING',
    },
    required_strategy_spec_fields: ['strategy_id', 'name', 'family', 'version', 'parent_strategy_id', 'git_commit', 'data_version', 'universe', 'factor_set', 'model', 'portfolio_rule', 'parameters', 'split', 'created_at', 'updated_at'],
    current_field_readiness: {
      preview_source_available_fields: ['strategy_id', 'data_version', 'universe', 'factor_set', 'portfolio_rule', 'parameters'],
      missing_registration_fields: ['name', 'family', 'version', 'parent_strategy_id', 'git_commit', 'model', 'split', 'created_at', 'updated_at'],
      available_field_count: 6,
      required_field_count: 15,
      complete: false,
    },
    required_gates: [
      { gate_id: 'all_ordered_research_stages_passed', passed: false, evidence: '1/3' },
      { gate_id: 'causal_backtest_passed', passed: false, evidence: 'backtest.passed=false' },
      { gate_id: 'signed_hypothesis_preregistration_attached', passed: false, evidence: 'NOT_ATTACHED' },
      { gate_id: 'independent_leakage_review_passed', passed: false, evidence: 'NOT_ATTACHED' },
      { gate_id: 'behavior_spec_hash_bound', passed: true, evidence: 'e'.repeat(64) },
      { gate_id: 'complete_registry_spec_and_hash_bound', passed: false, evidence: 'NOT_COMPUTABLE_REQUIRED_FIELDS_MISSING' },
      { gate_id: 'dataset_identity_bound', passed: true, evidence: 'baostock-five-minute-long-v1 · long-five-minute-checksum' },
      { gate_id: 'disjoint_time_split_frozen', passed: false, evidence: 'NOT_FROZEN' },
      { gate_id: 'lineage_root_or_parent_binding_declared', passed: false, evidence: 'NOT_DECLARED' },
    ],
    passed_gate_count: 2,
    required_gate_count: 9,
    all_required_gates_passed: false,
    earliest_registry_status: 'IDEA',
    idea_to_experiment_transition: {
      from: 'IDEA',
      to: 'EXPERIMENT',
      allowed: false,
      required_evidence: ['COMPLETED_EXPERIMENT_RUN_SPEC', 'QUALITY_PASSED_DATA_MANIFEST', 'INDEPENDENT_LEAKAGE_REVIEW_PASS'],
    },
    registration_operation_exposed: false,
    registry_write: false,
    state_mutation: false,
    paper_runtime_start: false,
    live_runtime_start: false,
    external_order_transmission: false,
  }
}

describe('matchesBlockedStrategyRegistrationPreflight', () => {
  it('admits the exact blocked nine-gate contract', () => {
    expect(matchesBlockedStrategyRegistrationPreflight(contract(), context)).toBe(true)
  })

  it('derives research and backtest gate counts dynamically', () => {
    const actual = contract()
    actual.required_gates[0] = { ...actual.required_gates[0], passed: true, evidence: '3/3' }
    actual.required_gates[1] = { ...actual.required_gates[1], passed: true, evidence: 'backtest.passed=true' }
    actual.passed_gate_count = 4
    expect(matchesBlockedStrategyRegistrationPreflight(actual, {
      ...context,
      factorValidationPassed: true,
      robustnessValidationPassed: true,
      backtestPassed: true,
    })).toBe(true)
  })

  it('rejects forged counts or a duplicated gate', () => {
    expect(matchesBlockedStrategyRegistrationPreflight({ ...contract(), passed_gate_count: 8 }, context)).toBe(false)
    const duplicated = contract()
    duplicated.required_gates[1] = { ...duplicated.required_gates[0] }
    expect(matchesBlockedStrategyRegistrationPreflight(duplicated, context)).toBe(false)
  })

  it('rejects field-readiness, transition, or authority drift', () => {
    expect(matchesBlockedStrategyRegistrationPreflight({
      ...contract(),
      current_field_readiness: { ...contract().current_field_readiness, available_field_count: 15 },
    }, context)).toBe(false)
    expect(matchesBlockedStrategyRegistrationPreflight({
      ...contract(),
      idea_to_experiment_transition: { ...contract().idea_to_experiment_transition, allowed: true },
    }, context)).toBe(false)
    expect(matchesBlockedStrategyRegistrationPreflight({ ...contract(), registration_operation_exposed: true }, context)).toBe(false)
    expect(matchesBlockedStrategyRegistrationPreflight(contract(), {
      ...context,
      factorValidationPassed: 'false' as unknown as boolean,
    })).toBe(false)
  })
})
