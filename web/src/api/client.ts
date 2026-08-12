import type { PlatformSnapshot } from '../types'

const API_BASE = import.meta.env.DEV ? '/api' : ''

async function getJson<T>(path: string): Promise<T> {
  const response = await fetch(`${API_BASE}${path}`)
  if (!response.ok) throw new Error(`请求失败：${response.status}`)
  return response.json() as Promise<T>
}

export async function loadPlatformSnapshot(): Promise<PlatformSnapshot> {
  const [system, health, account, pnl, positions, orders, metrics, reports] = await Promise.all([
    getJson<PlatformSnapshot['system']>('/system'),
    getJson<PlatformSnapshot['health']>('/health'),
    getJson<PlatformSnapshot['account']>('/account'),
    getJson<PlatformSnapshot['pnl']>('/pnl'),
    getJson<PlatformSnapshot['positions']>('/positions'),
    getJson<PlatformSnapshot['orders']>('/orders'),
    getJson<PlatformSnapshot['metrics']>('/metrics'),
    getJson<PlatformSnapshot['reports']>('/reports'),
  ])
  return { system, health, account, pnl, positions, orders, metrics, reports }
}
