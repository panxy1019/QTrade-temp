import { beforeEach, describe, expect, it } from 'vitest'
import type { FiveMinuteResearchReport, IntradayFactorCorrelationRule } from '../types'
import {
  deleteLocalIntradayFactorCandidate,
  loadLocalIntradayFactorCandidates,
  saveLocalIntradayFactorCandidate,
} from './localIntradayFactorCandidates'

type Dataset = NonNullable<FiveMinuteResearchReport['minute_dataset']>

const dataset = {
  data_version: 'baostock-five-minute-long-v1',
  checksum: 'long-five-minute-checksum',
} as Dataset

const rule: IntradayFactorCorrelationRule = {
  directions: ['BULLISH', 'BULLISH', 'BULLISH'],
  combination: 'ALL',
  minimum_body_return_bps: '0',
}

beforeEach(() => {
  window.localStorage.clear()
})

describe('local intraday factor candidates', () => {
  it('stores entry-factor drafts before any strategy or backtest result exists', () => {
    saveLocalIntradayFactorCandidate('三连阳草案', dataset, rule)
    saveLocalIntradayFactorCandidate('三连阳草案改名', dataset, rule)

    expect(loadLocalIntradayFactorCandidates(dataset)).toEqual([
      expect.objectContaining({
        name: '三连阳草案改名',
        expression: 'body_return_0930_0940 >= 0 bps AND body_return_0940_0950 >= 0 bps AND body_return_0950_1000 >= 0 bps',
        validation_status: 'NOT_RUN',
        strategy_constructed: false,
        strategy_backtest_run: false,
        factor_registry_write: false,
        strategy_registry_write: false,
        promotion: 'NOT_ELIGIBLE',
        state_mutation: false,
        external_order_transmission: false,
      }),
    ])
    expect(loadLocalIntradayFactorCandidates({
      ...dataset,
      checksum: 'different-minute-checksum',
    })).toEqual([])
  })

  it('rejects unsafe rules and deletes only the selected browser-local draft', () => {
    expect(() => saveLocalIntradayFactorCandidate('全忽略', dataset, {
      ...rule,
      directions: ['IGNORE', 'IGNORE', 'IGNORE'],
    })).toThrow('至少选择一个有效的十分钟方向条件')
    expect(() => saveLocalIntradayFactorCandidate('阈值越界', dataset, {
      ...rule,
      minimum_body_return_bps: '1001',
    })).toThrow('实体阈值需要在 0-1000 bps 之间')

    const saved = saveLocalIntradayFactorCandidate('可删除草案', dataset, rule)
    deleteLocalIntradayFactorCandidate(saved.draft_key)
    expect(loadLocalIntradayFactorCandidates(dataset)).toEqual([])
  })
})
