import { describe, expect, it } from 'vitest'
import { matchesBlockedPaperAdmission, type BlockedPaperAdmissionContext } from './paperAdmission'

const context: BlockedPaperAdmissionContext = {
  dataVersion: 'baostock-five-minute-long-v1',
  datasetChecksum: 'long-five-minute-checksum',
  researchGatePassed: false,
  backtestPassed: false,
  strategyId: `adhoc-intraday-${'e'.repeat(16)}`,
  strategySpecHash: 'e'.repeat(64),
  nextAllowedStage: 'PREDECLARE_NEW_HYPOTHESIS_BEFORE_NEW_EVIDENCE',
}

function contract() {
  return {
    classification: 'READ_ONLY_INTRADAY_FACTOR_PAPER_ADMISSION',
    decision: 'BLOCKED',
    required_gates: [
      { gate_id: 'verified_historical_dataset_identity_bound', passed: true, evidence: 'baostock-five-minute-long-v1 · long-five-minute-checksum' },
      { gate_id: 'research_gate_passed', passed: false, evidence: 'research_gate.passed=false' },
      { gate_id: 'causal_backtest_passed', passed: false, evidence: 'backtest.passed=false' },
      { gate_id: 'registered_immutable_strategy_version_available', passed: false, evidence: `AD_HOC_UNREGISTERED · adhoc-intraday-${'e'.repeat(16)} · ${'e'.repeat(64)}` },
      { gate_id: 'unseen_forward_shadow_evidence_attached', passed: false, evidence: 'NO_AUTHORIZED_FORWARD_PAPER_EVIDENCE' },
      { gate_id: 'authorized_realtime_provider_ready', passed: false, evidence: 'NO_AUTHORIZED_REALTIME_PROVIDER' },
      { gate_id: 'fresh_runtime_readiness_attestation_available', passed: false, evidence: 'NO_RUNTIME_ATTESTATION' },
      { gate_id: 'reconciliation_ready', passed: false, evidence: 'NO_BOUND_PAPER_RUNTIME' },
    ],
    passed_gate_count: 1,
    required_gate_count: 8,
    all_required_gates_passed: false,
    next_allowed_stage: 'PREDECLARE_NEW_HYPOTHESIS_BEFORE_NEW_EVIDENCE',
    automatic_start_allowed: false,
    runtime_mode_change: false,
    state_mutation: false,
    external_order_transmission: false,
  }
}

describe('matchesBlockedPaperAdmission', () => {
  it('admits the exact blocked eight-gate contract', () => {
    expect(matchesBlockedPaperAdmission(contract(), context)).toBe(true)
  })

  it('rejects forged gate-count arithmetic', () => {
    expect(matchesBlockedPaperAdmission({ ...contract(), passed_gate_count: 7 }, context)).toBe(false)
  })

  it('rejects a duplicated gate that hides a required gate', () => {
    const actual = contract()
    actual.required_gates[1] = { ...actual.required_gates[0] }
    expect(matchesBlockedPaperAdmission(actual, context)).toBe(false)
  })

  it('rejects drifted evidence or an extra authority field', () => {
    const actual = contract()
    actual.required_gates[3] = { ...actual.required_gates[3], evidence: 'REGISTERED' }
    expect(matchesBlockedPaperAdmission(actual, context)).toBe(false)
    expect(matchesBlockedPaperAdmission({ ...contract(), paper_runtime_start: true }, context)).toBe(false)
  })
})
