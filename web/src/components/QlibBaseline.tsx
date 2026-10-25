import { Alert, Button, Card, Descriptions, Space, Statistic, Table, Tag, Typography, theme } from 'antd'
import { lazy, Suspense, useCallback, useEffect, useRef, useState } from 'react'
import { loadQlibBaseline } from '../api/client'
import type { QlibBaselinePublication } from '../types'
import { JsonExportButton } from './JsonExportButton'

const ChartRenderer = lazy(() => import('../charts/ChartRenderer'))
const stages: Record<string, string> = {
  NOT_STARTED: '尚未运行', PREPARING_DATA: '正在适配数据', BUILDING_PROVIDER: '正在校验研究数据',
  COMPUTING_ALPHA158: '正在计算 Alpha158', TRAINING_LIGHTGBM: '正在训练 LightGBM',
  BACKTESTING: '正在计算样本外回测', COMPLETE: '固定基线已归档', FAILED: '基线运行失败',
}
const number = (value: number | null | undefined, percent = false) => value == null || !Number.isFinite(value)
  ? '—' : percent ? `${(value * 100).toFixed(2)}%` : value.toFixed(4)

export function QlibBaseline() {
  const { token } = theme.useToken()
  const [data, setData] = useState<QlibBaselinePublication | null>(null)
  const [error, setError] = useState<string | null>(null)
  const active = useRef(true)
  const inFlight = useRef(false)
  const refresh = useCallback(async () => {
      if (inFlight.current) return
      inFlight.current = true
      try {
        const result = await loadQlibBaseline()
        if (result.schema_version !== 'qtrade.qlib-baseline.v1' || result.external_order_transmission !== false
          || result.live_trading_enabled !== false || !(result.state in stages)
          || (result.state === 'COMPLETE' && (!result.run?.persisted || result.run.feature_count !== 158))) {
          throw new Error('Qlib 结果契约不匹配')
        }
        if (active.current) { setData(result); setError(null) }
      } catch (reason) {
        if (active.current) { setData(null); setError(reason instanceof Error ? reason.message : '读取失败') }
      } finally { inFlight.current = false }
  }, [])
  useEffect(() => {
    active.current = true
    const initial = window.setTimeout(() => void refresh(), 0)
    const timer = window.setInterval(() => void refresh(), 60_000)
    return () => { active.current = false; window.clearTimeout(initial); window.clearInterval(timer) }
  }, [refresh])
  const run = data?.run
  return <Card title="Qlib · Alpha158 + LightGBM 固定基线" extra={<Button onClick={() => void refresh()}>刷新 Qlib 结果</Button>}>
    <Space orientation="vertical" style={{ width: '100%' }}>
      <Alert showIcon type={error || data?.state === 'FAILED' ? 'error' : data?.publication_status === 'STALE' ? 'warning' : 'info'}
        title={error ? `Qlib 发布不可用：${error}` : data ? `${stages[data.state]}${data.publication_status === 'STALE' ? ' · 发布已过期' : ''}` : '正在读取 Qlib 研究结果'}
        description={data ? `数据适配 ${data.progress.completed ?? 0}/${data.progress.target ?? 1000} 股，排除/失败 ${data.progress.failed ?? 0} 股；发布于 ${data.published_at}。` : '读取失败时不显示旧的成功状态或默认收益。'} />
      {run && <>
        <Space wrap><Tag color="blue">正式不可变实验</Tag><Tag color="orange">非 PIT · 存在幸存者偏差</Tag><Tag>事后样本外诊断</Tag>
          <Typography.Text>数据截至 {run.data_end}；最近应有完整交易日 {data?.expected_complete_day}。{data?.result_data_status === 'HISTORICAL' ? '这是历史固定结果，尚未重跑到最新数据。' : '结果覆盖当前完整交易日；新行情不会改写此实验。'}</Typography.Text></Space>
        <Descriptions size="small" column={{ xs: 1, md: 2 }} bordered items={[
          { key: 'universe', label: '研究数据', children: `${run.symbols}/${run.requested_symbols} 股 · ${run.data_rows.toLocaleString()} 根日线 · ${run.feature_count} 个特征` },
          { key: 'train', label: '训练区间', children: run.segments.train.join(' 至 ') },
          { key: 'valid', label: '验证区间', children: run.segments.valid.join(' 至 ') },
          { key: 'test', label: '样本外区间', children: run.segments.test.join(' 至 ') },
          { key: 'signal', label: 'IC 有效标签区间', children: `${run.signal_evaluation.start} 至 ${run.signal_evaluation.end}；未来标签未成熟的末尾日期不计入 IC` },
          { key: 'execution', label: '组合与成交', children: 'Top50，每日最多替换5只；下一交易日开盘，至少持有2个交易步；100股整手，科创板不买入' },
          { key: 'cost', label: '成本假设', children: `买入 ${(run.cost_assumptions.open_cost * 10000).toFixed(1)} bp / 卖出 ${(run.cost_assumptions.close_cost * 10000).toFixed(1)} bp，最低 ${run.cost_assumptions.min_cost} 元；含固定滑点近似` },
          { key: 'constraints', label: '成交约束', children: '缺失行情不成交；保守4.5%方向性开盘跳空限制；前一交易日成交量的1%容量上限' },
        ]} />
        <Space wrap size="large">
          <Statistic title="IC" value={run.metrics.ic ?? '—'} formatter={() => number(run.metrics.ic)} />
          <Statistic title="RankIC" value={run.metrics.rank_ic ?? '—'} formatter={() => number(run.metrics.rank_ic)} />
          <Statistic title="成本后累计收益" value={number(run.metrics.total_return_net, true)} />
          <Statistic title="成本后 Sharpe" value={run.metrics.sharpe_net ?? '—'} formatter={() => number(run.metrics.sharpe_net)} />
          <Statistic title="最大回撤" value={number(run.metrics.max_drawdown, true)} />
          <Statistic title="日均双边换手" value={number(run.metrics.mean_daily_turnover, true)} />
        </Space>
        <Suspense fallback={<div style={{ height: 360 }}>正在加载净值曲线</div>}><ChartRenderer style={{ height: 360 }} option={{
          animation: false, textStyle: { color: token.colorText }, tooltip: { trigger: 'axis' },
          legend: { data: ['成本后净值', '沪深300', '回撤'], textStyle: { color: token.colorText } },
          grid: { left: 64, right: 64, bottom: 50 },
          xAxis: { type: 'category', data: run.curve.map((point) => point.date), axisLabel: { color: token.colorTextSecondary } },
          yAxis: [
            { type: 'value', scale: true, axisLabel: { color: token.colorTextSecondary }, splitLine: { lineStyle: { color: token.colorSplit } } },
            { type: 'value', max: 0, axisLabel: { color: token.colorTextSecondary, formatter: (value: number) => `${(value * 100).toFixed(0)}%` }, splitLine: { show: false } },
          ],
          series: [
            { name: '成本后净值', type: 'line', showSymbol: false, data: run.curve.map((point) => point.equity) },
            { name: '沪深300', type: 'line', showSymbol: false, data: run.curve.map((point) => point.benchmark_equity) },
            { name: '回撤', type: 'line', yAxisIndex: 1, showSymbol: false, data: run.curve.map((point) => point.drawdown), areaStyle: { opacity: 0.1 } },
          ],
        }} /></Suspense>
        <Alert showIcon type="warning" title="当前结论：研究诊断，不可晋级" description="当前股票池是在历史测试结束后筛选，存在幸存者和流动性筛选偏差；历史 ST、精确涨跌停和停牌原因尚未完整认证。已建模的保守限制不能替代逐日交易规则与公司行为核验。" />
        <Table size="small" rowKey="name" pagination={false} dataSource={[
          { name: '实验 ID', value: run.run_id }, { name: '代码提交', value: run.source_revision },
          { name: '产物清单 SHA256', value: data?.artifact_manifest_sha256 ?? '—' },
          { name: '结果 SHA256', value: run.evidence_sha256 },
        ]} columns={[{ title: '审计项', dataIndex: 'name' }, { title: '内容', dataIndex: 'value', render: (value: string) => <Typography.Text copyable style={{ wordBreak: 'break-all' }}>{value}</Typography.Text> }]} />
        <JsonExportButton filename={`${run.run_id}.json`} value={data} />
      </>}
    </Space>
  </Card>
}

