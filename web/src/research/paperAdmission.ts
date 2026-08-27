import type { IntradayFactorBacktest } from '../types'

type PaperAdmission = IntradayFactorBacktest['paper_admission']

export interface BlockedPaperAdmissionContext {
  dataVersion: string
  datasetChecksum: string
  researchGatePassed: boolean
  backtestPassed: boolean
  strategyId: string
  strategySpecHash: string
  nextAllowedStage: PaperAdmission['next_allowed_stage']
}

export function matchesBlockedPaperAdmission(
  actual: unknown,
  context: BlockedPaperAdmissionContext,
): actual is PaperAdmission {
  const expected: PaperAdmission = {
    classification: 'READ_ONLY_INTRADAY_FACTOR_PAPER_ADMISSION',
    decision: 'BLOCKED',
    required_gates: [
      {
        gate_id: 'verified_historical_dataset_identity_bound',
        passed: true,
        evidence: `${context.dataVersion} · ${context.datasetChecksum}`,
      },
      {
        gate_id: 'research_gate_passed',
        passed: context.researchGatePassed,
        evidence: `research_gate.passed=${String(context.researchGatePassed)}`,
      },
      {
        gate_id: 'causal_backtest_passed',
        passed: context.backtestPassed,
        evidence: `backtest.passed=${String(context.backtestPassed)}`,
      },
      {
        gate_id: 'registered_immutable_strategy_version_available',
        passed: false,
        evidence: `AD_HOC_UNREGISTERED · ${context.strategyId} · ${context.strategySpecHash}`,
      },
      {
        gate_id: 'unseen_forward_shadow_evidence_attached',
        passed: false,
        evidence: 'NO_AUTHORIZED_FORWARD_PAPER_EVIDENCE',
      },
      {
        gate_id: 'authorized_realtime_provider_ready',
        passed: false,
        evidence: 'NO_AUTHORIZED_REALTIME_PROVIDER',
      },
      {
        gate_id: 'fresh_runtime_readiness_attestation_available',
        passed: false,
        evidence: 'NO_RUNTIME_ATTESTATION',
      },
      {
        gate_id: 'reconciliation_ready',
        passed: false,
        evidence: 'NO_BOUND_PAPER_RUNTIME',
      },
    ],
    passed_gate_count: 1 + Number(context.researchGatePassed) + Number(context.backtestPassed),
    required_gate_count: 8,
    all_required_gates_passed: false,
    next_allowed_stage: context.nextAllowedStage,
    automatic_start_allowed: false,
    runtime_mode_change: false,
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
