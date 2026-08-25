const chinaMinuteFormatter = new Intl.DateTimeFormat('zh-CN', {
  timeZone: 'Asia/Shanghai',
  month: '2-digit',
  day: '2-digit',
  hour: '2-digit',
  minute: '2-digit',
  hour12: false,
})

export function formatChinaMinuteTimestamp(timestamp: string) {
  const parts = Object.fromEntries(
    chinaMinuteFormatter.formatToParts(new Date(timestamp)).map(({ type, value }) => [type, value]),
  )
  return `${parts.month}-${parts.day} ${parts.hour}:${parts.minute}`
}
