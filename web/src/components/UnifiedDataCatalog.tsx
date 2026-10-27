import { Alert, Button, Card, Descriptions, Input, Select, Space, Table, Tag, Typography } from 'antd'
import { useEffect, useState } from 'react'
import { loadUnifiedSecurities, loadUnifiedSummary } from '../api/client'
import type { UnifiedFrequency, UnifiedSecurity, UnifiedSummary } from '../types'

const labels: Record<string, string> = {
  PLANNED: '计划采集', NOT_PLANNED: '未纳入计划', DOWNLOADED_UNVERIFIED: '已下载 · 未验证',
  CONTENT_COVERAGE_VERIFIED: '内容与覆盖通过', QUARANTINED: '隔离 · 待修复',
  SOURCE_RECONCILED: '来源对账通过', LIMITED_RESEARCH: '有限研究',
  FAILED_RETAINING_LAST_SUCCESS: '新采集失败 · 保留旧成功',
}
const reasons: Record<string, string> = {
  SOURCE_TRANSPORT_ERROR: '来源连接失败', SOURCE_HTTP_ERROR: '来源 HTTP 错误',
  NO_COMPLETED_REQUESTED_BARS_REASON_UNKNOWN: '请求日没有完整行情，原因未认证',
  MINUTE_WINDOW_GAP_REASON_UNKNOWN: '分钟窗口有缺口，原因未认证',
  QUOTE_AMOUNT_UNAVAILABLE: '日线成交额未核验，等待补采',
  SAME_VENDOR_DAILY_QUOTE_MISMATCH: '日线与盘后快照不一致',
  PROCESS_INTERRUPTED: '采集中断，等待恢复',
}

function chinaClock(value: string | null | undefined) {
  if (!value) return '未取得'
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return value
  return new Intl.DateTimeFormat('zh-CN', { timeZone: 'Asia/Shanghai', year: 'numeric',
    month: '2-digit', day: '2-digit', hour: '2-digit', minute: '2-digit', hour12: false,
  }).format(new Date(value))
}

