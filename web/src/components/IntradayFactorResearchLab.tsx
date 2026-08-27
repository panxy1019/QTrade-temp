import { Alert, Button, Card, Form, Input, InputNumber, Popconfirm, Select, Space, Steps, Table, Tabs, Tag, Typography } from 'antd'
import { useState } from 'react'
import { runIntradayFactorBacktest, runIntradayFactorCorrelation, runIntradayFactorDiscovery } from '../api/client'
import { EquityChart } from '../charts/EquityChart'
import {
  deleteLocalIntradayFactorCandidate,
  loadLocalIntradayFactorCandidates,
  saveLocalIntradayFactorCandidate,
} from '../research/localIntradayFactorCandidates'
import type { LocalIntradayFactorCandidate } from '../research/localIntradayFactorCandidates'
import {
  buildLocalIntradayFactorComparisonExport,
  deleteLocalIntradayFactorExperiment,
  loadLocalIntradayFactorExperiments,
  saveLocalIntradayFactorExperiment,
} from '../research/localIntradayFactorExperiments'
import type { LocalIntradayFactorExperiment } from '../research/localIntradayFactorExperiments'
import { buildIntradayFactorStrategyLineage } from '../research/intradayFactorStrategyLineage'
import type { IntradayFactorStrategyLineage } from '../research/intradayFactorStrategyLineage'
import { matchesUnavailableL2FactorReadiness } from '../research/l2FactorReadiness'
import type { FiveMinuteResearchReport, IntradayExitRule, IntradayFactorBacktest, IntradayFactorBacktestRequest, IntradayFactorCorrelation, IntradayFactorDiscovery, IntradayFactorLongWindowReport, IntradayOpeningDirection } from '../types'
import { JsonExportButton } from './JsonExportButton'

type VerifiedFiveMinuteDataset = NonNullable<FiveMinuteResearchReport['minute_dataset']>

interface FormValues {
  factor: IntradayFactorBacktestRequest['factor']
  start: string
  end: string
  cost_bps: string | number
  minimum_body_return_bps: string | number
  first_direction: IntradayOpeningDirection
  second_direction: IntradayOpeningDirection
  third_direction: IntradayOpeningDirection
  combination: 'ALL' | 'ANY'
  exit_rule: IntradayExitRule
  exit_minimum_body_return_bps: string | number
}

interface CorrelationHandoff {
  candidate: LocalIntradayFactorCandidate
  ruleId: string
  correlation: IntradayFactorCorrelation
}

const directionOptions = [
  { value: 'BULLISH', label: '看涨（收盘高于开盘）' },
  { value: 'BEARISH', label: '看跌（收盘低于开盘）' },
  { value: 'IGNORE', label: '忽略该时段' },
]

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

const paperAdmissionLabels: Record<string, string> = {
  verified_historical_dataset_identity_bound: '历史数据身份已验证并绑定',
  research_gate_passed: '因子研究硬门通过',
  causal_backtest_passed: '因果回测收益门通过',
  registered_immutable_strategy_version_available: '不可变策略版本已注册',
  unseen_forward_shadow_evidence_attached: '已附真正未见前向 SHADOW 证据',
  authorized_realtime_provider_ready: '授权实时行情供应商已就绪',
  fresh_runtime_readiness_attestation_available: '新鲜运行时就绪证明可用',
  reconciliation_ready: 'PAPER 对账状态已就绪',
}

const advancementActionLabels: Record<string, string> = {
  PREDECLARE_NEW_HYPOTHESIS_BEFORE_NEW_EVIDENCE: '在接触新证据前预注册新假设',
  REGISTER_IMMUTABLE_STRATEGY_THEN_AUTHORIZED_FORWARD_SHADOW: '注册不可变策略后再进入授权前向 SHADOW',
}

const preregistrationGateLabels: Record<string, string> = {
  new_hypothesis_identity: '使用全新且未评估的假设 ID',
  new_evidence_period_disjoint_from_viewed_and_prior_test_periods: '新证据窗口与已查看窗口及历史测试窗口互斥',
  independent_leakage_review_pass: '独立数据泄漏审查通过',
  fixed_candidate_budget: '接触新数据前冻结候选预算',
  fixed_robustness_scenarios: '接触新数据前冻结有界稳健性情景',
  fixed_cost_model: '接触新数据前冻结成本模型',
  immutable_spec_and_hash_before_data_access: '接触新数据前冻结实验规格与哈希',
  contamination_disclosure: '披露用于构造新假设的既有证据',
}

const strategyRegistrationGateLabels: Record<string, string> = {
  all_ordered_research_stages_passed: '有效性、相关性与稳健性顺序门全部通过',
  causal_backtest_passed: '因果回测收益门通过',
  signed_hypothesis_preregistration_attached: '已绑定签名假设预注册',
  independent_leakage_review_passed: '独立数据泄漏审查通过',
  behavior_spec_hash_bound: '开平仓行为规格哈希已绑定',
  complete_registry_spec_and_hash_bound: '完整注册规格与哈希已绑定',
  dataset_identity_bound: '数据版本与 checksum 已绑定',
  disjoint_time_split_frozen: '互斥训练/验证/测试切分已冻结',
  lineage_root_or_parent_binding_declared: '根策略或父策略血缘已声明',
}

