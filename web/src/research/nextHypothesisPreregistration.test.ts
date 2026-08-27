import { describe, expect, it } from 'vitest'
import {
  matchesNextHypothesisPreregistration,
  type NextHypothesisPreregistrationContext,
} from './nextHypothesisPreregistration'

const context: NextHypothesisPreregistrationContext = {
  strategyId: `adhoc-intraday-${'e'.repeat(16)}`,
  strategySpecHash: 'e'.repeat(64),
  dataVersion: 'baostock-five-minute-long-v1',
  datasetChecksum: 'long-five-minute-checksum',
  viewedStart: '2026-07-28',
  viewedEnd: '2026-08-24',
  firstBlockingStage: 'FACTOR_VALIDATION',
  nextAllowedAction: 'PREDECLARE_NEW_HYPOTHESIS_BEFORE_NEW_EVIDENCE',
}

function contract() {
  return {
    classification: 'READ_ONLY_NEXT_HYPOTHESIS_PREREGISTRATION_PREFLIGHT',
    status: 'REQUIRED_NOT_CREATED',
    current_result_binding: {
      strategy_id: `adhoc-intraday-${'e'.repeat(16)}`,
      strategy_spec_hash: 'e'.repeat(64),
      data_version: 'baostock-five-minute-long-v1',
      dataset_checksum: 'long-five-minute-checksum',
      viewed_evidence_period: { start: '2026-07-28', end: '2026-08-24' },
      first_blocking_stage: 'FACTOR_VALIDATION',
      next_allowed_action: 'PREDECLARE_NEW_HYPOTHESIS_BEFORE_NEW_EVIDENCE',
    },
    current_result_may_seed_motivation: true,
    current_result_may_seed_new_parameters: false,
    required_hypothesis_fields: ['hypothesis_id', 'motivation', 'economic_intuition', 'expected_regimes', 'expected_failure_modes', 'required_data', 'evaluation_criteria'],
    required_experiment_lock_fields: ['experiment_id', 'strategy_id', 'git_commit', 'data_version', 'universe', 'factor_set', 'label', 'split', 'model', 'parameters', 'cost_model_id', 'transaction_cost', 'seed'],
    required_governance_gates: [
      { gate_id: 'new_hypothesis_identity', required: true, requirement: 'Use a hypothesis_id not evaluated before.' },
      { gate_id: 'new_evidence_period_disjoint_from_viewed_and_prior_test_periods', required: true, requirement: 'Use a new evidence period disjoint from this viewed window and prior held-out test periods.' },
      { gate_id: 'independent_leakage_review_pass', required: true, requirement: 'Attach an independent leakage review PASS.' },
      { gate_id: 'fixed_candidate_budget', required: true, requirement: 'Fix the candidate budget before data access.' },
      { gate_id: 'fixed_robustness_scenarios', required: true, requirement: 'Fix bounded robustness scenarios before data access.' },
      { gate_id: 'fixed_cost_model', required: true, requirement: 'Fix the cost model before data access.' },
      { gate_id: 'immutable_spec_and_hash_before_data_access', required: true, requirement: 'Freeze the experiment specification and hash before new evidence is read.' },
      { gate_id: 'contamination_disclosure', required: true, requirement: 'Disclose all prior evidence used to motivate the new hypothesis.' },
    ],
    evidence_window_contract: {
      must_be_new_and_disjoint: true,
      current_viewed_period_excluded_from_new_confirmation: true,
      prior_test_period_reuse_for_candidate_selection_allowed: false,
    },
    strategy_registry_bridge: {
      preregistration_is_registry_insertion: false,
      registry_write_performed: false,
      earliest_status_after_completed_preregistration_and_leakage_review: 'IDEA',
      legal_lifecycle: ['IDEA', 'EXPERIMENT', 'CANDIDATE', 'VALIDATED', 'PAPER', 'SHADOW', 'APPROVED', 'CHAMPION'],
      human_authorization_required_for: ['APPROVED', 'CHAMPION'],
      current_lifecycle_status: 'NOT_REGISTERED',
    },
    preregistration_created: false,
    state_mutation: false,
    registry_write: false,
    paper_runtime_start: false,
    live_runtime_start: false,
    external_order_transmission: false,
  }
}

describe('matchesNextHypothesisPreregistration', () => {
  it('admits the exact blocked preregistration contract', () => {
    expect(matchesNextHypothesisPreregistration(contract(), context)).toBe(true)
  })

  it('derives the research-admitted status from the sequential next action', () => {
    const actual = contract()
    actual.status = 'NOT_REQUIRED_CURRENT_RESULT_RESEARCH_ADMITTED'
    actual.current_result_binding.first_blocking_stage = 'STRATEGY_REGISTRATION'
    actual.current_result_binding.next_allowed_action = 'REGISTER_IMMUTABLE_STRATEGY_THEN_AUTHORIZED_FORWARD_SHADOW'
    expect(matchesNextHypothesisPreregistration(actual, {
      ...context,
      firstBlockingStage: 'STRATEGY_REGISTRATION',
      nextAllowedAction: 'REGISTER_IMMUTABLE_STRATEGY_THEN_AUTHORIZED_FORWARD_SHADOW',
    })).toBe(true)
  })

  it('rejects missing, reordered, or drifted governance requirements', () => {
    expect(matchesNextHypothesisPreregistration({
      ...contract(),
      required_governance_gates: contract().required_governance_gates.slice(0, 7),
    }, context)).toBe(false)
    const reordered = contract()
    reordered.required_hypothesis_fields = [...reordered.required_hypothesis_fields].reverse()
    expect(matchesNextHypothesisPreregistration(reordered, context)).toBe(false)
    const drifted = contract()
    drifted.required_governance_gates[0] = { ...drifted.required_governance_gates[0], required: false }
    expect(matchesNextHypothesisPreregistration(drifted, context)).toBe(false)
  })

  it('rejects evidence reuse, lifecycle, or authority drift', () => {
    expect(matchesNextHypothesisPreregistration({
      ...contract(),
      evidence_window_contract: {
        ...contract().evidence_window_contract,
        prior_test_period_reuse_for_candidate_selection_allowed: true,
      },
    }, context)).toBe(false)
    expect(matchesNextHypothesisPreregistration({
      ...contract(),
      strategy_registry_bridge: {
        ...contract().strategy_registry_bridge,
        current_lifecycle_status: 'IDEA',
      },
    }, context)).toBe(false)
    expect(matchesNextHypothesisPreregistration({ ...contract(), preregistration_created: true }, context)).toBe(false)
  })
})
