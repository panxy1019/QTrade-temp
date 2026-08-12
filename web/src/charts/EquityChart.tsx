import ReactECharts from 'echarts-for-react'

export function EquityChart({ dark }: { dark: boolean }) {
  const labels = ['08-05', '08-06', '08-07', '08-08', '08-11', '08-12']
  const option = {
    backgroundColor: 'transparent',
    grid: { left: 48, right: 20, top: 38, bottom: 34 },
    tooltip: { trigger: 'axis' },
    legend: { data: ['组合净值', '沪深300'], textStyle: { color: dark ? '#a8b3c7' : '#475569' } },
    xAxis: { type: 'category', data: labels, boundaryGap: false },
    yAxis: { type: 'value', scale: true },
    series: [
      { name: '组合净值', type: 'line', smooth: true, data: [1, 1.006, 1.003, 1.014, 1.012, 1.021], areaStyle: { opacity: 0.12 } },
      { name: '沪深300', type: 'line', data: [1, 1.002, 0.997, 1.005, 1.001, 1.008], lineStyle: { type: 'dashed' } },
    ],
  }
  return <ReactECharts option={option} style={{ height: 320 }} />
}
