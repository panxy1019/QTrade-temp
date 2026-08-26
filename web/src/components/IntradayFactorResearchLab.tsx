import { Alert, Button, Card, Form, Input, InputNumber, Select, Space, Steps, Table, Tabs, Tag, Typography } from 'antd'
import { useState } from 'react'
import { runIntradayFactorBacktest } from '../api/client'
import { EquityChart } from '../charts/EquityChart'
import type { FiveMinuteResearchReport, IntradayFactorBacktest, IntradayFactorBacktestRequest, IntradayFactorLongWindowReport } from '../types'
import { JsonExportButton } from './JsonExportButton'

type VerifiedFiveMinuteDataset = NonNullable<FiveMinuteResearchReport['minute_dataset']>

interface FormValues {
  factor: IntradayFactorBacktestRequest['factor']
  start: string
  end: string
  cost_bps: string | number
  minimum_body_return_bps: string | number
}

const workflowLabels: Record<string, string> = {
  FACTOR_DEFINITION: '因子定义',
  FACTOR_VALIDATION: '有效性检验',
  CORRELATION_VALIDATION: '相关性检验',
  ROBUSTNESS_VALIDATION: '滚动稳健性检验',
  STRATEGY_CONSTRUCTION: '策略构造',
  CAUSAL_BACKTEST: '因果回测',
  PAPER: '模拟盘',
  LIVE: '实盘',
}

const gateLabels: Record<string, string> = {
  minimum_observation_count: '至少 250 个观测',
  minimum_signal_count: '至少 20 次信号',
  positive_mean_signal_return: '信号后平均收益为正',
  positive_conditional_excess_return: '条件超额收益为正',
  positive_rank_ic: 'Rank IC 为正',
  positive_sector_ratio_at_least_60pct: '至少 60% 行业为正',
  maximum_absolute_factor_correlation_below_0_90: '相关性绝对值低于 0.90',
  base_cost_total_return_positive: '基础成本下总收益为正',
  base_cost_excess_return_positive: '基础成本下超额收益为正',
  walk_forward_oos_total_return_positive: '滚动样本外总收益为正',
  walk_forward_oos_excess_return_positive: '滚动样本外超额收益为正',
  walk_forward_positive_fold_ratio_at_least_60pct: '至少 60% 测试折收益为正',
  threshold_sensitivity_majority_positive: '多数固定阈值收益为正',
  minimum_two_disjoint_test_folds: '至少两个不重叠测试折',
  every_test_fold_has_signal: '每个测试折均有信号',
  causal_selection_known_before_test_signal: '阈值选择先于测试信号',
  stitched_oos_total_return_positive: '拼接样本外总收益为正',
  stitched_oos_excess_return_positive: '拼接样本外超额收益为正',
  positive_fold_ratio_at_least_60pct: '至少 60% 测试折为正',
  worst_threshold_total_return_non_negative: '最差固定阈值收益不为负',
}

