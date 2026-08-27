import { describe, expect, it } from 'vitest'
import {
  EXPECTED_UNAVAILABLE_L2_FACTOR_READINESS,
  matchesUnavailableL2FactorReadiness,
} from './l2FactorReadiness'

function validReadiness() {
  return structuredClone(EXPECTED_UNAVAILABLE_L2_FACTOR_READINESS)
}

describe('unavailable L2 factor readiness admission', () => {
  it('admits the exact fail-closed five-level contract', () => {
    expect(matchesUnavailableL2FactorReadiness(validReadiness())).toBe(true)
  })

  it('rejects a contract with an incomplete five-level schema', () => {
    const value = validReadiness()
    value.normalized_input_contract.ask_size_fields.pop()

    expect(matchesUnavailableL2FactorReadiness(value)).toBe(false)
  })

  it('rejects forged readiness gate arithmetic', () => {
    const value = validReadiness()
    value.required_gates[0].passed = true
    value.passed_gate_count = 1

    expect(matchesUnavailableL2FactorReadiness(value)).toBe(false)
  })

  it('rejects future-book or OHLCV substitution capability', () => {
    const value = validReadiness() as unknown as {
      causal_contract: { future_book_used: boolean }
      derived_from_ohlcv: boolean
    }
    value.causal_contract.future_book_used = true
    value.derived_from_ohlcv = true

    expect(matchesUnavailableL2FactorReadiness(value)).toBe(false)
  })
})
