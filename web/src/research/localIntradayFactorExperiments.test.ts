import { beforeEach, describe, expect, it, vi } from 'vitest'
import type { FiveMinuteResearchReport, IntradayFactorBacktest } from '../types'
import {
  deleteLocalIntradayFactorExperiment,
  loadLocalIntradayFactorExperiments,
  saveLocalIntradayFactorExperiment,
} from './localIntradayFactorExperiments'

type Dataset = NonNullable<FiveMinuteResearchReport['minute_dataset']>

const dataset = {
  data_version: 'baostock-five-minute-long-v1',
  checksum: 'long-five-minute-checksum',
} as Dataset

const result = {
  factor_definition: {
    factor_id: 'opening-ten-minute-direction-rule-v1',
    name: '自定义开盘十分钟方向规则',
    expression: 'bullish_10m_0930_0940 AND bearish_10m_0940_0950',
  },
  parameters: {
    factor: 'custom_opening_ten_minute_direction_rule',
    start: '2026-07-28',
    end: '2026-08-24',
    cost_bps: '5',
    minimum_body_return_bps: '0',
    directions: ['BULLISH', 'BEARISH', 'IGNORE'],
    combination: 'ALL',
  },
  factor_validation: { rank_ic: '-0.003' },
  correlation_validation: { maximum_absolute_correlation: '0.4712' },
  event_summary: {
    observation_count: 1500, signal_count: 192,
    clustered_signal_session_count: 20,
    newey_west_t_statistic: '-2.0',
    confidence_interval_95_lower: '-0.000396',
    confidence_interval_95_upper: '-0.000004',
  },
  robustness_validation: {
    stitched_strategy_metrics: { total_return: '-0.0467', excess_return: '-0.001' },
    passed: false,
  },
  backtest: {
    strategy_metrics: {
      total_return: '-0.0408', annualized_return: '-0.4087', sharpe: '-3.3347',
      maximum_drawdown: '-0.0510', excess_return: '0.0025',
    },
  },
  research_gate: { passed: false },
  data_version: dataset.data_version,
  dataset_checksum: dataset.checksum,
  selected_session_count: 20,
  symbol_count: 75,
  sector_count: 15,
  promotion: 'NOT_ELIGIBLE',
  state_mutation: false,
  external_order_transmission: false,
  preview_hash: 'a'.repeat(64),
} as unknown as IntradayFactorBacktest

beforeEach(() => {
  window.localStorage.clear()
  vi.useRealTimers()
})

describe('local intraday factor experiments', () => {
  it('binds records to the exact minute dataset and replaces duplicate results', () => {
    saveLocalIntradayFactorExperiment('方向规则 A', result)
    saveLocalIntradayFactorExperiment('方向规则 A 改名', result)

    expect(loadLocalIntradayFactorExperiments(dataset)).toEqual([
      expect.objectContaining({
        name: '方向规则 A 改名',
        preview_hash: result.preview_hash,
        research_gate_passed: false,
        robustness_gate_passed: false,
        metrics: expect.objectContaining({
          newey_west_t_statistic: '-2.0',
          confidence_interval_95_lower: '-0.000396',
          confidence_interval_95_upper: '-0.000004',
        }),
      }),
    ])
    expect(loadLocalIntradayFactorExperiments({
      ...dataset,
      checksum: 'different-minute-checksum',
    })).toEqual([])
  })

  it('round-trips the bounded bearish exit and rejects unsafe exit identity', () => {
    const exitResult = {
      ...result,
      preview_hash: 'b'.repeat(64),
      parameters: {
        ...result.parameters,
        exit_rule: 'NEXT_SESSION_THREE_TEN_MINUTE_BEARISH_ELSE_14_55_TIME_STOP' as const,
        exit_minimum_body_return_bps: '5',
      },
    }
    const saved = saveLocalIntradayFactorExperiment('三连阴退出', exitResult)
    expect(loadLocalIntradayFactorExperiments(dataset)[0].parameters).toEqual(
      exitResult.parameters,
    )

    window.localStorage.setItem(
      'qtrade.local-intraday-factor-experiments.v1',
      JSON.stringify([
        {
          ...saved,
          preview_hash: 'c'.repeat(64),
          parameters: {
            ...saved.parameters,
            exit_minimum_body_return_bps: '1001',
          },
        },
        {
          ...saved,
          preview_hash: 'd'.repeat(64),
          parameters: { ...saved.parameters, exit_rule: 'EVAL' },
        },
      ]),
    )
    expect(loadLocalIntradayFactorExperiments(dataset)).toEqual([])
  })

  it('ignores corrupted or safety-relaxed local records', () => {
    const valid = saveLocalIntradayFactorExperiment('有效规则', result)
    window.localStorage.setItem(
      'qtrade.local-intraday-factor-experiments.v1',
      JSON.stringify([
        { ...valid, preview_hash: 'not-a-hash' },
        { ...valid, preview_hash: 'b'.repeat(64), external_order_transmission: true },
        {
          ...valid,
          preview_hash: 'c'.repeat(64),
          factor_definition: {
            ...valid.factor_definition,
            expression: 'future_close_10m_0930_0940',
          },
        },
      ]),
    )

    expect(loadLocalIntradayFactorExperiments(dataset)).toEqual([])
  })

  it('rejects unsafe, blank, or incomplete results before writing', () => {
    expect(() => saveLocalIntradayFactorExperiment('   ', result)).toThrow(
      '实验名称需要 1-80 个字符',
    )
    expect(() => saveLocalIntradayFactorExperiment('不安全结果', {
      ...result,
      external_order_transmission: true,
    } as unknown as IntradayFactorBacktest)).toThrow(
      '因子结果不完整或安全边界不匹配，未保存',
    )
    expect(loadLocalIntradayFactorExperiments(dataset)).toEqual([])
  })

  it('deletes only the selected result and preserves parameter identity', () => {
    saveLocalIntradayFactorExperiment('规则 A', result)
    const second = {
      ...result,
      preview_hash: 'b'.repeat(64),
      parameters: {
        ...result.parameters,
        combination: 'ANY' as const,
      },
      factor_definition: {
        ...result.factor_definition,
        expression: 'bullish_10m_0930_0940 OR bearish_10m_0940_0950',
      },
    }
    saveLocalIntradayFactorExperiment('规则 B', second)
    deleteLocalIntradayFactorExperiment(second.preview_hash)

    const records = loadLocalIntradayFactorExperiments(dataset)
    expect(records.map((item) => item.name)).toEqual(['规则 A'])
    expect(records[0].parameters).toEqual(result.parameters)
    expect(records[0].parameters.directions).not.toBe(result.parameters.directions)
  })

  it('keeps only the thirty most recent summaries', () => {
    for (let index = 0; index < 32; index += 1) {
      saveLocalIntradayFactorExperiment(`规则 ${index}`, {
        ...result,
        preview_hash: index.toString(16).padStart(64, '0'),
      })
    }

    const records = loadLocalIntradayFactorExperiments(dataset)
    expect(records).toHaveLength(30)
    expect(records[0].name).toBe('规则 31')
    expect(records.at(-1)?.name).toBe('规则 2')
  })
})
