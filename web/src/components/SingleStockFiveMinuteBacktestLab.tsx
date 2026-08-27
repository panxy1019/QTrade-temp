import { Alert, Button, Card, Form, InputNumber, Select, Space, Table, Typography } from 'antd'
import { useState } from 'react'
import { runSingleStockFiveMinuteBacktest } from '../api/client'
import { EquityChart } from '../charts/EquityChart'
import type { FiveMinuteResearchReport, SingleStockFiveMinuteBacktest, SingleStockFiveMinuteBacktestRequest } from '../types'
import { JsonExportButton } from './JsonExportButton'

type VerifiedFiveMinuteDataset = NonNullable<FiveMinuteResearchReport['minute_dataset']>

interface FormValues {
  symbol: string
  start: string
  end: string
  cost_bps: string | number
  short_window: number
  long_window: number
  volatility_window: number
  maximum_minute_volatility: string | number
  maximum_intraday_drawdown: string | number
  reduction_multiplier: string | number
  cooldown_bars: number
}

interface SectorBatchStatus {
  attempts: SectorBatchAttempt[]
  completed: number
  completedSectors: number
  failed: number
  identityRejected: number
  representatives: Array<{ symbol: string; name: string; sector_code: string; sector_name: string }>
  representativesPerSector: BatchRepresentativesPerSector
  requests: SingleStockFiveMinuteBacktestRequest[]
  requested: number
  requestedSectors: number
}

type SectorBatchOutcome = 'COMPLETED' | 'REQUEST_FAILED' | 'IDENTITY_REJECTED'
type BatchRepresentativesPerSector = 1 | 2

const SESSION_RUN_LIMIT = 30

interface SectorBatchAttempt {
  symbol: string
  name: string
  sector_code: string
  sector_name: string
  request: SingleStockFiveMinuteBacktestRequest
  outcome: SectorBatchOutcome
  preview_hash: string | null
}

function toRequest(values: FormValues, symbol = values.symbol): SingleStockFiveMinuteBacktestRequest {
  return {
    ...values,
    symbol,
    cost_bps: String(values.cost_bps),
    maximum_minute_volatility: String(values.maximum_minute_volatility),
    maximum_intraday_drawdown: String(values.maximum_intraday_drawdown),
    reduction_multiplier: String(values.reduction_multiplier),
  }
}

function isBoundBatchResponse(
  response: SingleStockFiveMinuteBacktest | null | undefined,
  request: SingleStockFiveMinuteBacktestRequest,
  instrument: VerifiedFiveMinuteDataset['instruments'][number],
  dataset: VerifiedFiveMinuteDataset,
) {
  if (!response) return false
  const parameters = response.parameters
  return response.classification === 'AD_HOC_SINGLE_STOCK_FIVE_MINUTE_BACKTEST_CURRENT_NON_PIT'
    && response.promotion === 'NOT_ELIGIBLE'
    && response.symbol === request.symbol
    && response.instrument?.name === instrument.name
    && response.instrument?.sector_code === instrument.sector_code
    && response.instrument?.sector_name === instrument.sector_name
    && response.data_version === dataset.data_version
    && response.dataset_checksum === dataset.checksum
    && parameters?.symbol === request.symbol
    && parameters?.start === request.start
    && parameters?.end === request.end
    && parameters?.cost_bps === request.cost_bps
    && parameters?.short_window === request.short_window
    && parameters?.long_window === request.long_window
    && parameters?.volatility_window === request.volatility_window
    && parameters?.maximum_minute_volatility === request.maximum_minute_volatility
    && parameters?.maximum_intraday_drawdown === request.maximum_intraday_drawdown
    && parameters?.reduction_multiplier === request.reduction_multiplier
    && parameters?.cooldown_bars === request.cooldown_bars
    && response.realtime_readiness === false
    && response.real_data_paper_readiness === false
    && response.state_mutation === false
    && response.external_order_transmission === false
}

