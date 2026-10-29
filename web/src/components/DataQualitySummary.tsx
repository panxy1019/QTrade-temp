import { Alert, Button, Card, Descriptions, Space, Table, Tag, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { loadDataQuality } from '../api/client'

export interface DataQualityResponse {
  live_trading_enabled: false
  read_only: true
  stale: boolean
  snapshot_age_seconds: number
  source: { commit_sha: string; sha256: string }
  publication: {
    observed_at: string
    execution_admitted: false
    production_admission_effect: 'NONE'
    policy_status: string
    case_counts: Record<string, number>
    source_issues: { symbol: string | null; reason: string }[]
    recent_status: {
      available: boolean; observed_at?: string; window_start?: string; window_end?: string
      content_verified_symbols: number
      progress?: { completed_symbols: number; scope_symbols: number; unresolved_symbols: number }
    }
    cases: {
      symbol: string; frequency: string; trading_day: string; classification: string
      coverage: string; market_state: string; original_task_state: string
      reason: string; original_error: string | null; source_observed_at: string | null
      retry_recommendation: string; proposed_recheck_at: string | null
    }[]
  }
}

const labels: Record<string, string> = {
  COMPLETE: '行情窗口完整', SUSPENDED: '逐日停牌佐证', DATA_GAP: '行情缺口', UNKNOWN: '原因未知',
}
function clock(value?: string | null) {
  return value ? new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai',
    year: 'numeric', month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit',
    hour12: false }).format(new Date(value)) : '未知'
}

export function DataQualitySummary() {
  const [data, setData] = useState<DataQualityResponse | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [revision, setRevision] = useState(0)
  useEffect(() => {
    let cancelled = false
    const refresh = async () => {
      try {
        const value = await loadDataQuality()
        if (value.live_trading_enabled !== false || value.read_only !== true
          || value.publication.execution_admitted !== false
          || value.publication.production_admission_effect !== 'NONE') {
          throw new Error('质量摘要权限声明不符')
        }
        if (!cancelled) { setData(value); setError(null) }
      } catch (reason) {
        if (!cancelled) { setData(null); setError(reason instanceof Error ? reason.message : '来源不可用') }
      }
    }
    void refresh()
    const timer = window.setInterval(() => void refresh(), 60_000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [revision])
  const value = data?.publication
  const recent = value?.recent_status
  return <Card title="分级数据质量与近期状态" extra={<Button onClick={() => setRevision((r) => r + 1)}>刷新质量摘要</Button>}>
    <Space orientation="vertical" style={{ width: '100%' }}>
      <Alert showIcon type={error || data?.stale ? 'warning' : 'info'}
        title={error ? `独立质量摘要暂不可用：${error}` : data?.stale ? '质量摘要已过期，请核对来源时刻' : '按证券、频率、交易日和用途隔离缺口'}
        description="质量摘要为只读诊断，不改变生产准入。日频研究使用合格日线子集；分钟缺口保留 PARTIAL。模型仍须通过原输入验证，PAPER 遇未知交易约束继续阻断。此摘要失败不会隐藏下方行情目录。" />
      {value && <>
        <Descriptions size="small" column={{ xs: 1, md: 3 }} items={[
          { key: 'window', label: '近期已闭市状态窗口', children: recent?.available ? `${recent.window_start} → ${recent.window_end}` : '未取得' },
          { key: 'observed', label: '状态实际获取时刻（上海）', children: clock(recent?.observed_at) },
          { key: 'coverage', label: '状态队列覆盖', children: recent?.progress ? `${recent.progress.completed_symbols} / ${recent.progress.scope_symbols}；剩余 ${recent.progress.unresolved_symbols}` : '未取得' },
          { key: 'verified', label: '本摘要校验状态记录', children: recent?.content_verified_symbols ?? 0 },
          { key: 'built', label: '摘要构建时刻（上海）', children: clock(value.observed_at) },
          { key: 'policy', label: '准入变更', children: '诊断已接入；生产规则变更仍需审批' },
        ]} />
        <Space wrap>{Object.entries(value.case_counts).map(([name, count]) => <Tag key={name}>{labels[name] ?? name}：{count}</Tag>)}</Space>
        <Typography.Text type="secondary">以下分类只覆盖有失败记录的证券日任务，不是全市场统计。停牌日期须有逐日状态佐证，公告起始日不会自动延伸成整个停牌区间；历史可得时间未认证，事后补查不修改过去决策。</Typography.Text>
        <Table size="small" rowKey={(r) => `${r.symbol}-${r.frequency}-${r.trading_day}`}
          dataSource={value.cases} pagination={{ pageSize: 10 }} scroll={{ x: 850 }} columns={[
            { title: '证券', dataIndex: 'symbol' },
            { title: '频率', dataIndex: 'frequency', render: (v) => v === 'day' ? '日线' : '五分钟' },
            { title: '交易日', dataIndex: 'trading_day' },
            { title: '缺口分类', dataIndex: 'classification', render: (v) => <Tag>{labels[v] ?? v}</Tag> },
            { title: '行情覆盖', dataIndex: 'coverage' },
            { title: '原任务状态', dataIndex: 'original_task_state' },
            { title: '实际状态获取时刻（上海）', dataIndex: 'source_observed_at', render: clock },
          ]} expandable={{ expandedRowRender: (row) => <Space orientation="vertical">
            <Typography.Text>分类原因：{row.reason}；原始失败：{row.original_error ?? '无'}。原失败记录保留。</Typography.Text>
            <Typography.Text>{row.retry_recommendation === 'DEFER_PENDING_APPROVAL'
              ? `建议暂缓同日K线重抓并复查状态；待审批，建议复查时刻 ${clock(row.proposed_recheck_at)}`
              : '保持现有重试；证据不足时不认定停牌或复牌。'}</Typography.Text>
          </Space> }} />
        {value.source_issues.length > 0 && <Alert type="warning" showIcon title={`${value.source_issues.length} 项状态证据已隔离，其他证券继续展示`} />}
        <Typography.Text type="secondary">990股近期研究状态与1500证券目标池范围不同。完整历史PIT、退市行情、复权和精确交易限制仍为独立长期任务。公开版本 {data?.source.commit_sha.slice(0, 12)}。</Typography.Text>
      </>}
    </Space>
  </Card>
}
