import { Alert, Card, Col, Grid, Row, Space, Steps, Table, Tag, Typography } from 'antd'
import { Link } from 'react-router-dom'
import { EquityChart } from '../charts/EquityChart'
import { MetricCard } from '../components/MetricCard'
import { StatusTag } from '../components/StatusTag'
import { useResearchWorkflow } from '../components/ResearchWorkflowProvider'
import type { PlatformSnapshot } from '../types'

export function OverviewPage({ data, dark, publicNode }: { data: PlatformSnapshot | null; dark: boolean; publicNode: boolean }) {
  const screens = Grid.useBreakpoint()
  const { catalog } = useResearchWorkflow()
  const mode = data?.system.mode ?? 'PAPER'
  const risk = data?.system.risk_state ?? 'HALTED'
  const failures = Object.keys(data?.availability?.failures ?? {})
  const verifiedMinuteDataset = data?.longFiveMinuteResearch?.minute_dataset
  const hasVerifiedMinuteDataset = Boolean(verifiedMinuteDataset)
  const hasFactorValidation = Boolean(data?.intradayFactorRobustness?.report_id)
  const hasBacktestEvidence = Boolean(data?.backtestAnalytics?.leader)
  const activeTemplateCount = data?.strategyTemplates?.active_template_count ?? 0
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
      <Card title="Qlib 主研究工作流">
        <Typography.Paragraph>策略配置 → 登录后提交 → 集群训练/回测 → 不可变实验 → 同协议对照。数据按实验冻结，完成任务不会消除PIT与交易约束缺口。</Typography.Paragraph>
        <Space wrap>
          <Link to="/strategies">查看已接入模型</Link><Link to="/research">配置并提交Qlib任务</Link>
          <Link to="/experiments">查看集群任务与归档</Link><Link to="/backtest">分析Qlib结果</Link>
        </Space>
        <Typography.Paragraph type="secondary">{catalog?.datasets[0] ? `${catalog.datasets[0].symbols}股输入库存，截至${catalog.datasets[0].end}；${catalog.experiments.filter(r => r.state === 'COMPLETE').length}份已完成Qlib档案。` : '正在读取Qlib独立目录。'} 以下PAPER账本、分钟因子闸门与历史曲线属于原平台运行时，不代表Qlib任务状态。</Typography.Paragraph>
      </Card>
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
      <Card title="完整量化晋级路径（顺序闸门）">
        <Alert
          showIcon
          type={hasFactorValidation ? 'warning' : 'info'}
          title={hasFactorValidation ? '当前阻断：因子有效性与稳健性未通过' : '当前阻断：等待完整因子检验证据'}
          description="后续页面可以提供只读诊断，但不能绕过因子有效性、相关性与稳健性闸门；只有前一阶段通过，下一阶段才具备晋级资格。"
          style={{ marginBottom: 18 }}
        />
        <Steps
          orientation={screens.xl ? 'horizontal' : 'vertical'}
          responsive={false}
          items={[
            {
              title: <Link to="/archive">数据与因子定义</Link>,
              status: hasVerifiedMinuteDataset ? 'finish' : 'wait',
              content: hasVerifiedMinuteDataset ? `${verifiedMinuteDataset?.symbol_count ?? 0} 股、${verifiedMinuteDataset?.sector_count ?? 0} 板块的签名 5 分钟历史数据可用于研究。` : '等待校验通过的分钟数据与明确因子表达式。',
            },
            {
              title: <Link to="/archive">有效性、相关性与稳健性</Link>,
              status: hasFactorValidation ? 'error' : 'wait',
              content: hasFactorValidation ? '三连阳因子已完成检验，但结论为 NOT_ELIGIBLE。' : '尚无完整且校验通过的因子检验结果。',
            },
            {
              title: <Link to="/strategies">策略构造</Link>,
              status: 'wait',
              content: `${activeTemplateCount} 个主动模板可作诊断起点；当前因子不能注册或晋级。`,
            },
            {
              title: <Link to="/backtest">因果回测</Link>,
              status: 'wait',
              content: hasBacktestEvidence ? '已有可复算历史结果，但只能诊断，不能越过上游闸门。' : '等待可复算且身份绑定的回测结果。',
            },
            {
              title: <Link to="/paper">PAPER / SHADOW</Link>,
              status: 'wait',
              content: publicNode ? '公网节点只读；当前研究策略没有模拟盘准入。' : '仅本地安全核心可运行；当前研究策略仍被准入门阻断。',
            },
            {
              title: 'LIVE 实盘',
              status: 'wait',
              content: 'DISABLED；须全部闸门通过并由用户另行明确授权。',
            },
          ]}
        />
      </Card>
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