export function SingleStockFiveMinuteBacktestLab({ dataset }: { dataset: VerifiedFiveMinuteDataset | null }) {
  const [form] = Form.useForm<FormValues>()
  const [result, setResult] = useState<SingleStockFiveMinuteBacktest | null>(null)
  const [sessionRuns, setSessionRuns] = useState<SingleStockFiveMinuteBacktest[]>([])
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const [batchRunning, setBatchRunning] = useState(false)
  const [batchProgress, setBatchProgress] = useState(0)
  const [batchStatus, setBatchStatus] = useState<SectorBatchStatus | null>(null)
  const [batchRepresentativesPerSector, setBatchRepresentativesPerSector] = useState<BatchRepresentativesPerSector>(1)
  if (!dataset) {
    return <Card title="个股 5 分钟因果回测"><Alert showIcon type="warning" title="长历史 5 分钟数据不可用" description="系统不会用模拟行情替代真实且校验通过的数据。" /></Card>
  }
  const stocks = dataset.instruments.filter((row) => row.instrument_type === 'STOCK')
  const sectorSelectionCounts = new Map<string, number>()
  const sectorBatchInstruments = [...stocks].sort((left, right) => {
    const sectorOrder = (left.sector_code ?? '').localeCompare(right.sector_code ?? '')
    return sectorOrder || left.symbol.localeCompare(right.symbol)
  }).filter((stock) => {
    if (!stock.sector_code) return false
    const selected = sectorSelectionCounts.get(stock.sector_code) ?? 0
    if (selected >= batchRepresentativesPerSector) return false
    sectorSelectionCounts.set(stock.sector_code, selected + 1)
    return true
  })
  const requestedSectorCount = new Set(sectorBatchInstruments.map((row) => row.sector_code)).size
  const tradingDays = dataset.trading_days
  const initialValues: FormValues = {
    symbol: stocks[0]?.symbol ?? dataset.eligible_symbols[0],
    start: tradingDays[Math.max(0, tradingDays.length - 20)],
    end: tradingDays.at(-1) ?? tradingDays[0],
    cost_bps: '5',
    short_window: 12,
    long_window: 48,
    volatility_window: 24,
    maximum_minute_volatility: '0.02',
    maximum_intraday_drawdown: '0.04',
    reduction_multiplier: '0.98',
    cooldown_bars: 6,
  }
  const submit = async (values: FormValues) => {
    setRunning(true)
    setError(null)
    setBatchStatus(null)
    setBatchProgress(0)
    try {
      const next = await runSingleStockFiveMinuteBacktest(toRequest(values))
      setResult(next)
      setSessionRuns((current) => [
        next,
        ...current.filter((row) => row.preview_hash !== next.preview_hash),
      ].slice(0, SESSION_RUN_LIMIT))
    } catch (reason) {
      setResult(null)
      setError(reason instanceof Error ? reason.message : '个股 5 分钟回测失败')
    } finally {
      setRunning(false)
    }
  }
  const runSectorBatch = async () => {
    let values: FormValues
    try {
      values = await form.validateFields()
    } catch {
      return
    }
    setBatchRunning(true)
    setBatchProgress(0)
    setBatchStatus(null)
    setResult(null)
    setError(null)
    const requests = sectorBatchInstruments.map((representative) => toRequest(values, representative.symbol))
    const representatives = sectorBatchInstruments.map((representative) => ({
      symbol: representative.symbol,
      name: representative.name,
      sector_code: representative.sector_code ?? '',
      sector_name: representative.sector_name ?? '',
    }))
    const completed: SingleStockFiveMinuteBacktest[] = []
    const attempts: SectorBatchAttempt[] = []
    let failed = 0
    let identityRejected = 0
    for (const [index, representative] of sectorBatchInstruments.entries()) {
      const request = requests[index]
      const attemptIdentity = {
        symbol: representative.symbol,
        name: representative.name,
        sector_code: representative.sector_code ?? '',
        sector_name: representative.sector_name ?? '',
        request,
      }
      try {
        const next = await runSingleStockFiveMinuteBacktest(request)
        if (isBoundBatchResponse(next, request, representative, dataset)) {
          completed.push(next)
          attempts.push({ ...attemptIdentity, outcome: 'COMPLETED', preview_hash: next.preview_hash })
        } else {
          failed += 1
          identityRejected += 1
          attempts.push({ ...attemptIdentity, outcome: 'IDENTITY_REJECTED', preview_hash: null })
        }
      } catch {
        failed += 1
        attempts.push({ ...attemptIdentity, outcome: 'REQUEST_FAILED', preview_hash: null })
      }
      setBatchProgress(index + 1)
    }
    setSessionRuns(completed.slice(0, SESSION_RUN_LIMIT))
    setBatchStatus({
      attempts,
      completed: completed.length,
      completedSectors: new Set(completed.map((row) => row.instrument.sector_code)).size,
      failed,
      identityRejected,
      representatives,
      representativesPerSector: batchRepresentativesPerSector,
      requests,
      requested: sectorBatchInstruments.length,
      requestedSectors: requestedSectorCount,
    })
    setBatchRunning(false)
  }
  const reasons = result
    ? Object.entries(result.execution.reason_counts)
      .sort((left, right) => right[1] - left[1])
      .map(([reason, count]) => `${reason} ${count}`)
      .join('；')
    : ''
  const sessionSectorCount = new Set(sessionRuns.map((row) => row.instrument.sector_code)).size
  const batchMetricRows = batchStatus
    ? sessionRuns.map((row) => ({
      row,
      totalReturn: Number(row.metrics.overlay.total_return),
      annualizedReturn: Number(row.metrics.overlay.annualized_return),
      sharpe: Number(row.metrics.overlay.sharpe),
      excessReturn: Number(row.metrics.excess_return),
      informationRatio: Number(row.metrics.information_ratio),
      maximumDrawdown: Number(row.metrics.overlay.maximum_drawdown),
    })).filter((row) => Number.isFinite(row.totalReturn)
      && Number.isFinite(row.annualizedReturn)
      && Number.isFinite(row.sharpe)
      && Number.isFinite(row.excessReturn)
      && Number.isFinite(row.informationRatio)
      && Number.isFinite(row.maximumDrawdown))
    : []
  const sortedBatchReturns = [...batchMetricRows].sort((left, right) => left.totalReturn - right.totalReturn)
  const batchMedianReturn = sortedBatchReturns.length === 0
    ? null
    : sortedBatchReturns.length % 2 === 1
      ? sortedBatchReturns[Math.floor(sortedBatchReturns.length / 2)].totalReturn
      : (sortedBatchReturns[sortedBatchReturns.length / 2 - 1].totalReturn + sortedBatchReturns[sortedBatchReturns.length / 2].totalReturn) / 2
  const batchWorstReturn = sortedBatchReturns[0] ?? null
  const batchDeepestDrawdown = [...batchMetricRows].sort((left, right) => left.maximumDrawdown - right.maximumDrawdown)[0] ?? null
  const requestedBySector = new Map<string, number>()
  for (const representative of batchStatus?.representatives ?? []) {
    requestedBySector.set(representative.sector_code, (requestedBySector.get(representative.sector_code) ?? 0) + 1)
  }
  const requestedPairSectors = [...requestedBySector.entries()]
    .filter(([, count]) => count === 2)
    .map(([sectorCode]) => sectorCode)
    .sort()
  const withinSectorPairs = requestedPairSectors.flatMap((sectorCode) => {
    const pair = batchMetricRows
      .filter(({ row }) => row.instrument.sector_code === sectorCode)
      .sort((left, right) => left.row.symbol.localeCompare(right.row.symbol))
    if (pair.length !== 2) return []
    const [left, right] = pair
    return [{
      sector_code: sectorCode,
      sector_name: left.row.instrument.sector_name,
      symbols: [left.row.symbol, right.row.symbol],
      total_returns: [left.totalReturn, right.totalReturn],
      annualized_returns: [left.annualizedReturn, right.annualizedReturn],
      sharpes: [left.sharpe, right.sharpe],
      maximum_drawdowns: [left.maximumDrawdown, right.maximumDrawdown],
      excess_returns: [left.excessReturn, right.excessReturn],
      information_ratios: [left.informationRatio, right.informationRatio],
      return_direction_agreement: Math.sign(left.totalReturn) === Math.sign(right.totalReturn),
      excess_direction_agreement: Math.sign(left.excessReturn) === Math.sign(right.excessReturn),
      absolute_return_spread: Math.abs(left.totalReturn - right.totalReturn),
    }]
  })
  const sortedPairSpreads = withinSectorPairs.map((pair) => pair.absolute_return_spread).sort((left, right) => left - right)
  const medianPairSpread = sortedPairSpreads.length === 0
    ? null
    : sortedPairSpreads.length % 2 === 1
      ? sortedPairSpreads[Math.floor(sortedPairSpreads.length / 2)]
      : (sortedPairSpreads[sortedPairSpreads.length / 2 - 1] + sortedPairSpreads[sortedPairSpreads.length / 2]) / 2
  const worstPairSpread = [...withinSectorPairs].sort((left, right) => right.absolute_return_spread - left.absolute_return_spread)[0] ?? null
  const returnDirectionAgreementCount = withinSectorPairs.filter((pair) => pair.return_direction_agreement).length
  const excessDirectionAgreementCount = withinSectorPairs.filter((pair) => pair.excess_direction_agreement).length
  const withinSectorConsistency = batchStatus?.representativesPerSector === 2 ? {
    requested_pair_sector_count: requestedPairSectors.length,
    complete_pair_sector_count: withinSectorPairs.length,
    return_direction_agreement_count: returnDirectionAgreementCount,
    excess_direction_agreement_count: excessDirectionAgreementCount,
    median_absolute_return_spread: medianPairSpread,
    worst_absolute_return_spread: worstPairSpread ? {
      sector_code: worstPairSpread.sector_code,
      sector_name: worstPairSpread.sector_name,
      symbols: worstPairSpread.symbols,
      absolute_return_spread: worstPairSpread.absolute_return_spread,
    } : null,
    pairs: withinSectorPairs,
  } : null
  const batchDiagnosticExport = batchStatus ? {
    classification: 'BROWSER_SESSION_ONLY_CROSS_SECTOR_SAME_PARAMETER_DIAGNOSTIC',
    dataset_identity: { data_version: dataset.data_version, checksum: dataset.checksum },
    representative_selection: {
      method: 'SORT_SECTOR_CODE_THEN_SYMBOL_FIRST_N_PER_SECTOR',
      requested_per_sector: batchStatus.representativesPerSector,
      instruments: batchStatus.representatives,
    },
    status: {
      requested: batchStatus.requested,
      completed: batchStatus.completed,
      failed: batchStatus.failed,
      identity_rejected: batchStatus.identityRejected,
      requested_sectors: batchStatus.requestedSectors,
      completed_sectors: batchStatus.completedSectors,
    },
    requests: batchStatus.requests,
    attempts: batchStatus.attempts,
    results: sessionRuns,
    summary: {
      valid_metric_count: batchMetricRows.length,
      positive_return_count: batchMetricRows.filter((row) => row.totalReturn > 0).length,
      positive_excess_count: batchMetricRows.filter((row) => row.excessReturn > 0).length,
      median_total_return: batchMedianReturn,
      worst_return: batchWorstReturn ? {
        symbol: batchWorstReturn.row.symbol,
        sector_code: batchWorstReturn.row.instrument.sector_code,
        total_return: batchWorstReturn.totalReturn,
      } : null,
      deepest_drawdown: batchDeepestDrawdown ? {
        symbol: batchDeepestDrawdown.row.symbol,
        sector_code: batchDeepestDrawdown.row.instrument.sector_code,
        maximum_drawdown: batchDeepestDrawdown.maximumDrawdown,
      } : null,
      within_sector_consistency: withinSectorConsistency,
    },
    persistence: 'NONE_REFRESH_CLEARS',
    creates_immutable_evidence: false,
    registry_write: false,
    promotion: 'NOT_ELIGIBLE',
    state_mutation: false,
    external_order_transmission: false,
    limitations: [
      'UP_TO_TWO_DETERMINISTIC_REPRESENTATIVES_PER_SECTOR_NOT_FULL_UNIVERSE',
      'AD_HOC_VIEWED_HISTORY_NOT_INDEPENDENT_SAMPLE_OUT_EVIDENCE',
      'BROWSER_SESSION_DIAGNOSTIC_NOT_PAPER_OR_LIVE_ADMISSION',
    ],
  } : null

  return <Card title="个股 5 分钟因果回测" extra={<Typography.Text type="secondary">{dataset.symbol_count} 股 · {dataset.sector_count} 板块 · {dataset.session_count} 日</Typography.Text>}>
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <Alert showIcon type="info" title="可任选覆盖个股并反复调参" description="基准是该股买入持有；修正策略只允许在本根 5 分钟 Bar 收盘生成信号，并于下一根 Bar 开盘降低或恢复敞口。单次最多 120 个交易日，所选区间之前的数据只作因果预热。" />
      <Form<FormValues> form={form} name="single-stock-five-minute-backtest" layout="vertical" initialValues={initialValues} onValuesChange={() => { setResult(null); setError(null); setBatchStatus(null); setBatchProgress(0) }} onFinish={(values) => { void submit(values) }}>
        <div className="backtest-lab-grid">
          <Form.Item name="symbol" label="个股" rules={[{ required: true }]}><Select showSearch optionFilterProp="label" options={stocks.map((row) => ({ value: row.symbol, label: `${row.symbol} ${row.name} · ${row.sector_name}` }))} /></Form.Item>
          <Form.Item name="start" label="开始交易日" rules={[{ required: true }]}><Select showSearch options={tradingDays.map((value) => ({ value, label: value }))} /></Form.Item>
          <Form.Item name="end" label="结束交易日" rules={[{ required: true }]}><Select showSearch options={tradingDays.map((value) => ({ value, label: value }))} /></Form.Item>
          <Form.Item name="cost_bps" label="单边成本（bps）" rules={[{ required: true }]}><InputNumber min={0} max={100} step={1} stringMode /></Form.Item>
          <Form.Item name="short_window" label="短趋势窗口（Bar）" rules={[{ required: true }]}><InputNumber min={2} max={96} precision={0} /></Form.Item>
          <Form.Item name="long_window" label="长趋势窗口（Bar）" rules={[{ required: true }]}><InputNumber min={3} max={240} precision={0} /></Form.Item>
          <Form.Item name="volatility_window" label="波动窗口（Bar）" rules={[{ required: true }]}><InputNumber min={2} max={240} precision={0} /></Form.Item>
          <Form.Item name="maximum_minute_volatility" label="5 分钟波动上限" rules={[{ required: true }]}><InputNumber min={0.0001} max={1} step={0.001} stringMode /></Form.Item>
          <Form.Item name="maximum_intraday_drawdown" label="日内回撤上限" rules={[{ required: true }]}><InputNumber min={0.001} max={0.999} step={0.005} stringMode /></Form.Item>
          <Form.Item name="reduction_multiplier" label="触发后敞口倍数" rules={[{ required: true }]}><InputNumber min={0} max={0.999} step={0.01} stringMode /></Form.Item>
          <Form.Item name="cooldown_bars" label="恢复冷却（Bar）" rules={[{ required: true }]}><InputNumber min={0} max={240} precision={0} /></Form.Item>
        </div>
        <Form.Item noStyle shouldUpdate>
          {({ getFieldsValue }) => {
            const values = { ...initialValues, ...getFieldsValue(true) } as FormValues
            const requiredHistory = Math.max(values.long_window, values.volatility_window + 1)
            return <Alert
              style={{ marginBottom: 16 }}
              showIcon
              type="info"
              title="当前分钟修正规则草案（未运行）"
              description={<Space orientation="vertical" size={2}>
                <Typography.Text code>{`BASE_TARGET: ${values.symbol} constant 100% exposure / Buy-and-Hold benchmark`}</Typography.Text>
                <Typography.Text code>{`TREND: SMA_CLOSE_${values.short_window} < SMA_CLOSE_${values.long_window} → EXPOSURE ≤ ${formatDraftNumber(values.reduction_multiplier)}`}</Typography.Text>
                <Typography.Text code>{`VOL: STDEV(RET_CLOSE, ${values.volatility_window}) ≥ ${formatDraftPercent(values.maximum_minute_volatility)} → EXPOSURE ≤ ${formatDraftNumber(values.reduction_multiplier)}`}</Typography.Text>
                <Typography.Text code>{`DRAWDOWN: CLOSE / SESSION_PEAK - 1 ≤ -${formatDraftPercent(values.maximum_intraday_drawdown)} → EXPOSURE = 0`}</Typography.Text>
                <Typography.Text code>{`RECOVERY: ${values.cooldown_bars} completed safe Bars, then still safe → EXPOSURE = 1`}</Typography.Text>
                <Typography.Text code>{`CLOCK: KNOWN_AT_5M_CLOSE_T → APPLY_NEXT_5M_OPEN; WARMUP ≥ ${requiredHistory} Bars; COST ${values.cost_bps} bps / exposure change`}</Typography.Text>
                <Typography.Text type="secondary">这是从当前表单推导的只读修正草案：基础目标固定为该股满敞口，分钟层只能减仓、清仓或恢复，不能加到 100% 以上；它不是独立选股/买入因子，也不是服务端策略规范、回测证据、注册或 PAPER/LIVE 准入。</Typography.Text>
              </Space>}
            />
          }}
        </Form.Item>
        <Space wrap>
          <Button type="primary" htmlType="submit" loading={running} disabled={batchRunning}>运行个股 5 分钟回测</Button>
          <Button aria-pressed={batchRepresentativesPerSector === 1} type={batchRepresentativesPerSector === 1 ? 'primary' : 'default'} disabled={running || batchRunning} onClick={() => { setBatchRepresentativesPerSector(1); setBatchStatus(null); setBatchProgress(0) }}>每板块 1 股</Button>
          <Button aria-pressed={batchRepresentativesPerSector === 2} type={batchRepresentativesPerSector === 2 ? 'primary' : 'default'} disabled={running || batchRunning} onClick={() => { setBatchRepresentativesPerSector(2); setBatchStatus(null); setBatchProgress(0) }}>每板块 2 股</Button>
          <Button onClick={() => { void runSectorBatch() }} loading={batchRunning} disabled={running || sectorBatchInstruments.length === 0}>运行 {sectorBatchInstruments.length} 股 / {requestedSectorCount} 板块同参诊断</Button>
        </Space>
      </Form>
      {batchRunning && <Typography.Text type="secondary">同参批量进度：{batchProgress}/{sectorBatchInstruments.length} 只股票；请求按板块代码、股票代码的确定性顺序逐一执行。</Typography.Text>}
      {batchStatus && <Alert
        showIcon
        type={batchStatus.failed === 0 ? 'success' : 'warning'}
        title={batchStatus.failed === 0
          ? `同参批量完成：${batchStatus.completed}/${batchStatus.requested} 只代表股，覆盖 ${batchStatus.completedSectors}/${batchStatus.requestedSectors} 个板块`
          : `同参批量部分完成：${batchStatus.completed}/${batchStatus.requested} 只代表股，覆盖 ${batchStatus.completedSectors}/${batchStatus.requestedSectors} 个板块`}
        description={batchStatus.failed === 0
          ? `每个板块按板块代码、股票代码确定性选择最多 ${batchStatus.representativesPerSector} 股，日期、成本与分钟修正参数完全一致；结果只进入当前浏览器会话对比，不等于全股票稳健性证据。`
          : batchStatus.identityRejected > 0
            ? `${batchStatus.failed} 只代表股未进入结果，其中 ${batchStatus.identityRejected} 个响应身份或安全边界不匹配；拒绝结果未补造，股票与板块覆盖只能按通过绑定的成功行计数。`
            : `${batchStatus.failed} 只代表股请求失败，失败结果未补造；股票与板块覆盖必须按成功行计数。`}
      />}
      {batchStatus && <Card data-testid="sector-batch-attempts" type="inner" title={`同参批量逐股台账（${batchStatus.attempts.length}/${batchStatus.requested}）`}>
        <Space orientation="vertical" style={{ width: '100%' }}>
          <Table<SectorBatchAttempt>
            rowKey="symbol"
            size="small"
            dataSource={batchStatus.attempts}
            pagination={false}
            columns={[
              { title: '代表股', render: (_: unknown, row) => `${row.symbol} ${row.name}` },
              { title: '板块', render: (_: unknown, row) => `${row.sector_code} ${row.sector_name}` },
              { title: '结果状态', render: (_: unknown, row) => formatBatchOutcome(row.outcome) },
              { title: '结果哈希', render: (_: unknown, row) => row.preview_hash ? <Typography.Text code>{row.preview_hash.slice(0, 12)}…</Typography.Text> : '—' },
            ]}
            scroll={{ x: 720 }}
          />
          <Typography.Text type="secondary">台账逐股保留本次请求顺序和稳定结果分类；请求失败不导出底层异常文本，身份拒绝不保留未接纳响应，二者都不会补造结果或进入指标分母。</Typography.Text>
        </Space>
      </Card>}
      {batchStatus && batchStatus.completed > 0 && <Card data-testid="sector-batch-summary" type="inner" title="同参批量横截面摘要（仅成功代表股）">
        <Space orientation="vertical" style={{ width: '100%' }}>
          <div className="description-grid">
            <Metric label="指标有效代表股" value={`${batchMetricRows.length}/${batchStatus.completed}`} />
            <Metric label="正收益代表股" value={`${batchMetricRows.filter((row) => row.totalReturn > 0).length}/${batchMetricRows.length}`} />
            <Metric label="正超额代表股" value={`${batchMetricRows.filter((row) => row.excessReturn > 0).length}/${batchMetricRows.length}`} />
            <Metric label="收益中位数" value={batchMedianReturn === null ? '—' : formatPercent(String(batchMedianReturn))} />
            <Metric label="最差收益" value={batchWorstReturn ? `${batchWorstReturn.row.symbol} · ${formatPercent(String(batchWorstReturn.totalReturn))}` : '—'} />
            <Metric label="最深回撤" value={batchDeepestDrawdown ? `${batchDeepestDrawdown.row.symbol} · ${formatPercent(String(batchDeepestDrawdown.maximumDrawdown))}` : '—'} />
          </div>
          <Typography.Text type="secondary">摘要只按 {batchStatus.completed} 个成功代表股计算；失败请求和同板块其余股票均未进入分母。它用于识别同一参数的横截面脆弱性，不是统计稳健性门槛、独立样本外证据、策略注册或 PAPER/LIVE 晋级结论。</Typography.Text>
        </Space>
      </Card>}
      {withinSectorConsistency && <Card data-testid="sector-pair-consistency" type="inner" title="同参批量板块内双股一致性（仅完整双股板块）">
        <Space orientation="vertical" style={{ width: '100%' }}>
          <div className="description-grid">
            <Metric label="完整双股板块" value={`${withinSectorConsistency.complete_pair_sector_count}/${withinSectorConsistency.requested_pair_sector_count}`} />
            <Metric label="收益方向一致率" value={withinSectorPairs.length === 0 ? '—' : formatPercent(String(returnDirectionAgreementCount / withinSectorPairs.length))} />
            <Metric label="超额方向一致率" value={withinSectorPairs.length === 0 ? '—' : formatPercent(String(excessDirectionAgreementCount / withinSectorPairs.length))} />
            <Metric label="板块内收益差中位数" value={medianPairSpread === null ? '—' : formatPercent(String(medianPairSpread))} />
            <Metric label="最大板块内收益差" value={worstPairSpread ? formatPercent(String(worstPairSpread.absolute_return_spread)) : '—'} />
          </div>
          <Typography.Text type="secondary">分母只含同时接纳两只且关键指标有限的板块；单股板块、请求失败、身份拒绝或非有限指标均不补值。方向按收益符号严格比较，0 单独成类。{worstPairSpread ? ` 最大分歧来自 ${worstPairSpread.sector_code} ${worstPairSpread.sector_name}（${worstPairSpread.symbols.join(' / ')}）。` : ''}这只是板块内脆弱性诊断，不是正式稳健性门、独立样本外证据或 PAPER/LIVE 晋级结论。</Typography.Text>
        </Space>
      </Card>}
      {withinSectorPairs.length > 0 && <Card data-testid="sector-pair-details" type="inner" title={`完整双股板块明细（${withinSectorPairs.length}）`}>
        <Space orientation="vertical" style={{ width: '100%' }}>
          <Table<(typeof withinSectorPairs)[number]>
            rowKey="sector_code"
            size="small"
            dataSource={withinSectorPairs}
            pagination={false}
            columns={[
              { title: '板块', render: (_: unknown, row) => `${row.sector_code} ${row.sector_name}` },
              { title: '股票对', render: (_: unknown, row) => row.symbols.join(' / ') },
              { title: '分钟修正收益', render: (_: unknown, row) => row.total_returns.map((value) => formatPercent(String(value))).join(' / ') },
              { title: '年化收益', render: (_: unknown, row) => row.annualized_returns.map((value) => formatPercent(String(value))).join(' / ') },
              { title: 'Sharpe', render: (_: unknown, row) => row.sharpes.map((value) => formatNumber(String(value))).join(' / ') },
              { title: '最大回撤', render: (_: unknown, row) => row.maximum_drawdowns.map((value) => formatPercent(String(value))).join(' / ') },
              { title: '主动超额', render: (_: unknown, row) => row.excess_returns.map((value) => formatPercent(String(value))).join(' / ') },
              { title: '信息比率', render: (_: unknown, row) => row.information_ratios.map((value) => formatNumber(String(value))).join(' / ') },
              { title: '方向', render: (_: unknown, row) => `收益${row.return_direction_agreement ? '一致' : '不一致'} · 超额${row.excess_direction_agreement ? '一致' : '不一致'}` },
              { title: '绝对收益差', render: (_: unknown, row) => formatPercent(String(row.absolute_return_spread)) },
            ]}
            scroll={{ x: 1680 }}
          />
          <Typography.Text type="secondary">明细与本次会话导出的 pairs 使用同一份已接纳数据，按板块代码稳定排序；表格不会显示或补造不完整双股板块，也不会据此自动调参或晋级。</Typography.Text>
        </Space>
      </Card>}
      {error && <Alert showIcon type="error" title="个股回测未完成" description={error} />}
      {result && <>
        <Alert showIcon type="success" title={`已完成 ${result.symbol} ${result.instrument.name} · ${result.selected_session_count} 日 / ${result.selected_bar_count} Bar`} description={`使用 ${result.warmup_session_count} 日因果预热；结果哈希 ${result.preview_hash.slice(0, 16)}…；固定结论 ${result.promotion}。`} />
        <div className="description-grid">
          <Metric label="买入持有收益" value={formatPercent(result.metrics.base.total_return)} />
          <Metric label="分钟修正收益" value={formatPercent(result.metrics.overlay.total_return)} />
          <Metric label="相对基准" value={formatPercent(result.metrics.excess_return)} />
          <Metric label="买入持有年化收益" value={formatPercent(result.metrics.base.annualized_return)} />
          <Metric label="修正后年化收益" value={formatPercent(result.metrics.overlay.annualized_return)} />
          <Metric label="修正后年化波动" value={formatPercent(result.metrics.overlay.annualized_volatility)} />
          <Metric label="修正后 Sharpe" value={formatNumber(result.metrics.overlay.sharpe)} />
          <Metric label="信息比率" value={formatNumber(result.metrics.information_ratio)} />
          <Metric label="修正后最大回撤" value={formatPercent(result.metrics.overlay.maximum_drawdown)} />
          <Metric label="平均敞口" value={formatPercent(result.execution.mean_exposure)} />
          <Metric label="减仓 Bar 比例" value={formatPercent(result.execution.reduced_exposure_ratio)} />
          <Metric label="换手" value={formatNumber(result.execution.turnover)} />
          <Metric label="成本扣减" value={formatPercent(result.execution.transaction_cost)} />
          <Metric label="策略哈希" value={`${result.policy_hash.slice(0, 12)}…`} />
        </div>
        <EquityChart dark analytics={result} />
        <Typography.Text type="secondary">曲线：实线为分钟修正策略，虚线为该个股 Buy-and-Hold；按每年 {result.metrics.annualization.sessions_per_year} 个交易日、每日 {result.metrics.annualization.bars_per_session} 根 5 分钟 Bar 年化，Sharpe 使用零无风险利率；{reasons || '本区间没有风险触发'}。前复权价格可能包含未来复权知识，当前成分股样本非 PIT，本结果不可直接晋级 PAPER/LIVE。</Typography.Text>
        <Table<SingleStockFiveMinuteBacktest['daily'][number]>
          rowKey="trading_day"
          size="small"
          dataSource={result.daily}
          pagination={{ pageSize: 8, showSizeChanger: false }}
          columns={[
            { title: '交易日', dataIndex: 'trading_day' },
            { title: '买入持有', render: (_: unknown, row) => formatPercent(row.base.total_return) },
            { title: '分钟修正', render: (_: unknown, row) => formatPercent(row.overlay.total_return) },
            { title: '相对基准', render: (_: unknown, row) => formatPercent(row.excess_return) },
            { title: '平均敞口', render: (_: unknown, row) => formatPercent(row.execution.mean_exposure) },
            { title: '换手', render: (_: unknown, row) => formatNumber(row.execution.turnover) },
          ]}
        />
        <JsonExportButton filename={`qtrade-${result.symbol}-five-minute-${result.preview_hash.slice(0, 12)}.json`} value={result} label="导出个股回测" />
      </>}
      {(sessionRuns.length > 0 || batchDiagnosticExport) && <Card data-testid="single-stock-session-comparison" type="inner" title={`本次会话个股回测对比（${sessionRuns.length}/${SESSION_RUN_LIMIT}，覆盖 ${sessionSectorCount} 个板块）`} extra={<JsonExportButton filename={batchDiagnosticExport ? 'qtrade-cross-sector-five-minute-session-diagnostic.json' : 'qtrade-single-stock-five-minute-session-comparison.json'} value={batchDiagnosticExport ?? sessionRuns} label="导出本次会话对比" size="small" />}>
        <Space orientation="vertical" style={{ width: '100%' }}>
          <Typography.Text type="secondary">仅保留当前页面会话最近 {SESSION_RUN_LIMIT} 个不同结果哈希，可按每板块一股或两股检查同参表现；覆盖数只是当前会话去重统计。刷新后清空，不写服务端、不构成正式实验或晋级证据。</Typography.Text>
          <Table<SingleStockFiveMinuteBacktest>
            rowKey="preview_hash"
            size="small"
            dataSource={sessionRuns}
            pagination={sessionRuns.length > 15 ? { pageSize: 15, showSizeChanger: false } : false}
            columns={[
              { title: '个股', render: (_: unknown, row) => `${row.symbol} ${row.instrument.name}` },
              { title: '板块', render: (_: unknown, row) => row.instrument.sector_name },
              { title: '结果哈希', render: (_: unknown, row) => <Typography.Text code>{row.preview_hash.slice(0, 12)}…</Typography.Text> },
              { title: '区间', render: (_: unknown, row) => `${row.parameters.start} → ${row.parameters.end}` },
              { title: '短/长窗', render: (_: unknown, row) => `${row.parameters.short_window}/${row.parameters.long_window}` },
              { title: '成本 bps', render: (_: unknown, row) => row.parameters.cost_bps },
              { title: '累计收益', render: (_: unknown, row) => formatPercent(row.metrics.overlay.total_return) },
              { title: '年化收益', render: (_: unknown, row) => formatPercent(row.metrics.overlay.annualized_return) },
              { title: 'Sharpe', render: (_: unknown, row) => formatNumber(row.metrics.overlay.sharpe) },
              { title: '最大回撤', render: (_: unknown, row) => formatPercent(row.metrics.overlay.maximum_drawdown) },
              { title: '主动超额', render: (_: unknown, row) => formatPercent(row.metrics.excess_return) },
              { title: '信息比率', render: (_: unknown, row) => formatNumber(row.metrics.information_ratio) },
              { title: '平均敞口', render: (_: unknown, row) => formatPercent(row.execution.mean_exposure) },
              { title: '换手', render: (_: unknown, row) => formatNumber(row.execution.turnover) },
              { title: '操作', render: (_: unknown, row) => <Space size={4}>
                <Button type="link" size="small" onClick={() => {
                  form.setFieldsValue(row.parameters)
                  setResult(null)
                  setError(null)
                  setBatchStatus(null)
                  setBatchProgress(0)
                }}>载入该次参数</Button>
                <JsonExportButton filename={`qtrade-${row.symbol}-five-minute-${row.preview_hash.slice(0, 12)}.json`} value={row} label="导出该次" size="small" type="link" />
              </Space> },
            ]}
            scroll={{ x: 1820 }}
          />
        </Space>
      </Card>}
    </Space>
  </Card>
}

function Metric({ label, value }: { label: string; value: string }) {
  return <div><span>{label}</span><strong>{value}</strong></div>
}

function formatNumber(value: string) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toFixed(3) : '—'
}

function formatPercent(value: string) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `${(parsed * 100).toFixed(2)}%` : '—'
}

function formatDraftNumber(value: string | number) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? String(parsed) : '—'
}

function formatDraftPercent(value: string | number) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `${(parsed * 100).toFixed(2)}%` : '—'
}

function formatBatchOutcome(outcome: SectorBatchOutcome) {
  if (outcome === 'COMPLETED') return '已接纳'
  if (outcome === 'IDENTITY_REJECTED') return '身份拒绝'
  return '请求失败'
}
