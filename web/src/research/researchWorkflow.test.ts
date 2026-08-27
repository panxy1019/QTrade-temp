import { describe, expect, it } from 'vitest'
import { matchesResearchWorkflow, type ResearchWorkflowContext } from './researchWorkflow'

const context: ResearchWorkflowContext = {
  factorValidationPassed: false,
  correlationValidationPassed: true,
  robustnessValidationPassed: false,
  backtestPassed: false,
  hasExitFactorValidation: true,
}

function workflow() {
  return [
    { id: 'FACTOR_DEFINITION', status: 'AVAILABLE', detail: 'Exact expression and data contract are fixed for this run.' },
    { id: 'FACTOR_VALIDATION', status: 'FAILED', detail: 'Entry-factor tests and triggered-exit timing-advantage Newey-West tests completed.' },
    { id: 'CORRELATION_VALIDATION', status: 'PASSED', detail: 'Redundancy checks against related opening features completed.' },
    { id: 'ROBUSTNESS_VALIDATION', status: 'FAILED', detail: 'Expanding-window threshold selection and disjoint test folds completed without using future test returns.' },
    { id: 'STRATEGY_CONSTRUCTION', status: 'DIAGNOSTIC_ONLY', detail: 'Entry, exit, sizing, cost and benchmark rules are explicit, but strategy construction is not admitted because at least one prerequisite research stage failed.' },
    { id: 'CAUSAL_BACKTEST', status: 'FAILED', detail: '75-stock and 15-sector causal replay completed.' },
    { id: 'PAPER', status: 'NOT_ELIGIBLE', detail: 'No authorized forward realtime observation is attached.' },
    { id: 'LIVE', status: 'DISABLED', detail: 'Separate explicit user authorization is required.' },
  ]
}

describe('matchesResearchWorkflow', () => {
  it('admits the exact eight-stage conditional-exit workflow', () => {
    expect(matchesResearchWorkflow(workflow(), context)).toBe(true)
  })

  it('derives the research-admitted and successful-backtest branch', () => {
    const actual = workflow()
    actual[1] = { ...actual[1], status: 'PASSED' }
    actual[3] = { ...actual[3], status: 'PASSED' }
    actual[4] = {
      ...actual[4],
      status: 'RESEARCH_ADMITTED_PREVIEW',
      detail: 'All prerequisite research stages passed; the strategy remains a read-only preview pending causal backtest and immutable registration.',
    }
    actual[5] = { ...actual[5], status: 'PASSED' }
    expect(matchesResearchWorkflow(actual, {
      ...context,
      factorValidationPassed: true,
      robustnessValidationPassed: true,
      backtestPassed: true,
    })).toBe(true)
  })

  it('distinguishes an entry-only factor validation detail', () => {
    const actual = workflow()
    actual[1] = {
      ...actual[1],
      detail: 'Cross-sectional IC, session-clustered Newey-West and four contiguous-block stability tests completed.',
    }
    expect(matchesResearchWorkflow(actual, { ...context, hasExitFactorValidation: false })).toBe(true)
  })

  it('rejects forged, reordered, extra, or non-boolean workflow state', () => {
    const forged = workflow()
    forged[1] = { ...forged[1], status: 'PASSED' }
    expect(matchesResearchWorkflow(forged, context)).toBe(false)
    expect(matchesResearchWorkflow([...workflow()].reverse(), context)).toBe(false)
    expect(matchesResearchWorkflow([...workflow(), { id: 'ORDER', status: 'ENABLED', detail: 'forged' }], context)).toBe(false)
    expect(matchesResearchWorkflow(workflow(), {
      ...context,
      robustnessValidationPassed: 'false' as unknown as boolean,
    })).toBe(false)
  })
})
