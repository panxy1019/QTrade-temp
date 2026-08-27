import type { IntradayFactorBacktest } from '../types'

type SequentialAdvancement = IntradayFactorBacktest['sequential_advancement']

export interface SequentialAdvancementContext {
  factorValidationPassed: boolean
  correlationValidationPassed: boolean
  robustnessValidationPassed: boolean
  backtestPassed: boolean
}

export function matchesSequentialAdvancement(
  actual: unknown,
  context: SequentialAdvancementContext,
): actual is SequentialAdvancement {
  const researchStages: SequentialAdvancement['required_research_stages'] = [
    { stage_id: 'FACTOR_VALIDATION', passed: context.factorValidationPassed },
    { stage_id: 'CORRELATION_VALIDATION', passed: context.correlationValidationPassed },
    { stage_id: 'ROBUSTNESS_VALIDATION', passed: context.robustnessValidationPassed },
  ]
  const stageResults: unknown[] = [
    context.factorValidationPassed,
    context.correlationValidationPassed,
    context.robustnessValidationPassed,
    context.backtestPassed,
  ]
  if (!stageResults.every((value) => typeof value === 'boolean')) return false
  const passedResearchStageCount = researchStages.filter((stage) => stage.passed).length
  const allResearchStagesPassed = passedResearchStageCount === researchStages.length
  const firstBlockingStage = researchStages.find((stage) => !stage.passed)?.stage_id
    ?? (context.backtestPassed ? 'STRATEGY_REGISTRATION' : 'CAUSAL_BACKTEST')
  const expected: SequentialAdvancement = {
    classification: 'READ_ONLY_SEQUENTIAL_FACTOR_TO_STRATEGY_ADVANCEMENT',
    required_research_stages: researchStages,
    passed_research_stage_count: passedResearchStageCount,
    required_research_stage_count: 3,
    all_research_stages_passed: allResearchStagesPassed,
    first_blocking_stage: firstBlockingStage,
    strategy_construction_status: allResearchStagesPassed
      ? 'RESEARCH_ADMITTED_READ_ONLY_PREVIEW'
      : 'DIAGNOSTIC_PREVIEW_ONLY',
    strategy_preview_generated: true,
    causal_backtest_diagnostic_completed: true,
    causal_backtest_passed: context.backtestPassed,
    strategy_registration_allowed: false,
    historical_result_can_authorize_paper: false,
    viewed_window_reselection_allowed: false,
    next_allowed_action: allResearchStagesPassed && context.backtestPassed
      ? 'REGISTER_IMMUTABLE_STRATEGY_THEN_AUTHORIZED_FORWARD_SHADOW'
      : 'PREDECLARE_NEW_HYPOTHESIS_BEFORE_NEW_EVIDENCE',
    state_mutation: false,
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
