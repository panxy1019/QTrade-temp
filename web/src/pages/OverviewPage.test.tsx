import { render, screen } from '@testing-library/react'
import { MemoryRouter } from 'react-router-dom'
import { describe, expect, it, vi } from 'vitest'
import type { PlatformSnapshot } from '../types'
import { OverviewPage } from './OverviewPage'

vi.mock('../charts/EquityChart', () => ({ EquityChart: () => <div data-testid="equity-chart" /> }))

describe('quant workflow overview', () => {
  it('stops the displayed promotion path at failed factor validation', () => {
    const data = {
      system: { mode: 'PAPER', risk_state: 'HALTED' },
      health: { healthy: true },
      availability: { failures: {} },
      account: { cash: '100000', equity: null },
      pnl: { realized: '0' },
      positions: [],
      orders: [],
      market: { historical: null },
      experiments: { experiments: [] },
      walkForward: { result: { folds: [] } },
      strategyTemplates: { active_template_count: 4 },
      minuteOverlay: { readiness: 'UNCONFIGURED' },
      longFiveMinuteResearch: { minute_dataset: { symbol_count: 75, sector_count: 15 } },
      intradayFactorRobustness: { report_id: 'opening-triple-bullish-long-window-robustness-v1' },
      backtestAnalytics: { leader: { strategy_id: 'diagnostic-only' } },
    } as unknown as PlatformSnapshot

    render(<MemoryRouter><OverviewPage data={data} dark publicNode /></MemoryRouter>)

    expect(screen.getByText('当前阻断：因子有效性与稳健性未通过')).toBeInTheDocument()
    expect(screen.getByText('数据与因子定义').closest('.ant-steps')).toHaveClass('ant-steps-vertical')
    expect(screen.getByText('75 股、15 板块的签名 5 分钟历史数据可用于研究。')).toBeInTheDocument()
    expect(screen.getByText('三连阳因子已完成检验，但结论为 NOT_ELIGIBLE。')).toBeInTheDocument()
    expect(screen.getByText('4 个主动模板可作诊断起点；当前因子不能注册或晋级。')).toBeInTheDocument()
    expect(screen.getByText('已有可复算历史结果，但只能诊断，不能越过上游闸门。')).toBeInTheDocument()
    expect(screen.getByText('公网节点只读；当前研究策略没有模拟盘准入。')).toBeInTheDocument()
    expect(screen.getByText('DISABLED；须全部闸门通过并由用户另行明确授权。')).toBeInTheDocument()
  })
})
