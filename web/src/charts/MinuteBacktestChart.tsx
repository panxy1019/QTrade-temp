import { Empty } from 'antd'
import { lazy, Suspense } from 'react'
import type { MinuteResearchReport } from '../types'

const ChartRenderer = lazy(() => import('./ChartRenderer'))

export function MinuteBacktestChart({ report }: { report: MinuteResearchReport | null }) {
  if (!report?.curve.length) {
    return <Empty description="暂无已验证分钟回测曲线" style={{ padding: 72 }} />
  }
  const timestamps = report.curve.map((row) => row.timestamp.slice(5, 16).replace('T', ' '))
  return <Suspense fallback={<div style={{ height: 360 }} />}><ChartRenderer style={{ height: 360 }} option={{
    backgroundColor: 'transparent', animation: false,
    legend: { data: ['等权全暴露基准', '分钟风险修正'] },
    tooltip: { trigger: 'axis' },
    grid: { left: 64, right: 24, top: 45, bottom: 55 },
    xAxis: { type: 'category', data: timestamps, boundaryGap: false, axisLabel: { hideOverlap: true } },
    yAxis: { type: 'value', scale: true },
    dataZoom: [{ type: 'inside', start: 0, end: 100 }, { show: true, bottom: 4, start: 0, end: 100 }],
    series: [
      { name: '等权全暴露基准', type: 'line', showSymbol: false, data: report.curve.map((row) => row.base_equity), lineStyle: { color: '#94a3b8' } },
      { name: '分钟风险修正', type: 'line', showSymbol: false, data: report.curve.map((row) => row.overlay_equity), lineStyle: { color: '#4f8cff', width: 2 } },
    ],
  }} /></Suspense>
}
