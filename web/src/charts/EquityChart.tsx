import { Empty } from 'antd'
import ReactECharts from 'echarts-for-react'
import type { BacktestAnalytics } from '../types'

export function EquityChart({ dark, analytics }: { dark: boolean; analytics?: BacktestAnalytics | null }) {
  if (!analytics?.curve.length) return <Empty description="暂无经校验的净值与回撤数据" style={{ padding: 72 }} />
  const initial = Number(analytics.curve[0].equity)
  return <ReactECharts style={{ height: 380 }} theme={dark ? 'dark' : undefined} option={{
    backgroundColor: 'transparent', animation: false, tooltip: { trigger: 'axis' }, legend: { data: ['累计收益', '回撤'] },
    grid: { left: 64, right: 64, top: 48, bottom: 58 },
    xAxis: { type: 'category', data: analytics.curve.map((point) => point.session), axisLabel: { hideOverlap: true } },
    yAxis: [
      { type: 'value', axisLabel: { formatter: (value: number) => `${(value * 100).toFixed(0)}%` } },
      { type: 'value', max: 0, min: -0.4, axisLabel: { formatter: (value: number) => `${(value * 100).toFixed(0)}%` } },
    ],
    series: [
      { name: '累计收益', type: 'line', showSymbol: false, data: analytics.curve.map((point) => Number(point.equity) / initial - 1), lineStyle: { width: 2 } },
      { name: '回撤', type: 'line', yAxisIndex: 1, showSymbol: false, data: analytics.curve.map((point) => Number(point.drawdown)), areaStyle: { opacity: 0.12 }, lineStyle: { width: 1 } },
    ],
  }} />
}
