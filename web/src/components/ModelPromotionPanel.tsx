import { useRef, useState } from 'react'
import { Alert, Button, Card, Descriptions, Space } from 'antd'
import { workflowPost } from '../research/qlibWorkflow'

export interface PromotionPolicy {
  policy_id: string
  policy: { mode: 'OFF' | 'ENABLED'; allowed_model_ids: string[]; allowed_models: string[] }
  enabled_at?: string
  actor?: string
}

export function ModelPromotionPanel({ value, authenticated, ready, refresh }: {
  value?: PromotionPolicy; authenticated: boolean; ready: boolean; refresh: () => Promise<void>
}) {
  const [busy, setBusy] = useState(false)
  const [notice, setNotice] = useState<string | null>(null)
  const pending = useRef<{ request_id: string; expected_policy_id: string; policy: PromotionPolicy['policy']; explicit_human_authorization: true } | null>(null)
  const change = async (mode: 'OFF' | 'ENABLED') => {
    if (!value || !ready || !authenticated || busy) return
    if (!pending.current || pending.current.expected_policy_id !== value.policy_id || pending.current.policy.mode !== mode) {
      pending.current = { request_id: crypto.randomUUID().replaceAll('-', ''), expected_policy_id: value.policy_id,
        policy: { mode, allowed_model_ids: [], allowed_models: mode === 'ENABLED' ? ['lightgbm'] : [] }, explicit_human_authorization: true }
    }
    setBusy(true)
    try {
      const result = await workflowPost<{ state: string }>('/online/model-promotion-policy', pending.current)
      setNotice(`政策${result.state === 'QUEUED' ? '已排队，等待集群回执' : result.state}`)
      pending.current = null
      await refresh()
    } catch (error) { setNotice(String(error)) }
    finally { setBusy(false) }
  }
  return <Card title="研究模型自动批准政策">
    <Descriptions column={1} items={[
      { key: 'mode', label: '当前政策', children: value ? value.policy.mode === 'ENABLED' ? '已启用' : '已关闭' : '尚未接入' },
      { key: 'scope', label: '允许模型', children: value ? [...value.policy.allowed_models, ...value.policy.allowed_model_ids].join('、') || '无' : '未提供' },
      { key: 'version', label: '政策版本', children: value?.policy_id ?? '未提供' },
      { key: 'actor', label: '授权记录', children: value?.actor ? `${value.actor} · ${value.enabled_at}` : '尚无启用记录' },
    ]} />
    <Alert type="info" showIcon title="日预测与候选替换分开记录" description="已批准 ACTIVE 在合格新日版本上自动预测。启用此政策后，唯一合格 LightGBM 候选通过当前版本验证、时钟与身份复核才可替换 ACTIVE；多个候选或检查失败均阻断。输入兼容验证不代表收益更优，政策不恢复或启动新的 PAPER 账户，也不开启实盘。" />
    <Space style={{ marginTop: 12 }}>
      <Button disabled={!value || !ready || !authenticated || busy || value.policy.mode === 'ENABLED'} loading={busy} onClick={() => void change('ENABLED')}>启用 LightGBM 自动批准</Button>
      <Button disabled={!value || !ready || !authenticated || busy || value.policy.mode === 'OFF'} onClick={() => void change('OFF')}>关闭自动批准</Button>
    </Space>
    {notice && <Alert style={{ marginTop: 12 }} type="info" title={notice} />}
  </Card>
}
