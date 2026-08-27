import { describe, expect, it } from 'vitest'
import {
  matchesSequentialAdvancement,
  type SequentialAdvancementContext,
} from './sequentialAdvancement'

const context: SequentialAdvancementContext = {
  factorValidationPassed: false,
  correlationValidationPassed: true,
  robustnessValidationPassed: false,
  backtestPassed: false,
}

function contract() {
  return {
    classification: 'READ_ONLY_SEQUENTIAL_FACTOR_TO_STRATEGY_ADVANCEMENT',
    required_research_stages: [
      { stage_id: 'FACTOR_VALIDATION', passed: false },
      { stage_id: 'CORRELATION_VALIDATION', passed: true },
      { stage_id: 'ROBUSTNESS_VALIDATION', passed: false },
    ],
    passed_research_stage_count: 1,
    required_research_stage_count: 3,
    all_research_stages_passed: false,
    first_blocking_stage: 'FACTOR_VALIDATION',
    strategy_construction_status: 'DIAGNOSTIC_PREVIEW_ONLY',
    strategy_preview_generated: true,
    causal_backtest_diagnostic_completed: true,
    causal_backtest_passed: false,
    strategy_registration_allowed: false,
    historical_result_can_authorize_paper: false,
    viewed_window_reselection_allowed: false,
    next_allowed_action: 'PREDECLARE_NEW_HYPOTHESIS_BEFORE_NEW_EVIDENCE',
    state_mutation: false,
    external_order_transmission: false,
  }
}

describe('matchesSequentialAdvancement', () => {
  it('admits the exact ordered read-only advancement contract', () => {
    expect(matchesSequentialAdvancement(contract(), context)).toBe(true)
  })

  it('derives the admitted research and successful-backtest branch', () => {
    expect(matchesSequentialAdvancement({
      ...contract(),
      required_research_stages: [
        { stage_id: 'FACTOR_VALIDATION', passed: true },
        { stage_id: 'CORRELATION_VALIDATION', passed: true },
        { stage_id: 'ROBUSTNESS_VALIDATION', passed: true },
      ],
      passed_research_stage_count: 3,
      all_research_stages_passed: true,
      first_blocking_stage: 'STRATEGY_REGISTRATION',
      strategy_construction_status: 'RESEARCH_ADMITTED_READ_ONLY_PREVIEW',
      causal_backtest_passed: true,
      next_allowed_action: 'REGISTER_IMMUTABLE_STRATEGY_THEN_AUTHORIZED_FORWARD_SHADOW',
    }, {
      factorValidationPassed: true,
      correlationValidationPassed: true,
      robustnessValidationPassed: true,
      backtestPassed: true,
    })).toBe(true)
  })

  it('rejects forged counts, reordered stages, or inconsistent blocking stages', () => {
    expect(matchesSequentialAdvancement({ ...contract(), passed_research_stage_count: 2 }, context)).toBe(false)
    const reordered = contract()
    reordered.required_research_stages = [...reordered.required_research_stages].reverse()
    expect(matchesSequentialAdvancement(reordered, context)).toBe(false)
    expect(matchesSequentialAdvancement({ ...contract(), first_blocking_stage: 'ROBUSTNESS_VALIDATION' }, context)).toBe(false)
  })

  it('rejects promotion authority, extra fields, or non-boolean context drift', () => {
    expect(matchesSequentialAdvancement({ ...contract(), strategy_registration_allowed: true }, context)).toBe(false)
    expect(matchesSequentialAdvancement({ ...contract(), unexpected_authority: true }, context)).toBe(false)
    expect(matchesSequentialAdvancement(contract(), {
      ...context,
      robustnessValidationPassed: 'false' as unknown as boolean,
    })).toBe(false)
  })
})
