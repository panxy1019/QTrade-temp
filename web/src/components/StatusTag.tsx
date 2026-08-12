import { Tag } from 'antd'

const colors: Record<string, string> = {
  NORMAL: 'success',
  HEALTHY: 'success',
  CONNECTED: 'success',
  OK: 'success',
  PAPER: 'blue',
  SHADOW: 'purple',
  BACKTEST: 'cyan',
  WATCH: 'warning',
  DEGRADED: 'warning',
  PAUSED: 'warning',
  HALTED: 'error',
  FAILED: 'error',
  DISCONNECTED: 'default',
  UNKNOWN: 'default',
}

const labels: Record<string, string> = {
  PAPER: '模拟交易',
  SHADOW: '影子交易',
  BACKTEST: '回测',
  NORMAL: '正常',
  PAUSED: '已暂停',
  REDUCE_ONLY: '仅减仓',
  HALTED: '已熔断',
  CONNECTED: '已连接',
  DISCONNECTED: '未连接',
  UNKNOWN: '未知',
}

export function StatusTag({ value, prominent = false }: { value: string; prominent?: boolean }) {
  return (
    <Tag color={colors[value] ?? 'default'} className={prominent ? 'mode-tag' : ''}>
      {labels[value] ?? value}
    </Tag>
  )
}