export function IntradayFactorLongWindowEvidence({ report }: { report: IntradayFactorLongWindowReport }) {
  const [symbolQuery, setSymbolQuery] = useState('')
  const summary = report.summary
  if (!summary || !report.evaluation_contract) {
    return <Card title="120 日固定因子稳健性证据"><Alert showIcon type="warning" title="长期因子报告不可用" description="报告缺失，或其签名、数据身份、源码哈希、固定结果哈希未通过校验。" /></Card>
  }
  const contract = report.evaluation_contract
  const result = report.result
  const folds = result?.robustness_validation.folds ?? []
  const sectors = result?.backtest.sectors ?? []
  const symbols = (result?.backtest.symbols ?? []).filter((row) => row.group_id.includes(symbolQuery.trim()))
  const positiveSectors = sectors.filter((row) => Number(row.strategy_metrics.total_return) > 0).length
  const positiveSymbols = (result?.backtest.symbols ?? []).filter((row) => Number(row.strategy_metrics.total_return) > 0).length
  const researchGates = result
    ? Object.entries(result.research_gate.criteria).map(([gate_id, passed]) => ({ gate_id, passed }))
    : []
  const robustnessGates = result
    ? Object.entries(result.robustness_validation.criteria).map(([gate_id, passed]) => ({ gate_id, passed }))
    : []
  const curve = result ? {
    curve: [
      { session: '起点', equity: '1', drawdown: '0', benchmark_equity: '1', benchmark_drawdown: '0', excess_return: '0' },
      ...buildChartCurve(result),
    ],
  } : null
  return <Card
    title="120 日固定因子稳健性证据"
    extra={<JsonExportButton filename={`qtrade-intraday-factor-long-${report.evidence_sha256?.slice(0, 12) ?? 'unverified'}.json`} value={report} label="导出长期证据" />}
  >
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <Alert
        showIcon
        type="error"
        title={`长期复核未通过：${summary.positive_fold_count}/${summary.fold_count} 个测试折为正`}
        description={`${contract.selected_start} 至 ${contract.selected_end} 共 ${contract.selected_session_count} 日；首段 ${contract.initial_training_session_count} 日训练，随后 ${contract.fold_count} 折 × ${contract.test_sessions_per_fold} 日互不重叠测试。该窗口已经被查看，只是固化的复用历史诊断，不是独立确认。`}
      />
      <div className="description-grid">
        <Metric label="股票日观测" value={summary.observation_count.toLocaleString('zh-CN')} />
        <Metric label="触发信号" value={summary.signal_count.toLocaleString('zh-CN')} />
        <Metric label="全窗口收益" value={formatPercent(summary.full_window_strategy_metrics.total_return)} />
        <Metric label="全窗口超额" value={formatPercent(summary.full_window_strategy_metrics.excess_return)} />
        <Metric label="Rank IC" value={formatNumber(summary.rank_ic)} />
        <Metric label="样本外收益" value={formatPercent(summary.stitched_strategy_metrics.total_return)} />
        <Metric label="样本外超额" value={formatPercent(summary.stitched_strategy_metrics.excess_return)} />
        <Metric label="样本外最大回撤" value={formatPercent(summary.stitched_strategy_metrics.maximum_drawdown)} />
      </div>
      <Typography.Text>逐折训练选择阈值：{summary.selected_thresholds_bps.join(' → ')} bps；固定结果哈希 <Typography.Text code>{report.result_preview_hash}</Typography.Text>。</Typography.Text>
      {result ? <Tabs items={[
        {
          key: 'curve',
          label: '长期收益曲线',
          children: <Space orientation="vertical" style={{ width: '100%' }}>
            <EquityChart dark analytics={curve} />
            <Typography.Text type="secondary">120 日策略、同期等权基准、回撤与超额曲线均来自同一固定结果哈希；不使用盘中未完成 Bar。</Typography.Text>
          </Space>,
        },
        {
          key: 'folds',
          label: `滚动测试折 · ${folds.length}`,
          children: <Table rowKey="fold_id" size="small" pagination={false} dataSource={folds} columns={[
            { title: '测试折', dataIndex: 'fold_id' },
            { title: '训练区间', render: (_: unknown, row: typeof folds[number]) => `${row.training_start} → ${row.training_end}` },
            { title: '测试区间', render: (_: unknown, row: typeof folds[number]) => `${row.test_start} → ${row.test_end}` },
            { title: '阈值 bps', dataIndex: 'selected_minimum_body_return_bps' },
            { title: '信号', dataIndex: 'test_signal_count' },
            { title: '测试收益', render: (_: unknown, row: typeof folds[number]) => formatPercent(row.test_strategy_metrics.total_return) },
            { title: '测试超额', render: (_: unknown, row: typeof folds[number]) => formatPercent(row.test_strategy_metrics.excess_return) },
            { title: '最大回撤', render: (_: unknown, row: typeof folds[number]) => formatPercent(row.test_strategy_metrics.maximum_drawdown) },
            { title: '选择已知时间', dataIndex: 'selection_known_at' },
          ]} scroll={{ x: 1280 }} />,
        },
        {
          key: 'thresholds',
          label: '阈值压力',
          children: <Table rowKey="minimum_body_return_bps" size="small" pagination={false} dataSource={summary.threshold_sensitivity} columns={[
            { title: '固定阈值 bps', dataIndex: 'minimum_body_return_bps' },
            { title: '样本外信号', dataIndex: 'signal_count' },
            { title: '样本外收益', dataIndex: 'total_return', render: (value: string) => formatPercent(value) },
            { title: '样本外超额', dataIndex: 'excess_return', render: (value: string) => formatPercent(value) },
          ]} />,
        },
        {
          key: 'sectors',
          label: `板块拆分 · ${sectors.length}`,
          children: <Space orientation="vertical" style={{ width: '100%' }}>
            <Alert showIcon type={positiveSectors >= 9 ? 'success' : 'warning'} title={`正收益板块 ${positiveSectors}/${sectors.length}`} description="板块指标是各板块独立等权回放，不是把弱板块从组合中事后删除后的新策略。" />
            <Table rowKey="group_id" size="small" pagination={false} dataSource={sectors} columns={[
              { title: '板块', dataIndex: 'group_name' },
              { title: '代码', dataIndex: 'group_id' },
              { title: '观测', dataIndex: 'observation_count' },
              { title: '信号', dataIndex: 'signal_count' },
              { title: '信号后均值', dataIndex: 'mean_signal_forward_return', render: (value: string) => formatPercent(value) },
              { title: '策略收益', render: (_: unknown, row: typeof sectors[number]) => formatPercent(row.strategy_metrics.total_return) },
              { title: '超额收益', render: (_: unknown, row: typeof sectors[number]) => formatPercent(row.strategy_metrics.excess_return) },
              { title: 'Sharpe', render: (_: unknown, row: typeof sectors[number]) => formatNumber(row.strategy_metrics.sharpe) },
              { title: '最大回撤', render: (_: unknown, row: typeof sectors[number]) => formatPercent(row.strategy_metrics.maximum_drawdown) },
            ]} scroll={{ x: 1150 }} />
          </Space>,
        },
        {
          key: 'symbols',
          label: `个股拆分 · ${result.backtest.symbols.length}`,
          children: <Space orientation="vertical" style={{ width: '100%' }}>
            <Alert showIcon type="info" title={`正收益个股 ${positiveSymbols}/${result.backtest.symbols.length}`} description="个股拆分用于发现集中度和迁移失败，不参与当前固定阈值的再次选择。" />
            <Input.Search aria-label="搜索长期因子个股" allowClear placeholder="搜索六位股票代码" value={symbolQuery} onChange={(event) => setSymbolQuery(event.target.value)} style={{ width: 280 }} />
            <Table rowKey="group_id" size="small" dataSource={symbols} pagination={{ pageSize: 8, showSizeChanger: false }} columns={[
              { title: '股票', dataIndex: 'group_id' },
              { title: '信号', dataIndex: 'signal_count' },
              { title: '信号后均值', dataIndex: 'mean_signal_forward_return', render: (value: string) => formatPercent(value) },
              { title: '命中率', dataIndex: 'signal_hit_rate', render: (value: string) => formatPercent(value) },
              { title: '策略收益', render: (_: unknown, row: typeof symbols[number]) => formatPercent(row.strategy_metrics.total_return) },
              { title: '超额收益', render: (_: unknown, row: typeof symbols[number]) => formatPercent(row.strategy_metrics.excess_return) },
              { title: 'Sharpe', render: (_: unknown, row: typeof symbols[number]) => formatNumber(row.strategy_metrics.sharpe) },
              { title: '最大回撤', render: (_: unknown, row: typeof symbols[number]) => formatPercent(row.strategy_metrics.maximum_drawdown) },
            ]} scroll={{ x: 1050 }} />
          </Space>,
        },
        {
          key: 'gates',
          label: '研究硬门',
          children: <Space orientation="vertical" style={{ width: '100%' }}>
            <Table rowKey="gate_id" size="small" pagination={false} dataSource={researchGates} columns={[
              { title: '有效性、相关性与回测门槛', dataIndex: 'gate_id', render: (value: string) => gateLabels[value] ?? value },
              { title: '结果', dataIndex: 'passed', render: (value: boolean) => value ? <Tag color="green">通过</Tag> : <Tag color="red">未通过</Tag> },
            ]} />
            <Table rowKey="gate_id" size="small" pagination={false} dataSource={robustnessGates} columns={[
              { title: '滚动稳健性门槛', dataIndex: 'gate_id', render: (value: string) => gateLabels[value] ?? value },
              { title: '结果', dataIndex: 'passed', render: (value: boolean) => value ? <Tag color="green">通过</Tag> : <Tag color="red">未通过</Tag> },
            ]} />
          </Space>,
        },
      ]} /> : <Alert showIcon type="warning" title="长期明细不可用" description="汇总仍可显示，但完整收益曲线、折级、板块、个股或硬门明细没有通过校验。" />}
      <Typography.Text type="secondary">报告绑定 75 股、15 行业、原生 5 分钟数据 checksum、因子实现源码和完整响应哈希；结论固定为 {report.promotion}，PAPER {report.paper_readiness}，LIVE {report.live_readiness}，外部订单 DISABLED。</Typography.Text>
    </Space>
  </Card>
}

