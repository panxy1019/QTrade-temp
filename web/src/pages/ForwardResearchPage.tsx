import { useEffect, useState } from 'react'
import { Alert, Button, Card, Descriptions, Empty, Select, Space, Table, Tabs, Tag, Typography } from 'antd'
import { JsonExportButton } from '../components/JsonExportButton'
import { workflowGet } from '../research/qlibWorkflow'
import { EtfShadowPanel } from '../components/EtfShadowPanel'

type Focus = 'overview' | 'holdings' | 'fills' | 'risk'
interface Holding { instrument: string; quantity: number }
interface Fill { symbol: string; side: string; quantity: number; price: string; fee: string; timestamp: string }
interface Order { symbol: string; side: string; quantity: number; status: string; reason: string | null }
interface FrozenTarget {
  target_id: string; signal_id: string; data_version: string; source_revision: string
  generated_at: string; execution_at: string; frozen_at: string
  weight_count: number; weights: Array<{ instrument: string; weight: string }>; weights_truncated: boolean
}
interface ForwardSession {
  day: string; receipt_state: string; state: string; target_id: string | null
  observed_at: string | null; signal_id: string | null; data_version: string | null
  market_evidence: string | null; calendar_evidence: string | null
  fill_count: number | null; fills: Fill[]; fills_truncated: boolean
  holding_count: number | null; holdings: Holding[]; holdings_truncated: boolean
  order_count: number | null; orders: Order[]; orders_truncated: boolean
  blocked_count: number | null; blockers: Array<{ instrument: string; reason: string }>; blockers_truncated: boolean
  blocked_reasons: Record<string, number>; cash: string | null; net_asset_value: string | null
  forward_return: string | null; turnover: string | null; limitations: string[]
}
interface ForwardBook {
  strategy_id: string; mode: 'PAPER' | 'SHADOW'; risk_state: string; running: boolean; recovery_required: boolean
  target_count: number; frozen_targets: FrozenTarget[]; sessions: ForwardSession[]
  archive: { kind: string; artifact_id: string; manifest_sha256: string; model_id: string | null }
}
interface Publication {
  schema_version: 'qtrade.forward-service.v1'; generated_at: string; heartbeat_at: string
  source_revision: string; state: string; live_trading_enabled: false; external_order_transmission: false
  books: ForwardBook[]; errors: Array<{ strategy_id: string | null; mode: string | null; code: string }>
  limitations: string[]
}
interface Reply { publication_available: boolean; service_fresh: boolean; publication: Publication | null }

const strategyNames: Record<string, string> = {
  momentum20: '20日动量', reversal5: '5日反转', lowvol20: '20日低波动',
  multifactor_equal_rank: '多因子等秩', equal_weight_universe: '全池等权',
}
const headings: Record<Focus, string> = {
  overview: 'PAPER / SHADOW 前向研究', holdings: '模拟持仓与资金', fills: '模拟订单与成交', risk: '模拟账本风险与约束',
}
const explanations: Record<Focus, string> = {
  overview: '开盘前冻结的研究目标与后来实际观察到的日线分开记录。PAPER 经过组合、风险、OMS 和模拟执行；SHADOW 仅观察目标和行情。',
  holdings: '只展示前向账本记录的模拟持仓、现金和净资产。目标权重不是持仓；未发生或无法估值的结果保留为空。',
  fills: '只展示前向账本的模拟订单与成交。日线开盘价成交在收盘后计算，记录真实处理时间，不代表实际开盘成交。',
  risk: '检查前向账本的风险状态、交易阻断和数据限制。HALT 后必须由人工完成对账并显式恢复。',
}
const blockerNames: Record<string, string> = {
  UNKNOWN_TRADING_CONSTRAINTS: 'ST、停牌、精确涨跌停或公司行动状态未知',
  UNKNOWN_PREVIOUS_SESSION_VOLUME: '缺少开盘前已知的上一交易日成交量证明',
  ONE_PERCENT_PREVIOUS_SESSION_VOLUME_LIMIT: '超过上一交易日成交量 1% 研究上限',
  SUSPENDED: '停牌', ST_EXCLUDED: 'ST 排除', MISSING_MARKET: '缺少当日行情',
  LIMIT_UP_BUY_BLOCKED: '涨停禁止买入', LIMIT_DOWN_SELL_BLOCKED: '跌停禁止卖出',
  CORPORATE_ACTION_REQUIRES_RECONCILIATION: '公司行动需要人工核对',
  INVALID_MARKET_VALUES: '行情数值无效', INVALID_TRADING_CONSTRAINTS: '交易约束无效',
}
const keyOf = (book: ForwardBook) => `${book.strategy_id}:${book.mode}`
const nameOf = (id: string) => strategyNames[id] ?? (id.startsWith('approved_model_') ? `批准模型 ${id.slice(15, 27)}` : id)
function metric(value: string | null | undefined, percent = false): string {
  if (value == null || !Number.isFinite(Number(value))) return '尚无可报告值'
  return percent ? `${(Number(value) * 100).toFixed(2)}%` : Number(value).toLocaleString('zh-CN', { maximumFractionDigits: 2 })
}
function bounded(shown: number, total: number | null, truncated: boolean) {
  return total == null ? '尚无完整收据' : `展示 ${shown} / 共 ${total} 条${truncated ? '；其余记录保留在完整账本' : ''}`
}

