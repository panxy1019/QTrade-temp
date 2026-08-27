import type { IntradayFactorBacktest } from '../types'

type RegistrationPreflight = IntradayFactorBacktest['strategy_registration_preflight']

export interface BlockedStrategyRegistrationContext {
  dataVersion: string
  datasetChecksum: string
  strategyId: string
  strategySpecHash: string
  factorValidationPassed: boolean
  correlationValidationPassed: boolean
  robustnessValidationPassed: boolean
  backtestPassed: boolean
}

export function matchesBlockedStrategyRegistrationPreflight(
  actual: unknown,
  context: BlockedStrategyRegistrationContext,
): actual is RegistrationPreflight {
  const stageResults: unknown[] = [
    context.factorValidationPassed,
    context.correlationValidationPassed,
    context.robustnessValidationPassed,
    context.backtestPassed,
  ]
  if (!stageResults.every((value) => typeof value === 'boolean')) return false
  const passedResearchStageCount = stageResults.slice(0, 3).filter((value) => value === true).length
  const allResearchStagesPassed = passedResearchStageCount === 3
  const expected: RegistrationPreflight = {
    classification: 'READ_ONLY_STRATEGY_REGISTRY_ADMISSION_PREFLIGHT',
    decision: 'BLOCKED',
    current_candidate: {
      strategy_id: context.strategyId,
      behavior_spec_hash: context.strategySpecHash,
      behavior_spec_hash_is_complete_registry_spec_hash: false,
      registration_status: 'AD_HOC_UNREGISTERED',
      registry_spec_hash_status: 'NOT_COMPUTABLE_REQUIRED_FIELDS_MISSING',
    },
    required_strategy_spec_fields: [
      'strategy_id',
      'name',
      'family',
      'version',
      'parent_strategy_id',
      'git_commit',
      'data_version',
      'universe',
      'factor_set',
      'model',
      'portfolio_rule',
      'parameters',
      'split',
      'created_at',
      'updated_at',
    ],
    current_field_readiness: {
      preview_source_available_fields: [
        'strategy_id',
        'data_version',
        'universe',
        'factor_set',
        'portfolio_rule',
        'parameters',
      ],
      missing_registration_fields: [
        'name',
        'family',
        'version',
        'parent_strategy_id',
        'git_commit',
        'model',
        'split',
        'created_at',
        'updated_at',
      ],
      available_field_count: 6,
      required_field_count: 15,
      complete: false,
    },
    required_gates: [
      {
        gate_id: 'all_ordered_research_stages_passed',
        passed: allResearchStagesPassed,
        evidence: `${passedResearchStageCount}/3`,
      },
      {
        gate_id: 'causal_backtest_passed',
        passed: context.backtestPassed,
        evidence: `backtest.passed=${String(context.backtestPassed)}`,
      },
      {
        gate_id: 'signed_hypothesis_preregistration_attached',
        passed: false,
        evidence: 'NOT_ATTACHED',
      },
      {
        gate_id: 'independent_leakage_review_passed',
        passed: false,
        evidence: 'NOT_ATTACHED',
      },
      {
        gate_id: 'behavior_spec_hash_bound',
        passed: true,
        evidence: context.strategySpecHash,
      },
      {
        gate_id: 'complete_registry_spec_and_hash_bound',
        passed: false,
        evidence: 'NOT_COMPUTABLE_REQUIRED_FIELDS_MISSING',
      },
      {
        gate_id: 'dataset_identity_bound',
        passed: true,
        evidence: `${context.dataVersion} · ${context.datasetChecksum}`,
      },
      {
        gate_id: 'disjoint_time_split_frozen',
        passed: false,
        evidence: 'NOT_FROZEN',
      },
      {
        gate_id: 'lineage_root_or_parent_binding_declared',
        passed: false,
        evidence: 'NOT_DECLARED',
      },
    ],
    passed_gate_count: 2 + Number(allResearchStagesPassed) + Number(context.backtestPassed),
    required_gate_count: 9,
    all_required_gates_passed: false,
    earliest_registry_status: 'IDEA',
    idea_to_experiment_transition: {
      from: 'IDEA',
      to: 'EXPERIMENT',
      allowed: false,
      required_evidence: [
        'COMPLETED_EXPERIMENT_RUN_SPEC',
        'QUALITY_PASSED_DATA_MANIFEST',
        'INDEPENDENT_LEAKAGE_REVIEW_PASS',
      ],
    },
    registration_operation_exposed: false,
    registry_write: false,
    state_mutation: false,
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