export function UnifiedDataCatalog() {
  const [summary, setSummary] = useState<UnifiedSummary | null>(null)
  const [rows, setRows] = useState<UnifiedSecurity[]>([])
  const [total, setTotal] = useState(0)
  const [error, setError] = useState<string | null>(null)
  const [frequency, setFrequency] = useState<'day' | '5min'>('day')
  const [coverage, setCoverage] = useState<'historical' | 'incremental'>('incremental')
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
            query, frequency, quality, coverage, target_only: targetOnly, offset: (page - 1) * 25,
          }),
        ])
        if (info.live_trading_enabled !== false || result.live_trading_enabled !== false
          || info.master.universe_version !== result.universe_version
          || info.evidence_sha256 !== result.catalog_sha256) {
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
  }, [frequency, coverage, quality, query, targetOnly, page, refresh])
  const master = summary?.master
  const cell = (row: UnifiedSecurity): UnifiedFrequency => coverage === 'historical'
    ? row.frequencies[frequency] : row.incremental?.[frequency] ?? {
      planned: row.in_target, state: 'PLANNED', issues: [], observed_start: null,
      observed_end: null, bar_count: 0,
    }
  const stageLabels: Record<string, string> = {
    requested: '请求（次）', downloaded: '下载成功（次）', parsed_symbols: '解析任务',
    validated_symbols: '内容初验任务', admitted_symbols: '接纳任务', admitted_rows: '接纳 Bars',
    same_vendor_endpoint_matched: '同源接口一致', independent_source_reconciled: '独立来源对账',
  }
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
          { key: 'built', label: '目录构建时间（上海）', children: chinaClock(summary.built_at) },
          { key: 'source', label: '主表源观测时间（上海）', children: chinaClock(master.vendor_master_observed_at) },
          { key: 'pit', label: '历史 PIT 认证', children: '0 · 尚未取得完整认证' },
          { key: 'version', label: '证券池版本', children: <Typography.Text copyable>{master.universe_version}</Typography.Text> },
        ]} />
        <Table size="small" rowKey="frequency" pagination={false} dataSource={(['day', '5min'] as const).map((freq) => ({
          frequency: freq, counts: master.frequency_counts[freq],
        }))} columns={[
          { title: '频率', dataIndex: 'frequency', render: (v) => v === 'day' ? '日线' : '五分钟' },
          { title: '长期历史覆盖 · 逐步回补', render: (_, row) => Object.entries(row.counts).map(([state, count]) => <Tag key={state}>{labels[state] ?? state}：{count}</Tag>) },
        ]} />
        {master.incremental_counts && <Table size="small" rowKey="frequency" pagination={false} dataSource={(['day', '5min'] as const).map((freq) => ({ frequency: freq, counts: master.incremental_counts![freq], freshness: summary.incremental_freshness?.[freq] }))} columns={[
          { title: '日常增量', dataIndex: 'frequency', render: (v) => v === 'day' ? '日线' : '五分钟' },
          { title: '当天采集状态', render: (_, row) => Object.entries(row.counts).map(([state, count]) => <Tag key={state}>{labels[state] ?? state}：{count}</Tag>) },
          { title: '时间齐备 / 目标', render: (_, row) => `${row.freshness?.ready_symbols ?? 0} / ${row.freshness?.required_symbols ?? master.target_actual} · ${row.freshness?.status ?? '未检查'}` },
          { title: '当前应覆盖至（上海）', render: (_, row) => chinaClock(row.freshness?.expected_end) },
        ]} />}
        {master.collection && <>
          <Typography.Text>统一调度：{master.collection.state}；进度更新 {chinaClock(master.collection.updated_at)}；已封存数据事件 {master.collection.data_ready_events}。事件表示新增数据已接纳，尚不代表 Qlib 版本或模型已就绪。</Typography.Text>
          <Space wrap>{Object.entries(master.collection.stage_counts).map(([stage, count]) => <Tag key={stage}>{stageLabels[stage] ?? stage}：{count}</Tag>)}</Space>
          <Typography.Text type="secondary">上方阶段计数属于本轮；日线与分钟分别算采集任务，不能相加视为独立证券数。同供应商两接口一致不等于独立来源对账。</Typography.Text>
        </>}
        <Typography.Text type="secondary">目录快照年龄 {Math.round(summary.snapshot_age_seconds)} 秒。目录时间不代表行情时间；内容验收尚未认证完整历史交易日历、成交额、复权时点和交易规则。Qlib 日版本与每日预测状态在“在线模型”中独立核对，采集成功不代表模型输入已就绪。</Typography.Text>
      </>}
      <Space wrap>
        <Input.Search aria-label="查询统一证券" placeholder="证券代码或名称，回车查询" allowClear onSearch={(v) => { setQuery(v); setPage(1) }} style={{ width: 260 }} />
        <Select aria-label="统一证券频率" value={frequency} options={[{ value: 'day', label: '日线' }, { value: '5min', label: '五分钟' }]} onChange={(v) => { setFrequency(v); setPage(1) }} />
        <Select aria-label="统一证券覆盖口径" value={coverage} options={[{ value: 'incremental', label: '日常增量（新源独立）' }, { value: 'historical', label: '长期历史（独立口径）' }]} onChange={(v) => { setCoverage(v); setPage(1) }} style={{ width: 220 }} />
        <Select aria-label="统一证券质量" value={quality} style={{ width: 210 }} options={[{ value: '', label: '全部质量状态' }, ...Object.entries(labels).map(([value, label]) => ({ value, label }))]} onChange={(v) => { setQuality(v); setPage(1) }} />
        <Select aria-label="统一证券范围" value={targetOnly ? 'target' : 'all'} options={[{ value: 'target', label: '当前目标观察池' }, { value: 'all', label: '全部主表身份（含停用）' }]} onChange={(v) => { setTargetOnly(v === 'target'); setPage(1) }} />
      </Space>
      <Typography.Text type="secondary">当前查看：{coverage === 'incremental' ? '新浪原生日常增量，复权口径未独立认证' : '旧长期历史，保留原来源与截止日'}。新采集成功不会消除旧历史缺口；两种价格基础未经验证不会拼接。</Typography.Text>
      <Table<UnifiedSecurity> size="small" rowKey="instrument_id" dataSource={rows} loading={loading}
        pagination={{ current: page, pageSize: 25, total, showSizeChanger: false, onChange: setPage }} scroll={{ x: 1000 }}
        columns={[
          { title: '证券', render: (_, r) => `${r.instrument_id} ${r.name}` },
          { title: '类型', dataIndex: 'security_type', render: (v) => v === 'ETF' ? 'ETF' : '股票' },
          { title: '当前身份声明', dataIndex: 'status' },
          { title: '频率质量', render: (_, r) => <Tag>{labels[cell(r).state] ?? cell(r).state}</Tag> },
          { title: '已观测范围（上海）', render: (_, r) => `${chinaClock(cell(r).observed_start)} → ${chinaClock(cell(r).observed_end)}` },
          { title: 'Bars', render: (_, r) => cell(r).bar_count ?? 0 },
        ]}
        expandable={{ expandedRowRender: (row) => <Space orientation="vertical">
          <Typography.Text>来源声明上市：{row.listing_date ?? '未知'}；退市：{row.delisting_date ?? '未知'}；历史 available_at：未知。源日期声明尚未独立认证。</Typography.Text>
          <Typography.Text>旧清单：{row.legacy_sources.join('、') || '候选补足或非目标身份'}；身份问题：{row.issues.join('、') || '无已识别冲突'}；频率问题：{cell(row).issues.join('、') || '无已识别问题（不等于完整认证）'}</Typography.Text>
          {coverage === 'incremental' && <>
            <Typography.Text>任务：{cell(row).task_state ?? '待调度'}；最近采集成功（上海）：{chinaClock(cell(row).last_success_at)}；最近成功批次：{cell(row).last_success_batch ?? '无'}；最近块缺失成交额：{cell(row).last_chunk_missing_amount_bars ?? '未验证'}；下次可重试：{chinaClock(cell(row).next_attempt_at)}</Typography.Text>
            {cell(row).issues.map((reason) => <Typography.Text key={reason}>{reasons[reason] ?? reason}</Typography.Text>)}
          </>}
          {row.source.map((source, i) => <Typography.Text key={`${source.source_id}-${i}`} copyable>{source.source_id} · {source.sha256}</Typography.Text>)}
        </Space> }} />
    </Space>
  </Card>
}