export function ForwardResearchPage({ focus = 'overview' }: { focus?: Focus }) {
  const [reply, setReply] = useState<Reply | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [refresh, setRefresh] = useState(0)
  const [selected, setSelected] = useState('')
  const [detail, setDetail] = useState<{ focus: Focus; key: string } | null>(null)
  const tab = detail?.focus === focus ? detail.key : focus === 'overview' ? 'targets' : focus
  useEffect(() => {
    let active = true
    let inFlight = false
    const load = async () => {
      if (inFlight) return
      inFlight = true
      try {
        const result = await workflowGet<Reply>('/online/forward-books')
        if (active) { setReply(result); setError(null); setLoading(false) }
      } catch {
        if (active) { setReply(null); setError('前向账本读取失败；当前没有可展示的已校验发布。'); setLoading(false) }
      } finally { inFlight = false }
    }
    void load()
    const timer = window.setInterval(() => void load(), 30000)
    return () => { active = false; window.clearInterval(timer) }
  }, [refresh])
  const publication = reply?.publication_available ? reply.publication : null
  const books = publication?.books ?? []
  const chosen = books.find(book => keyOf(book) === selected) ?? books[0]
  const latest = chosen?.sessions[0]
  const frozen = chosen?.frozen_targets[0]

  return <Space orientation="vertical" style={{ width: '100%' }}>
    <Space wrap><Typography.Title level={3} style={{ margin: 0 }}>{headings[focus]}</Typography.Title>
      <Tag color="blue">仅模拟研究</Tag><Tag>LIVE_TRADING_ENABLED=false</Tag>
      <Button onClick={() => setRefresh(n => n + 1)}>刷新前向账本</Button>
    </Space>
    <Typography.Paragraph>{explanations[focus]}</Typography.Paragraph>
    {error && <Alert type="error" showIcon title={error} />}
    {!publication && <Card loading={loading}><Empty description="尚无通过校验的前向账本发布；不展示示例持仓或收益。" /></Card>}
    {publication && <>
      <Alert type={reply?.service_fresh ? 'info' : 'warning'} showIcon
        title={reply?.service_fresh ? `前向服务：${publication.state} · 心跳有效` : '前向服务心跳已过期或停止；以下仅为最后一次发布'}
        description={`服务心跳 ${publication.heartbeat_at}；发布生成 ${publication.generated_at}。服务运行不等于发生模拟成交；真实下一交易日尚未发生时，成交和收益保持空值。`} />
      <Alert type="warning" showIcon title="研究约束仍需明确核验" description="未知 ST、停牌、精确涨跌停、公司行动或上一交易日成交量时，PAPER 阻断调仓。公开日线来源尚未独立认证未复权价格；SHADOW 不产生订单、持仓或组合收益。" />
      <Card title="独立策略账本" extra={<JsonExportButton filename="qtrade-forward-books.json" value={publication} />}>
        <Table<ForwardBook> rowKey={keyOf} pagination={false} dataSource={books} scroll={{ x: 1000 }} columns={[
          { title: '策略', render: (_, book) => nameOf(book.strategy_id) },
          { title: '模式', dataIndex: 'mode', render: mode => <Tag color={mode === 'PAPER' ? 'blue' : 'default'}>{mode}</Tag> },
          { title: '风险状态', render: (_, book) => <Tag color={book.risk_state === 'HALTED' ? 'red' : 'default'}>{book.risk_state}</Tag> },
          { title: '恢复', render: (_, book) => book.recovery_required ? '人工对账与显式恢复待完成' : book.mode === 'SHADOW' ? '仅观察' : '运行中' },
          { title: '已冻结目标', dataIndex: 'target_count' },
          { title: '最近完整观察', render: (_, book) => book.sessions[0]?.observed_at ?? '尚未观察' },
          { title: '持仓数', render: (_, book) => book.sessions[0]?.holding_count ?? '尚无完整收据' },
          { title: '前向日收益', render: (_, book) => metric(book.sessions[0]?.forward_return, true) },
        ]} />
        {!books.length && <Typography.Paragraph>服务尚未接受可冻结的归档目标。</Typography.Paragraph>}
      </Card>
      {chosen && <Card title="账本明细">
        <Space orientation="vertical" style={{ width: '100%' }}>
          <Select aria-label="选择前向研究账本" style={{ width: 'min(100%, 480px)' }} value={keyOf(chosen)} onChange={setSelected}
            options={books.map(book => ({ value: keyOf(book), label: `${nameOf(book.strategy_id)} · ${book.mode}` }))} />
          {chosen.recovery_required && <Alert type="error" showIcon title="PAPER 已停止接收新风险；需要人工对账与显式恢复" description="此页面只读。自动轮询或进程重启不会恢复 HALTED 账本。" />}
          <Descriptions size="small" column={2} items={[
            { key: 'strategy', label: '策略身份', children: chosen.strategy_id },
            { key: 'observed', label: '最近实际观察时间', children: latest?.observed_at ?? '尚未观察' },
            { key: 'cash', label: '模拟现金', children: metric(latest?.cash) },
            { key: 'nav', label: '模拟净资产', children: metric(latest?.net_asset_value) },
            { key: 'return', label: '前向日收益', children: metric(latest?.forward_return, true) },
            { key: 'turnover', label: '双边换手', children: metric(latest?.turnover, true) },
          ]} />
          <Tabs activeKey={tab} onChange={key => setDetail({ focus, key })} items={[
            { key: 'targets', label: '冻结目标', children: frozen ? <Space orientation="vertical" style={{ width: '100%' }}>
              <Descriptions size="small" column={1} items={[
                { key: 'generated', label: '信号实际生成', children: frozen.generated_at },
                { key: 'frozen', label: '目标实际冻结', children: frozen.frozen_at },
                { key: 'execution', label: '预期研究执行', children: frozen.execution_at },
                { key: 'signal', label: '信号身份', children: <Typography.Text copyable>{frozen.signal_id}</Typography.Text> },
                { key: 'data', label: '数据版本', children: <Typography.Text copyable>{frozen.data_version}</Typography.Text> },
              ]} />
              <Typography.Text>{bounded(frozen.weights.length, frozen.weight_count, frozen.weights_truncated)}；目标权重不是持仓。</Typography.Text>
              <Table rowKey="instrument" pagination={false} dataSource={frozen.weights} columns={[
                { title: '证券', dataIndex: 'instrument' }, { title: '冻结目标权重', dataIndex: 'weight', render: value => metric(value, true) },
              ]} />
            </Space> : <Empty description="尚无在真实开盘前冻结的目标" /> },
            { key: 'holdings', label: '模拟持仓', children: <>
              <Typography.Paragraph>{chosen.mode === 'SHADOW' ? 'SHADOW 不建立持仓或现金账户。' : '持仓来自已经完成的模拟成交与账本，不用目标权重代替。'}</Typography.Paragraph>
              <Typography.Text>{bounded(latest?.holdings.length ?? 0, latest?.holding_count ?? null, latest?.holdings_truncated ?? false)}</Typography.Text>
              <Table<Holding> rowKey="instrument" pagination={false} dataSource={latest?.holdings ?? []} columns={[
                { title: '证券', dataIndex: 'instrument' }, { title: '实际模拟持仓数量', dataIndex: 'quantity' },
              ]} />
            </> },
            { key: 'fills', label: '模拟订单与成交', children: <Space orientation="vertical" style={{ width: '100%' }}>
              <Typography.Paragraph>日线开盘价仅用于收盘后模拟；时间列是实际处理时间。SHADOW 永远没有订单或成交。</Typography.Paragraph>
              <Typography.Text>订单：{bounded(latest?.orders.length ?? 0, latest?.order_count ?? null, latest?.orders_truncated ?? false)}</Typography.Text>
              <Table<Order> rowKey={row => `${row.symbol}:${row.side}`} pagination={false} dataSource={latest?.orders ?? []} columns={[
                { title: '证券', dataIndex: 'symbol' }, { title: '方向', dataIndex: 'side' }, { title: '委托数量', dataIndex: 'quantity' },
                { title: '状态', dataIndex: 'status' }, { title: '拒绝原因', dataIndex: 'reason', render: reason => reason ?? '—' },
              ]} />
              <Typography.Text>成交：{bounded(latest?.fills.length ?? 0, latest?.fill_count ?? null, latest?.fills_truncated ?? false)}</Typography.Text>
              <Table<Fill> rowKey={row => `${row.symbol}:${row.side}`} pagination={false} dataSource={latest?.fills ?? []} scroll={{ x: 800 }} columns={[
                { title: '证券', dataIndex: 'symbol' }, { title: '方向', dataIndex: 'side' }, { title: '成交数量', dataIndex: 'quantity' },
                { title: '模拟成交价', dataIndex: 'price' }, { title: '费用', dataIndex: 'fee' }, { title: '实际处理时间', dataIndex: 'timestamp' },
              ]} />
            </Space> },
            { key: 'risk', label: '风险与阻断', children: <Space orientation="vertical" style={{ width: '100%' }}>
              <Typography.Text>风险状态 {chosen.risk_state}；{chosen.running ? '执行管线运行中' : '执行管线未启动'}；{chosen.recovery_required ? '需要人工恢复' : '无待处理恢复'}</Typography.Text>
              <Table rowKey="reason" pagination={false} dataSource={Object.entries(latest?.blocked_reasons ?? {}).map(([reason, count]) => ({ reason, count }))} columns={[
                { title: '交易阻断原因', dataIndex: 'reason', render: reason => blockerNames[reason] ?? reason }, { title: '涉及证券数', dataIndex: 'count' },
              ]} />
              <Typography.Text>{bounded(latest?.blockers.length ?? 0, latest?.blocked_count ?? null, latest?.blockers_truncated ?? false)}</Typography.Text>
              <Table rowKey="instrument" pagination={false} dataSource={latest?.blockers ?? []} columns={[
                { title: '证券', dataIndex: 'instrument' }, { title: '阻断原因', dataIndex: 'reason', render: reason => blockerNames[reason] ?? reason },
              ]} />
              {latest?.limitations.map(reason => <Typography.Paragraph key={reason}>{reason}</Typography.Paragraph>)}
            </Space> },
          ]} />
          <Typography.Title level={5}>逐日观察收据</Typography.Title>
          <Table<ForwardSession> rowKey="day" pagination={{ pageSize: 10 }} dataSource={chosen.sessions} scroll={{ x: 900 }} columns={[
            { title: '交易日', dataIndex: 'day' }, { title: '状态', dataIndex: 'state' },
            { title: '实际观察', dataIndex: 'observed_at', render: value => value ?? '处理中，尚无结果' },
            { title: '模拟成交数', dataIndex: 'fill_count', render: value => value ?? '尚无完整收据' },
            { title: '净资产', dataIndex: 'net_asset_value', render: value => metric(value) },
            { title: '前向日收益', dataIndex: 'forward_return', render: value => metric(value, true) },
          ]} />
          <Typography.Paragraph>仅最近一日展示至多 50 条持仓、订单、成交和阻断明细；历史行保留总数与收益。完整账本保存在研究服务。</Typography.Paragraph>
          <Typography.Text copyable>原归档身份：{chosen.archive.artifact_id}</Typography.Text>
          <Typography.Text copyable>归档摘要：{chosen.archive.manifest_sha256}</Typography.Text>
        </Space>
      </Card>}
      {!!publication.errors.length && <Alert type="warning" title="部分账本未能更新" description={publication.errors.map(row => `${row.strategy_id ?? '服务'} / ${row.mode ?? '观察'}: ${row.code}`).join('；')} />}
      {publication.limitations.map(reason => <Typography.Paragraph key={reason}>{reason}</Typography.Paragraph>)}
    </>}
    {focus === 'overview' && <EtfShadowPanel />}
  </Space>
}
