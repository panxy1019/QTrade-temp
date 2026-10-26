import { useCallback, useEffect, useRef, useState } from 'react'
import { Alert, Button, Card, Descriptions, Modal, Select, Space, Table, Typography } from 'antd'
import { workflowGet, workflowPost } from '../research/qlibWorkflow'

interface Version {
  version_id: string; actual_end: string; admitted_at: string; is_head: boolean
  current_symbols: number; historical_symbols: number; blocked_symbols: number
}
interface Receipt { request_id: string; state: string; at: string; version_id: string; error_code?: string }
export interface DataCatalogResponse {
  publication_available: boolean; worker_fresh: boolean
  catalog: null | { head_version_id: string | null; head_event_id: string | null
    versions: Version[]; total_versions: number; versions_truncated: boolean; receipts: Receipt[] }
}
const reasons = [
  { value: 'DATA_QUALITY_REVIEW', label: '数据质量复核' },
  { value: 'PROVIDER_REGRESSION', label: 'Provider 回归问题' },
  { value: 'RESEARCH_REPRODUCTION', label: '研究复现' },
]
interface Review {
  version: Version
  command: { request_id: string; version_id: string; expected_head: string
    expected_event_id: string; reason: string }
}

export function DataVersionsPanel({ authenticated, ready, expectedHead, refreshModels }: {
  authenticated: boolean; ready: boolean; expectedHead?: string; refreshModels: () => Promise<void>
}) {
  const [value, setValue] = useState<DataCatalogResponse | null>(null)
  const [review, setReview] = useState<Review | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [notice, setNotice] = useState<string | null>(null)
  const sequence = useRef(0)
  const refresh = useCallback(async () => {
    const id = ++sequence.current
    try {
      const result = await workflowGet<DataCatalogResponse>('/online/data-versions')
      if (id === sequence.current) { setValue(result); setError(null) }
    } catch (reason) {
      if (id === sequence.current) { setValue(null); setError(String(reason)) }
    }
  }, [])
  const invalidate = useCallback(() => { sequence.current++ }, [])
  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0)
    const timer = window.setInterval(() => void refresh(), 15000)
    return () => { invalidate(); window.clearTimeout(initial); window.clearInterval(timer) }
  }, [refresh, invalidate])
  const catalog = value?.catalog
  const available = !!(ready && authenticated && value?.publication_available && value.worker_fresh
    && catalog?.head_version_id && catalog.head_event_id && expectedHead === catalog.head_version_id)
  const changed = !!review && (review.command.expected_head !== catalog?.head_version_id
    || review.command.expected_event_id !== catalog?.head_event_id
    || !catalog.versions.some(v => v.version_id === review.command.version_id && !v.is_head))
  const choose = (version: Version) => {
    if (!available || !catalog?.head_version_id || !catalog.head_event_id || version.is_head) return
    setReview({ version, command: { request_id: crypto.randomUUID().replaceAll('-', ''),
      version_id: version.version_id, expected_head: catalog.head_version_id,
      expected_event_id: catalog.head_event_id, reason: 'DATA_QUALITY_REVIEW' } })
  }
  const submit = async () => {
    if (!review || !available || changed || busy) return
    setBusy(true)
    try {
      const result = await workflowPost<{ state: string; error_code?: string }>('/online/data-rollback', review.command)
      setNotice(result.state === 'QUEUED'
        ? `数据回退已排队：${review.command.request_id}。当前版本尚未确认更改，以集群回执为准。`
        : `数据回退回执：${result.state} ${result.error_code ?? ''}`)
      setReview(null)
      await Promise.all([refresh(), refreshModels()])
    } catch (reason) { setNotice(String(reason)) }
    finally { setBusy(false) }
  }
  return <Card title="数据版本目录与回退" extra={<Button onClick={() => void refresh()}>刷新数据版本</Button>}>
    <Typography.Paragraph>目录展示清单身份，回退前集群会完整检查 Provider 文件。历史 PIT、复权与交易约束限制继续保留；此操作不授予 PAPER/SHADOW 执行权限，实盘保持关闭。</Typography.Paragraph>
    {error && <Alert type="error" showIcon title={error} />}
    {notice && <Alert type="info" showIcon title={notice} />}
    {!available && <Alert type="warning" showIcon title="数据回退入口暂不可用" description="需要登录、有效控制心跳，以及模型目录与数据目录的当前版本一致。数据过期不会阻止选择已验收版本来恢复；预测仍需通过最新数据门禁。" />}
    {catalog && <Descriptions column={1} items={[
      { key: 'head', label: '当前数据版本', children: <Typography.Text copyable>{catalog.head_version_id ?? '尚无版本'}</Typography.Text> },
      { key: 'event', label: '当前事件身份', children: <Typography.Text copyable>{catalog.head_event_id ?? '尚无事件'}</Typography.Text> },
      { key: 'count', label: '目录范围', children: `${catalog.versions.length} / ${catalog.total_versions} 个版本${catalog.versions_truncated ? '（只展示最近版本）' : ''}` },
    ]} />}
    <Table<Version> rowKey="version_id" dataSource={catalog?.versions ?? []} pagination={{ pageSize: 5 }} scroll={{ x: 800 }} columns={[
      { title: '数据截止', dataIndex: 'actual_end' },
      { title: '版本', render: (_, row) => <Typography.Text copyable={{ text: row.version_id }}>{row.version_id.slice(0, 16)}</Typography.Text> },
      { title: '当日合格 / 历史保留 / 缺口', render: (_, row) => `${row.current_symbols} / ${row.historical_symbols} / ${row.blocked_symbols}` },
      { title: '操作', render: (_, row) => row.is_head ? '当前版本' : <Button disabled={!available || busy} onClick={() => choose(row)}>审阅数据回退</Button> },
    ]} />
    <Typography.Title level={5}>数据回退回执</Typography.Title>
    <Table<Receipt> rowKey="request_id" dataSource={catalog?.receipts ?? []} pagination={{ pageSize: 5 }} scroll={{ x: 800 }} columns={[
      { title: '时间', dataIndex: 'at' }, { title: '请求', dataIndex: 'request_id' },
      { title: '结果', dataIndex: 'state' }, { title: '拒绝原因', dataIndex: 'error_code' },
    ]} />
    <Modal title="审阅不可变数据版本回退" open={!!review} onCancel={() => !busy && setReview(null)}
      onOk={() => void submit()} okText="批准数据回退" cancelText="取消" confirmLoading={busy}
      okButtonProps={{ disabled: !available || changed }}>
      {review && <Space orientation="vertical" style={{ width: '100%' }}>
        <Typography.Text>目标数据截止：{review.version.actual_end}</Typography.Text>
        <Typography.Text>目标版本：{review.command.version_id}</Typography.Text>
        <Typography.Text>当前版本：{review.command.expected_head}</Typography.Text>
        <Typography.Text>当前事件：{review.command.expected_event_id}</Typography.Text>
        <label>回退原因 <Select aria-label="回退原因" disabled={busy} value={review.command.reason} options={reasons} style={{ width: 220 }}
          onChange={reason => setReview(old => old ? { ...old, command: { ...old.command, reason,
            request_id: crypto.randomUUID().replaceAll('-', '') } } : null)} /></label>
        <Alert type="warning" showIcon title="旧数据不会恢复为最新可用预测" description="回退会产生新的事件身份；原预测与信号需要重新核验。过期数据会阻止当前研究预测，历史结果仅供追溯。不会激活模型、创建模拟交易或开启实盘。" />
        {changed && <Alert type="error" title="数据版本或事件已变化，请关闭后重新审阅" />}
      </Space>}
    </Modal>
  </Card>
}
