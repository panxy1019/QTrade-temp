import { DownloadOutlined } from '@ant-design/icons'
import { Button } from 'antd'

export function JsonExportButton({ filename, value, label = '导出 JSON' }: {
  filename: string
  value: unknown
  label?: string
}) {
  const download = () => {
    const blob = new Blob([JSON.stringify(value, null, 2)], { type: 'application/json;charset=utf-8' })
    const url = URL.createObjectURL(blob)
    const link = document.createElement('a')
    link.href = url
    link.download = filename
    link.click()
    URL.revokeObjectURL(url)
  }
  return <Button icon={<DownloadOutlined />} onClick={download} disabled={value === null || value === undefined}>{label}</Button>
}