const gateLabels: Record<string, string> = {
  minimum_observation_count: '至少 250 个观测',
  minimum_signal_count: '至少 20 次信号',
  positive_mean_signal_return: '信号后平均收益为正',
  positive_conditional_excess_return: '条件超额收益为正',
  minimum_ten_clustered_signal_sessions: '至少 10 个聚类信号交易日',
  newey_west_t_statistic_above_1_96: 'Newey-West t 大于 1.96',
  temporal_stability_passed: '连续时间块稳定性通过',
  minimum_four_contiguous_blocks: '四个连续互斥时间块完整',
  every_block_has_signal_session: '每个时间块至少一个信号日',
  positive_block_ratio_at_least_75pct: '至少 75% 时间块条件超额为正',
  maximum_absolute_block_contribution_share_below_0_60: '单块绝对贡献占比低于 60%',
  positive_rank_ic: 'Rank IC 为正',
  positive_sector_ratio_at_least_60pct: '至少 60% 行业为正',
  maximum_absolute_factor_correlation_below_0_90: '相关性绝对值低于 0.90',
  base_cost_total_return_positive: '基础成本下总收益为正',
  base_cost_excess_return_positive: '基础成本下超额收益为正',
  walk_forward_oos_total_return_positive: '滚动样本外总收益为正',
  walk_forward_oos_excess_return_positive: '滚动样本外超额收益为正',
  walk_forward_positive_fold_ratio_at_least_60pct: '至少 60% 测试折收益为正',
  threshold_sensitivity_majority_positive: '多数固定阈值收益为正',
  exit_factor_effectiveness_passed: '平仓因子有效性通过',
  minimum_twenty_exit_triggers: '至少 20 次平仓因子触发',
  minimum_ten_clustered_exit_trigger_sessions: '至少 10 个聚类平仓触发日',
  positive_mean_session_exit_timing_advantage: '平仓触发的日聚类卖出价优势为正',
  exit_timing_newey_west_t_statistic_above_1_96: '平仓卖出价优势 Newey-West t 大于 1.96',
  minimum_two_disjoint_test_folds: '至少两个不重叠测试折',
  every_test_fold_has_signal: '每个测试折均有信号',
  causal_selection_known_before_test_signal: '阈值选择先于测试信号',
  stitched_oos_total_return_positive: '拼接样本外总收益为正',
  stitched_oos_excess_return_positive: '拼接样本外超额收益为正',
  positive_fold_ratio_at_least_60pct: '至少 60% 测试折为正',
  worst_threshold_total_return_non_negative: '最差固定阈值收益不为负',
  minimum_250_test_observations: '测试段至少 250 个观测',
  minimum_twenty_test_signals: '测试段至少 20 次信号',
  minimum_ten_test_signal_sessions: '测试段至少 10 个信号日',
  positive_test_session_conditional_excess: '测试段日聚类条件超额为正',
  test_newey_west_t_statistic_above_1_96: '测试段 Newey-West t 大于 1.96',
  test_strategy_total_return_positive: '测试段策略累计收益为正',
  test_strategy_excess_return_positive: '测试段策略超额收益为正',
  probability_of_backtest_overfitting_below_0_25: '组合式回测过拟合概率低于 25%',
  selected_oos_positive_excess_ratio_at_least_0_60: '组合样本外正超额比例至少 60%',
  median_selected_oos_excess_return_positive: '组合样本外超额中位数为正',
  zero_cost_locked_test_total_return_positive: '零成本锁定测试收益为正',
  all_fixed_cost_locked_test_total_returns_positive: '四档固定成本下锁定测试收益均为正',
  twenty_bps_locked_test_excess_return_positive: '20 bps 下锁定测试超额收益为正',
  locked_test_total_return_non_increasing_with_cost: '成本升高时策略收益不反向增加',
  chronological_locked_test_passed: '时间顺序锁定测试通过',
  selection_bias_audit_passed: '组合式选择偏差审计通过',
  locked_test_cost_stress_passed: '锁定测试固定成本压力通过',
  locked_test_sector_stress_passed: '锁定测试多行业压力通过',
  locked_test_symbol_stress_passed: '锁定测试逐股票压力通过',
  locked_test_regime_stress_passed: '锁定测试信号前市场状态压力通过',
  locked_test_placebo_audit_passed: '锁定测试会话内置换安慰剂通过',
  locked_test_execution_delay_stress_passed: '锁定测试执行延迟压力通过',
  locked_test_liquidity_capacity_stress_passed: '锁定测试流动性/容量压力通过',
  every_locked_test_sector_has_signal: '每个测试行业均有信号',
  positive_locked_test_sector_ratio_at_least_0_60: '至少 60% 测试行业收益为正',
  positive_leave_one_sector_out_ratio_at_least_0_80: '至少 80% 整行业剔除收益为正',
  worst_leave_one_sector_out_total_return_non_negative: '最差整行业剔除收益不为负',
  every_locked_test_symbol_has_signal: '每只测试股票均有信号',
  positive_locked_test_symbol_ratio_at_least_0_60: '至少 60% 测试股票收益为正',
  median_locked_test_symbol_total_return_positive: '测试股票收益中位数为正',
  top_five_absolute_symbol_return_share_below_0_30: '绝对收益贡献前五股占比低于 30%',
  both_previous_session_direction_regimes_present: '上一交易日涨跌两类状态均出现',
  minimum_five_sessions_per_previous_session_regime: '每类上一交易日状态至少五个测试日',
  every_previous_session_regime_known_before_signal: '每个状态均在当前信号前确定',
  every_previous_session_regime_has_signal: '每类状态均有测试信号',
  all_previous_session_regime_total_returns_positive: '每类状态策略收益均为正',
  all_previous_session_regime_excess_returns_positive: '每类状态策略超额均为正',
  minimum_ten_locked_test_signal_sessions_for_placebo: '安慰剂检验至少覆盖 10 个锁定测试信号日',
  placebo_preserves_every_session_signal_count: '每次置换保留每个测试日的信号数',
  observed_conditional_excess_positive: '实际条件超额为正',
  empirical_one_sided_placebo_p_value_at_most_0_05: '单侧置换 p 值不高于 0.05',
  three_predeclared_participation_rates_present: '固定 1% / 5% / 10% 三档参与率齐全',
  all_capacity_uses_completed_signal_bar_volume: '容量只使用入场前已完成的信号 Bar 成交量',
  every_participation_row_preserves_locked_signal_identity: '每档容量均保留锁定测试信号身份',
  every_locked_signal_supports_one_round_lot_at_1pct: '每个锁定信号在 1% 参与率下至少支持一手',
  exact_provider_rmb_amount_available: '供应商原始人民币成交额可用',
  authorized_l2_provider_selected: '获授权 L2 供应商已选择',
  five_level_schema_certified: '五档价格与委托量字段已认证',
  size_unit_normalized_to_shares: '委托量单位已认证并归一为股',
  event_time_and_sequence_available: '交易所事件时间与序号可用',
  freshness_threshold_certified: '供应商新鲜度阈值已认证',
  recorded_replay_reconciliation_passed: 'L2 录制与回放对账已通过',
  observed_conditional_excess_above_placebo_p95: '实际条件超额高于安慰剂第 95 百分位',
  three_predeclared_execution_delays_present: '10:05/10:10/10:15 三档执行时点完整',
  every_execution_delay_preserves_locked_signal_identity: '每档延迟保持锁定信号身份与数量',
  all_execution_delay_total_returns_positive: '每档执行延迟的策略收益均为正',
  all_execution_delay_excess_returns_positive: '每档执行延迟的策略超额均为正',
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
        {result && <Metric label="聚类信号日" value={String(result.event_summary.clustered_signal_session_count)} />}
        {result && <Metric label="Newey-West t" value={formatNumber(result.event_summary.newey_west_t_statistic)} />}
        {result && <Metric label="聚类超额 95% 区间" value={`${formatPercent(result.event_summary.confidence_interval_95_lower)} ～ ${formatPercent(result.event_summary.confidence_interval_95_upper)}`} />}
        {result && <Metric label="正向时间块" value={`${result.stability_validation.positive_block_count}/${result.stability_validation.block_count}`} />}
        {result && <Metric label="最差时间块超额" value={formatPercent(result.stability_validation.worst_block_mean_conditional_excess_return)} />}
        <Metric label="样本外收益" value={formatPercent(summary.stitched_strategy_metrics.total_return)} />
        <Metric label="样本外超额" value={formatPercent(summary.stitched_strategy_metrics.excess_return)} />
        <Metric label="样本外最大回撤" value={formatPercent(summary.stitched_strategy_metrics.maximum_drawdown)} />
        <Metric label="Bootstrap 超额中位" value={formatPercent(summary.stitched_excess_bootstrap.median_excess_total_return)} />
        <Metric label="Bootstrap 超额 90% 区间" value={`${formatPercent(summary.stitched_excess_bootstrap.p05_excess_total_return)} ～ ${formatPercent(summary.stitched_excess_bootstrap.p95_excess_total_return)}`} />
        <Metric label="Bootstrap 超额为正概率" value={formatPercent(summary.stitched_excess_bootstrap.probability_positive_excess)} />
      </div>
      <Typography.Text>逐折训练选择阈值：{summary.selected_thresholds_bps.join(' → ')} bps；固定结果哈希 <Typography.Text code>{report.result_preview_hash}</Typography.Text>。样本外超额区间使用 60 个测试日、策略与基准共用重采样路径的 1,000 次配对 stationary bootstrap（期望块长 10 日）；这是复用历史诊断，不是新的盲测。</Typography.Text>
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
          key: 'stability',
          label: `时间稳定性 · ${result.stability_validation.positive_block_count}/${result.stability_validation.block_count}`,
          children: <StabilityPanel stability={result.stability_validation} />,
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
  const [form] = Form.useForm<FormValues>()
  const watchedMinimumBodyReturnBps = Form.useWatch('minimum_body_return_bps', form)
  const watchedFirstDirection = Form.useWatch('first_direction', form)
  const watchedSecondDirection = Form.useWatch('second_direction', form)
  const watchedThirdDirection = Form.useWatch('third_direction', form)
  const watchedCombination = Form.useWatch('combination', form)
  const watchedExitMinimumBodyReturnBps = Form.useWatch('exit_minimum_body_return_bps', form)
  const [result, setResult] = useState<IntradayFactorBacktest | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [running, setRunning] = useState(false)
  const [selectedFactor, setSelectedFactor] = useState<IntradayFactorBacktestRequest['factor']>('opening_three_ten_minute_bullish')
  const [selectedExitRule, setSelectedExitRule] = useState<IntradayExitRule>('NEXT_SESSION_OPEN')
  const [experimentName, setExperimentName] = useState('')
  const [saveStatus, setSaveStatus] = useState<string | null>(null)
  const [, setExperimentRevision] = useState(0)
  const [factorCandidateName, setFactorCandidateName] = useState('')
  const [factorCandidateStatus, setFactorCandidateStatus] = useState<string | null>(null)
  const [, setFactorCandidateRevision] = useState(0)
  const [selectedFactorCandidateKeys, setSelectedFactorCandidateKeys] = useState<string[]>([])
  const [correlationResult, setCorrelationResult] = useState<IntradayFactorCorrelation | null>(null)
  const [correlationLabels, setCorrelationLabels] = useState<Record<string, string>>({})
  const [correlationBindings, setCorrelationBindings] = useState<Record<string, LocalIntradayFactorCandidate>>({})
  const [correlationHandoff, setCorrelationHandoff] = useState<CorrelationHandoff | null>(null)
  const [strategyLineage, setStrategyLineage] = useState<IntradayFactorStrategyLineage | null>(null)
  const [correlationHandoffStatus, setCorrelationHandoffStatus] = useState<string | null>(null)
  const [correlationRunning, setCorrelationRunning] = useState(false)
  const [correlationError, setCorrelationError] = useState<string | null>(null)
  const [discoveryResult, setDiscoveryResult] = useState<IntradayFactorDiscovery | null>(null)
  const [discoveryRunning, setDiscoveryRunning] = useState(false)
  const [discoveryError, setDiscoveryError] = useState<string | null>(null)
  const [discoveryHandoffStatus, setDiscoveryHandoffStatus] = useState<string | null>(null)

  if (!dataset) {
    return <Card title="分钟因子研究工作台"><Alert showIcon type="warning" title="长历史 5 分钟数据不可用" description="系统不会用合成行情或不完整缓存替代经过 checksum 验证的多股票数据。" /></Card>
  }
  const savedExperiments = loadLocalIntradayFactorExperiments(dataset)
  const factorCandidates = loadLocalIntradayFactorCandidates(dataset)
  const factorComparisonExport = savedExperiments.length > 0
    ? buildLocalIntradayFactorComparisonExport(dataset)
    : null

  const eligibleEndDays = dataset.trading_days.slice(0, -1)
  const defaultEndIndex = eligibleEndDays.length - 1
  const initialValues: FormValues = {
    factor: 'opening_three_ten_minute_bullish',
    start: eligibleEndDays[Math.max(0, defaultEndIndex - 19)],
    end: eligibleEndDays[defaultEndIndex],
    cost_bps: '5',
    minimum_body_return_bps: '0',
    first_direction: 'BULLISH',
    second_direction: 'BULLISH',
    third_direction: 'BULLISH',
    combination: 'ALL',
    exit_rule: 'NEXT_SESSION_OPEN',
    exit_minimum_body_return_bps: '0',
  }
  const submit = async (values: FormValues) => {
    const payload: IntradayFactorBacktestRequest = {
      factor: values.factor,
      start: values.start,
      end: values.end,
      cost_bps: String(values.cost_bps),
      minimum_body_return_bps: String(values.minimum_body_return_bps),
      directions: values.factor === 'opening_three_ten_minute_bullish'
        ? ['BULLISH', 'BULLISH', 'BULLISH']
        : [values.first_direction, values.second_direction, values.third_direction],
      combination: values.factor === 'opening_three_ten_minute_bullish'
        ? 'ALL'
        : values.combination,
      ...(values.exit_rule === 'NEXT_SESSION_THREE_TEN_MINUTE_BEARISH_ELSE_14_55_TIME_STOP'
        ? {
            exit_rule: values.exit_rule,
            exit_minimum_body_return_bps: String(values.exit_minimum_body_return_bps),
          }
        : {}),
    }
    setRunning(true)
    setError(null)
    try {
      const next = await runIntradayFactorBacktest(payload)
      if (!matchesBacktestResponse(next, payload, dataset)) {
        throw new Error('策略回测响应身份或安全边界与请求不一致，结果已拒绝')
      }
      setResult(next)
      setStrategyLineage(correlationHandoff
        ? buildIntradayFactorStrategyLineage({
            dataset,
            candidate: correlationHandoff.candidate,
            ruleId: correlationHandoff.ruleId,
            correlation: correlationHandoff.correlation,
            request: payload,
            backtest: next,
          })
        : null)
      setSaveStatus(null)
    } catch (reason) {
      setResult(null)
      setStrategyLineage(null)
      setError(reason instanceof Error ? reason.message : '分钟因子研究失败')
    } finally {
      setRunning(false)
    }
  }
  const saveResult = () => {
    if (!result) return
    if (result.data_version !== dataset.data_version
      || result.dataset_checksum !== dataset.checksum) {
      setSaveStatus('数据集已变化，请重新运行后再保存。')
      return
    }
    try {
      const saved = saveLocalIntradayFactorExperiment(experimentName, result)
      setExperimentRevision((value) => value + 1)
      setExperimentName(saved.name)
      setSaveStatus(`已保存“${saved.name}”；相同结果哈希会更新原记录。`)
    } catch (reason) {
      setSaveStatus(reason instanceof Error ? reason.message : '本地因子实验保存失败')
    }
  }
  const loadParameters = (record: LocalIntradayFactorExperiment) => {
    const parameters = record.parameters
    form.setFieldsValue({
      factor: parameters.factor,
      start: parameters.start,
      end: parameters.end,
      cost_bps: parameters.cost_bps,
      minimum_body_return_bps: parameters.minimum_body_return_bps,
      first_direction: parameters.directions[0],
      second_direction: parameters.directions[1],
      third_direction: parameters.directions[2],
      combination: parameters.combination,
      exit_rule: parameters.exit_rule ?? 'NEXT_SESSION_OPEN',
      exit_minimum_body_return_bps: parameters.exit_minimum_body_return_bps ?? '0',
    })
    setSelectedFactor(parameters.factor)
    setSelectedExitRule(parameters.exit_rule ?? 'NEXT_SESSION_OPEN')
    setResult(null)
    setError(null)
    setCorrelationResult(null)
    setCorrelationError(null)
    setCorrelationBindings({})
    setCorrelationHandoff(null)
    setStrategyLineage(null)
    setCorrelationHandoffStatus(null)
    setFactorCandidateStatus(null)
    setExperimentName(record.name)
    setSaveStatus(`已载入“${record.name}”的参数；请重新运行以验证当前结果。`)
  }
  const removeExperiment = (previewHash: string) => {
    deleteLocalIntradayFactorExperiment(previewHash)
    setExperimentRevision((value) => value + 1)
    setSaveStatus('本地因子实验记录已删除。')
  }
  const runSelectedCorrelation = async () => {
    const selected = factorCandidates.filter((candidate) => selectedFactorCandidateKeys.includes(candidate.draft_key))
    if (selected.length < 2 || selected.length > 8) {
      setCorrelationError('请选择 2 至 8 条本地开仓因子候选。')
      return
    }
    const { start, end } = await form.validateFields(['start', 'end'])
    setCorrelationRunning(true)
    setCorrelationError(null)
    setCorrelationHandoff(null)
    setStrategyLineage(null)
    setCorrelationHandoffStatus(null)
    try {
      const request = {
        start,
        end,
        rules: selected.map((candidate) => ({
          directions: candidate.directions,
          combination: candidate.combination,
          minimum_body_return_bps: candidate.minimum_body_return_bps,
        })),
      }
      const correlation = await runIntradayFactorCorrelation(request)
      if (!matchesCorrelationResponse(correlation, request, dataset)) {
        throw new Error('相关性响应身份或安全边界与请求不一致，结果已拒绝')
      }
      setCorrelationResult(correlation)
      setCorrelationLabels(Object.fromEntries(
        selected.map((candidate, index) => [`R${String(index + 1).padStart(2, '0')}`, candidate.name]),
      ))
      setCorrelationBindings(Object.fromEntries(
        correlation.rules.map((rule, index) => [rule.rule_id, selected[index]]),
      ))
    } catch (reason) {
      setCorrelationResult(null)
      setCorrelationLabels({})
      setCorrelationBindings({})
      setCorrelationHandoff(null)
      setStrategyLineage(null)
      setCorrelationError(reason instanceof Error ? reason.message : '因子规则相关性检验失败')
    } finally {
      setCorrelationRunning(false)
    }
  }
  const saveCurrentFactorCandidate = async () => {
    try {
      await form.validateFields([
        'minimum_body_return_bps',
        ...(selectedFactor === 'custom_opening_ten_minute_direction_rule'
          ? ['first_direction', 'second_direction', 'third_direction', 'combination'] as const
          : []),
      ])
      const candidate = saveLocalIntradayFactorCandidate(factorCandidateName, dataset, {
        directions: [draftDirections[0], draftDirections[1], draftDirections[2]],
        combination: draftCombination,
        minimum_body_return_bps: draftThresholdBps,
      })
      setFactorCandidateName(candidate.name)
      setFactorCandidateStatus(`已保存“${candidate.name}”的开仓因子草案；尚未运行有效性、相关性或策略回测。`)
      setFactorCandidateRevision((value) => value + 1)
      setCorrelationResult(null)
      setCorrelationError(null)
      setCorrelationBindings({})
      setCorrelationHandoff(null)
      setStrategyLineage(null)
      setCorrelationHandoffStatus(null)
    } catch (reason) {
      setFactorCandidateStatus(reason instanceof Error ? reason.message : '开仓因子候选保存失败')
    }
  }
  const loadFactorCandidate = (candidate: LocalIntradayFactorCandidate) => {
    form.setFieldsValue({
      factor: 'custom_opening_ten_minute_direction_rule',
      minimum_body_return_bps: candidate.minimum_body_return_bps,
      first_direction: candidate.directions[0],
      second_direction: candidate.directions[1],
      third_direction: candidate.directions[2],
      combination: candidate.combination,
    })
    setSelectedFactor('custom_opening_ten_minute_direction_rule')
    setFactorCandidateName(candidate.name)
    setFactorCandidateStatus(`已载入“${candidate.name}”的开仓因子草案；日期、成本和平仓条件保持不变。`)
    setResult(null)
    setError(null)
    setSaveStatus(null)
    setCorrelationResult(null)
    setCorrelationError(null)
    setCorrelationBindings({})
    setCorrelationHandoff(null)
    setStrategyLineage(null)
    setCorrelationHandoffStatus(null)
  }
  const removeFactorCandidate = (draftKey: string) => {
    deleteLocalIntradayFactorCandidate(draftKey)
    setSelectedFactorCandidateKeys((values) => values.filter((value) => value !== draftKey))
    setFactorCandidateRevision((value) => value + 1)
    setCorrelationResult(null)
    setCorrelationError(null)
    setCorrelationBindings({})
    setCorrelationHandoff(null)
    setStrategyLineage(null)
    setCorrelationHandoffStatus(null)
    setFactorCandidateStatus('本地开仓因子候选已删除。')
  }
  const loadValidatedFactorIntoStrategy = (ruleId: string) => {
    if (!correlationResult) return
    const candidate = correlationBindings[ruleId]
    const effectiveness = correlationResult.effectiveness.find((row) => row.rule_id === ruleId)
    const isRedundant = correlationResult.pairs.some((pair) => (
      pair.redundant && (pair.left_rule_id === ruleId || pair.right_rule_id === ruleId)
    ))
    if (!candidate || !effectiveness?.passed || isRedundant) return
    form.setFieldsValue({
      factor: 'custom_opening_ten_minute_direction_rule',
      minimum_body_return_bps: candidate.minimum_body_return_bps,
      first_direction: candidate.directions[0],
      second_direction: candidate.directions[1],
      third_direction: candidate.directions[2],
      combination: candidate.combination,
    })
    setSelectedFactor('custom_opening_ten_minute_direction_rule')
    setResult(null)
    setError(null)
    setSaveStatus(null)
    setDiscoveryHandoffStatus(null)
    setFactorCandidateStatus(null)
    setCorrelationHandoff({ candidate, ruleId, correlation: correlationResult })
    setStrategyLineage(null)
    setCorrelationHandoffStatus(`已载入“${candidate.name}”进入策略构造`)
  }
  const runDiscovery = async () => {
    const values = await form.validateFields(['end', 'cost_bps'])
    const endIndex = eligibleEndDays.indexOf(values.end)
    if (endIndex < 39) {
      setDiscoveryResult(null)
      setDiscoveryError('所选结束日前不足 40 个完整交易日，无法建立互斥训练/测试段。')
      return
    }
    setDiscoveryRunning(true)
    setDiscoveryError(null)
    setDiscoveryHandoffStatus(null)
    try {
      setDiscoveryResult(await runIntradayFactorDiscovery({
        start: eligibleEndDays[endIndex - 39],
        end: values.end,
        cost_bps: String(values.cost_bps),
      }))
    } catch (reason) {
      setDiscoveryResult(null)
      setDiscoveryError(reason instanceof Error ? reason.message : '分钟因子挖掘失败')
    } finally {
      setDiscoveryRunning(false)
    }
  }
  const loadSelectedDiscoveryEntryFactor = () => {
    if (!discoveryResult) return
    const selected = discoveryResult.selected
    form.setFieldsValue({
      factor: 'custom_opening_ten_minute_direction_rule',
      minimum_body_return_bps: selected.minimum_body_return_bps,
      first_direction: selected.directions[0],
      second_direction: selected.directions[1],
      third_direction: selected.directions[2],
      combination: selected.combination,
    })
    setSelectedFactor('custom_opening_ten_minute_direction_rule')
    setResult(null)
    setError(null)
    setSaveStatus(null)
    setCorrelationResult(null)
    setCorrelationError(null)
    setCorrelationBindings({})
    setCorrelationHandoff(null)
    setStrategyLineage(null)
    setCorrelationHandoffStatus(null)
    setFactorCandidateStatus(null)
    setDiscoveryHandoffStatus(`已载入 ${selected.candidate_id} 的训练锁定开仓因子`)
  }
  const experimentColumns = [
    { title: '名称', dataIndex: 'name' },
    { title: '保存时间', dataIndex: 'saved_at', render: (value: string) => formatTimestamp(value) },
    {
      title: '规则',
      render: (_: unknown, row: LocalIntradayFactorExperiment) => (
        `${row.parameters.directions.map(directionLabel).join('/')} · ${row.parameters.combination} · ${row.parameters.minimum_body_return_bps} bps`
      ),
    },
    {
      title: '策略身份',
      render: (_: unknown, row: LocalIntradayFactorExperiment) => row.strategy_identity
        ? <Space orientation="vertical" size={0}>
            <Typography.Text code>{row.strategy_identity.strategy_id}</Typography.Text>
            <Typography.Text type="secondary">规格 {row.strategy_identity.spec_hash.slice(0, 12)}… · 未注册</Typography.Text>
          </Space>
        : <Tag>旧记录未保存</Tag>,
    },
    {
      title: '退出',
      render: (_: unknown, row: LocalIntradayFactorExperiment) => (
        row.parameters.exit_rule === 'NEXT_SESSION_THREE_TEN_MINUTE_BEARISH_ELSE_14_55_TIME_STOP'
          ? `T+1 三连阴 / 14:55 · ${row.parameters.exit_minimum_body_return_bps} bps`
          : 'T+1 09:30 开盘'
      ),
    },
    { title: '信号', render: (_: unknown, row: LocalIntradayFactorExperiment) => `${row.signal_count}/${row.observation_count} · ${row.clustered_signal_session_count} 日` },
    { title: '累计收益', render: (_: unknown, row: LocalIntradayFactorExperiment) => formatPercent(row.metrics.total_return) },
    { title: '年化收益', render: (_: unknown, row: LocalIntradayFactorExperiment) => formatPercent(row.metrics.annualized_return) },
    { title: '主动超额', render: (_: unknown, row: LocalIntradayFactorExperiment) => formatPercent(row.metrics.excess_return) },
    { title: 'Sharpe', render: (_: unknown, row: LocalIntradayFactorExperiment) => formatNumber(row.metrics.sharpe) },
    { title: '最大回撤', render: (_: unknown, row: LocalIntradayFactorExperiment) => formatPercent(row.metrics.maximum_drawdown) },
    { title: 'Rank IC', render: (_: unknown, row: LocalIntradayFactorExperiment) => formatNumber(row.metrics.rank_ic) },
    { title: '最大 |ρ|', render: (_: unknown, row: LocalIntradayFactorExperiment) => formatNumber(row.metrics.maximum_absolute_correlation) },
    { title: '样本外收益', render: (_: unknown, row: LocalIntradayFactorExperiment) => formatPercent(row.metrics.stitched_oos_total_return) },
    { title: '样本外超额', render: (_: unknown, row: LocalIntradayFactorExperiment) => formatPercent(row.metrics.stitched_oos_excess_return) },
    { title: 'NW t', render: (_: unknown, row: LocalIntradayFactorExperiment) => formatNumber(row.metrics.newey_west_t_statistic) },
    { title: 'NW 95% 区间', render: (_: unknown, row: LocalIntradayFactorExperiment) => `${formatPercent(row.metrics.confidence_interval_95_lower)} ～ ${formatPercent(row.metrics.confidence_interval_95_upper)}` },
    {
      title: '硬门',
      render: (_: unknown, row: LocalIntradayFactorExperiment) => (
        <Space size={4}>
          <Tag color={row.research_gate_passed ? 'green' : 'red'}>研究{row.research_gate_passed ? '通过' : '失败'}</Tag>
          <Tag color={row.robustness_gate_passed ? 'green' : 'red'}>稳健性{row.robustness_gate_passed ? '通过' : '失败'}</Tag>
        </Space>
      ),
    },
    {
      title: '操作',
      render: (_: unknown, row: LocalIntradayFactorExperiment) => <Space size={4}>
        <Button type="link" size="small" onClick={() => loadParameters(row)}>载入因子参数</Button>
        <Popconfirm title="删除这条本地因子实验？" onConfirm={() => removeExperiment(row.preview_hash)}>
          <Button type="link" danger size="small">删除</Button>
        </Popconfirm>
      </Space>,
    },
  ]
  const factorCandidateColumns = [
    { title: '候选名称', dataIndex: 'name' },
    { title: '保存时间', dataIndex: 'saved_at', render: (value: string) => formatTimestamp(value) },
    { title: '开仓表达式', dataIndex: 'expression' },
    { title: '检验状态', render: () => <Tag>未检验草案</Tag> },
    {
      title: '操作',
      render: (_: unknown, candidate: LocalIntradayFactorCandidate) => <Space size={4}>
        <Button type="link" size="small" onClick={() => loadFactorCandidate(candidate)}>载入开仓因子</Button>
        <Popconfirm title="删除这个本地开仓因子候选？" onConfirm={() => removeFactorCandidate(candidate.draft_key)}>
          <Button type="link" danger size="small">删除候选</Button>
        </Popconfirm>
      </Space>,
    },
  ]
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
  const correlationMatrixRows = correlationResult
    ? correlationResult.matrix.map((row) => ({ ...row, label: correlationLabels[row.rule_id] ?? row.rule_id }))
    : []
  const redundantPairs = correlationResult?.pairs.filter((pair) => pair.redundant) ?? []
  const redundantRuleIds = new Set(redundantPairs.flatMap((pair) => [pair.left_rule_id, pair.right_rule_id]))
  const discoveryCandidates = discoveryResult
    ? [...discoveryResult.candidates].sort((left, right) => (
        Number(right.training_gate_passed) - Number(left.training_gate_passed)
        || Number(right.training_newey_west_t_statistic) - Number(left.training_newey_west_t_statistic)
        || left.candidate_id.localeCompare(right.candidate_id)
      ))
    : []
  const discoveryCostRows = discoveryResult
    ? Object.entries(discoveryResult.locked_test_cost_stress.rows).map(([cost_bps, value]) => ({ cost_bps, ...value }))
    : []
  const draftThresholdBps = String(watchedMinimumBodyReturnBps ?? initialValues.minimum_body_return_bps)
  const draftDirections: IntradayOpeningDirection[] = selectedFactor === 'opening_three_ten_minute_bullish'
    ? ['BULLISH', 'BULLISH', 'BULLISH']
    : [
        watchedFirstDirection ?? initialValues.first_direction,
        watchedSecondDirection ?? initialValues.second_direction,
        watchedThirdDirection ?? initialValues.third_direction,
      ]
  const draftCombination = selectedFactor === 'opening_three_ten_minute_bullish'
    ? 'ALL'
    : watchedCombination ?? initialValues.combination
  const draftEntryExpression = buildDraftEntryExpression(draftDirections, draftCombination, draftThresholdBps)
  const draftExitExpression = selectedExitRule === 'NEXT_SESSION_THREE_TEN_MINUTE_BEARISH_ELSE_14_55_TIME_STOP'
    ? `IF T+1 (${buildDraftEntryExpression(['BEARISH', 'BEARISH', 'BEARISH'], 'ALL', String(watchedExitMinimumBodyReturnBps ?? initialValues.exit_minimum_body_return_bps))}) THEN NEXT_5M_OPEN_10_05 ELSE TIME_STOP_OPEN_14_55`
    : 'T+1_OPEN_09_30'
  const invalidateChangedParameters = (changedValues: Partial<FormValues>) => {
    setResult(null)
    setError(null)
    setSaveStatus(null)
    setCorrelationResult(null)
    setCorrelationError(null)
    setCorrelationBindings({})
    setCorrelationHandoff(null)
    setStrategyLineage(null)
    setCorrelationHandoffStatus(null)
    setFactorCandidateStatus(null)
    setDiscoveryHandoffStatus(null)
    if ('end' in changedValues || 'cost_bps' in changedValues) {
      setDiscoveryResult(null)
      setDiscoveryError(null)
    }
  }

  return <Card title="分钟因子研究工作台" extra={<Typography.Text type="secondary">{dataset.symbol_count} 股 · {dataset.sector_count} 板块 · 原生 5 分钟</Typography.Text>}>
    <Space orientation="vertical" size={16} style={{ width: '100%' }}>
      <Alert showIcon type="info" title="从因子到模拟盘的完整门控" description="开仓因子在 10:00 确认，10:05 标记的下一根原生 5 分钟 Bar 开盘买入；平仓可固定为 T+1 09:30 开盘，或在 T+1 10:00 确认三根十分钟阴线后于下一 Bar 开盘退出，未触发则用 15:00 标记 Bar 的开盘价（区间起点 14:55）时间退出。当前事后非 PIT 研究永远不会自动进入 PAPER 或 LIVE。" />
      <Form<FormValues> form={form} name="intraday-factor-research-backtest" layout="vertical" initialValues={initialValues} onValuesChange={invalidateChangedParameters} onFinish={(values) => { void submit(values) }}>
        <div className="backtest-lab-grid">
          <Form.Item name="factor" label="因子" rules={[{ required: true }]}><Select onChange={setSelectedFactor} options={[
            { value: 'opening_three_ten_minute_bullish', label: '09:30 起三根 10 分钟阳线' },
            { value: 'custom_opening_ten_minute_direction_rule', label: '自定义开盘十分钟方向规则' },
          ]} /></Form.Item>
          <Form.Item name="start" label="开始交易日" rules={[{ required: true }]}><Select showSearch options={eligibleEndDays.map((value) => ({ value, label: value }))} /></Form.Item>
          <Form.Item name="end" label="结束交易日" rules={[{ required: true }]}><Select showSearch options={eligibleEndDays.map((value) => ({ value, label: value }))} /></Form.Item>
          <Form.Item name="cost_bps" label="单边成本（bps）" rules={[{ required: true }]}><InputNumber min={0} max={100} step={1} stringMode /></Form.Item>
          <Form.Item name="minimum_body_return_bps" label="每根 10 分钟实体下限（bps）" rules={[{ required: true }]}><InputNumber min={0} max={1000} step={1} stringMode /></Form.Item>
          <Form.Item name="exit_rule" label="平仓规则" rules={[{ required: true }]}><Select onChange={(value: IntradayExitRule) => setSelectedExitRule(value)} options={[
            { value: 'NEXT_SESSION_OPEN', label: 'T+1 09:30 开盘退出（基准）' },
            { value: 'NEXT_SESSION_THREE_TEN_MINUTE_BEARISH_ELSE_14_55_TIME_STOP', label: 'T+1 三根 10 分钟阴线退出，否则 14:55 时间退出' },
          ]} /></Form.Item>
          {selectedExitRule === 'NEXT_SESSION_THREE_TEN_MINUTE_BEARISH_ELSE_14_55_TIME_STOP' && <Form.Item name="exit_minimum_body_return_bps" label="每根退出阴线实体下限（bps）" rules={[{ required: true }]}><InputNumber min={0} max={1000} step={1} stringMode /></Form.Item>}
          {selectedFactor === 'custom_opening_ten_minute_direction_rule' && <>
            <Form.Item name="first_direction" label="第一段 09:30–09:40" rules={[{ required: true }]}><Select options={directionOptions} /></Form.Item>
            <Form.Item name="second_direction" label="第二段 09:40–09:50" rules={[{ required: true }]}><Select options={directionOptions} /></Form.Item>
            <Form.Item name="third_direction" label="第三段 09:50–10:00" rules={[{ required: true }]}><Select options={directionOptions} /></Form.Item>
            <Form.Item name="combination" label="条件组合" rules={[{ required: true }]}><Select options={[{ value: 'ALL', label: '全部满足（AND）' }, { value: 'ANY', label: '任一满足（OR）' }]} /></Form.Item>
          </>}
        </div>
        <Space wrap>
          <Button type="primary" htmlType="submit" loading={running}>运行 75 股因子检验与回测</Button>
          <Button loading={discoveryRunning} onClick={() => { void runDiscovery() }}>挖掘最近 40 日的 104 个有界因子</Button>
        </Space>
      </Form>
      <Card size="small" title="当前开平仓条件草案（未运行）">
        <Space orientation="vertical" size={6} style={{ width: '100%' }}>
          <Typography.Text>开仓因子：<Typography.Text code>{draftEntryExpression}</Typography.Text></Typography.Text>
          <Typography.Text>开仓时点：<Typography.Text code>KNOWN_AT_10_00_CLOSE → NEXT_5M_OPEN_10_05</Typography.Text></Typography.Text>
          <Typography.Text>平仓条件：<Typography.Text code>{draftExitExpression}</Typography.Text></Typography.Text>
          <Typography.Text type="secondary">这是当前表单的可读草案，不是服务端规范、规格哈希、有效性结论或晋级证据；参数变化会清除旧结果，只有重新运行才能生成绑定结果。</Typography.Text>
        </Space>
      </Card>
      {selectedFactor === 'custom_opening_ten_minute_direction_rule' && <Alert showIcon type="warning" title="受限规则，不执行任意表达式" description="只允许三段已结束十分钟 K 线的看涨、看跌或忽略条件，以及 AND/OR 组合；10:00 收盘后才知道信号，10:05 下一根原生 5 分钟 Bar 开盘执行。禁止 eval、未来字段、同 Bar 成交和自动 PAPER 晋级。" />}
      {selectedExitRule === 'NEXT_SESSION_THREE_TEN_MINUTE_BEARISH_ELSE_14_55_TIME_STOP' && <Alert showIcon type="warning" title="平仓因子严格遵守 T+1 与下一 Bar 执行" description="T+1 09:30–10:00 三根十分钟阴线全部超过阈值后，条件到 10:00 收盘才完整；成交取 10:05 标记 Bar 的开盘价。未触发时取 15:00 标记 Bar 的开盘价，即 14:55 区间起点，不使用同 Bar 收盘价。" />}
      {discoveryError && <Alert showIcon type="error" title="分钟因子挖掘未完成" description={discoveryError} />}
      {discoveryResult && <Card type="inner" title="104 个有界分钟因子 · 训练锁定后单次测试" extra={<JsonExportButton filename={`qtrade-intraday-discovery-${discoveryResult.preview_hash.slice(0, 12)}.json`} value={discoveryResult} label="导出挖掘证据" />}>
        <Space orientation="vertical" size={12} style={{ width: '100%' }}>
          <Alert
            showIcon
            type={discoveryResult.test_validation.passed ? 'success' : 'warning'}
            title={discoveryResult.test_validation.passed ? '锁定规则测试门通过，但仍不可自动晋级' : '训练最优规则未通过互斥测试段'}
            description={`${discoveryResult.split.training_start} 至 ${discoveryResult.split.training_end} 只用于扫描 104 个固定候选；${discoveryResult.split.test_start} 至 ${discoveryResult.split.test_end} 只评估训练锁定的唯一规则。窗口已经被查看，结论固定为 ${discoveryResult.promotion}，不是新盲测。`}
          />
          <Typography.Text code>{discoveryResult.selected.factor_expression}</Typography.Text>
          <Space wrap>
            <Button onClick={loadSelectedDiscoveryEntryFactor}>载入训练锁定开仓因子</Button>
            <Typography.Text type="secondary">仅复制训练阶段已经锁定的开仓表达式，不复制测试结论，也不会自动回测或晋级。</Typography.Text>
          </Space>
          {discoveryHandoffStatus && <Alert
            showIcon
            type="info"
            title={discoveryHandoffStatus}
            description="只回填开仓方向、ALL 组合和实体阈值；日期、成本与平仓条件保持不变，必须人工核对后重新运行。当前测试窗口已经查看，复现结果仍不是新的独立证据。"
          />}
          <div className="description-grid">
            <Metric label="锁定候选" value={discoveryResult.selected.candidate_id} />
            <Metric label="训练 NW t" value={formatNumber(discoveryResult.selected.training_newey_west_t_statistic)} />
            <Metric label="训练条件超额" value={formatPercent(discoveryResult.selected.training_mean_session_conditional_excess_return)} />
            <Metric label="测试信号" value={`${discoveryResult.test_validation.signal_count}/${discoveryResult.test_validation.observation_count}`} />
            <Metric label="测试 NW t" value={formatNumber(discoveryResult.test_validation.newey_west_t_statistic)} />
            <Metric label="测试条件超额" value={formatPercent(discoveryResult.test_validation.mean_session_conditional_excess_return)} />
            <Metric label="测试累计收益" value={formatPercent(discoveryResult.test_validation.strategy_metrics.total_return)} />
            <Metric label="测试超额收益" value={formatPercent(discoveryResult.test_validation.strategy_metrics.excess_return)} />
            <Metric label="测试最大回撤" value={formatPercent(discoveryResult.test_validation.strategy_metrics.maximum_drawdown)} />
            <Metric label="测试 Sharpe" value={formatNumber(discoveryResult.test_validation.strategy_metrics.sharpe)} />
          </div>
          <Table rowKey="gate_id" size="small" pagination={false} dataSource={Object.entries(discoveryResult.test_validation.criteria).map(([gate_id, passed]) => ({ gate_id, passed }))} columns={[
            { title: '锁定测试门槛', dataIndex: 'gate_id', render: (value: string) => gateLabels[value] ?? value },
            { title: '结果', dataIndex: 'passed', render: (value: boolean) => value ? <Tag color="green">通过</Tag> : <Tag color="red">未通过</Tag> },
          ]} />
          <Alert
            showIcon
            type={discoveryResult.locked_test_cost_stress.passed ? 'success' : 'warning'}
            title={discoveryResult.locked_test_cost_stress.passed ? '锁定规则通过固定成本压力门' : '锁定规则未通过固定成本压力门'}
            description={`同一 ${discoveryResult.locked_test_cost_stress.selected_candidate_id}、同一 ${discoveryResult.locked_test_cost_stress.signal_count} 次测试信号、同一互斥测试日只复算 0/5/10/20 bps 成本。成本行不参与候选选择，也不会触发重新选参。`}
          />
          <Table rowKey="cost_bps" size="small" pagination={false} dataSource={discoveryCostRows} columns={[
            { title: '成本', dataIndex: 'cost_bps', render: (value: string) => `${value} bps` },
            { title: '策略收益', render: (_: unknown, row: typeof discoveryCostRows[number]) => formatPercent(row.strategy_metrics.total_return) },
            { title: '年化收益', render: (_: unknown, row: typeof discoveryCostRows[number]) => formatPercent(row.strategy_metrics.annualized_return) },
            { title: 'Sharpe', render: (_: unknown, row: typeof discoveryCostRows[number]) => formatNumber(row.strategy_metrics.sharpe) },
            { title: '最大回撤', render: (_: unknown, row: typeof discoveryCostRows[number]) => formatPercent(row.strategy_metrics.maximum_drawdown) },
            { title: '超额收益', render: (_: unknown, row: typeof discoveryCostRows[number]) => formatPercent(row.strategy_metrics.excess_return) },
            { title: '策略成本', render: (_: unknown, row: typeof discoveryCostRows[number]) => formatPercent(row.strategy_metrics.transaction_costs) },
            { title: '基准收益', render: (_: unknown, row: typeof discoveryCostRows[number]) => formatPercent(row.benchmark_metrics.total_return) },
          ]} />
          <Table rowKey="gate_id" size="small" pagination={false} dataSource={Object.entries(discoveryResult.locked_test_cost_stress.criteria).map(([gate_id, passed]) => ({ gate_id, passed }))} columns={[
            { title: '锁定测试成本门槛', dataIndex: 'gate_id', render: (value: string) => gateLabels[value] ?? value },
            { title: '结果', dataIndex: 'passed', render: (value: boolean) => value ? <Tag color="green">通过</Tag> : <Tag color="red">未通过</Tag> },
          ]} />
          <Alert
            showIcon
            type={discoveryResult.locked_test_sector_stress.passed ? 'success' : 'warning'}
            title={discoveryResult.locked_test_sector_stress.passed ? '锁定规则通过多行业鲁棒性门' : '锁定规则未通过多行业鲁棒性门'}
            description={`固定 ${discoveryResult.locked_test_sector_stress.selected_candidate_id} 在同一互斥测试段分别复算 15 个行业内结果和 15 次整行业剔除。行业行不参与训练排序，也不会重选因子或股票。`}
          />
          <div className="description-grid">
            <Metric label="正收益行业" value={`${discoveryResult.locked_test_sector_stress.positive_sector_count}/${discoveryResult.locked_test_sector_stress.sector_only.length} · ${formatPercent(discoveryResult.locked_test_sector_stress.positive_sector_ratio)}`} />
            <Metric label="正收益整行业剔除" value={`${discoveryResult.locked_test_sector_stress.positive_exclusion_count}/${discoveryResult.locked_test_sector_stress.leave_one_sector_out.length} · ${formatPercent(discoveryResult.locked_test_sector_stress.positive_exclusion_ratio)}`} />
          </div>
          <Tabs items={[
            {
              key: 'locked-sector-only',
              label: '15 个行业内测试',
              children: <Table rowKey="sector_code" size="small" pagination={{ pageSize: 8, showSizeChanger: false }} dataSource={discoveryResult.locked_test_sector_stress.sector_only} columns={[
                { title: '行业', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_sector_stress']['sector_only'][number]) => `${row.sector_code} · ${row.sector_name}` },
                { title: '信号 / 观测', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_sector_stress']['sector_only'][number]) => `${row.signal_count} / ${row.observation_count}` },
                { title: '策略收益', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_sector_stress']['sector_only'][number]) => formatPercent(row.strategy_metrics.total_return) },
                { title: '超额收益', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_sector_stress']['sector_only'][number]) => formatPercent(row.strategy_metrics.excess_return) },
                { title: 'Sharpe', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_sector_stress']['sector_only'][number]) => formatNumber(row.strategy_metrics.sharpe) },
                { title: '最大回撤', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_sector_stress']['sector_only'][number]) => formatPercent(row.strategy_metrics.maximum_drawdown) },
              ]} />,
            },
            {
              key: 'locked-sector-exclusion',
              label: '15 次整行业剔除',
              children: <Table rowKey="excluded_sector_code" size="small" pagination={{ pageSize: 8, showSizeChanger: false }} dataSource={discoveryResult.locked_test_sector_stress.leave_one_sector_out} columns={[
                { title: '剔除行业', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_sector_stress']['leave_one_sector_out'][number]) => `${row.excluded_sector_code} · ${row.excluded_sector_name}` },
                { title: '剩余信号 / 观测', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_sector_stress']['leave_one_sector_out'][number]) => `${row.signal_count} / ${row.observation_count}` },
                { title: '策略收益', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_sector_stress']['leave_one_sector_out'][number]) => formatPercent(row.strategy_metrics.total_return) },
                { title: '超额收益', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_sector_stress']['leave_one_sector_out'][number]) => formatPercent(row.strategy_metrics.excess_return) },
                { title: 'Sharpe', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_sector_stress']['leave_one_sector_out'][number]) => formatNumber(row.strategy_metrics.sharpe) },
                { title: '最大回撤', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_sector_stress']['leave_one_sector_out'][number]) => formatPercent(row.strategy_metrics.maximum_drawdown) },
              ]} />,
            },
          ]} />
          <Table rowKey="gate_id" size="small" pagination={false} dataSource={Object.entries(discoveryResult.locked_test_sector_stress.criteria).map(([gate_id, passed]) => ({ gate_id, passed }))} columns={[
            { title: '锁定测试行业门槛', dataIndex: 'gate_id', render: (value: string) => gateLabels[value] ?? value },
            { title: '结果', dataIndex: 'passed', render: (value: boolean) => value ? <Tag color="green">通过</Tag> : <Tag color="red">未通过</Tag> },
          ]} />
          <Alert
            showIcon
            type={discoveryResult.locked_test_symbol_stress.passed ? 'success' : 'warning'}
            title={discoveryResult.locked_test_symbol_stress.passed ? '锁定规则通过逐股票鲁棒性门' : '锁定规则未通过逐股票鲁棒性门'}
            description={`固定 ${discoveryResult.locked_test_symbol_stress.selected_candidate_id} 在同一互斥测试段分别复算 ${discoveryResult.locked_test_symbol_stress.symbols.length} 只股票。股票行不参与训练排序，不重选因子或股票。`}
          />
          <div className="description-grid">
            <Metric label="正收益股票" value={`${discoveryResult.locked_test_symbol_stress.positive_symbol_count}/${discoveryResult.locked_test_symbol_stress.symbols.length} · ${formatPercent(discoveryResult.locked_test_symbol_stress.positive_symbol_ratio)}`} />
            <Metric label="股票收益中位数" value={formatPercent(discoveryResult.locked_test_symbol_stress.median_symbol_total_return)} />
            <Metric label="绝对收益前五集中度" value={formatPercent(discoveryResult.locked_test_symbol_stress.top_five_absolute_return_share)} />
            <Metric label="绝对收益前五股票" value={discoveryResult.locked_test_symbol_stress.top_five_absolute_return_symbols.join(' / ')} />
          </div>
          <Table
            rowKey="symbol"
            size="small"
            pagination={{ pageSize: 10, showSizeChanger: false }}
            dataSource={discoveryResult.locked_test_symbol_stress.symbols}
            columns={[
              { title: '股票', dataIndex: 'symbol' },
              { title: '行业', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_symbol_stress']['symbols'][number]) => `${row.sector_code} · ${row.sector_name}` },
              { title: '信号 / 观测', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_symbol_stress']['symbols'][number]) => `${row.signal_count} / ${row.observation_count}` },
              { title: '策略收益', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_symbol_stress']['symbols'][number]) => formatPercent(row.strategy_metrics.total_return) },
              { title: '超额收益', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_symbol_stress']['symbols'][number]) => formatPercent(row.strategy_metrics.excess_return) },
              { title: 'Sharpe', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_symbol_stress']['symbols'][number]) => formatNumber(row.strategy_metrics.sharpe) },
              { title: '最大回撤', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_symbol_stress']['symbols'][number]) => formatPercent(row.strategy_metrics.maximum_drawdown) },
            ]}
            scroll={{ x: 960 }}
          />
          <Table rowKey="gate_id" size="small" pagination={false} dataSource={Object.entries(discoveryResult.locked_test_symbol_stress.criteria).map(([gate_id, passed]) => ({ gate_id, passed }))} columns={[
            { title: '锁定测试股票门槛', dataIndex: 'gate_id', render: (value: string) => gateLabels[value] ?? value },
            { title: '结果', dataIndex: 'passed', render: (value: boolean) => value ? <Tag color="green">通过</Tag> : <Tag color="red">未通过</Tag> },
          ]} />
          <Alert
            showIcon
            type={discoveryResult.locked_test_regime_stress.passed ? 'success' : 'warning'}
            title={discoveryResult.locked_test_regime_stress.passed ? '锁定规则通过信号前市场状态压力门' : '锁定规则未通过信号前市场状态压力门'}
            description={`每个测试日按上一交易日 75 股等权 T+1 开盘收益的正负分组；结果在当前日 09:35 已知，早于 10:00 因子信号。固定 ${discoveryResult.locked_test_regime_stress.selected_candidate_id}，状态行不参与训练选择或重选。`}
          />
          <Table
            rowKey="regime_id"
            size="small"
            pagination={false}
            dataSource={discoveryResult.locked_test_regime_stress.regimes}
            columns={[
              { title: '信号前市场状态', dataIndex: 'regime_name' },
              { title: '测试日 / 观测 / 信号', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_regime_stress']['regimes'][number]) => `${row.session_count} / ${row.observation_count} / ${row.signal_count}` },
              { title: '上一日等权收益区间', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_regime_stress']['regimes'][number]) => `${formatPercent(row.previous_session_mean_universe_return_minimum)} 至 ${formatPercent(row.previous_session_mean_universe_return_maximum)}` },
              { title: '策略收益', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_regime_stress']['regimes'][number]) => formatPercent(row.strategy_metrics.total_return) },
              { title: '超额收益', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_regime_stress']['regimes'][number]) => formatPercent(row.strategy_metrics.excess_return) },
              { title: 'Sharpe', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_regime_stress']['regimes'][number]) => formatNumber(row.strategy_metrics.sharpe) },
              { title: '最大回撤', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_regime_stress']['regimes'][number]) => formatPercent(row.strategy_metrics.maximum_drawdown) },
              { title: '信号前已知', dataIndex: 'known_before_signal', render: (value: boolean) => value ? <Tag color="green">是</Tag> : <Tag color="red">否</Tag> },
            ]}
            scroll={{ x: 1120 }}
          />
          <Table rowKey="gate_id" size="small" pagination={false} dataSource={Object.entries(discoveryResult.locked_test_regime_stress.criteria).map(([gate_id, passed]) => ({ gate_id, passed }))} columns={[
            { title: '锁定测试状态门槛', dataIndex: 'gate_id', render: (value: string) => gateLabels[value] ?? value },
            { title: '结果', dataIndex: 'passed', render: (value: boolean) => value ? <Tag color="green">通过</Tag> : <Tag color="red">未通过</Tag> },
          ]} />
          <Alert
            showIcon
            type={discoveryResult.locked_test_placebo_audit.passed ? 'success' : 'warning'}
            title={discoveryResult.locked_test_placebo_audit.passed ? '锁定规则通过会话内置换安慰剂门' : '锁定规则未通过会话内置换安慰剂门'}
            description={`固定 ${discoveryResult.locked_test_placebo_audit.selected_candidate_id}，按每个测试日保留实际信号数，只在当日股票间随机重排信号归属；使用固定种子 ${discoveryResult.locked_test_placebo_audit.seed} 完成 ${discoveryResult.locked_test_placebo_audit.replications} 次置换。安慰剂行不参与候选选择或重选；窗口已经被查看，不是独立确认。`}
          />
          <div className="description-grid">
            <Metric label="实际条件超额" value={formatBasisPoints(discoveryResult.locked_test_placebo_audit.observed_mean_session_conditional_excess_return)} />
            <Metric label="安慰剂均值" value={formatBasisPoints(discoveryResult.locked_test_placebo_audit.placebo_mean_session_conditional_excess_return)} />
            <Metric label="安慰剂中位" value={formatBasisPoints(discoveryResult.locked_test_placebo_audit.placebo_median_session_conditional_excess_return)} />
            <Metric label="安慰剂 P95" value={formatBasisPoints(discoveryResult.locked_test_placebo_audit.placebo_p95_session_conditional_excess_return)} />
            <Metric label="单侧置换 p 值" value={formatPercent(discoveryResult.locked_test_placebo_audit.empirical_one_sided_p_value)} />
            <Metric label="不低于实际的置换" value={`${discoveryResult.locked_test_placebo_audit.placebo_at_least_observed_count}/${discoveryResult.locked_test_placebo_audit.replications}`} />
          </div>
          <Table rowKey="gate_id" size="small" pagination={false} dataSource={Object.entries(discoveryResult.locked_test_placebo_audit.criteria).map(([gate_id, passed]) => ({ gate_id, passed }))} columns={[
            { title: '锁定测试安慰剂门槛', dataIndex: 'gate_id', render: (value: string) => gateLabels[value] ?? value },
            { title: '结果', dataIndex: 'passed', render: (value: boolean) => value ? <Tag color="green">通过</Tag> : <Tag color="red">未通过</Tag> },
          ]} />
          <Alert
            showIcon
            type={discoveryResult.locked_test_execution_delay_stress.passed ? 'success' : 'warning'}
            title={discoveryResult.locked_test_execution_delay_stress.passed ? '锁定规则通过执行延迟压力门' : '锁定规则未通过执行延迟压力门'}
            description={`信号固定在 10:00 已知，使用 ${discoveryResult.locked_test_execution_delay_stress.cost_bps} bps 成本，分别取 10:05、10:10、10:15 标记的原生 5 分钟 Bar 开盘价。固定 ${discoveryResult.locked_test_execution_delay_stress.selected_candidate_id}、测试日和信号归属，只改变策略与同期全股票基准的入场时点；延迟行不参与选择或重选。`}
          />
          <Table
            rowKey="delay_bars_after_signal"
            size="small"
            pagination={false}
            dataSource={discoveryResult.locked_test_execution_delay_stress.rows}
            columns={[
              { title: '信号后延迟', dataIndex: 'delay_bars_after_signal', render: (value: number) => `${value} 根 5 分钟 Bar` },
              { title: '入场源 Bar', dataIndex: 'entry_source_bar_time' },
              { title: '信号 / 信号日', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_execution_delay_stress']['rows'][number]) => `${row.signal_count} / ${row.signal_session_count}` },
              { title: '日聚类条件超额', dataIndex: 'mean_session_conditional_excess_return', render: (value: string) => formatBasisPoints(value) },
              { title: '策略收益', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_execution_delay_stress']['rows'][number]) => formatPercent(row.strategy_metrics.total_return) },
              { title: '超额收益', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_execution_delay_stress']['rows'][number]) => formatPercent(row.strategy_metrics.excess_return) },
              { title: 'Sharpe', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_execution_delay_stress']['rows'][number]) => formatNumber(row.strategy_metrics.sharpe) },
              { title: '最大回撤', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_execution_delay_stress']['rows'][number]) => formatPercent(row.strategy_metrics.maximum_drawdown) },
            ]}
            scroll={{ x: 1080 }}
          />
          <Table rowKey="gate_id" size="small" pagination={false} dataSource={Object.entries(discoveryResult.locked_test_execution_delay_stress.criteria).map(([gate_id, passed]) => ({ gate_id, passed }))} columns={[
            { title: '锁定测试延迟门槛', dataIndex: 'gate_id', render: (value: string) => gateLabels[value] ?? value },
            { title: '结果', dataIndex: 'passed', render: (value: boolean) => value ? <Tag color="green">通过</Tag> : <Tag color="red">未通过</Tag> },
          ]} />
          <Alert
            showIcon
            type={discoveryResult.locked_test_liquidity_capacity_stress.passed ? 'success' : 'warning'}
            title={discoveryResult.locked_test_liquidity_capacity_stress.passed ? '锁定规则通过流动性/容量压力门' : '锁定规则股数容量可复算，但人民币容量不可用'}
            description={`固定 ${discoveryResult.locked_test_liquidity_capacity_stress.selected_candidate_id}、测试日和信号归属，只使用入场前已完成的 ${discoveryResult.locked_test_liquidity_capacity_stress.volume_source_interval} 源 Bar 成交量。BaoStock 分钟 volume 单位为股，三档参与率容量均按 ${discoveryResult.locked_test_liquidity_capacity_stress.order_lot_size_shares} 股整手向下取整；冻结数据缺少供应商原始成交额且价格为前复权，不使用价格×成交量冒充实际人民币容量。容量行不参与选择或重选。`}
          />
          <div className="description-grid">
            <Metric label="成交量源区间" value={discoveryResult.locked_test_liquidity_capacity_stress.volume_source_interval} />
            <Metric label="1% 最小容量" value={`${Number(discoveryResult.locked_test_liquidity_capacity_stress.rows[0].minimum_round_lot_share_capacity).toLocaleString('zh-CN')} 股`} />
            <Metric label="1% 下中位容量" value={`${Number(discoveryResult.locked_test_liquidity_capacity_stress.rows[0].lower_median_round_lot_share_capacity).toLocaleString('zh-CN')} 股 · 1%`} />
            <Metric label="1% 一手覆盖" value={`${discoveryResult.locked_test_liquidity_capacity_stress.rows[0].one_lot_eligible_signal_count}/${discoveryResult.locked_test_liquidity_capacity_stress.rows[0].signal_count} · ${formatPercent(discoveryResult.locked_test_liquidity_capacity_stress.rows[0].one_lot_eligible_signal_ratio)}`} />
            <Metric label="人民币容量" value="人民币容量不可用" />
          </div>
          <Table
            rowKey="participation_rate"
            size="small"
            pagination={false}
            dataSource={discoveryResult.locked_test_liquidity_capacity_stress.rows}
            columns={[
              { title: '固定参与率', dataIndex: 'participation_rate', render: (value: string) => formatPercent(value) },
              { title: '信号 / 信号日', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_liquidity_capacity_stress']['rows'][number]) => `${row.signal_count} / ${row.signal_session_count}` },
              { title: '最小容量', dataIndex: 'minimum_round_lot_share_capacity', render: (value: number) => `${Number(value).toLocaleString('zh-CN')} 股` },
              { title: '下中位容量', dataIndex: 'lower_median_round_lot_share_capacity', render: (value: number) => `${Number(value).toLocaleString('zh-CN')} 股` },
              { title: '最大容量', dataIndex: 'maximum_round_lot_share_capacity', render: (value: number) => `${Number(value).toLocaleString('zh-CN')} 股` },
              { title: '零容量信号', dataIndex: 'zero_capacity_signal_count' },
              { title: '至少一手覆盖', render: (_: unknown, row: IntradayFactorDiscovery['locked_test_liquidity_capacity_stress']['rows'][number]) => `${row.one_lot_eligible_signal_count}/${row.signal_count} · ${formatPercent(row.one_lot_eligible_signal_ratio)}` },
            ]}
            scroll={{ x: 1000 }}
          />
          <Table rowKey="gate_id" size="small" pagination={false} dataSource={Object.entries(discoveryResult.locked_test_liquidity_capacity_stress.criteria).map(([gate_id, passed]) => ({ gate_id, passed }))} columns={[
            { title: '锁定测试容量门槛', dataIndex: 'gate_id', render: (value: string) => gateLabels[value] ?? value },
            { title: '结果', dataIndex: 'passed', render: (value: boolean) => value ? <Tag color="green">通过</Tag> : <Tag color="red">未通过</Tag> },
          ]} />
          <Alert
            showIcon
            type={discoveryResult.overfitting_audit.passed ? 'info' : 'warning'}
            title={discoveryResult.overfitting_audit.passed && !discoveryResult.test_validation.passed
              ? 'CSCV 选择偏差门通过，但时间顺序锁定测试仍失败'
              : discoveryResult.overfitting_audit.passed ? 'CSCV 选择偏差门通过' : 'CSCV 检出较高选择偏差风险'}
            description={`把同一已查看窗口固定切成 ${discoveryResult.overfitting_audit.block_count} 个连续块，遍历 ${discoveryResult.overfitting_audit.split_count} 个四块训练/四块互补测试组合，并沿用“训练硬门 → Newey-West t → 条件超额 → 较低阈值”的原筛选顺序。该诊断会复用原锁定测试段，只量化候选选择偏差，不是新的时间顺序回测、独立确认或晋级依据。`}
          />
          <div className="description-grid">
            <Metric label="回测过拟合概率 PBO" value={formatPercent(discoveryResult.overfitting_audit.probability_of_backtest_overfitting)} />
            <Metric label="组合样本外正超额" value={`${discoveryResult.overfitting_audit.selected_oos_positive_excess_split_count}/${discoveryResult.overfitting_audit.split_count} · ${formatPercent(discoveryResult.overfitting_audit.selected_oos_positive_excess_ratio)}`} />
            <Metric label="组合样本外超额中位" value={formatPercent(discoveryResult.overfitting_audit.median_selected_oos_excess_return)} />
            <Metric label="不同训练赢家" value={`${discoveryResult.overfitting_audit.unique_selected_candidate_count} 个`} />
            <Metric label="最常被选" value={`${discoveryResult.overfitting_audit.most_selected_candidate_id} · ${discoveryResult.overfitting_audit.most_selected_candidate_count}/${discoveryResult.overfitting_audit.split_count}`} />
            <Metric label="低于测试中位" value={`${discoveryResult.overfitting_audit.below_median_split_count}/${discoveryResult.overfitting_audit.split_count}`} />
          </div>
          <Typography.Text type="secondary">连续块：{discoveryResult.overfitting_audit.blocks.map((block) => `B${block.block_id} ${block.start}–${block.end}（${block.session_count} 日）`).join('；')}</Typography.Text>
          <Table rowKey="gate_id" size="small" pagination={false} dataSource={Object.entries(discoveryResult.overfitting_audit.criteria).map(([gate_id, passed]) => ({ gate_id, passed }))} columns={[
            { title: '组合式选择偏差门槛', dataIndex: 'gate_id', render: (value: string) => gateLabels[value] ?? value },
            { title: '结果', dataIndex: 'passed', render: (value: boolean) => value ? <Tag color="green">通过</Tag> : <Tag color="red">未通过</Tag> },
          ]} />
          <Table
            rowKey="split_id"
            size="small"
            dataSource={discoveryResult.overfitting_audit.splits}
            pagination={{ pageSize: 10, showSizeChanger: false }}
            columns={[
              { title: '组合', dataIndex: 'split_id' },
              { title: '训练块', dataIndex: 'training_block_ids', render: (values: number[]) => values.map((value) => `B${value}`).join('/') },
              { title: '互补测试块', dataIndex: 'test_block_ids', render: (values: number[]) => values.map((value) => `B${value}`).join('/') },
              { title: '训练赢家', dataIndex: 'selected_candidate_id' },
              { title: '训练超额', dataIndex: 'training_excess_return', render: (value: string) => formatPercent(value) },
              { title: '测试超额', dataIndex: 'test_excess_return', render: (value: string) => formatPercent(value) },
              { title: '测试排名百分位', dataIndex: 'test_rank_percentile', render: (value: string) => formatPercent(value) },
              { title: '排名 logit', dataIndex: 'test_rank_logit', render: (value: string) => formatNumber(value) },
              { title: '低于中位', dataIndex: 'below_median', render: (value: boolean) => value ? <Tag color="red">是</Tag> : <Tag color="green">否</Tag> },
            ]}
            scroll={{ x: 1100 }}
          />
          <Table
            rowKey="candidate_id"
            size="small"
            dataSource={discoveryCandidates}
            pagination={{ pageSize: 10, showSizeChanger: false }}
            columns={[
              { title: '候选', dataIndex: 'candidate_id', render: (value: string) => value === discoveryResult.selected.candidate_id ? <Tag color="blue">{value} · 锁定</Tag> : value },
              { title: '三段方向', dataIndex: 'directions', render: (values: IntradayOpeningDirection[]) => values.map(directionLabel).join('/') },
              { title: '阈值 bps', dataIndex: 'minimum_body_return_bps' },
              { title: '训练信号 / 日', render: (_: unknown, row: IntradayFactorDiscovery['candidates'][number]) => `${row.training_signal_count} / ${row.training_signal_session_count}` },
              { title: '训练条件超额', dataIndex: 'training_mean_session_conditional_excess_return', render: (value: string) => formatPercent(value) },
              { title: '训练 NW t', dataIndex: 'training_newey_west_t_statistic', render: (value: string) => formatNumber(value) },
              { title: '训练门', dataIndex: 'training_gate_passed', render: (value: boolean) => value ? <Tag color="green">通过</Tag> : <Tag color="red">失败</Tag> },
            ]}
            scroll={{ x: 980 }}
          />
          <Alert
            showIcon
            type={discoveryResult.discovery_gate.passed ? 'success' : 'error'}
            title={discoveryResult.discovery_gate.passed ? '完整因子发现研究门通过，但仍需独立前向证据' : '完整因子发现研究门未通过'}
            description="时间顺序锁定测试、组合式选择偏差、固定成本压力、多行业压力、逐股票压力、信号前市场状态压力、会话内置换安慰剂、执行延迟压力和流动性/容量压力必须同时通过。当前门只评价历史研究质量，即使通过也没有 PAPER/LIVE 或外部订单授权。"
          />
          <Table rowKey="gate_id" size="small" pagination={false} dataSource={Object.entries(discoveryResult.discovery_gate.criteria).map(([gate_id, passed]) => ({ gate_id, passed }))} columns={[
            { title: '完整发现门槛', dataIndex: 'gate_id', render: (value: string) => gateLabels[value] ?? value },
            { title: '结果', dataIndex: 'passed', render: (value: boolean) => value ? <Tag color="green">通过</Tag> : <Tag color="red">未通过</Tag> },
          ]} />
          <Typography.Text type="secondary">选择时刻 {formatTimestamp(discoveryResult.split.selection_known_at)}，早于首个测试信号 {formatTimestamp(discoveryResult.split.first_test_signal_known_at)}；测试收益不参与选择。无状态写入、无外部订单。</Typography.Text>
        </Space>
      </Card>}
      {error && <Alert showIcon type="error" title="因子研究未完成" description={error} />}
      {result && <>
        <Alert
          showIcon
          type="success"
          title="策略回测响应身份已绑定"
          description={`已逐项核对当前请求、${result.data_version}、数据 checksum、开平仓行为规格、策略只读身份与 PAPER/LIVE 安全边界；结果哈希 ${result.preview_hash.slice(0, 16)}…。`}
        />
        {strategyLineage ? <Alert
          showIcon
          type="info"
          title="因子检验 → 策略回测血缘已绑定"
          description={<Space orientation="vertical" size={4}>
            <Typography.Text>{strategyLineage.factor_evidence.rule_id} · {strategyLineage.factor_evidence.candidate_name} · 相关性证据 {strategyLineage.factor_evidence.correlation_preview_hash.slice(0, 12)}… → 策略结果 {strategyLineage.strategy_preview.preview_hash.slice(0, 12)}…</Typography.Text>
            <Typography.Text type="secondary">只证明本次回测的开仓条件来自同窗已通过有效性且非冗余的手工交接；平仓规则和策略收益没有被相关性检验验证，同一历史窗口不是独立证据。</Typography.Text>
            <JsonExportButton
              filename={`qtrade-factor-strategy-lineage-${strategyLineage.strategy_preview.preview_hash.slice(0, 12)}.json`}
              value={strategyLineage}
              label="导出因子到策略血缘"
            />
          </Space>}
        /> : <Alert
          showIcon
          type="warning"
          title="本次回测没有因子检验手工交接血缘"
          description="结果本身已通过请求与安全边界校验，但不是从当前会话中已绑定、有效且非冗余的因子手工交接生成；因此不会导出因子到策略的联合身份包。"
        />}
        <Alert
          showIcon
          type={result.research_gate.passed ? 'success' : 'warning'}
          title={result.research_gate.passed ? '研究硬门通过，但仍不可自动晋级' : '回测已完成，研究硬门未通过'}
          description={`实际触发 ${result.event_summary.signal_count}/${result.event_summary.observation_count} 次；${result.event_summary.positive_sector_count}/${result.event_summary.sector_count} 个行业信号后均值为正。当前结论 ${result.promotion}，PAPER ${result.paper_readiness}，LIVE ${result.live_readiness}。`}
        />
        <Alert
          showIcon
          type={result.sequential_advancement.all_research_stages_passed ? 'info' : 'error'}
          title={`顺序晋级停在：${workflowLabels[result.sequential_advancement.first_blocking_stage] ?? result.sequential_advancement.first_blocking_stage}`}
          description="只有因子有效性、相关性和滚动稳健性全部通过，策略构造才获得研究准入。当前策略构造与因果回测只是诊断预览，不是候选策略或晋级证据；禁止在已查看窗口重选参数，下一假设必须先预注册再接触新证据。"
        />
        <Space wrap>
          <Tag color={result.sequential_advancement.all_research_stages_passed ? 'green' : 'red'}>前置研究门 {result.sequential_advancement.passed_research_stage_count}/{result.sequential_advancement.required_research_stage_count}</Tag>
          <Tag color="default">策略构造 {result.sequential_advancement.strategy_construction_status}</Tag>
          <Tag color="default">历史结果不能授权 PAPER</Tag>
        </Space>
        <Card type="inner" title={`下一假设预注册：${result.next_hypothesis_preregistration.status === 'REQUIRED_NOT_CREATED' ? '尚未创建' : '当前结果无需创建'}`}>
          <Space orientation="vertical" size={12} style={{ width: '100%' }}>
            <Alert
              showIcon
              type="warning"
              title="只读预检，不会写入研究或策略注册表"
              description="当前失败结果可以解释研究动机，但不能直接用于重选新参数；新假设必须先冻结，再接触与当前窗口互斥的新证据。"
            />
            <Space wrap>
              <Tag>ResearchHypothesis 必填字段 {result.next_hypothesis_preregistration.required_hypothesis_fields.length} 个</Tag>
              <Tag>ExperimentRunSpec 冻结字段 {result.next_hypothesis_preregistration.required_experiment_lock_fields.length} 个</Tag>
              <Tag>治理门槛 {result.next_hypothesis_preregistration.required_governance_gates.length} 项</Tag>
              <Tag color="default">当前注册表状态 {result.next_hypothesis_preregistration.strategy_registry_bridge.current_lifecycle_status}</Tag>
            </Space>
            <Typography.Text type="secondary">
              完成预注册与独立泄漏审查后，最早只能注册为 IDEA；随后依次经过 EXPERIMENT、CANDIDATE、VALIDATED、PAPER、SHADOW，APPROVED、CHAMPION 仍需人工授权。
            </Typography.Text>
            <Typography.Text type="secondary">
              当前绑定：{result.next_hypothesis_preregistration.current_result_binding.strategy_id} · {result.next_hypothesis_preregistration.current_result_binding.strategy_spec_hash} · {result.next_hypothesis_preregistration.current_result_binding.viewed_evidence_period.start} 至 {result.next_hypothesis_preregistration.current_result_binding.viewed_evidence_period.end}
            </Typography.Text>
            <Table
              rowKey="gate_id"
              size="small"
              pagination={false}
              dataSource={result.next_hypothesis_preregistration.required_governance_gates}
              columns={[
                { title: '预注册治理门槛', dataIndex: 'gate_id', render: (value: string) => preregistrationGateLabels[value] ?? value },
                { title: '要求', dataIndex: 'required', render: (value: boolean) => value ? <Tag color="orange">必须冻结</Tag> : <Tag>可选</Tag> },
                { title: '契约', dataIndex: 'requirement' },
              ]}
              scroll={{ x: 820 }}
            />
          </Space>
        </Card>
        <Card type="inner" title={`策略注册前审查：${result.strategy_registration_preflight.decision}`}>
          <Space orientation="vertical" size={12} style={{ width: '100%' }}>
            <Alert
              showIcon
              type="error"
              title="当前结果不能写入 StrategyRegistry"
              description="当前行为规格哈希不是完整 StrategySpec 哈希；名称、家族、版本、代码提交、模型、互斥切分、时间戳和血缘声明尚未完整冻结。当前不能写入 IDEA，也不能迁移到 EXPERIMENT。"
            />
            <Space wrap>
              <Tag color="red">注册门 {result.strategy_registration_preflight.passed_gate_count}/{result.strategy_registration_preflight.required_gate_count}</Tag>
              <Tag>StrategySpec 字段 {result.strategy_registration_preflight.current_field_readiness.available_field_count}/{result.strategy_registration_preflight.current_field_readiness.required_field_count}</Tag>
              <Tag color="default">最早状态 {result.strategy_registration_preflight.earliest_registry_status}</Tag>
              <Tag color="default">注册操作未开放</Tag>
            </Space>
            <Typography.Text type="secondary">
              IDEA → EXPERIMENT 仍要求完整 ExperimentRunSpec、质量通过的数据清单和独立泄漏审查；本页不会创建注册表记录。
            </Typography.Text>
            <Table
              rowKey="gate_id"
              size="small"
              pagination={false}
              dataSource={result.strategy_registration_preflight.required_gates}
              columns={[
                { title: '策略注册门槛', dataIndex: 'gate_id', render: (value: string) => strategyRegistrationGateLabels[value] ?? value },
                { title: '结果', dataIndex: 'passed', render: (value: boolean) => value ? <Tag color="green">通过</Tag> : <Tag color="red">阻断</Tag> },
                { title: '证据 / 原因码', dataIndex: 'evidence' },
              ]}
              scroll={{ x: 820 }}
            />
          </Space>
        </Card>
        <Steps
          size="small"
          orientation="vertical"
          items={result.workflow.map((stage) => ({
            title: `${workflowLabels[stage.id] ?? stage.id} · ${stage.status}`,
            content: stage.detail,
            status: stage.status === 'FAILED' ? 'error' : ['PASSED', 'AVAILABLE', 'COMPLETED'].includes(stage.status) ? 'finish' : 'wait',
          }))}
        />
        <Card type="inner" title={`PAPER 模拟盘准入：${result.paper_admission.decision}`}>
          <Space orientation="vertical" size={12} style={{ width: '100%' }}>
            <Alert
              showIcon
              type="error"
              title={`已通过 ${result.paper_admission.passed_gate_count}/${result.paper_admission.required_gate_count} 项`}
              description={`这是只读准入判定，不会注册策略、启动运行时或发送订单。下一允许动作：${advancementActionLabels[result.paper_admission.next_allowed_stage] ?? result.paper_admission.next_allowed_stage}。`}
            />
            <Table
              rowKey="gate_id"
              size="small"
              pagination={false}
              dataSource={result.paper_admission.required_gates}
              columns={[
                { title: 'PAPER 准入门槛', dataIndex: 'gate_id', render: (value: string) => paperAdmissionLabels[value] ?? value },
                { title: '结果', dataIndex: 'passed', render: (value: boolean) => value ? <Tag color="green">通过</Tag> : <Tag color="red">阻断</Tag> },
                { title: '证据 / 原因码', dataIndex: 'evidence' },
              ]}
              scroll={{ x: 820 }}
            />
          </Space>
        </Card>
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
          <Metric label="聚类信号日" value={String(result.event_summary.clustered_signal_session_count)} />
          <Metric label="日聚类超额均值" value={formatPercent(result.event_summary.mean_session_conditional_excess_return)} />
          <Metric label="Newey-West t" value={formatNumber(result.event_summary.newey_west_t_statistic)} />
          <Metric label="95% 置信区间" value={`${formatPercent(result.event_summary.confidence_interval_95_lower)} ～ ${formatPercent(result.event_summary.confidence_interval_95_upper)}`} />
          <Metric label="正向时间块" value={`${result.stability_validation.positive_block_count}/${result.stability_validation.block_count}`} />
          <Metric label="最差时间块超额" value={formatPercent(result.stability_validation.worst_block_mean_conditional_excess_return)} />
          <Metric label="滚动样本外收益" value={formatPercent(result.robustness_validation.stitched_strategy_metrics.total_return)} />
          <Metric label="滚动样本外超额" value={formatPercent(result.robustness_validation.stitched_strategy_metrics.excess_return)} />
          {result.exit_factor_validation && <>
            <Metric label="平仓因子触发" value={`${result.exit_factor_validation.trigger_count}/${result.exit_factor_validation.entry_signal_count}`} />
            <Metric label="平仓触发率" value={formatPercent(result.exit_factor_validation.trigger_rate)} />
            <Metric label="平仓卖出价优势" value={formatPercent(result.exit_factor_validation.mean_session_triggered_timing_advantage)} />
            <Metric label="平仓优势 NW t" value={formatNumber(result.exit_factor_validation.newey_west_t_statistic)} />
          </>}
        </div>
        <EquityChart dark analytics={curve} />
        <Tabs items={[
          {
            key: 'contract',
            label: '因子与时序',
            children: <Space orientation="vertical" style={{ width: '100%' }}>
              <Alert
                showIcon
                type="warning"
                title="策略规格已冻结为只读预览，但未注册"
                description="开仓因子、平仓规则、仓位、基准与成本已进入同一哈希；观测到的平仓触发次数不进入规格哈希。该 ID 只标识本次可复算预览，不会写入策略注册表，也没有 PAPER 晋级权限。"
              />
              <div className="description-grid">
                <Metric label="只读策略 ID" value={result.strategy_identity.strategy_id} />
                <Metric label="注册状态" value={result.strategy_identity.registration_status} />
                <Metric label="规格绑定" value="策略规格 SHA-256 已绑定" />
                <Metric label="PAPER 资格" value="无" />
              </div>
              <Typography.Text code>{result.strategy_identity.spec_hash}</Typography.Text>
              <Typography.Text code>{result.factor_definition.expression}</Typography.Text>
              <Typography.Text>数据：{result.factor_definition.source_interval}；信号已知：{result.factor_definition.known_at}；入场：{result.strategy.entry_rule}；退出：{result.strategy.exit_rule}。</Typography.Text>
              {result.strategy.conditional_exit_count !== undefined && <Typography.Text>平仓因子触发 {result.strategy.conditional_exit_count} 次；14:55 时间退出 {result.strategy.fallback_time_stop_count} 次。退出条件只统计已开仓信号，并与同期等权基准使用同一逐股票退出规则。</Typography.Text>}
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
              {result.exit_factor_validation && <>
                <Alert
                  showIcon
                  type={result.exit_factor_validation.passed ? 'success' : 'warning'}
                  title={result.exit_factor_validation.passed ? '平仓因子有效性门通过' : '平仓因子有效性门未通过'}
                  description={`只在实际开仓且三连阴触发的股票上，比较 10:05 条件退出价与 14:55 时间退出价；触发 ${result.exit_factor_validation.trigger_count} 次、覆盖 ${result.exit_factor_validation.clustered_trigger_session_count} 个交易日，日聚类卖出价优势 ${formatPercent(result.exit_factor_validation.mean_session_triggered_timing_advantage)}，Newey-West t ${formatNumber(result.exit_factor_validation.newey_west_t_statistic)}，95% 区间 ${formatPercent(result.exit_factor_validation.confidence_interval_95_lower)} ～ ${formatPercent(result.exit_factor_validation.confidence_interval_95_upper)}。这是同一路径反事实诊断，不是独立盲测。`}
                />
                <Table rowKey="gate_id" size="small" pagination={false} dataSource={Object.entries(result.exit_factor_validation.criteria).map(([gate_id, passed]) => ({ gate_id, passed }))} columns={[
                  { title: '平仓因子门槛', dataIndex: 'gate_id', render: (value: string) => gateLabels[value] ?? value },
                  { title: '结果', dataIndex: 'passed', render: (value: boolean) => value ? <Tag color="green">通过</Tag> : <Tag color="red">未通过</Tag> },
                ]} />
              </>}
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
            key: 'stability',
            label: `时间稳定性 · ${result.stability_validation.positive_block_count}/${result.stability_validation.block_count}`,
            children: <StabilityPanel stability={result.stability_validation} />,
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
            children: <GroupTable rows={result.backtest.symbols} nameLabel="证券" searchLabel="搜索本次因子个股" summaryLabel="当前结果个股" />,
          },
        ]} />
        <Alert
          showIcon
          type="warning"
          title={`五档买卖盘强度：${result.l2_factor_readiness.readiness} · ${result.l2_factor_readiness.passed_gate_count}/${result.l2_factor_readiness.required_gate_count}`}
          description={`公式 ${result.l2_factor_readiness.formula} 是待验证的买入假设，不是已经有效的策略。它需要 ${result.l2_factor_readiness.required_data}；当前没有获授权的 L2 来源，系统不会从 OHLCV 伪造买一至买五/卖一至卖五，也不会自动补出卖出条件。`}
        />
        <div className="description-grid">
          <Metric label="买入条件" value="买入假设 > 1 · 未检验" />
          <Metric label="平仓条件" value="卖出条件未定义" />
          <Metric label="深度模式" value="规范深度字段 20 个" />
          <Metric label="委托量单位" value="股（供应商单位待认证）" />
          <Metric label="修正时钟" value="封闭分钟后决定" />
          <Metric label="无效分母" value="零卖方总量：因子无定义" />
        </div>
        <Alert
          showIcon
          type="info"
          title={`L2 数据门 ${result.l2_factor_readiness.passed_gate_count}/${result.l2_factor_readiness.required_gate_count}`}
          description="必须先完成供应商授权、五档字段与单位认证、事件时钟/序号、新鲜度和录制回放对账，才允许采集因子样本。当前不会计算因子、运行相关性/有效性检验、回测策略或进入 PAPER。"
        />
        <Table
          rowKey="gate_id"
          size="small"
          pagination={false}
          dataSource={result.l2_factor_readiness.required_gates}
          columns={[
            { title: 'L2 数据门槛', dataIndex: 'gate_id', render: (value: string) => gateLabels[value] ?? value },
            { title: '结果', dataIndex: 'passed', render: (value: boolean) => value ? <Tag color="green">通过</Tag> : <Tag color="red">未通过</Tag> },
            { title: '原因码', dataIndex: 'reason' },
          ]}
          scroll={{ x: 760 }}
        />
        <Space wrap>
          <Input aria-label="本地因子实验名称" placeholder="为本次因子结果命名" value={experimentName} maxLength={80} onChange={(event) => setExperimentName(event.target.value)} style={{ width: 280 }} />
          <Button onClick={saveResult}>保存到本地因子实验</Button>
          <JsonExportButton filename={`qtrade-intraday-factor-${result.preview_hash.slice(0, 12)}.json`} value={result} label="导出因子研究证据" />
        </Space>
      </>}
      {saveStatus && <Alert showIcon type="info" title={saveStatus} />}
      <Card
        type="inner"
        title={`本地开仓因子候选（${factorCandidates.length}/30）`}
        extra={<Typography.Text type="secondary">仅当前浏览器 · 草案不是证据</Typography.Text>}
      >
        <Space orientation="vertical" style={{ width: '100%' }}>
          <Space wrap>
            <Input aria-label="开仓因子候选名称" placeholder="为当前开仓表达式命名" value={factorCandidateName} maxLength={80} onChange={(event) => setFactorCandidateName(event.target.value)} style={{ width: 280 }} />
            <Button onClick={() => { void saveCurrentFactorCandidate() }}>保存当前开仓因子候选（不回测）</Button>
          </Space>
          <Typography.Text type="secondary">候选只保存三段方向、ALL/ANY 和实体阈值，不包含平仓条件、策略指标或回测结论；保存不会调用服务端，也不会生成有效性、注册或晋级证据。</Typography.Text>
          {factorCandidateStatus && <Alert showIcon type="info" title={factorCandidateStatus} />}
        </Space>
        <Table<LocalIntradayFactorCandidate>
          rowKey="draft_key"
          size="small"
          columns={factorCandidateColumns}
          dataSource={factorCandidates}
          rowSelection={{
            selectedRowKeys: selectedFactorCandidateKeys,
            onChange: (keys) => {
              setSelectedFactorCandidateKeys(keys.map(String))
              setCorrelationResult(null)
              setCorrelationError(null)
              setCorrelationBindings({})
              setCorrelationHandoff(null)
              setStrategyLineage(null)
              setCorrelationHandoffStatus(null)
            },
          }}
          pagination={{ pageSize: 8, showSizeChanger: false }}
          scroll={{ x: 1100 }}
          locale={{ emptyText: '先保存至少两个开仓因子草案，再做有效性、多重检验与相关性检验；不必先运行策略回测。' }}
        />
        <Space orientation="vertical" style={{ width: '100%', marginTop: 12 }}>
          <Space wrap>
            <Button
              loading={correlationRunning}
              disabled={selectedFactorCandidateKeys.length < 2 || selectedFactorCandidateKeys.length > 8}
              onClick={() => { void runSelectedCorrelation() }}
            >检验选中规则有效性与相关性（{selectedFactorCandidateKeys.length}/8）</Button>
            <Typography.Text type="secondary">按当前表单日期，在同一 75 股窗口检验 2–8 条纯开仓因子候选；有效性标签固定为 T+1 开盘收益，相关性使用连续方向分数，不读取平仓条件或策略收益。</Typography.Text>
          </Space>
          {correlationError && <Alert showIcon type="error" title="规则相关性检验未完成" description={correlationError} />}
          {correlationResult && <>
            <Alert
              showIcon
              type={correlationResult.effective_rule_count === correlationResult.multiple_testing_contract.family_size ? 'success' : 'warning'}
              title={`${correlationResult.effective_rule_count}/${correlationResult.multiple_testing_contract.family_size} 条规则通过同窗有效性硬门`}
              description={`每条规则使用保存时的实体阈值；先按交易日聚类条件超额计算 Newey-West t 与双侧原始 p，再对本次 ${correlationResult.multiple_testing_contract.family_size} 条规则做 Holm-Bonferroni 校正。要求至少 250 个观测、${correlationResult.multiple_testing_contract.minimum_signal_count} 次信号、${correlationResult.multiple_testing_contract.minimum_clustered_signal_sessions} 个信号交易日、正向条件超额、t > ${correlationResult.multiple_testing_contract.minimum_t_statistic} 且校正 p ≤ ${correlationResult.multiple_testing_contract.alpha}。`}
            />
            <Table
              rowKey="rule_id"
              size="small"
              pagination={false}
              dataSource={correlationResult.effectiveness}
              columns={[
                { title: '本地规则', dataIndex: 'rule_id', render: (value: string) => `${value} · ${correlationLabels[value] ?? value}` },
                { title: '阈值 bps', dataIndex: 'minimum_body_return_bps' },
                { title: '信号 / 交易日', render: (_: unknown, row: IntradayFactorCorrelation['effectiveness'][number]) => `${row.signal_count} / ${row.clustered_signal_session_count}` },
                { title: '日聚类条件超额', dataIndex: 'mean_session_conditional_excess_return', render: (value: string) => formatPercent(value) },
                { title: 'NW t', dataIndex: 'newey_west_t_statistic', render: (value: string) => formatNumber(value) },
                { title: '原始 p', dataIndex: 'raw_p_value', render: (value: string) => formatNumber(value) },
                { title: 'Holm p', dataIndex: 'holm_adjusted_p_value', render: (value: string) => formatNumber(value) },
                { title: '有效性', dataIndex: 'passed', render: (value: boolean) => value ? <Tag color="green">通过</Tag> : <Tag color="red">未通过</Tag> },
                {
                  title: '进入策略构造',
                  render: (_: unknown, row: IntradayFactorCorrelation['effectiveness'][number]) => {
                    const candidate = correlationBindings[row.rule_id]
                    if (!row.passed) return <Tag>有效性未通过</Tag>
                    if (redundantRuleIds.has(row.rule_id)) return <Tag color="red">相关性冗余</Tag>
                    if (!candidate) return <Tag color="red">身份未绑定</Tag>
                    return <Button size="small" onClick={() => loadValidatedFactorIntoStrategy(row.rule_id)}>载入“{candidate.name}”进入策略构造</Button>
                  },
                },
              ]}
              scroll={{ x: 1340 }}
            />
            {correlationHandoffStatus && <Alert
              showIcon
              type="info"
              title={correlationHandoffStatus}
              description="只复制已绑定的开仓因子；日期、成本和平仓条件保持不变。不会自动运行策略回测、写入注册表或晋级 PAPER/LIVE；当前检验窗口已被查看，后续同窗回测仍只是诊断。"
            />}
            <Alert
              showIcon
              type={correlationResult.passed ? 'success' : 'warning'}
              title={correlationResult.passed ? '选中规则未发现高度相关对' : `发现 ${redundantPairs.length} 对高度相关规则`}
              description={`${correlationResult.observation_count.toLocaleString('zh-CN')} 个同窗股票日观测；Pearson |ρ| ≥ ${correlationResult.redundancy_threshold} 判为冗余。使用三段十分钟实体收益构造连续方向分数，不使用二元开仓信号、未来字段或策略收益。结果只读，PAPER ${correlationResult.paper_readiness}，LIVE ${correlationResult.live_readiness}。`}
            />
            <Table
              rowKey="rule_id"
              size="small"
              pagination={false}
              dataSource={correlationMatrixRows}
              columns={[
                { title: '本地规则', dataIndex: 'label' },
                ...correlationResult.rules.map((rule) => ({
                  title: rule.rule_id,
                  render: (_: unknown, row: IntradayFactorCorrelation['matrix'][number]) => formatNumber(row.correlations[rule.rule_id]),
                })),
              ]}
              scroll={{ x: 720 }}
            />
            <Table
              rowKey={(row) => `${row.left_rule_id}:${row.right_rule_id}`}
              size="small"
              pagination={false}
              dataSource={correlationResult.pairs}
              columns={[
                { title: '规则 A', dataIndex: 'left_rule_id', render: (value: string) => `${value} · ${correlationLabels[value] ?? value}` },
                { title: '规则 B', dataIndex: 'right_rule_id', render: (value: string) => `${value} · ${correlationLabels[value] ?? value}` },
                { title: 'Pearson ρ', dataIndex: 'correlation', render: (value: string) => formatNumber(value) },
                { title: '去冗余判定', dataIndex: 'redundant', render: (value: boolean) => value ? <Tag color="red">冗余</Tag> : <Tag color="green">保留</Tag> },
              ]}
              scroll={{ x: 760 }}
            />
            <Space wrap>
              <JsonExportButton
                filename={`qtrade-intraday-factor-correlation-${correlationResult.preview_hash.slice(0, 12)}.json`}
                value={correlationResult}
                label="导出有效性与相关性证据"
              />
              <Typography.Text type="secondary">相关性响应哈希 {correlationResult.preview_hash}；数据 checksum {correlationResult.dataset_checksum}。导出保留服务端原始有效性、多重检验、相关矩阵和冗余判定；不写本地实验或注册表，无状态写入、无外部订单。</Typography.Text>
            </Space>
          </>}
          <Typography.Text type="secondary">草案绑定当前数据版本与 checksum，但只有服务端检验响应才包含有效性、Holm 校正和相关矩阵；草案本身始终是 NOT_RUN。响应仍是已查看历史的只读诊断，不写 FactorRegistry/StrategyRegistry，不构造平仓策略，不产生 PAPER/LIVE 或订单权限。</Typography.Text>
        </Space>
      </Card>
      <Card
        type="inner"
        title={`本地因子实验对比（${savedExperiments.length}/30）`}
        extra={<Space wrap>
          <Typography.Text type="secondary">仅当前浏览器</Typography.Text>
          <JsonExportButton
            filename={`qtrade-local-intraday-factor-comparison-${dataset.checksum.slice(0, 12)}.json`}
            value={factorComparisonExport}
            label="导出因子对比"
          />
        </Space>}
      >
        <Table<LocalIntradayFactorExperiment>
          rowKey="preview_hash"
          size="small"
          columns={experimentColumns}
          dataSource={savedExperiments}
          pagination={{ pageSize: 5, showSizeChanger: false }}
          scroll={{ x: 2200 }}
          locale={{ emptyText: '完成策略构造和因果回测后可保存结果摘要，用于反复调整和横向比较。' }}
        />
        <Typography.Text type="secondary">记录绑定当前数据版本与 checksum，保留规则、结果哈希、只读策略 ID/规格哈希、常用绩效、Rank IC、最大相关性、Newey-West 与滚动样本外摘要；载入后必须重新运行。可导出确定性只读 JSON 留档或跨设备核对，但不支持导入、自动同步或自动选优。策略身份仍是 AD_HOC_UNREGISTERED；表中统计都来自已经查看的同一历史响应，不是不可变实验、FactorRegistry/StrategyRegistry 写入、独立样本外证据或 PAPER/LIVE 晋级输入。</Typography.Text>
      </Card>
    </Space>
  </Card>
}

function StabilityPanel({ stability }: { stability: IntradayFactorBacktest['stability_validation'] }) {
  const gateRows = Object.entries(stability.criteria).map(([gate_id, passed]) => ({ gate_id, passed }))
  return <Space orientation="vertical" style={{ width: '100%' }}>
    <Alert
      showIcon
      type={stability.passed ? 'success' : 'warning'}
      title={`连续时间块 ${stability.positive_block_count}/${stability.block_count} 正向`}
      description={`最差块日聚类条件超额 ${formatPercent(stability.worst_block_mean_conditional_excess_return)}；最大单块绝对贡献占比 ${formatPercent(stability.maximum_absolute_block_contribution_share)}，硬门要求低于 ${formatPercent(stability.contribution_share_threshold)}。四个块按日期固定且互不重叠，不参与参数重新选择。`}
    />
    <Table
      rowKey="block_id"
      size="small"
      pagination={false}
      dataSource={stability.blocks}
      columns={[
        { title: '时间块', dataIndex: 'block_id' },
        { title: '区间', render: (_: unknown, row: typeof stability.blocks[number]) => `${row.start} → ${row.end}` },
        { title: '交易日', dataIndex: 'session_count' },
        { title: '信号日', dataIndex: 'signal_session_count' },
        { title: '信号', dataIndex: 'signal_count' },
        { title: '日聚类条件超额', dataIndex: 'mean_session_conditional_excess_return', render: (value: string) => formatPercent(value) },
      ]}
      scroll={{ x: 800 }}
    />
    <Table
      rowKey="gate_id"
      size="small"
      pagination={false}
      dataSource={gateRows}
      columns={[
        { title: '时间稳定性门槛', dataIndex: 'gate_id', render: (value: string) => gateLabels[value] ?? value },
        { title: '结果', dataIndex: 'passed', render: (value: boolean) => value ? <Tag color="green">通过</Tag> : <Tag color="red">未通过</Tag> },
      ]}
    />
  </Space>
}

function GroupTable({ rows, nameLabel, searchLabel, summaryLabel }: {
  rows: IntradayFactorBacktest['backtest']['sectors']
  nameLabel: string
  searchLabel?: string
  summaryLabel?: string
}) {
  const [query, setQuery] = useState('')
  const normalized = query.trim().toLowerCase()
  const filteredRows = normalized
    ? rows.filter((row) => row.group_id.toLowerCase().includes(normalized) || row.group_name.toLowerCase().includes(normalized))
    : rows
  return <Space orientation="vertical" style={{ width: '100%' }}>
    {searchLabel && <>
      <Space wrap>
        <Input.Search aria-label={searchLabel} allowClear placeholder="输入六位股票代码" value={query} onChange={(event) => setQuery(event.target.value)} style={{ width: 280 }} />
        <Typography.Text>{summaryLabel} {filteredRows.length}/{rows.length}</Typography.Text>
      </Space>
      <Typography.Text type="secondary">筛选只定位本次同一 75 股因果回放的逐股票结果，不重跑、不重选股票或参数。</Typography.Text>
    </>}
    <Table
      rowKey="group_id"
      size="small"
      dataSource={filteredRows}
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
  </Space>
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

function matchesBacktestResponse(
  response: IntradayFactorBacktest | null,
  request: IntradayFactorBacktestRequest,
  dataset: VerifiedFiveMinuteDataset,
): response is IntradayFactorBacktest {
  if (!response) return false
  try {
    const identity = response.strategy_identity
    const advancement = response.sequential_advancement
    const preregistration = response.next_hypothesis_preregistration
    const registration = response.strategy_registration_preflight
    const paperAdmission = response.paper_admission
    const l2Readiness = response.l2_factor_readiness
    const expectedExitRule = request.exit_rule
      ?? 'NEXT_SESSION_OPEN'
    const expectedExitThreshold = request.exit_minimum_body_return_bps ?? '0'
    const expectedStrategyExitRule = expectedExitRule === 'NEXT_SESSION_OPEN'
      ? 'NEXT_SESSION_OPEN_T_PLUS_ONE'
      : expectedExitRule
    const workflowBlocksExecution = response.workflow.some((stage) => (
      stage.id === 'PAPER' && stage.status === 'NOT_ELIGIBLE'
    )) && response.workflow.some((stage) => (
      stage.id === 'LIVE' && stage.status === 'DISABLED'
    ))
    return response.classification === 'AD_HOC_INTRADAY_FACTOR_RESEARCH_CURRENT_NON_PIT'
      && sameBacktestRequest(response.parameters, request)
      && response.data_version === dataset.data_version
      && response.dataset_checksum === dataset.checksum
      && response.data_interval === '5m'
      && response.symbol_count === dataset.symbol_count
      && response.sector_count === dataset.sector_count
      && response.factor_definition.minimum_body_return_bps === request.minimum_body_return_bps
      && response.factor_definition.combination === request.combination
      && sameDirections(response.factor_definition.directions, request.directions)
      && response.factor_definition.arbitrary_code_execution === false
      && response.strategy.entry_minimum_body_return_bps === request.minimum_body_return_bps
      && response.strategy.entry_combination === request.combination
      && sameDirections(response.strategy.entry_directions, request.directions)
      && response.strategy.entry_rule === 'NEXT_5M_OPEN_10_05'
      && response.strategy.exit_rule === expectedStrategyExitRule
      && (expectedExitRule === 'NEXT_SESSION_OPEN'
        || response.strategy.exit_minimum_body_return_bps === expectedExitThreshold)
      && response.strategy.single_side_cost_bps === request.cost_bps
      && response.strategy.t_plus_one_compliant === true
      && response.causal_contract.same_bar_execution === false
      && response.causal_contract.t_plus_one_compliant === true
      && /^[a-f0-9]{64}$/.test(identity.spec_hash)
      && identity.strategy_id === `adhoc-intraday-${identity.spec_hash.slice(0, 16)}`
      && identity.classification === 'AD_HOC_READ_ONLY_STRATEGY_IDENTITY'
      && identity.registration_status === 'AD_HOC_UNREGISTERED'
      && identity.immutable_within_response === true
      && identity.paper_eligible === false
      && identity.promotion_authority === false
      && identity.state_mutation === false
      && identity.external_order_transmission === false
      && advancement.strategy_registration_allowed === false
      && advancement.historical_result_can_authorize_paper === false
      && advancement.viewed_window_reselection_allowed === false
      && advancement.state_mutation === false
      && advancement.external_order_transmission === false
      && preregistration.current_result_binding.strategy_id === identity.strategy_id
      && preregistration.current_result_binding.strategy_spec_hash === identity.spec_hash
      && preregistration.current_result_binding.data_version === dataset.data_version
      && preregistration.current_result_binding.dataset_checksum === dataset.checksum
      && preregistration.current_result_binding.viewed_evidence_period.start === request.start
      && preregistration.current_result_binding.viewed_evidence_period.end === request.end
      && preregistration.current_result_binding.first_blocking_stage === advancement.first_blocking_stage
      && preregistration.current_result_binding.next_allowed_action === advancement.next_allowed_action
      && preregistration.preregistration_created === false
      && preregistration.registry_write === false
      && preregistration.paper_runtime_start === false
      && preregistration.live_runtime_start === false
      && preregistration.state_mutation === false
      && preregistration.external_order_transmission === false
      && registration.decision === 'BLOCKED'
      && registration.current_candidate.strategy_id === identity.strategy_id
      && registration.current_candidate.behavior_spec_hash === identity.spec_hash
      && registration.registration_operation_exposed === false
      && registration.registry_write === false
      && registration.paper_runtime_start === false
      && registration.live_runtime_start === false
      && registration.state_mutation === false
      && registration.external_order_transmission === false
      && paperAdmission.decision === 'BLOCKED'
      && paperAdmission.all_required_gates_passed === false
      && paperAdmission.automatic_start_allowed === false
      && paperAdmission.runtime_mode_change === false
      && paperAdmission.state_mutation === false
      && paperAdmission.external_order_transmission === false
      && paperAdmission.next_allowed_stage === advancement.next_allowed_action
      && paperAdmission.required_gates.length === 8
      && paperAdmission.required_gates.some((gate) => (
        gate.gate_id === 'verified_historical_dataset_identity_bound' && gate.passed
      ))
      && [
        'registered_immutable_strategy_version_available',
        'unseen_forward_shadow_evidence_attached',
        'authorized_realtime_provider_ready',
        'fresh_runtime_readiness_attestation_available',
        'reconciliation_ready',
      ].every((gateId) => paperAdmission.required_gates.some((gate) => (
        gate.gate_id === gateId && !gate.passed
      )))
      && workflowBlocksExecution
      && matchesUnavailableL2FactorReadiness(l2Readiness)
      && /^[a-f0-9]{64}$/.test(response.preview_hash)
      && response.point_in_time === false
      && response.future_adjustment_knowledge_possible === true
      && response.realtime_readiness === false
      && response.promotion === 'NOT_ELIGIBLE'
      && response.paper_readiness === 'NOT_ELIGIBLE'
      && response.live_readiness === 'DISABLED_REQUIRES_SEPARATE_AUTHORIZATION'
      && response.state_mutation === false
      && response.external_order_transmission === false
  } catch {
    return false
  }
}

function sameBacktestRequest(
  left: IntradayFactorBacktestRequest,
  right: IntradayFactorBacktestRequest,
): boolean {
  return left.factor === right.factor
    && left.start === right.start
    && left.end === right.end
    && left.cost_bps === right.cost_bps
    && left.minimum_body_return_bps === right.minimum_body_return_bps
    && left.combination === right.combination
    && sameDirections(left.directions, right.directions)
    && (left.exit_rule ?? 'NEXT_SESSION_OPEN') === (right.exit_rule ?? 'NEXT_SESSION_OPEN')
    && (left.exit_minimum_body_return_bps ?? '0') === (right.exit_minimum_body_return_bps ?? '0')
}

function sameDirections(
  left: readonly IntradayOpeningDirection[],
  right: readonly IntradayOpeningDirection[],
): boolean {
  return left.length === right.length
    && left.every((direction, index) => direction === right[index])
}

function matchesCorrelationResponse(
  response: IntradayFactorCorrelation,
  request: IntradayFactorCorrelation['parameters'],
  dataset: VerifiedFiveMinuteDataset,
): boolean {
  try {
    const ruleIds = request.rules.map((_, index) => `R${String(index + 1).padStart(2, '0')}`)
    const startIndex = dataset.trading_days.indexOf(request.start)
    const endIndex = dataset.trading_days.indexOf(request.end)
    if (startIndex < 0 || endIndex < startIndex || endIndex + 1 >= dataset.trading_days.length) return false
    const selectedTradingDays = dataset.trading_days.slice(startIndex, endIndex + 1)
    const exitCompletionDay = dataset.trading_days[endIndex + 1]
    const expectedObservationCount = selectedTradingDays.length * dataset.symbol_count
    const expectedSourceBarCount = (selectedTradingDays.length + 1)
      * dataset.symbol_count
      * dataset.session_bar_count_per_symbol
    const parametersMatch = response.parameters.start === request.start
      && response.parameters.end === request.end
      && sameCorrelationRules(response.parameters.rules, request.rules)
    const scoreContractMatches = response.score_contract.source_interval === 'NATIVE_5M_AGGREGATED_TO_10M'
      && response.score_contract.source_timestamp_convention === 'BAR_END_TIME'
      && response.score_contract.source_bars === '09:35,09:40,09:45,09:50,09:55,10:00'
      && response.score_contract.known_at === '10:00_BAR_CLOSE'
      && response.score_contract.score_definition === 'ALL_MIN_OR_ANY_MAX_OF_SIGNED_TEN_MINUTE_BODY_RETURNS'
      && response.score_contract.binary_signal_used === false
      && response.score_contract.future_features_used === false
      && response.score_contract.arbitrary_code_execution === false
    const rulesMatch = response.rules.length === request.rules.length
      && response.rules.every((rule, index) => (
        rule.rule_id === ruleIds[index]
        && typeof rule.factor_id === 'string'
        && rule.factor_id.length > 0
        && typeof rule.name === 'string'
        && rule.name.length > 0
        && typeof rule.expression === 'string'
        && rule.expression.length > 0
        && sameCorrelationRule(rule, request.rules[index])
      ))
    const testingContract = response.multiple_testing_contract
    const testingContractMatches = testingContract.method === 'HOLM_BONFERRONI'
      && testingContract.family_size === ruleIds.length
      && testingContract.alpha === '0.05'
      && testingContract.raw_p_value === 'TWO_SIDED_ASYMPTOTIC_NORMAL_FROM_NEWEY_WEST_T'
      && testingContract.effect_direction === 'POSITIVE_SESSION_CONDITIONAL_EXCESS'
      && testingContract.minimum_clustered_signal_sessions === 10
      && testingContract.minimum_signal_count === 20
      && testingContract.minimum_t_statistic === '1.96'
    const effectivenessMatches = response.effectiveness.length === ruleIds.length
      && response.effectiveness.every((row, index) => matchesEffectivenessRow(
        row,
        ruleIds[index],
        request.rules[index],
        expectedObservationCount,
        selectedTradingDays.length,
      ))
      && matchesHolmAdjustedPValues(response.effectiveness)
      && response.effective_rule_count === response.effectiveness.filter((row) => row.passed).length
    const matrixMatches = matchesCorrelationMatrix(response.matrix, ruleIds)
    const redundancyThreshold = finiteDecimal(response.redundancy_threshold)
    const pairValidation = redundancyThreshold !== null
      && redundancyThreshold > 0
      && redundancyThreshold <= 1
      ? validateCorrelationPairs(response, ruleIds, redundancyThreshold)
      : null
    return response.classification === 'AD_HOC_INTRADAY_FACTOR_CORRELATION_CURRENT_NON_PIT'
      && response.data_version === dataset.data_version
      && response.dataset_checksum === dataset.checksum
      && response.data_interval === '5m'
      && response.symbol_count === dataset.symbol_count
      && response.sector_count === dataset.sector_count
      && response.observation_count === expectedObservationCount
      && response.source_bar_count === expectedSourceBarCount
      && sameStrings(response.selected_trading_days, selectedTradingDays)
      && response.exit_completion_day === exitCompletionDay
      && parametersMatch
      && scoreContractMatches
      && rulesMatch
      && testingContractMatches
      && effectivenessMatches
      && matrixMatches
      && pairValidation !== null
      && pairValidation.matches
      && approximatelyEqual(response.maximum_absolute_correlation, pairValidation.maximumAbsoluteCorrelation)
      && response.passed === !pairValidation.hasRedundantPair
      && /^[a-f0-9]{64}$/.test(response.preview_hash)
      && response.point_in_time === false
      && response.future_adjustment_knowledge_possible === true
      && response.realtime_readiness === false
      && response.promotion === 'NOT_ELIGIBLE'
      && response.paper_readiness === 'NOT_ELIGIBLE'
      && response.live_readiness === 'DISABLED_REQUIRES_SEPARATE_AUTHORIZATION'
      && response.state_mutation === false
      && response.external_order_transmission === false
  } catch {
    return false
  }
}

const effectivenessCriteriaKeys = [
  'minimum_observation_count',
  'minimum_signal_count',
  'minimum_ten_clustered_signal_sessions',
  'positive_session_conditional_excess',
  'newey_west_t_statistic_above_1_96',
  'holm_adjusted_p_value_at_most_0_05',
] as const

function matchesEffectivenessRow(
  row: IntradayFactorCorrelation['effectiveness'][number],
  ruleId: string,
  rule: IntradayFactorCorrelation['parameters']['rules'][number] | undefined,
  observationCount: number,
  selectedSessionCount: number,
): boolean {
  if (!rule || row.rule_id !== ruleId || row.minimum_body_return_bps !== rule.minimum_body_return_bps) return false
  const mean = finiteDecimal(row.mean_session_conditional_excess_return)
  const standardError = finiteDecimal(row.newey_west_standard_error)
  const tStatistic = finiteDecimal(row.newey_west_t_statistic)
  const confidenceLower = finiteDecimal(row.confidence_interval_95_lower)
  const confidenceUpper = finiteDecimal(row.confidence_interval_95_upper)
  const rawPValue = finiteDecimal(row.raw_p_value)
  const adjustedPValue = finiteDecimal(row.holm_adjusted_p_value)
  if (mean === null || standardError === null || standardError < 0 || tStatistic === null
    || confidenceLower === null || confidenceUpper === null || confidenceLower > confidenceUpper
    || rawPValue === null || adjustedPValue === null || rawPValue < 0 || adjustedPValue < rawPValue
    || adjustedPValue > 1) return false
  if (row.observation_count !== observationCount
    || !boundedInteger(row.signal_count, 0, observationCount)
    || !boundedInteger(row.clustered_signal_session_count, 0, selectedSessionCount)
    || !boundedInteger(row.newey_west_lag, 0, selectedSessionCount - 1)) return false
  const expectedCriteria: Record<(typeof effectivenessCriteriaKeys)[number], boolean> = {
    minimum_observation_count: observationCount >= 250,
    minimum_signal_count: row.signal_count >= 20,
    minimum_ten_clustered_signal_sessions: row.clustered_signal_session_count >= 10,
    positive_session_conditional_excess: mean > 0,
    newey_west_t_statistic_above_1_96: tStatistic > 1.96,
    holm_adjusted_p_value_at_most_0_05: adjustedPValue <= 0.05,
  }
  return sameStringSet(Object.keys(row.criteria), effectivenessCriteriaKeys)
    && effectivenessCriteriaKeys.every((key) => row.criteria[key] === expectedCriteria[key])
    && row.passed === effectivenessCriteriaKeys.every((key) => expectedCriteria[key])
}

function matchesCorrelationMatrix(
  matrix: IntradayFactorCorrelation['matrix'],
  ruleIds: string[],
): boolean {
  if (matrix.length !== ruleIds.length) return false
  for (const [rowIndex, row] of matrix.entries()) {
    if (row.rule_id !== ruleIds[rowIndex] || !sameStringSet(Object.keys(row.correlations), ruleIds)) return false
    for (const [columnIndex, ruleId] of ruleIds.entries()) {
      const value = finiteDecimal(row.correlations[ruleId])
      const symmetric = finiteDecimal(matrix[columnIndex]?.correlations[row.rule_id])
      if (value === null || symmetric === null || value < -1 || value > 1 || Math.abs(value - symmetric) > 1e-12) return false
      if (rowIndex === columnIndex && Math.abs(value - 1) > 1e-12) return false
    }
  }
  return true
}

function matchesHolmAdjustedPValues(effectiveness: IntradayFactorCorrelation['effectiveness']): boolean {
  const ranked = effectiveness.map((row, index) => ({
    index,
    raw: finiteDecimal(row.raw_p_value),
  }))
  if (ranked.some((item) => item.raw === null)) return false
  ranked.sort((left, right) => (left.raw as number) - (right.raw as number) || left.index - right.index)
  const expected = Array<number>(effectiveness.length)
  let runningMaximum = 0
  for (const [rank, item] of ranked.entries()) {
    runningMaximum = Math.max(
      runningMaximum,
      Math.min(1, (item.raw as number) * (effectiveness.length - rank)),
    )
    expected[item.index] = runningMaximum
  }
  return effectiveness.every((row, index) => approximatelyEqual(row.holm_adjusted_p_value, expected[index]))
}

function validateCorrelationPairs(
  response: IntradayFactorCorrelation,
  ruleIds: string[],
  redundancyThreshold: number,
): { matches: boolean; maximumAbsoluteCorrelation: number; hasRedundantPair: boolean } {
  const expectedPairs = ruleIds.flatMap((leftRuleId, index) => (
    ruleIds.slice(index + 1).map((rightRuleId) => ({ leftRuleId, rightRuleId }))
  ))
  if (response.pairs.length !== expectedPairs.length) {
    return { matches: false, maximumAbsoluteCorrelation: 0, hasRedundantPair: false }
  }
  let maximumAbsoluteCorrelation = 0
  let hasRedundantPair = false
  for (const [index, expected] of expectedPairs.entries()) {
    const pair = response.pairs[index]
    const correlation = finiteDecimal(pair.correlation)
    const matrixCorrelation = finiteDecimal(
      response.matrix[ruleIds.indexOf(expected.leftRuleId)]?.correlations[expected.rightRuleId],
    )
    if (pair.left_rule_id !== expected.leftRuleId || pair.right_rule_id !== expected.rightRuleId
      || correlation === null || matrixCorrelation === null || correlation < -1 || correlation > 1
      || Math.abs(correlation - matrixCorrelation) > 1e-12
      || pair.redundant !== (Math.abs(correlation) >= redundancyThreshold)) {
      return { matches: false, maximumAbsoluteCorrelation: 0, hasRedundantPair: false }
    }
    maximumAbsoluteCorrelation = Math.max(maximumAbsoluteCorrelation, Math.abs(correlation))
    hasRedundantPair ||= pair.redundant
  }
  return { matches: true, maximumAbsoluteCorrelation, hasRedundantPair }
}

function finiteDecimal(value: unknown): number | null {
  if (typeof value !== 'string' || value.trim() === '') return null
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed : null
}

function approximatelyEqual(value: unknown, expected: number): boolean {
  const parsed = finiteDecimal(value)
  return parsed !== null && Math.abs(parsed - expected) <= 1e-12
}

function boundedInteger(value: unknown, minimum: number, maximum: number): boolean {
  return typeof value === 'number' && Number.isInteger(value) && value >= minimum && value <= maximum
}

function sameStrings(left: string[], right: string[]): boolean {
  return left.length === right.length && left.every((value, index) => value === right[index])
}

function sameStringSet(left: readonly string[], right: readonly string[]): boolean {
  return left.length === right.length && left.every((value) => right.includes(value))
}

function sameCorrelationRules(
  left: IntradayFactorCorrelation['parameters']['rules'],
  right: IntradayFactorCorrelation['parameters']['rules'],
): boolean {
  return left.length === right.length
    && left.every((rule, index) => sameCorrelationRule(rule, right[index]))
}

function sameCorrelationRule(
  left: IntradayFactorCorrelation['parameters']['rules'][number],
  right: IntradayFactorCorrelation['parameters']['rules'][number] | undefined,
): boolean {
  return right !== undefined
    && left.combination === right.combination
    && left.minimum_body_return_bps === right.minimum_body_return_bps
    && sameDirections(left.directions, right.directions)
}

function buildDraftEntryExpression(
  directions: IntradayOpeningDirection[],
  combination: 'ALL' | 'ANY',
  thresholdBps: string,
) {
  const intervals = ['0930_0940', '0940_0950', '0950_1000']
  const predicates = directions.flatMap((direction, index) => {
    if (direction === 'IGNORE') return []
    const comparison = direction === 'BULLISH' ? '>=' : '<='
    const threshold = direction === 'BULLISH' ? thresholdBps : `-${thresholdBps}`
    return [`body_return_${intervals[index]} ${comparison} ${threshold} bps`]
  })
  return predicates.join(combination === 'ALL' ? ' AND ' : ' OR ')
}

function formatNumber(value: string) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? parsed.toFixed(3) : '—'
}

function formatPercent(value: string) {
  const parsed = Number(value)
  return Number.isFinite(parsed) ? `${(parsed * 100).toFixed(2)}%` : '—'
}

function formatBasisPoints(value: string) {
  const parsed = Number(value)
  if (!Number.isFinite(parsed)) return '—'
  const basisPoints = parsed * 10_000
  return `${basisPoints > 0 ? '+' : ''}${basisPoints.toFixed(4)} bp`
}

function formatTimestamp(value: string) {
  const parsed = new Date(value)
  return Number.isNaN(parsed.getTime()) ? '—' : parsed.toLocaleString('zh-CN', { hour12: false })
}

function directionLabel(value: IntradayOpeningDirection) {
  if (value === 'BULLISH') return '阳'
  if (value === 'BEARISH') return '阴'
  return '忽略'
}