export function IntradayFactorResearchLab({ dataset }: { dataset: VerifiedFiveMinuteDataset | null }) {
  const [result, setResult] = useState<IntradayFactorBacktest | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)

  if (!dataset) {
    return <Card title="分钟因子研究工作台"><Alert showIcon type="warning" title="长历史 5 分钟数据不可用" description="系统不会用合成行情或不完整缓存替代经过 checksum 验证的多股票数据。" /></Card>
  }

  const eligibleEndDays = dataset.trading_days.slice(0, -1)
  const defaultEndIndex = eligibleEndDays.length - 1
  const initialValues: FormValues = {
    factor: 'opening_three_ten_minute_bullish',
    start: eligibleEndDays[Math.max(0, defaultEndIndex - 19)],
    end: eligibleEndDays[defaultEndIndex],
    cost_bps: '5',
    minimum_body_return_bps: '0',
  }
  const submit = async (values: FormValues) => {
    const payload: IntradayFactorBacktestRequest = {
      ...values,
      cost_bps: String(values.cost_bps),
      minimum_body_return_bps: String(values.minimum_body_return_bps),
    }
    setRunning(true)
    setError(null)
    try {
      setResult(await runIntradayFactorBacktest(payload))
    } catch (reason) {
      setResult(null)
      setError(reason instanceof Error ? reason.message : '分钟因子研究失败')
    } finally {
      setRunning(false)
    }
  }
  const curve = result ? {
    curve: [
      { session: '起点', equity: '1', drawdown: '0', benchmark_equity: '1', benchmark_drawdown: '0', excess_return: '0' },
      ...buildChartCurve(result),
    ],
  } : null
  const stressRows = result
    ? Object.entries(result.backtest.cost_stress).map(([cost_bps, value]) => ({ cost_bps, ...value }))
    : []
  const gateRows = result
    ? Object.entries(result.research_gate.criteria).map(([gate_id, passed]) => ({ gate_id, passed }))
    : []
  const correlationRows = result
    ? Object.entries(result.correlation_validation.correlations).map(([factor, correlation]) => ({ factor, correlation }))
    : []
  const robustnessGateRows = result
    ? Object.entries(result.robustness_validation.criteria).map(([gate_id, passed]) => ({ gate_id, passed }))
    : []

  return <Card title="分钟因子研究工作台" extra={<Typography.Text type="secondary">{dataset.symbol_count} 股 · {dataset.sector_count} 板块 · 原生 5 分钟</Typography.Text>}>
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <Alert showIcon type="info" title="从因子到模拟盘的完整门控" description="三根 10 分钟阳线在 10:00 才确认，最早取 10:05 标记的下一根 5 分钟 Bar 开盘价（区间起点 10:00）买入，并按 A 股 T+1 取下一交易日 09:35 标记 Bar 的开盘价（区间起点 09:30）退出。当前参数研究是事后非 PIT 检验，永远不会自动进入 PAPER 或 LIVE。" />
      <Form<FormValues> name="intraday-factor-research-backtest" layout="vertical" initialValues={initialValues} onFinish={(values) => { void submit(values) }}>
        <div className="backtest-lab-grid">
          <Form.Item name="factor" label="因子" rules={[{ required: true }]}><Select options={[{ value: 'opening_three_ten_minute_bullish', label: '09:30 起三根 10 分钟阳线' }]} /></Form.Item>
          <Form.Item name="start" label="开始交易日" rules={[{ required: true }]}><Select showSearch options={eligibleEndDays.map((value) => ({ value, label: value }))} /></Form.Item>
          <Form.Item name="end" label="结束交易日" rules={[{ required: true }]}><Select showSearch options={eligibleEndDays.map((value) => ({ value, label: value }))} /></Form.Item>
          <Form.Item name="cost_bps" label="单边成本（bps）" rules={[{ required: true }]}><InputNumber min={0} max={100} step={1} stringMode /></Form.Item>
          <Form.Item name="minimum_body_return_bps" label="每根 10 分钟实体下限（bps）" rules={[{ required: true }]}><InputNumber min={0} max={1000} step={1} stringMode /></Form.Item>
        </div>
        <Button type="primary" htmlType="submit" loading={running}>运行 75 股因子检验与回测</Button>
      </Form>
      {error && <Alert showIcon type="error" title="因子研究未完成" description={error} />}
      {result && <>
        <Alert
          showIcon
          type={result.research_gate.passed ? 'success' : 'warning'}
          title={result.research_gate.passed ? '研究硬门通过，但仍不可自动晋级' : '回测已完成，研究硬门未通过'}
          description={`实际触发 ${result.event_summary.signal_count}/${result.event_summary.observation_count} 次；${result.event_summary.positive_sector_count}/${result.event_summary.sector_count} 个行业信号后均值为正。当前结论 ${result.promotion}，PAPER ${result.paper_readiness}，LIVE ${result.live_readiness}。`}
        />
        <Steps
          size="small"
          orientation="vertical"
          items={result.workflow.map((stage) => ({
            title: `${workflowLabels[stage.id] ?? stage.id} · ${stage.status}`,
            content: stage.detail,
            status: stage.status === 'FAILED' ? 'error' : ['PASSED', 'AVAILABLE', 'COMPLETED'].includes(stage.status) ? 'finish' : 'wait',
          }))}
        />
        <div className="description-grid">
          <Metric label="累计收益" value={formatPercent(result.backtest.strategy_metrics.total_return)} />
          <Metric label="年化收益" value={formatPercent(result.backtest.strategy_metrics.annualized_return)} />
          <Metric label="Sharpe" value={formatNumber(result.backtest.strategy_metrics.sharpe)} />
          <Metric label="最大回撤" value={formatPercent(result.backtest.strategy_metrics.maximum_drawdown)} />
          <Metric label="超额收益" value={formatPercent(result.backtest.strategy_metrics.excess_return)} />
          <Metric label="胜率" value={formatPercent(result.backtest.strategy_metrics.win_rate)} />
          <Metric label="Rank IC" value={formatNumber(result.factor_validation.rank_ic)} />
          <Metric label="ICIR" value={formatNumber(result.factor_validation.icir)} />
          <Metric label="信号率" value={formatPercent(result.event_summary.signal_rate)} />
          <Metric label="条件超额" value={formatPercent(result.event_summary.conditional_excess_return)} />
          <Metric label="滚动样本外收益" value={formatPercent(result.robustness_validation.stitched_strategy_metrics.total_return)} />
          <Metric label="滚动样本外超额" value={formatPercent(result.robustness_validation.stitched_strategy_metrics.excess_return)} />
        </div>
        <EquityChart dark analytics={curve} />
        <Tabs items={[
          {
            key: 'contract',
            label: '因子与时序',
            children: <Space orientation="vertical" style={{ width: '100%' }}>
              <Typography.Text code>{result.factor_definition.expression}</Typography.Text>
              <Typography.Text>数据：{result.factor_definition.source_interval}；信号已知：{result.factor_definition.known_at}；入场：{result.strategy.entry_rule}；退出：{result.strategy.exit_rule}。</Typography.Text>
              <Typography.Text type="secondary">响应哈希 {result.preview_hash}；数据 checksum {result.dataset_checksum}；读取 {result.source_bar_count.toLocaleString('zh-CN')} 根已验证 Bar，无状态写入、无外部订单。</Typography.Text>
            </Space>,
          },
          {
            key: 'gates',
            label: '有效性与相关性',
            children: <Space orientation="vertical" style={{ width: '100%' }}>
              <Table rowKey="gate_id" size="small" pagination={false} dataSource={gateRows} columns={[
                { title: '检验门槛', dataIndex: 'gate_id', render: (value: string) => gateLabels[value] ?? value },
                { title: '结果', dataIndex: 'passed', render: (value: boolean) => value ? <Tag color="green">通过</Tag> : <Tag color="red">未通过</Tag> },
              ]} />
              <Table rowKey="factor" size="small" pagination={false} dataSource={correlationRows} columns={[
                { title: '相关开盘特征', dataIndex: 'factor' },
                { title: 'Pearson 相关系数', dataIndex: 'correlation', render: (value: string) => formatNumber(value) },
                { title: '绝对值门槛', render: () => `< ${result.correlation_validation.maximum_absolute_correlation}` },
              ]} />
            </Space>,
          },
          {
            key: 'robustness',
            label: '滚动样本外与阈值',
            children: <Space orientation="vertical" style={{ width: '100%' }}>
              <Alert
                showIcon
                type="warning"
                title="复用历史样本的因果诊断，不是新的独立证据"
                description={`固定候选阈值 ${result.robustness_validation.candidate_thresholds_bps.join('/')} bps；首段 ${result.robustness_validation.initial_training_session_count} 日训练，其后 ${result.robustness_validation.folds.length} 个测试折互不重叠。每折只用此前训练日选阈值，测试收益不参与当折选择。`}
              />
              <Table rowKey="fold_id" size="small" pagination={false} dataSource={result.robustness_validation.folds} columns={[
                { title: '测试折', dataIndex: 'fold_id' },
                { title: '训练区间', render: (_: unknown, row: IntradayFactorBacktest['robustness_validation']['folds'][number]) => `${row.training_start} → ${row.training_end}` },
                { title: '测试区间', render: (_: unknown, row: IntradayFactorBacktest['robustness_validation']['folds'][number]) => `${row.test_start} → ${row.test_end}` },
                { title: '所选阈值 bps', dataIndex: 'selected_minimum_body_return_bps' },
                { title: '测试信号', dataIndex: 'test_signal_count' },
                { title: '测试收益', render: (_: unknown, row: IntradayFactorBacktest['robustness_validation']['folds'][number]) => formatPercent(row.test_strategy_metrics.total_return) },
                { title: '测试超额', render: (_: unknown, row: IntradayFactorBacktest['robustness_validation']['folds'][number]) => formatPercent(row.test_strategy_metrics.excess_return) },
                { title: '最大回撤', render: (_: unknown, row: IntradayFactorBacktest['robustness_validation']['folds'][number]) => formatPercent(row.test_strategy_metrics.maximum_drawdown) },
              ]} scroll={{ x: 1050 }} />
              <Table rowKey="minimum_body_return_bps" size="small" pagination={false} dataSource={result.robustness_validation.threshold_sensitivity} columns={[
                { title: '固定阈值 bps', dataIndex: 'minimum_body_return_bps' },
                { title: '样本外信号', dataIndex: 'signal_count' },
                { title: '信号率', dataIndex: 'signal_rate', render: (value: string) => formatPercent(value) },
                { title: '样本外收益', render: (_: unknown, row: IntradayFactorBacktest['robustness_validation']['threshold_sensitivity'][number]) => formatPercent(row.strategy_metrics.total_return) },
                { title: '样本外超额', render: (_: unknown, row: IntradayFactorBacktest['robustness_validation']['threshold_sensitivity'][number]) => formatPercent(row.strategy_metrics.excess_return) },
                { title: 'Sharpe', render: (_: unknown, row: IntradayFactorBacktest['robustness_validation']['threshold_sensitivity'][number]) => formatNumber(row.strategy_metrics.sharpe) },
              ]} />
              <Table rowKey="gate_id" size="small" pagination={false} dataSource={robustnessGateRows} columns={[
                { title: '稳健性门槛', dataIndex: 'gate_id', render: (value: string) => gateLabels[value] ?? value },
                { title: '结果', dataIndex: 'passed', render: (value: boolean) => value ? <Tag color="green">通过</Tag> : <Tag color="red">未通过</Tag> },
              ]} />
            </Space>,
          },
          {
            key: 'stress',
            label: '成本压力',
            children: <Table rowKey="cost_bps" size="small" pagination={false} dataSource={stressRows} columns={[
              { title: '单边成本 bps', dataIndex: 'cost_bps' },
              { title: '策略收益', render: (_: unknown, row: typeof stressRows[number]) => formatPercent(row.strategy_metrics.total_return) },
              { title: '年化收益', render: (_: unknown, row: typeof stressRows[number]) => formatPercent(row.strategy_metrics.annualized_return) },
              { title: 'Sharpe', render: (_: unknown, row: typeof stressRows[number]) => formatNumber(row.strategy_metrics.sharpe) },
              { title: '最大回撤', render: (_: unknown, row: typeof stressRows[number]) => formatPercent(row.strategy_metrics.maximum_drawdown) },
              { title: '超额收益', render: (_: unknown, row: typeof stressRows[number]) => formatPercent(row.strategy_metrics.excess_return) },
            ]} />,
          },
          {
            key: 'sectors',
            label: '15 行业',
            children: <GroupTable rows={result.backtest.sectors} nameLabel="行业" />,
          },
          {
            key: 'symbols',
            label: '75 个股',
            children: <GroupTable rows={result.backtest.symbols} nameLabel="证券" />,
          },
        ]} />
        <Alert showIcon type="warning" title={`五档买卖盘强度：${result.l2_factor_readiness.readiness}`} description={`公式 ${result.l2_factor_readiness.formula} 需要 ${result.l2_factor_readiness.required_data}。当前没有获授权的 L2 来源，系统不会从 OHLCV 伪造买一至买五/卖一至卖五。`} />
        <JsonExportButton filename={`qtrade-intraday-factor-${result.preview_hash.slice(0, 12)}.json`} value={result} label="导出因子研究证据" />
      </>}
    </Space>
  </Card>
}

