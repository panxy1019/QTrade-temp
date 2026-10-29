import { useEffect, useState } from 'react'
import { Alert, Descriptions, Space } from 'antd'
import { workflowGet } from '../research/qlibWorkflow'
import { canonicalJson, sha256Hex } from '../research/strategyIdentity'
import { JsonExportButton } from './JsonExportButton'

export function FactorRegistrationReceipt({ jobId }: { jobId: string }) {
  const [value, setValue] = useState<Record<string, unknown> | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    const controller = new AbortController()
    void workflowGet<Record<string, unknown>>('/factor-strategies/jobs/' + encodeURIComponent(jobId), { signal: controller.signal })
      .then(async receipt => {
        const { evidence_sha256, ...body } = receipt
        if (receipt.schema_version !== 'qtrade.factor-strategy-registration.v1' || receipt.job_id !== jobId
          || receipt.state !== 'COMPLETE' || receipt.registration_only !== true || receipt.status !== 'EXPERIMENT'
          || receipt.role !== 'NONE' || ['backtest_implemented', 'paper_admission', 'model_activation',
            'live_trading_enabled', 'external_order_transmission'].some(key => receipt[key] !== false)
          || evidence_sha256 !== await sha256Hex(canonicalJson(body))) throw new Error('登记回执身份或权限校验失败')
        if (!controller.signal.aborted) { setValue(receipt); setError(null) }
      }).catch(reason => { if (!controller.signal.aborted) { setValue(null); setError(String(reason)) } })
    return () => controller.abort()
  }, [jobId])
  if (error) return <Alert type="error" title={error} />
  if (!value) return <Alert type="info" title="正在核验因子策略登记回执" />
  return <Space orientation="vertical" style={{ width: '100%' }}>
    <Alert type="info" title="研究合同已登记 · 未运行组合回测" description="EXPERIMENT / NONE；登记不授予模型激活、PAPER 或实盘权限。" />
    <Descriptions column={1} items={[
      { key: 'version', label: '策略版本', children: String(value.strategy_version) },
      { key: 'data', label: '数据版本', children: String(value.data_version_id) },
      { key: 'source', label: '执行代码', children: String(value.source_revision) },
      { key: 'clock', label: '实际登记时间', children: String(value.registered_at) },
    ]} />
    <JsonExportButton filename={`factor-registration-${jobId}.json`} value={value} label="导出因子登记回执" />
  </Space>
}
