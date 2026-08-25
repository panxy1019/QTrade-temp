import { Alert, Card, Col, Row, Space, Table, Tag, Typography } from 'antd'
import { EquityChart } from '../charts/EquityChart'
import { MetricCard } from '../components/MetricCard'
import { StatusTag } from '../components/StatusTag'
import type { PlatformSnapshot } from '../types'

export function OverviewPage({ data, dark, publicNode }: { data: PlatformSnapshot | null; dark: boolean; publicNode: boolean }) {
  const mode = data?.system.mode ?? 'PAPER'
  const risk = data?.system.risk_state ?? 'HALTED'
  const failures = Object.keys(data?.availability?.failures ?? {})
  const capabilities = [
    { capability: '历史行情与 K 线', status: data?.market.historical ? 'READY' : 'UNAVAILABLE', detail: data?.market.historical ? `${data.market.historical.universe.symbols.length} 只 ETF · ${data.market.historical.bar_count} 根日线` : '无校验通过的数据集' },
    { capability: '正式研究证据', status: data?.experiments.experiments.length ? 'READY' : 'UNAVAILABLE', detail: `${data?.experiments.experiments.length ?? 0} 个实验 · ${data?.walkForward.result.folds.length ?? 0} 个滚动折` },
    { capability: '参数化回测', status: data?.market.historical ? 'READY' : 'BLOCKED', detail: '动量 / 反转 / 低波动；结果始终 NOT_ELIGIBLE' },
    { capability: 'PAPER 运行时', status: publicNode ? 'READ_ONLY' : 'LOCAL', detail: publicNode ? '公网节点无状态、无控制令牌' : '本地 SQLite 持久运行时' },
    { capability: '分钟级风险修正', status: data?.minuteOverlay.readiness ?? 'UNAVAILABLE', detail: data?.minuteOverlay.readiness === 'READY' ? `${data.minuteOverlay.ready_symbol_count}/${data.minuteOverlay.symbol_count} 标的就绪` : '等待授权实时 Tick 与完整预热' },
    { capability: '外部实盘下单', status: 'DISABLED', detail: '软件无 LIVE 模式，external_order_transmission=false' },
  ]
  return (
    <Space orientation="vertical" size={18} style={{ width: '100%' }}>
      <div className="page-title-row">
        <div><Typography.Title level={2}>总览</Typography.Title><Typography.Text type="secondary">研究、模拟交易与系统安全态势</Typography.Text></div>
        <Space><StatusTag value={mode} prominent /><StatusTag value={risk} prominent /></Space>
      </div>
      <Alert
        type={data?.health?.healthy ? 'success' : 'warning'}
        showIcon
        title={data?.health?.healthy ? '平台运行状态正常' : '平台处于安全停机或待就绪状态'}
        description="本系统仅支持回测、模拟交易和影子交易，不存在实盘下单模式。"
      />
      {failures.length > 0 && <Alert showIcon type="warning" title={`${failures.length} 个数据分区读取失败`} description={`已保留其余成功分区：${failures.join('、')}`} />}
      <Row gutter={[16, 16]}>
        <Col xs={12} lg={6}><MetricCard title="PAPER 现金" value={data?.account?.cash ?? '—'} suffix="元" note="无实时市价，不等于账户权益" /></Col>
        <Col xs={12} lg={6}><MetricCard title="累计已实现盈亏" value={data?.pnl?.realized ?? '0'} suffix="元" note="来自 PAPER 持久化账本" /></Col>
        <Col xs={12} lg={6}><MetricCard title="当前持仓" value={data?.positions?.length ?? 0} suffix="只" /></Col>
        <Col xs={12} lg={6}><MetricCard title="活动委托" value={data?.orders?.length ?? 0} suffix="笔" /></Col>
      </Row>
      <Card title="已验证历史研究曲线，不是当前 PAPER 权益" variant="borderless"><EquityChart dark={dark} analytics={data?.backtestAnalytics} /><Typography.Text type="secondary">仅展示 checksum 与语义校验通过的历史研究回测；非 PIT、NOT_ELIGIBLE，不代表当前账户表现。</Typography.Text></Card>
      <Card title="能力与阻塞状态">
        <Table rowKey="capability" size="small" pagination={false} dataSource={capabilities} columns={[
          { title: '能力', dataIndex: 'capability' },
          { title: '状态', dataIndex: 'status', render: (value: string) => <Tag color={value === 'READY' || value === 'LOCAL' ? 'green' : value === 'DISABLED' ? 'red' : 'orange'}>{value}</Tag> },
          { title: '边界', dataIndex: 'detail' },
        ]} />
      </Card>
    </Space>
  )
}
