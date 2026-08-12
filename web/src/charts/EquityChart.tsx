import { Empty } from 'antd'

export function EquityChart({ dark }: { dark: boolean }) {
  void dark
  return <Empty description="暂无经校验的净值与基准数据" style={{ padding: 72 }} />
}
