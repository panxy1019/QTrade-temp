import { render, screen } from '@testing-library/react'
import { describe, expect, it } from 'vitest'
import type { FactorSearchReport, IntradayFactorLongWindowReport } from '../types'
import { buildEvidenceFactorCatalog } from '../research/factorEvidenceCatalog'
import { FactorEvidenceCatalog } from './FactorEvidenceCatalog'

const factorSearch = {
  classification: 'SIGNED_DAILY_FACTOR_SEARCH',
  promotion: 'NOT_ELIGIBLE', promotion_reasons: ['VIEWED_CONFIRMATION_WINDOW'],
  factor_selection: {
    passing_factor_ids: ['momentum-252-skip-20'],
    discovery_start: '2023-01-01', discovery_end: '2024-12-31',
  },
  factor_diagnostics: [
    { factor_id: 'momentum-252-skip-20', periods: 100, instruments_minimum: 30, metrics: {} },
    { factor_id: 'reversal-5', periods: 100, instruments_minimum: 30, metrics: {} },
  ],
  validation: { scores: [] }, test: {},
} as unknown as FactorSearchReport

const intradayReport = {
  schema_version: 1,
  report_id: 'opening-triple-bullish-long-window-robustness-v1',
  dataset: { checksum: 'dataset-checksum' },
  result_preview_hash: 'a'.repeat(64),
  result: {
    factor_definition: {
      factor_id: 'opening-three-ten-minute-bullish-v1', name: '09:30 起三根十分钟阳线',
      expression: 'bullish_10m_0930_0940 AND bullish_10m_0940_0950 AND bullish_10m_0950_1000',
      source_bars: '09:35,09:40,09:45,09:50,09:55,10:00', known_at: '10:00_BAR_CLOSE',
    },
    workflow: [{ id: 'FACTOR_VALIDATION', status: 'FAILED', detail: 'negative excess' }],
    strategy: {
      exit_factor_id: 'next-session-three-ten-minute-bearish-v1',
      exit_condition: 'bearish_10m_0930_0940 AND bearish_10m_0940_0950 AND bearish_10m_0950_1000',
      exit_condition_known_at: 'NEXT_SESSION_10:00_BAR_CLOSE',
      conditional_exit_price_source: 'NEXT_SESSION_10:05_OPEN',
    },
    exit_factor_validation: { passed: false },
    l2_factor_readiness: {
      classification: 'READ_ONLY_L2_FACTOR_DATA_CONTRACT',
      factor_id: 'order-book-buying-intensity-v1',
      formula: '(bid_size_1+...+bid_size_5)/(ask_size_1+...+ask_size_5)',
      buying_hypothesis: { condition: 'buying_intensity > 1', validation_status: 'UNTESTED_NO_AUTHORIZED_L2_DATA' },
      sell_condition_status: 'NOT_DEFINED_REQUIRES_SEPARATE_RESEARCH',
      required_data: 'AUTHORIZED_L2_FIVE_LEVEL_ORDER_BOOK', readiness: 'UNCONFIGURED',
      causal_contract: { decision_clock: 'FINALIZED_MINUTE_CORRECTION' },
      passed_gate_count: 0, required_gate_count: 6,
    },
  },
  summary: { research_gate_passed: false }, promotion: 'NOT_ELIGIBLE',
} as unknown as IntradayFactorLongWindowReport

describe('factor evidence catalog', () => {
  it('binds daily, entry, exit and unavailable L2 definitions without inventing readiness', () => {
    const rows = buildEvidenceFactorCatalog(factorSearch, intradayReport)
    expect(rows.map((row) => row.factor_id)).toEqual([
      'momentum-252-skip-20',
      'reversal-5',
      'opening-three-ten-minute-bullish-v1',
      'next-session-three-ten-minute-bearish-v1',
      'order-book-buying-intensity-v1',
    ])
    expect(rows[0]).toMatchObject({
      formula: 'close_(t-20) / close_(t-272) - 1',
      validation_status: '发现期单因子门槛通过；全局 NOT_ELIGIBLE',
    })
    expect(rows.find((row) => row.factor_id === 'opening-three-ten-minute-bullish-v1')).toMatchObject({
      known_at: '10:00_BAR_CLOSE', earliest_execution: 'NEXT_5M_OPEN_10:05',
      validation_status: '长期有效性/稳健性未通过',
    })
    expect(rows.find((row) => row.factor_id === 'order-book-buying-intensity-v1')).toMatchObject({
      data_status: 'UNCONFIGURED · 0/6 数据门',
      validation_status: 'UNTESTED_NO_AUTHORIZED_L2_DATA',
    })
  })

  it('renders a read-only exportable catalog and does not synthesize L2 without its contract', () => {
    render(<FactorEvidenceCatalog factorSearch={factorSearch} intradayReport={{ ...intradayReport, result: null }} />)
    expect(screen.getByText('因子定义与数据契约目录 · 3')).toBeInTheDocument()
    expect(screen.getByText('只读证据索引，不是 FactorRegistry')).toBeInTheDocument()
    expect(screen.getByText('momentum-252-skip-20')).toBeInTheDocument()
    expect(screen.getByText('opening-three-ten-minute-bullish-v1')).toBeInTheDocument()
    expect(screen.queryByText('order-book-buying-intensity-v1')).not.toBeInTheDocument()
    expect(screen.getByRole('button', { name: /导出因子目录/ })).toBeInTheDocument()
  })
})
