import type { FactorSearchReport, IntradayFactorLongWindowReport } from '../types'

export interface EvidenceFactorCatalogRow {
  factor_id: string
  name: string
  role: 'SELECTION' | 'ENTRY_TIMING' | 'EXIT_TIMING' | 'UNTESTED_L2_HYPOTHESIS'
  interval: string
  formula: string
  required_data: string
  known_at: string
  earliest_execution: string
  data_status: string
  validation_status: string
  validation_tone: 'blue' | 'gold' | 'red' | 'default'
  evidence_binding: string
  limitation: string
}

const dailyDefinitions: Record<string, Pick<EvidenceFactorCatalogRow, 'name' | 'formula' | 'required_data'>> = {
  'momentum-60': { name: '60 日动量', formula: 'close_t / close_(t-60) - 1', required_data: '至少 61 个已完成日收盘价' },
  'momentum-120-skip-20': { name: '120 日动量（跳过最近 20 日）', formula: 'close_(t-20) / close_(t-140) - 1', required_data: '至少 141 个已完成日收盘价' },
  'momentum-252-skip-20': { name: '252 日动量（跳过最近 20 日）', formula: 'close_(t-20) / close_(t-272) - 1', required_data: '至少 273 个已完成日收盘价' },
  'reversal-5': { name: '5 日反转', formula: '-(close_t / close_(t-5) - 1)', required_data: '至少 6 个已完成日收盘价' },
  'low_volatility-60': { name: '60 日低波动', formula: '-PSTDEV(close_to_close_return, 60)', required_data: '至少 61 个已完成日收盘价' },
}

