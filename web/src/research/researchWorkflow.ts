import type { IntradayFactorBacktest } from '../types'

type ResearchWorkflow = IntradayFactorBacktest['workflow']

export interface ResearchWorkflowContext {
  factorValidationPassed: boolean
  correlationValidationPassed: boolean
  robustnessValidationPassed: boolean
  backtestPassed: boolean
  hasExitFactorValidation: boolean
}

export function matchesResearchWorkflow(
  actual: unknown,
  context: ResearchWorkflowContext,
): actual is ResearchWorkflow {
  const contextValues: unknown[] = [
    context.factorValidationPassed,
    context.correlationValidationPassed,
    context.robustnessValidationPassed,
    context.backtestPassed,
    context.hasExitFactorValidation,
  ]
  if (!contextValues.every((value) => typeof value === 'boolean')) return false
  const allResearchStagesPassed = context.factorValidationPassed
    && context.correlationValidationPassed
    && context.robustnessValidationPassed
  const expected: ResearchWorkflow = [
    {
      id: 'FACTOR_DEFINITION',
      status: 'AVAILABLE',
      detail: 'Exact expression and data contract are fixed for this run.',
    },
    {
      id: 'FACTOR_VALIDATION',
      status: context.factorValidationPassed ? 'PASSED' : 'FAILED',
      detail: context.hasExitFactorValidation
        ? 'Entry-factor tests and triggered-exit timing-advantage Newey-West tests completed.'
        : 'Cross-sectional IC, session-clustered Newey-West and four contiguous-block stability tests completed.',
    },
    {
      id: 'CORRELATION_VALIDATION',
      status: context.correlationValidationPassed ? 'PASSED' : 'FAILED',
      detail: 'Redundancy checks against related opening features completed.',
    },
    {
      id: 'ROBUSTNESS_VALIDATION',
      status: context.robustnessValidationPassed ? 'PASSED' : 'FAILED',
      detail: 'Expanding-window threshold selection and disjoint test folds completed without using future test returns.',
    },
    {
      id: 'STRATEGY_CONSTRUCTION',
      status: allResearchStagesPassed ? 'RESEARCH_ADMITTED_PREVIEW' : 'DIAGNOSTIC_ONLY',
      detail: allResearchStagesPassed
        ? 'All prerequisite research stages passed; the strategy remains a read-only preview pending causal backtest and immutable registration.'
        : 'Entry, exit, sizing, cost and benchmark rules are explicit, but strategy construction is not admitted because at least one prerequisite research stage failed.',
    },
    {
      id: 'CAUSAL_BACKTEST',
      status: context.backtestPassed ? 'PASSED' : 'FAILED',
      detail: '75-stock and 15-sector causal replay completed.',
    },
    {
      id: 'PAPER',
      status: 'NOT_ELIGIBLE',
      detail: 'No authorized forward realtime observation is attached.',
    },
    {
      id: 'LIVE',
      status: 'DISABLED',
      detail: 'Separate explicit user authorization is required.',
    },
  ]
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
