import { lazy, Suspense, useEffect, useMemo, useState } from 'react'
import { Button, ConfigProvider, Layout, Menu, Space, Switch, Tabs, Tag, theme, Typography } from 'antd'
import {
  AreaChartOutlined, BarChartOutlined, DatabaseOutlined, ExperimentOutlined,
  FundOutlined, LineChartOutlined, MonitorOutlined, PieChartOutlined,
  ReconciliationOutlined, ReloadOutlined, SafetyCertificateOutlined, StockOutlined, ThunderboltOutlined,
} from '@ant-design/icons'
import { HashRouter, Navigate, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { usePlatformSnapshot } from './hooks/usePlatformSnapshot'
import { StatusTag } from './components/StatusTag'
import { PageErrorBoundary } from './components/PageErrorBoundary'
import { isPublicResearchNode } from './runtime/environment'
import { CurrentDataProvider, CurrentDataStatus } from './components/CurrentWorkspace'
import { ResearchWorkflowProvider } from './components/ResearchWorkflowProvider'
import './styles.css'

const OverviewPage = lazy(() => import('./pages/OverviewPage').then((module) => ({ default: module.OverviewPage })))
const MarketPage = lazy(() => import('./pages/DataPages').then((module) => ({ default: module.MarketPage })))
const StrategyPage = lazy(() => import('./pages/DataPages').then((module) => ({ default: module.StrategyPage })))
const ResearchPage = lazy(() => import('./pages/DataPages').then((module) => ({ default: module.ResearchPage })))
const ExperimentPage = lazy(() => import('./pages/DataPages').then((module) => ({ default: module.ExperimentPage })))
const BacktestPage = lazy(() => import('./pages/DataPages').then((module) => ({ default: module.BacktestPage })))
const PaperPage = lazy(() => import('./pages/DataPages').then((module) => ({ default: module.PaperPage })))
const PositionsPage = lazy(() => import('./pages/DataPages').then((module) => ({ default: module.PositionsPage })))
const OrdersPage = lazy(() => import('./pages/DataPages').then((module) => ({ default: module.OrdersPage })))
const RiskPage = lazy(() => import('./pages/DataPages').then((module) => ({ default: module.RiskPage })))
const DataCenterPage = lazy(() => import('./pages/DataPages').then((module) => ({ default: module.DataCenterPage })))
const MonitorPage = lazy(() => import('./pages/DataPages').then((module) => ({ default: module.MonitorPage })))
const QlibStrategyPage = lazy(() => import('./pages/QlibWorkflowPages').then(m => ({ default: m.QlibStrategyPage })))
const QlibResearchPage = lazy(() => import('./pages/QlibWorkflowPages').then(m => ({ default: m.QlibResearchPage })))
const QlibExperimentPage = lazy(() => import('./pages/QlibWorkflowPages').then(m => ({ default: m.QlibExperimentPage })))
const QlibBacktestPage = lazy(() => import('./pages/QlibWorkflowPages').then(m => ({ default: m.QlibBacktestPage })))
const QlibArchivePage = lazy(() => import('./pages/QlibWorkflowPages').then(m => ({ default: m.QlibArchivePage })))
const OnlineModelsPage = lazy(() => import('./pages/OnlineModelsPage').then(m => ({ default: m.OnlineModelsPage })))
const SignalsPage = lazy(() => import('./pages/SignalsPage').then(m => ({ default: m.SignalsPage })))

const menu = [
  ['/', '总览', <AreaChartOutlined />], ['/market', '行情中心', <StockOutlined />],
  ['/strategies', '策略中心', <ThunderboltOutlined />], ['/research', '策略研究', <FundOutlined />],
  ['/experiments', '实验中心', <ExperimentOutlined />], ['/backtest', '回测分析', <LineChartOutlined />],
  ['/models', '在线模型', <ExperimentOutlined />],
  ['/signals', '每日研究信号', <ThunderboltOutlined />],
  ['/archive', '历史工作区', <DatabaseOutlined />],
  ['/paper', '模拟交易', <PieChartOutlined />], ['/positions', '持仓', <BarChartOutlined />],
  ['/orders', '委托与成交', <ReconciliationOutlined />], ['/risk', '风控中心', <SafetyCertificateOutlined />],
  ['/data', '数据中心', <DatabaseOutlined />], ['/monitor', '系统监控', <MonitorOutlined />],
] as const

function Shell() {
  const [dark, setDark] = useState(true)
  const [collapsed, setCollapsed] = useState(false)
  const { data, error, loading, refreshing, lastUpdated, refresh } = usePlatformSnapshot()
  const publicNode = isPublicResearchNode()
  const runtimeLabel = publicNode ? '公开研究结果 · 登录后提交研究' : '本地安全核心'
  const location = useLocation()
  const researchRoute = ['/strategies', '/research', '/experiments', '/backtest', '/models', '/signals'].includes(location.pathname)
  const navigate = useNavigate()
  const algorithm = dark ? theme.darkAlgorithm : theme.defaultAlgorithm
  const items = useMemo(() => menu.map(([key, label, icon]) => ({ key, label, icon })), [])
  const failedSections = Object.keys(data?.availability?.failures ?? {})
  useEffect(() => {
    const media = window.matchMedia('(max-width: 720px)')
    const update = () => setCollapsed(media.matches)
    update()
    media.addEventListener('change', update)
    return () => media.removeEventListener('change', update)
  }, [])
  return <ConfigProvider theme={{ algorithm, token: { colorPrimary: '#2f81f7', borderRadius: 8, fontFamily: 'Inter, "PingFang SC", "Microsoft YaHei", sans-serif' } }}>
    <Layout className={dark ? 'app dark' : 'app light'}>
      <Layout.Sider collapsible collapsed={collapsed} onCollapse={setCollapsed} width={232} theme="dark">
        <div className="brand"><div className="brand-mark">Q</div>{!collapsed && <div><b>QTrade</b><span>量化研究平台</span></div>}</div>
        <Menu theme="dark" mode="inline" selectedKeys={[location.pathname]} items={items} onClick={({ key }) => navigate(key)} />
        {!collapsed && <div className="live-boundary">实盘模式不可用<br/><small>仅回测 / 模拟 / 影子</small></div>}
      </Layout.Sider>
      <Layout>
        <Layout.Header className="topbar">
          <Space wrap>
            {researchRoute ? <Tag color="blue">Qlib 研究</Tag> : <StatusTag value={data?.system.mode ?? 'PAPER'} prominent />}
            <Typography.Text type={error ? 'danger' : 'secondary'}>{error ?? runtimeLabel}</Typography.Text>
            {failedSections.length > 0 && <Tag color="orange">{failedSections.length} 个数据分区异常</Tag>}
            <Typography.Text type="secondary">{lastUpdated ? `更新 ${lastUpdated.toLocaleTimeString('zh-CN', { hour12: false })}` : loading ? '首次加载中…' : '尚未更新'}</Typography.Text>
            <Button aria-label="刷新平台快照" size="small" icon={<ReloadOutlined />} loading={refreshing} onClick={() => { void refresh() }}>刷新</Button>
          </Space>
          <Space><span>深色模式</span><Switch checked={dark} onChange={setDark} /></Space>
        </Layout.Header>
        <Layout.Content className="content">
          {!researchRoute && <CurrentDataStatus archive={location.pathname !== '/market'} />}
          <PageErrorBoundary key={location.pathname}>
            <Suspense fallback={<Typography.Text type="secondary">页面加载中…</Typography.Text>}>
              <Routes>
                <Route path="/" element={<OverviewPage data={data} dark={dark} publicNode={publicNode} />} />
                <Route path="/market" element={<MarketPage data={data} />} />
                <Route path="/strategies" element={<QlibStrategyPage />} />
                <Route path="/research" element={<QlibResearchPage />} />
                <Route path="/models" element={<OnlineModelsPage />} />
                <Route path="/signals" element={<SignalsPage />} />
                <Route path="/experiments" element={<QlibExperimentPage />} />
                <Route path="/backtest" element={<QlibBacktestPage />} />
                <Route path="/archive" element={<QlibArchivePage><Tabs items={[
                  { key: 'strategies', label: '原策略目录', children: <StrategyPage data={data} /> },
                  { key: 'research', label: '因子与分钟研究', children: <ResearchPage data={data} /> },
                  { key: 'experiments', label: '原实验档案', children: <ExperimentPage data={data} /> },
                  { key: 'backtest', label: 'ETF/分钟回测工具', children: <BacktestPage data={data} /> },
                ]} /></QlibArchivePage>} />
                <Route path="/paper" element={<PaperPage data={data} publicNode={publicNode} onRefresh={async () => { await refresh() }} />} />
                <Route path="/positions" element={<PositionsPage data={data} />} />
                <Route path="/orders" element={<OrdersPage data={data} publicNode={publicNode} onRefresh={async () => { await refresh() }} />} />
                <Route path="/risk" element={<RiskPage data={data} />} />
                <Route path="/data" element={<DataCenterPage data={data} />} />
                <Route path="/monitor" element={<MonitorPage data={data} />} />
                <Route path="*" element={<Navigate to="/" replace />} />
              </Routes>
            </Suspense>
          </PageErrorBoundary>
        </Layout.Content>
      </Layout>
    </Layout>
  </ConfigProvider>
}

export default function App() { return <CurrentDataProvider><ResearchWorkflowProvider><HashRouter><Shell /></HashRouter></ResearchWorkflowProvider></CurrentDataProvider> }