export function buildEvidenceFactorCatalog(
  factorSearch: FactorSearchReport | null | undefined,
  intradayReport: IntradayFactorLongWindowReport | null | undefined,
): EvidenceFactorCatalogRow[] {
  const rows: EvidenceFactorCatalogRow[] = []
  const passingDailyFactors = new Set(factorSearch?.factor_selection?.passing_factor_ids ?? [])
  const dailyFactorIds = [...new Set([
    ...(factorSearch?.factor_diagnostics ?? []).map((row) => row.factor_id),
    ...passingDailyFactors,
  ])].sort((left, right) => {
    const order = Object.keys(dailyDefinitions)
    return (order.indexOf(left) === -1 ? order.length : order.indexOf(left))
      - (order.indexOf(right) === -1 ? order.length : order.indexOf(right))
  })
  for (const factorId of dailyFactorIds) {
    const definition = dailyDefinitions[factorId]
    const passedDiscovery = passingDailyFactors.has(factorId)
    rows.push({
      factor_id: factorId,
      name: definition?.name ?? factorId,
      role: 'SELECTION',
      interval: '1d',
      formula: definition?.formula ?? 'UNREGISTERED_FORMULA_NOT_EXECUTABLE_FROM_CATALOG',
      required_data: definition?.required_data ?? '当前证据没有登记该因子的输入契约',
      known_at: 'DAILY_BAR_CLOSE_T',
      earliest_execution: 'NEXT_SESSION_OPEN',
      data_status: factorSearch ? '历史日线可用；当前标的池非 PIT' : 'UNAVAILABLE',
      validation_status: passedDiscovery
        ? `发现期单因子门槛通过；全局 ${factorSearch?.promotion ?? 'NOT_EVALUATED'}`
        : '发现期未通过或未进入候选',
      validation_tone: passedDiscovery ? 'gold' : 'red',
      evidence_binding: factorSearch?.classification ?? 'MISSING_FACTOR_SEARCH_EVIDENCE',
      limitation: `全局结论 ${factorSearch?.promotion ?? 'NOT_EVALUATED'}；已查看确认窗不能再用于选参。`,
    })
  }

  const result = intradayReport?.result
  const entryDefinition = result?.factor_definition
  if (entryDefinition || intradayReport?.report_id === 'opening-triple-bullish-long-window-robustness-v1') {
    const entryValidation = result?.workflow?.find((stage) => stage.id === 'FACTOR_VALIDATION')?.status
    rows.push({
      factor_id: entryDefinition?.factor_id ?? 'opening-three-ten-minute-bullish-v1',
      name: entryDefinition?.name ?? '09:30 起三根十分钟阳线',
      role: 'ENTRY_TIMING',
      interval: '5m → 10m',
      formula: entryDefinition?.expression ?? 'bullish_10m_0930_0940 AND bullish_10m_0940_0950 AND bullish_10m_0950_1000',
      required_data: entryDefinition?.source_bars ? `BaoStock 原生 5 分钟 OHLCV：${entryDefinition.source_bars}` : '09:35 至 10:00 六根已完成原生 5 分钟 OHLCV',
      known_at: entryDefinition?.known_at ?? '10:00_BAR_CLOSE',
      earliest_execution: 'NEXT_5M_OPEN_10:05',
      data_status: intradayReport?.dataset ? '签名历史 5 分钟可用；QFQ/非 PIT' : '历史报告可用；数据身份未展开',
      validation_status: entryValidation === 'PASSED' ? '有效性通过；仍受后续稳健性与确认门约束' : '长期有效性/稳健性未通过',
      validation_tone: entryValidation === 'PASSED' ? 'gold' : 'red',
      evidence_binding: intradayReport?.result_preview_hash ?? 'MISSING_RESULT_PREVIEW_HASH',
      limitation: `长期报告 ${intradayReport?.promotion ?? 'NOT_EVALUATED'}；仅可构造诊断策略。`,
    })
  }

  const strategy = result?.strategy
  if (strategy?.exit_factor_id && strategy.exit_condition) {
    const exitPassed = result?.exit_factor_validation?.passed === true
    rows.push({
      factor_id: strategy.exit_factor_id,
      name: 'T+1 三根十分钟阴线退出',
      role: 'EXIT_TIMING',
      interval: 'T+1 · 5m → 10m',
      formula: strategy.exit_condition,
      required_data: '下一交易日 09:35 至 10:00 六根已完成原生 5 分钟 OHLCV',
      known_at: strategy.exit_condition_known_at ?? 'NEXT_SESSION_10:00_BAR_CLOSE',
      earliest_execution: strategy.conditional_exit_price_source ?? 'NEXT_SESSION_10:05_OPEN',
      data_status: '签名历史 5 分钟可用；QFQ/非 PIT',
      validation_status: exitPassed ? '退出时点优势检验通过' : '退出时点优势检验未通过',
      validation_tone: exitPassed ? 'gold' : 'red',
      evidence_binding: intradayReport?.result_preview_hash ?? 'MISSING_RESULT_PREVIEW_HASH',
      limitation: '未触发时使用 14:55 时间退出；当前结果不能注册或晋级。',
    })
  }

  const l2 = result?.l2_factor_readiness
  if (l2) {
    rows.push({
      factor_id: l2.factor_id,
      name: '五档盘口买入强度',
      role: 'UNTESTED_L2_HYPOTHESIS',
      interval: 'L2 snapshot → finalized minute',
      formula: `${l2.formula}; BUY when ${l2.buying_hypothesis.condition}`,
      required_data: `${l2.required_data}；20 个五档价量字段 + 事件/接收时钟与序号`,
      known_at: l2.causal_contract.decision_clock,
      earliest_execution: 'UNAVAILABLE_UNTIL_PROVIDER_AND_FRESHNESS_CERTIFIED',
      data_status: `${l2.readiness} · ${l2.passed_gate_count}/${l2.required_gate_count} 数据门`,
      validation_status: l2.buying_hypothesis.validation_status,
      validation_tone: 'default',
      evidence_binding: l2.classification,
      limitation: `${l2.sell_condition_status}；不允许从 OHLCV 伪造盘口。`,
    })
  }
  return rows
}
