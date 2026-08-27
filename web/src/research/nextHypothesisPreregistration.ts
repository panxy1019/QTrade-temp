import type { IntradayFactorBacktest } from '../types'

type Preregistration = IntradayFactorBacktest['next_hypothesis_preregistration']
type Advancement = IntradayFactorBacktest['sequential_advancement']

export interface NextHypothesisPreregistrationContext {
  strategyId: string
  strategySpecHash: string
  dataVersion: string
  datasetChecksum: string
  viewedStart: string
  viewedEnd: string
  firstBlockingStage: Advancement['first_blocking_stage']
  nextAllowedAction: Advancement['next_allowed_action']
}

export function matchesNextHypothesisPreregistration(
  actual: unknown,
  context: NextHypothesisPreregistrationContext,
): actual is Preregistration {
  const expected: Preregistration = {
    classification: 'READ_ONLY_NEXT_HYPOTHESIS_PREREGISTRATION_PREFLIGHT',
    status: context.nextAllowedAction === 'PREDECLARE_NEW_HYPOTHESIS_BEFORE_NEW_EVIDENCE'
      ? 'REQUIRED_NOT_CREATED'
      : 'NOT_REQUIRED_CURRENT_RESULT_RESEARCH_ADMITTED',
    current_result_binding: {
      strategy_id: context.strategyId,
      strategy_spec_hash: context.strategySpecHash,
      data_version: context.dataVersion,
      dataset_checksum: context.datasetChecksum,
      viewed_evidence_period: {
        start: context.viewedStart,
        end: context.viewedEnd,
      },
      first_blocking_stage: context.firstBlockingStage,
      next_allowed_action: context.nextAllowedAction,
    },
    current_result_may_seed_motivation: true,
    current_result_may_seed_new_parameters: false,
    required_hypothesis_fields: [
      'hypothesis_id',
      'motivation',
      'economic_intuition',
      'expected_regimes',
      'expected_failure_modes',
      'required_data',
      'evaluation_criteria',
    ],
    required_experiment_lock_fields: [
      'experiment_id',
      'strategy_id',
      'git_commit',
      'data_version',
      'universe',
      'factor_set',
      'label',
      'split',
      'model',
      'parameters',
      'cost_model_id',
      'transaction_cost',
      'seed',
    ],
    required_governance_gates: [
      {
        gate_id: 'new_hypothesis_identity',
        required: true,
        requirement: 'Use a hypothesis_id not evaluated before.',
      },
      {
        gate_id: 'new_evidence_period_disjoint_from_viewed_and_prior_test_periods',
        required: true,
        requirement: 'Use a new evidence period disjoint from this viewed window and prior held-out test periods.',
      },
      {
        gate_id: 'independent_leakage_review_pass',
        required: true,
        requirement: 'Attach an independent leakage review PASS.',
      },
      {
        gate_id: 'fixed_candidate_budget',
        required: true,
        requirement: 'Fix the candidate budget before data access.',
      },
      {
        gate_id: 'fixed_robustness_scenarios',
        required: true,
        requirement: 'Fix bounded robustness scenarios before data access.',
      },
      {
        gate_id: 'fixed_cost_model',
        required: true,
        requirement: 'Fix the cost model before data access.',
      },
      {
        gate_id: 'immutable_spec_and_hash_before_data_access',
        required: true,
        requirement: 'Freeze the experiment specification and hash before new evidence is read.',
      },
      {
        gate_id: 'contamination_disclosure',
        required: true,
        requirement: 'Disclose all prior evidence used to motivate the new hypothesis.',
      },
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
      legal_lifecycle: [
        'IDEA',
        'EXPERIMENT',
        'CANDIDATE',
        'VALIDATED',
        'PAPER',
        'SHADOW',
        'APPROVED',
        'CHAMPION',
      ],
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
  return sameContract(actual, expected)
}

function sameContract(actual: unknown, expected: unknown): boolean {
  if (Array.isArray(expected)) {
    return Array.isArray(actual)
      && actual.length === expected.length
      && expected.every((item, index) => sameContract(actual[index], item))
  }
  if (expected !== null && typeof expected === 'object') {
    if (actual === null || typeof actual !== 'object' || Array.isArray(actual)) return false
    const actualRecord = actual as Record<string, unknown>
    const expectedRecord = expected as Record<string, unknown>
    const actualKeys = Object.keys(actualRecord)
    const expectedKeys = Object.keys(expectedRecord)
    return actualKeys.length === expectedKeys.length
      && expectedKeys.every((key) => Object.hasOwn(actualRecord, key)
        && sameContract(actualRecord[key], expectedRecord[key]))
  }
  return Object.is(actual, expected)
}
