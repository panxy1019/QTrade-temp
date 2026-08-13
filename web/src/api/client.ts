import type { HistoricalBarSeries, PlatformSnapshot } from '../types'

const API_BASE = import.meta.env.DEV ? '/api' : ''

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`)
  if (!response.ok) throw new Error(`请求失败：${response.status}`)
  return response.json() as Promise<T>
}

export function loadHistoricalBarSeries(symbol: string): Promise<HistoricalBarSeries> {
  return getJson<HistoricalBarSeries>(`/historical-bars/${encodeURIComponent(symbol)}`)
}

export async function loadPlatformSnapshot(): Promise<PlatformSnapshot> {
  const [system, health, account, pnl, positions, orders, metrics, reports, strategies, experiments, walkForward, robustness, backtestAnalytics, market, historicalBars] = await Promise.all([
    getJson<PlatformSnapshot['system']>('/system'),
    getJson<PlatformSnapshot['health']>('/health'),
    getJson<PlatformSnapshot['account']>('/account'),
    getJson<PlatformSnapshot['pnl']>('/pnl'),
    getJson<PlatformSnapshot['positions']>('/positions'),
    getJson<PlatformSnapshot['orders']>('/orders'),
    getJson<PlatformSnapshot['metrics']>('/metrics'),
    getJson<PlatformSnapshot['reports']>('/reports'),
    getJson<PlatformSnapshot['strategies']>('/strategies'),
    getJson<PlatformSnapshot['experiments']>('/experiments'),
    getJson<PlatformSnapshot['walkForward']>('/walk-forward'),
    getJson<PlatformSnapshot['robustness']>('/robustness'),
    getJson<PlatformSnapshot['backtestAnalytics']>('/backtest-analytics'),
    getJson<PlatformSnapshot['market']>('/market'),
    getJson<PlatformSnapshot['historicalBars']>('/historical-bars/latest'),
  ])
  return { system, health, account, pnl, positions, orders, metrics, reports, strategies, experiments, walkForward, robustness, backtestAnalytics, market, historicalBars }
}