function GroupTable({ rows, nameLabel }: { rows: IntradayFactorBacktest['backtest']['sectors']; nameLabel: string }) {
  return <Table
    rowKey="group_id"
    size="small"
    dataSource={rows}
    pagination={{ pageSize: 8, showSizeChanger: false }}
    columns={[
      { title: nameLabel, dataIndex: 'group_name' },
      { title: '信号数', dataIndex: 'signal_count' },
      { title: '信号后均值', dataIndex: 'mean_signal_forward_return', render: (value: string) => formatPercent(value) },
      { title: '信号胜率', dataIndex: 'signal_hit_rate', render: (value: string) => formatPercent(value) },
      { title: '策略收益', render: (_: unknown, row: typeof rows[number]) => formatPercent(row.strategy_metrics.total_return) },
      { title: '超额收益', render: (_: unknown, row: typeof rows[number]) => formatPercent(row.strategy_metrics.excess_return) },
      { title: '最大回撤', render: (_: unknown, row: typeof rows[number]) => formatPercent(row.strategy_metrics.maximum_drawdown) },
    ]}
    scroll={{ x: 900 }}
  />
}

function buildChartCurve(result: IntradayFactorBacktest) {
  let strategyPeak = 1
  let benchmarkPeak = 1
  return result.backtest.curve.map((point) => {
    const strategy = Number(point.strategy_equity)
    const benchmark = Number(point.benchmark_equity)
    strategyPeak = Math.max(strategyPeak, strategy)
    benchmarkPeak = Math.max(benchmarkPeak, benchmark)
    return {
      session: point.trading_day,
      equity: point.strategy_equity,
      drawdown: String(strategy / strategyPeak - 1),
      benchmark_equity: point.benchmark_equity,
      benchmark_drawdown: String(benchmark / benchmarkPeak - 1),
      excess_return: point.excess_equity,
    }
  })
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
