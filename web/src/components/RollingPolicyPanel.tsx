import { useState } from 'react'
import { Alert, Button, Card, Descriptions, Modal, Select, Space, Table, Typography } from 'antd'
import { workflowPost } from '../research/qlibWorkflow'

interface Policy {
  mode: 'OFF' | 'DAILY' | 'WEEKLY' | 'MONTHLY' | 'CUSTOM'
  custom_sessions: number | null; model: 'linear' | 'lightgbm'
  train_sessions: number; valid_sessions: number; test_sessions: number; seed: number
}
export interface RollingCatalog {
  mode: string; state: string; policy_id?: string; policy?: Policy; last_attempt_day?: string | null
  tasks?: Array<{ job_id: string; state: string; error_code?: string; plan: {
    data_end: string; segments: Record<string, [string, string]>
  } }>
  validation_tasks?: Array<{ job_id: string; model_id: string; state: string; error_code?: string }>
}
const modes = [{ value: 'OFF', label: '关闭自动重训' }, { value: 'DAILY', label: '每 1 个交易会话' }, { value: 'WEEKLY', label: '每 5 个交易会话' }, { value: 'MONTHLY', label: '每 20 个交易会话' }, { value: 'CUSTOM', label: '自定义会话间隔' }]

export function RollingPolicyPanel({ value, authenticated, ready, refresh }: {
  value?: RollingCatalog; authenticated: boolean; ready: boolean; refresh: () => Promise<void>
}) {
  const [draft, setDraft] = useState<{ request_id: string; expected_policy_id: string; policy: Policy } | null>(null)
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const available = ready && value?.state === 'CONTROL_READY' && !!value.policy && !!value.policy_id
  const changed = !!draft && draft.expected_policy_id !== value?.policy_id
  const change = (patch: Partial<Policy>) => setDraft(old => old ? { ...old, policy: { ...old.policy, ...patch } } : null)
  const submit = async () => {
    if (!draft || !available || !authenticated || changed) return
    setBusy(true)
    try {
      const result = await workflowPost<{ state: string }>('/online/training-policy', draft)
      setNotice(`训练政策${result.state === 'QUEUED' ? '已排队，等待集群回执' : result.state}。候选激活按独立批准政策执行。`)
      setDraft(null); await refresh()
    } catch (error) { setNotice(String(error)) }
    finally { setBusy(false) }
  }
  return <Card title="受控滚动训练" extra={<Button disabled={!available || !authenticated} onClick={() => value?.policy && value.policy_id && setDraft({ request_id: crypto.randomUUID().replaceAll('-', ''), expected_policy_id: value.policy_id, policy: structuredClone(value.policy) })}>配置训练政策</Button>}>
    <Typography.Paragraph>按真实交易会话安排新候选；数据就绪、标签成熟和资源门禁通过后才执行。预测优先，失败保留当前模型；候选激活由独立的人工或自动批准政策控制。</Typography.Paragraph>
    <Descriptions column={1} items={[
      { key: 'mode', label: '生效政策', children: modes.find(m => m.value === value?.mode)?.label ?? '尚未接入' },
      { key: 'model', label: '候选模型', children: value?.policy?.model ?? '未配置' },
      { key: 'day', label: '最近创建任务的数据日', children: value?.last_attempt_day ?? '尚无自动重训尝试' },
    ]} />
    {!!value?.validation_tasks?.length && <Table rowKey="job_id" dataSource={value.validation_tasks} pagination={{ pageSize: 5 }} columns={[
      { title: '候选输入验证', dataIndex: 'model_id', render: id => <Typography.Text copyable>{id.slice(0, 16)}</Typography.Text> },
      { title: '状态', dataIndex: 'state' }, { title: '诊断', dataIndex: 'error_code' },
    ]} />}
    {notice && <Alert type="info" showIcon title={notice} />}
    <Table rowKey="job_id" dataSource={value?.tasks ?? []} pagination={{ pageSize: 5 }} columns={[
      { title: '任务', dataIndex: 'job_id', render: id => <Typography.Text copyable>{id}</Typography.Text> },
      { title: '数据截止', render: (_, row) => row.plan.data_end },
      { title: '成熟测试区间', render: (_, row) => row.plan.segments.test.join(' 至 ') },
      { title: '状态', dataIndex: 'state' }, { title: '诊断', dataIndex: 'error_code' },
    ]} />
    <Modal title="审阅滚动训练政策" open={!!draft} onCancel={() => !busy && setDraft(null)} onOk={() => void submit()} okText="保存训练政策" confirmLoading={busy} okButtonProps={{ disabled: !available || !authenticated || changed }}>
      {draft && <Space orientation="vertical" style={{ width: '100%' }}>
        <label>训练频率 <Select aria-label="训练频率" value={draft.policy.mode} options={modes} style={{ width: 220 }} onChange={mode => change({ mode, custom_sessions: mode === 'CUSTOM' ? 10 : null })} /></label>
        {draft.policy.mode === 'CUSTOM' && <label>会话间隔 <Select aria-label="会话间隔" value={draft.policy.custom_sessions} options={[2, 5, 10, 20, 40, 60].map(value => ({ value, label: value }))} onChange={custom_sessions => change({ custom_sessions })} /></label>}
        <label>候选模型 <Select aria-label="候选模型" value={draft.policy.model} options={[{ value: 'linear', label: 'Ridge' }, { value: 'lightgbm', label: 'LightGBM' }]} onChange={model => change({ model })} /></label>
        {(['train_sessions', 'valid_sessions', 'test_sessions'] as const).map((key, i) => <label key={key}>{['训练会话数', '验证会话数', '测试会话数'][i]} <Select aria-label={['训练会话数', '验证会话数', '测试会话数'][i]} value={draft.policy[key]} options={[[252, 504, 756, 1260], [60, 126, 252], [20, 60, 126]][i].map(value => ({ value, label: value }))} onChange={value => change({ [key]: value })} /></label>)}
        <Typography.Text>固定随机种子 {draft.policy.seed}；两会话标签成熟与切分隔离。当前数据池仍存在幸存者偏差及 PIT、复权、交易约束限制。</Typography.Text>
        <Alert type="warning" showIcon title="仅创建研究候选，批准政策保持独立" description="关闭或更改训练政策将取消旧政策下尚未执行的滚动任务；已经运行的任务可完成归档。此配置本身不会更换当前模型或开启实盘。" />
        {changed && <Alert type="error" title="政策已变化，请关闭后重新审阅" />}
      </Space>}
    </Modal>
  </Card>
}
