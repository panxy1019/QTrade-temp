import { Empty } from 'antd'
import { lazy, Suspense } from 'react'
import type { HistoricalBarSeries } from '../types'

const ReactECharts = lazy(() => import('echarts-for-react'))

export function MarketHistoryChart({ series }: { series: HistoricalBarSeries | null }) {
  if (!series?.bars.length) {
    return <Empty description="暂无校验通过的历史 K 线" style={{ padding: 72 }} />
  }
  const days = series.bars.map((bar) => bar.trading_day)
  const prices = series.bars.map((bar) => [bar.open, bar.close, bar.low, bar.high].map(Number))
  const volume = series.bars.map((bar) => Number(bar.volume))
  return <Suspense fallback={<div style={{ height: 430 }} />}><ReactECharts style={{ height: 430 }} option={{
    backgroundColor: 'transparent', animation: false,
    legend: { data: ['日 K', '成交量'] },
    tooltip: { trigger: 'axis', axisPointer: { type: 'cross' } },
    axisPointer: { link: [{ xAxisIndex: 'all' }] },
    grid: [{ left: 64, right: 24, top: 45, height: '60%' }, { left: 64, right: 24, top: '76%', height: '15%' }],
    xAxis: [
      { type: 'category', data: days, boundaryGap: true, axisLine: { onZero: false }, axisLabel: { hideOverlap: true }, min: 'dataMin', max: 'dataMax' },
      { type: 'category', gridIndex: 1, data: days, boundaryGap: true, axisLabel: { show: false }, axisTick: { show: false }, min: 'dataMin', max: 'dataMax' },
    ],
    yAxis: [{ scale: true, splitArea: { show: true } }, { gridIndex: 1, scale: true, splitNumber: 2 }],
    dataZoom: [{ type: 'inside', xAxisIndex: [0, 1], start: 65, end: 100 }, { show: true, xAxisIndex: [0, 1], bottom: 4, start: 65, end: 100 }],
    series: [
      { name: '日 K', type: 'candlestick', data: prices, itemStyle: { color: '#ef4444', color0: '#22c55e', borderColor: '#ef4444', borderColor0: '#22c55e' } },
      { name: '成交量', type: 'bar', xAxisIndex: 1, yAxisIndex: 1, data: volume, itemStyle: { color: '#4f8cff' } },
    ],
  }} /></Suspense>
}
