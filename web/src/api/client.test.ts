import { afterEach, describe, expect, it, vi } from 'vitest'
import { loadPlatformSnapshot } from './client'

describe('platform snapshot request fan-out', () => {
  afterEach(() => vi.unstubAllGlobals())

  it('limits concurrent GETs and uses the minute evidence bundle', async () => {
    let active = 0
    let maximumActive = 0
    const paths: string[] = []
    const cacheModes: Array<RequestCache | undefined> = []
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL, init?: RequestInit) => {
      const path = String(input)
      paths.push(path)
      cacheModes.push(init?.cache)
      active += 1
      maximumActive = Math.max(maximumActive, active)
      await new Promise((resolve) => window.setTimeout(resolve, 5))
      active -= 1
      if (path.endsWith('/system')) {
        return { ok: true, status: 200, json: async () => ({ mode: 'PAPER', running: false, risk_state: 'HALTED' }) }
      }
      if (path.endsWith('/health')) {
        return { ok: true, status: 200, json: async () => ({ healthy: false, runtime_mode: 'PAPER', risk_state: 'HALTED', gateway_connected: false, reconciliation_status: 'UNKNOWN' }) }
      }
      if (path.endsWith('/paper-snapshot')) {
        return { ok: true, status: 200, json: async () => ({ account: { cash: '0', equity: null }, pnl: { realized: '0' }, positions: [], orders: [], trades: [], risk: { state: 'HALTED' } }) }
      }
      return { ok: false, status: 503, json: async () => ({ detail: 'optional partition unavailable' }) }
    }))

    const snapshot = await loadPlatformSnapshot()

    expect(maximumActive).toBe(6)
    expect(paths).toContain('/api/minute-research-snapshot')
    expect(paths).not.toContain('/api/long-five-minute-research')
    expect(paths).not.toContain('/api/intraday-breadth-observation')
    expect(new Set(cacheModes)).toEqual(new Set(['no-store']))
    expect(Object.keys(snapshot.availability.failures)).toHaveLength(27)
  })
})
