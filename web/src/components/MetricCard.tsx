import { Card, Statistic, Typography } from 'antd'

export function MetricCard({
  title,
  value,
  suffix,
  note,
}: {
  title: string
  value: string | number
  suffix?: string
  note?: string
}) {
  return (
    <Card className="metric-card" variant="borderless">
      <Statistic title={title} value={value} suffix={suffix} />
      {note && <Typography.Text type="secondary">{note}</Typography.Text>}
    </Card>
  )
}
