import { useMemo, useState } from 'react'
import { ConfigProvider, Layout, Menu, Space, Switch, theme, Typography } from 'antd'
import {
  AreaChartOutlined, BarChartOutlined, DatabaseOutlined, ExperimentOutlined,
  FundOutlined, LineChartOutlined, MonitorOutlined, PieChartOutlined,
  ReconciliationOutlined, SafetyCertificateOutlined, StockOutlined, ThunderboltOutlined,
} from '@ant-design/icons'
import { HashRouter, Route, Routes, useLocation, useNavigate } from 'react-router-dom'
import { usePlatformSnapshot } from './hooks/usePlatformSnapshot'
import { OverviewPage } from './pages/OverviewPage'
import { BacktestPage, DataCenterPage, ExperimentPage, MarketPage, MonitorPage, OrdersPage, PaperPage, PositionsPage, ResearchPage, RiskPage, StrategyPage } from './pages/DataPages'
import { StatusTag } from './components/StatusTag'
import './styles.css'

const menu = [
  ['/', '总览', <AreaChartOutlined />], ['/market', '行情中心', <StockOutlined />],
  ['/strategies', '策略中心', <ThunderboltOutlined />], ['/research', '策略研究', <FundOutlined />],
  ['/experiments', '实验中心', <ExperimentOutlined />], ['/backtest', '回测分析', <LineChartOutlined />],
  ['/paper', '模拟交易', <PieChartOutlined />], ['/positions', '持仓', <BarChartOutlined />],
  ['/orders', '委托与成交', <ReconciliationOutlined />], ['/risk', '风控中心', <SafetyCertificateOutlined />],
  ['/data', '数据中心', <DatabaseOutlined />], ['/monitor', '系统监控', <MonitorOutlined />],
] as const

function Shell() {
  const [dark, setDark] = useState(true)
  const [collapsed, setCollapsed] = useState(false)
  const { data, error } = usePlatformSnapshot()
  const location = useLocation()
  const navigate = useNavigate()
  const algorithm = dark ? theme.darkAlgorithm : theme.defaultAlgorithm
  const items = useMemo(() => menu.map(([key, label, icon]) => ({ key, label, icon })), [])
  return <ConfigProvider theme={{ algorithm, token: { colorPrimary: '#2f81f7', borderRadius: 8, fontFamily: 'Inter, "PingFang SC", "Microsoft YaHei", sans-serif' } }}>
    <Layout className={dark ? 'app dark' : 'app light'}>
      <Layout.Sider collapsible collapsed={collapsed} onCollapse={setCollapsed} width={232} theme="dark">
        <div className="brand"><div className="brand-mark">Q</div>{!collapsed && <div><b>QTrade</b><span>量化研究平台</span></div>}</div>
        <Menu theme="dark" mode="inline" selectedKeys={[location.pathname]} items={items} onClick={({ key }) => navigate(key)} />
        {!collapsed && <div className="live-boundary">实盘模式不可用<br/><small>仅回测 / 模拟 / 影子</small></div>}
      </Layout.Sider>
      <Layout>
        <Layout.Header className="topbar">
          <Space><StatusTag value={data?.system.mode ?? 'PAPER'} prominent /><Typography.Text type="secondary">{error ?? '本地安全核心'}</Typography.Text></Space>
          <Space><span>深色模式</span><Switch checked={dark} onChange={setDark} /></Space>
        </Layout.Header>
        <Layout.Content className="content">
          <Routes>
            <Route path="/" element={<OverviewPage data={data} dark={dark} />} />
            <Route path="/market" element={<MarketPage />} />
            <Route path="/strategies" element={<StrategyPage />} />
            <Route path="/research" element={<ResearchPage />} />
            <Route path="/experiments" element={<ExperimentPage />} />
            <Route path="/backtest" element={<BacktestPage />} />
            <Route path="/paper" element={<PaperPage data={data} />} />
            <Route path="/positions" element={<PositionsPage data={data} />} />
            <Route path="/orders" element={<OrdersPage data={data} />} />
            <Route path="/risk" element={<RiskPage data={data} />} />
            <Route path="/data" element={<DataCenterPage data={data} />} />
            <Route path="/monitor" element={<MonitorPage data={data} />} />
          </Routes>
        </Layout.Content>
      </Layout>
    </Layout>
  </ConfigProvider>
}

export default function App() { return <HashRouter><Shell /></HashRouter> }
