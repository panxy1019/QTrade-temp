import { Alert, Card, Col, Row, Space, Typography } from 'antd'
import { EquityChart } from '../charts/EquityChart'
import { MetricCard } from '../components/MetricCard'
import { StatusTag } from '../components/StatusTag'
import type { PlatformSnapshot } from '../types'

export function OverviewPage({ data, dark }: { data: PlatformSnapshot | null; dark: boolean }) {
  const mode = data?.system.mode ?? 'PAPER'
  const risk = data?.system.risk_state ?? 'HALTED'
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
      <Row gutter={[16, 16]}>
        <Col xs={12} lg={6}><MetricCard title="PAPER 现金" value={data?.account?.cash ?? '—'} suffix="元" note="无实时市价，不等于账户权益" /></Col>
        <Col xs={12} lg={6}><MetricCard title="累计已实现盈亏" value={data?.pnl?.realized ?? '0'} suffix="元" note="来自 PAPER 持久化账本" /></Col>
        <Col xs={12} lg={6}><MetricCard title="当前持仓" value={data?.positions?.length ?? 0} suffix="只" /></Col>
        <Col xs={12} lg={6}><MetricCard title="活动委托" value={data?.orders?.length ?? 0} suffix="笔" /></Col>
      </Row>
      <Card title="已验证历史研究曲线，不是当前 PAPER 权益" variant="borderless"><EquityChart dark={dark} analytics={data?.backtestAnalytics} /><Typography.Text type="secondary">仅展示 checksum 与语义校验通过的历史研究回测；非 PIT、NOT_ELIGIBLE，不代表当前账户表现。</Typography.Text></Card>
    </Space>
  )
}
