import { Empty } from 'antd'
import { lazy, Suspense } from 'react'
import type { ReservedTailDiagnosticReport } from '../types'
import { formatChinaMinuteTimestamp } from './formatChinaMinuteTimestamp'

const ChartRenderer = lazy(() => import('./ChartRenderer'))

export function ReservedTailChart({ report }: { report: ReservedTailDiagnosticReport | null }) {
  if (!report?.curve.length) {
    return <Empty description="暂无已验证的尾部诊断曲线" style={{ padding: 72 }} />
  }
  const timestamps = report.curve.map((row) => formatChinaMinuteTimestamp(row.timestamp))
  return <Suspense fallback={<div style={{ height: 360 }} />}><ChartRenderer style={{ height: 360 }} option={{
    backgroundColor: 'transparent', animation: false,
    legend: { data: ['全暴露基准', '预注册候选', '原低暴露对照'] },
    tooltip: { trigger: 'axis' },
    grid: { left: 64, right: 24, top: 45, bottom: 55 },
    xAxis: { type: 'category', data: timestamps, boundaryGap: false, axisLabel: { hideOverlap: true } },
    yAxis: { type: 'value', scale: true },
    dataZoom: [{ type: 'inside', start: 0, end: 100 }, { show: true, bottom: 4, start: 0, end: 100 }],
    series: [
      { name: '全暴露基准', type: 'line', showSymbol: false, data: report.curve.map((row) => row.base_equity), lineStyle: { color: '#94a3b8' } },
      { name: '预注册候选', type: 'line', showSymbol: false, data: report.curve.map((row) => row.candidate_equity), lineStyle: { color: '#f97316', width: 2 } },
      { name: '原低暴露对照', type: 'line', showSymbol: false, data: report.curve.map((row) => row.comparator_equity), lineStyle: { color: '#4f8cff', width: 2 } },
    ],
  }} /></Suspense>
}
