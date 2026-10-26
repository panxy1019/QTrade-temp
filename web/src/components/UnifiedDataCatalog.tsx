import { Alert, Button, Card, Descriptions, Input, Select, Space, Table, Tag, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { loadUnifiedSecurities, loadUnifiedSummary } from '../api/client'
import type { UnifiedSecurity, UnifiedSummary } from '../types'

const labels: Record<string, string> = {
  PLANNED: '计划采集', NOT_PLANNED: '未纳入计划', DOWNLOADED_UNVERIFIED: '已下载 · 未验证',
  CONTENT_COVERAGE_VERIFIED: '内容与覆盖通过', QUARANTINED: '隔离 · 待修复',
  SOURCE_RECONCILED: '来源对账通过', LIMITED_RESEARCH: '有限研究',
}

export function UnifiedDataCatalog() {
  const [summary, setSummary] = useState<UnifiedSummary | null>(null)
  const [rows, setRows] = useState<UnifiedSecurity[]>([])
  const [total, setTotal] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [frequency, setFrequency] = useState<'day' | '5min'>('day')
  const [quality, setQuality] = useState('')
  const [query, setQuery] = useState('')
  const [targetOnly, setTargetOnly] = useState(true)
  const [page, setPage] = useState(1)
  const [refresh, setRefresh] = useState(0)
  const [loading, setLoading] = useState(true)
  useEffect(() => {
    let cancelled = false
    const load = async () => {
      setLoading(true)
      try {
        const [info, result] = await Promise.all([
          loadUnifiedSummary(), loadUnifiedSecurities({
            query, frequency, quality, target_only: targetOnly, offset: (page - 1) * 25,
          }),
        ])
        if (info.live_trading_enabled !== false || result.live_trading_enabled !== false
          || info.master.universe_version !== result.universe_version) {
          throw new Error('证券池身份变化，请刷新重试')
        }
        if (!cancelled) {
          setSummary(info); setRows(result.rows); setTotal(result.total); setError(null)
        }
      } catch (reason) {
        if (!cancelled) {
          setError(reason instanceof Error ? reason.message : '统一目录不可用')
          setSummary(null); setRows([]); setTotal(0)
        }
      } finally { if (!cancelled) setLoading(false) }
    }
    void load()
    const timer = window.setInterval(() => void load(), 60_000)
    return () => { cancelled = true; window.clearInterval(timer) }
  }, [frequency, quality, query, targetOnly, page, refresh])
  const master = summary?.master
  return <Card title="统一证券观察池与数据覆盖" extra={<Button onClick={() => setRefresh((r) => r + 1)}>刷新统一目录</Button>}>
    <Space orientation="vertical" style={{ width: '100%' }}>
      <Alert showIcon type={error ? 'error' : 'info'}
        title={error ? `统一目录不可用：${error}` : master ? `观察目标 ${master.target_actual} / ${master.target_count} 只独立证券` : '正在读取统一证券目录'}
        description="当前观察池有幸存者和流动性筛选偏差，不能回填为历史 PIT 股票池。计划数量、下载数量、内容验收和来源对账分别统计；日线合格不依赖分钟覆盖。" />
      {master && <>
        <Descriptions size="small" column={{ xs: 1, md: 3 }} items={[
          { key: 'union', label: '旧清单实际去重', children: `${master.legacy_union_count}（活跃保留 ${master.legacy_active_retained}）` },
          { key: 'topup', label: '当前候选清单补足', children: master.top_up_count },
          { key: 'types', label: '目标分类', children: `股票 ${master.target_by_type.STOCK ?? 0} / ETF ${master.target_by_type.ETF ?? 0}` },
          { key: 'all', label: '主表身份（含非目标及历史停用）', children: master.master_count },
          { key: 'built', label: '目录构建时间', children: summary.built_at },
          { key: 'source', label: '主表源观测时间', children: master.vendor_master_observed_at ?? '未知' },
          { key: 'pit', label: '历史 PIT 认证', children: '0 · 尚未取得完整认证' },
          { key: 'version', label: '证券池版本', children: <Typography.Text copyable>{master.universe_version}</Typography.Text> },
        ]} />
        <Table size="small" rowKey="frequency" pagination={false} dataSource={(['day', '5min'] as const).map((freq) => ({
          frequency: freq, counts: master.frequency_counts[freq],
        }))} columns={[
          { title: '频率', dataIndex: 'frequency', render: (v) => v === 'day' ? '日线' : '五分钟' },
          { title: '目标池内验收分布', render: (_, row) => Object.entries(row.counts).map(([state, count]) => <Tag key={state}>{labels[state] ?? state}：{count}</Tag>) },
        ]} />
        <Typography.Text type="secondary">目录快照年龄 {Math.round(summary.snapshot_age_seconds)} 秒。目录时间不代表行情时间；内容验收尚未认证完整历史交易日历、成交额、复权时点和交易规则。Qlib 新版本及每日预测链路仍在开发。</Typography.Text>
      </>}
      <Space wrap>
        <Input.Search aria-label="查询统一证券" placeholder="证券代码或名称，回车查询" allowClear onSearch={(v) => { setQuery(v); setPage(1) }} style={{ width: 260 }} />
        <Select aria-label="统一证券频率" value={frequency} options={[{ value: 'day', label: '日线' }, { value: '5min', label: '五分钟' }]} onChange={(v) => { setFrequency(v); setPage(1) }} />
        <Select aria-label="统一证券质量" value={quality} style={{ width: 210 }} options={[{ value: '', label: '全部质量状态' }, ...Object.entries(labels).map(([value, label]) => ({ value, label }))]} onChange={(v) => { setQuality(v); setPage(1) }} />
        <Select aria-label="统一证券范围" value={targetOnly ? 'target' : 'all'} options={[{ value: 'target', label: '当前目标观察池' }, { value: 'all', label: '全部主表身份（含停用）' }]} onChange={(v) => { setTargetOnly(v === 'target'); setPage(1) }} />
      </Space>
      <Table<UnifiedSecurity> size="small" rowKey="instrument_id" dataSource={rows} loading={loading}
        pagination={{ current: page, pageSize: 25, total, showSizeChanger: false, onChange: setPage }} scroll={{ x: 1000 }}
        columns={[
          { title: '证券', render: (_, r) => `${r.instrument_id} ${r.name}` },
          { title: '类型', dataIndex: 'security_type', render: (v) => v === 'ETF' ? 'ETF' : '股票' },
          { title: '当前身份声明', dataIndex: 'status' },
          { title: '频率质量', render: (_, r) => <Tag>{labels[r.frequencies[frequency].state] ?? r.frequencies[frequency].state}</Tag> },
          { title: '已观测范围', render: (_, r) => `${r.frequencies[frequency].observed_start ?? '未取得'} → ${r.frequencies[frequency].observed_end ?? '未取得'}` },
          { title: 'Bars', render: (_, r) => r.frequencies[frequency].bar_count ?? 0 },
        ]}
        expandable={{ expandedRowRender: (row) => <Space orientation="vertical">
          <Typography.Text>来源声明上市：{row.listing_date ?? '未知'}；退市：{row.delisting_date ?? '未知'}；历史 available_at：未知。源日期声明尚未独立认证。</Typography.Text>
          <Typography.Text>旧清单：{row.legacy_sources.join('、') || '候选补足或非目标身份'}；身份问题：{row.issues.join('、') || '无已识别冲突'}；频率问题：{row.frequencies[frequency].issues.join('、') || '无已识别问题（不等于完整认证）'}</Typography.Text>
          {row.source.map((source, i) => <Typography.Text key={`${source.source_id}-${i}`} copyable>{source.source_id} · {source.sha256}</Typography.Text>)}
        </Space> }} />
    </Space>
  </Card>
}
