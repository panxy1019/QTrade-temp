import { Empty } from 'antd'
import { lazy, Suspense } from 'react'
import type { MinuteBarSeries } from '../types'

const ChartRenderer = lazy(() => import('./ChartRenderer'))

export function MinuteHistoryChart({ series }: { series: MinuteBarSeries | null }) {
  if (!series?.bars.length) {
    return <Empty description="暂无校验通过的历史分时线" style={{ padding: 72 }} />
  }
  const timestamps = series.bars.map((bar) => bar.timestamp.slice(5, 16).replace('T', ' '))
  const close = series.bars.map((bar) => Number(bar.close))
  const volume = series.bars.map((bar) => Number(bar.volume))
  const closeLabel = series.interval === '5m' ? '5 分钟收盘' : '1 分钟收盘'
  return <Suspense fallback={<div style={{ height: 430 }} />}><ChartRenderer style={{ height: 430 }} option={{
    backgroundColor: 'transparent', animation: false,
    legend: { data: [closeLabel, '成交量'] },
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [{ left: 64, right: 24, top: 45, height: '60%' }, { left: 64, right: 24, top: '76%', height: '15%' }],
    xAxis: [
      { type: 'category', data: timestamps, boundaryGap: false, axisLabel: { hideOverlap: true }, min: 'dataMin', max: 'dataMax' },
      { type: 'category', gridIndex: 1, data: timestamps, boundaryGap: true, axisLabel: { show: false }, axisTick: { show: false }, min: 'dataMin', max: 'dataMax' },
    ],
    yAxis: [{ scale: true, splitArea: { show: true } }, { gridIndex: 1, scale: true, splitNumber: 2 }],
    dataZoom: [{ type: 'inside', xAxisIndex: [0, 1], start: 80, end: 100 }, { show: true, xAxisIndex: [0, 1], bottom: 4, start: 80, end: 100 }],
    series: [
      { name: closeLabel, type: 'line', data: close, showSymbol: false, lineStyle: { width: 1.5, color: '#4f8cff' }, areaStyle: { opacity: 0.08 } },
      { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: volume, itemStyle: { color: '#64748b' } },
    ],
  }} /></Suspense>
}
