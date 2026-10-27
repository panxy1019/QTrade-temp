import { afterEach, describe, expect, it, vi } from 'vitest'
import type { ExperimentRunSpecDraftRequest, IntradayFactorBacktestRequest, IntradayFactorCorrelationRequest, IntradayFactorDiscoveryRequest } from '../types'
import { loadMinuteBarSeries, loadPlatformSnapshot, loadUnifiedBars, runExperimentRunSpecBacktest, runIntradayFactorBacktest, runIntradayFactorCorrelation, runIntradayFactorDiscovery } from './client'

describe('platform snapshot request fan-out', () => {
  afterEach(() => {
    vi.useRealTimers()
    vi.unstubAllGlobals()
  })

  it('removes cancelled queued unified requests without consuming a GET slot', async () => {
    const finish: Array<() => void> = []
    const fetcher = vi.fn((_: RequestInfo | URL, init?: RequestInit) => new Promise((resolve, reject) => {
      const done = () => resolve({ ok: true, status: 200, json: async () => ({ bars: [] }) })
      if (finish.length < 6) finish.push(done)
      else done()
      init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
    }))
    vi.stubGlobal('fetch', fetcher)
    const requests = Array.from({ length: 6 }, () => loadUnifiedBars({ symbol: '000063', frequency: 'day' }))
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(6))
    const controller = new AbortController()
    const queued = loadUnifiedBars({ symbol: '000063', frequency: '5min' }, { signal: controller.signal })
    controller.abort()
    await expect(queued).rejects.toMatchObject({ name: 'AbortError' })
    expect(fetcher).toHaveBeenCalledTimes(6)
    finish.forEach(resolve => resolve())
    await Promise.all(requests)
    await loadUnifiedBars({ symbol: '000063', frequency: 'day' })
    expect(fetcher).toHaveBeenCalledTimes(7)
  })

  it('aborts an active unified GET and permits the next bounded request', async () => {
    const controller = new AbortController()
    const fetcher = vi.fn().mockImplementationOnce((_: RequestInfo | URL, init?: RequestInit) => new Promise((_, reject) => {
      init?.signal?.addEventListener('abort', () => reject(new DOMException('Aborted', 'AbortError')), { once: true })
    })).mockResolvedValue({ ok: true, json: async () => ({ bars: [] }) })
    vi.stubGlobal('fetch', fetcher)
    const request = loadUnifiedBars({ symbol: '000063', frequency: 'day' }, { signal: controller.signal })
    await vi.waitFor(() => expect(fetcher).toHaveBeenCalledTimes(1))
    controller.abort()
    await expect(request).rejects.toMatchObject({ name: 'AbortError' })
    await loadUnifiedBars({ symbol: '000063', frequency: '5min', start: '2026-10-26', end: '2026-10-27', version: 'a'.repeat(64), limit: 2000 })
    expect(fetcher.mock.calls[1][0]).toContain('/unified/bars?symbol=000063&frequency=5min&start=2026-10-26&end=2026-10-27&version=')
  })

  it('refuses unbounded or inverted unified queries before fetching', async () => {
    const fetcher = vi.fn()
    vi.stubGlobal('fetch', fetcher)
    await expect(loadUnifiedBars({ symbol: '000063', frequency: 'day', limit: 2001 })).rejects.toThrow(/范围/)
    await expect(loadUnifiedBars({ symbol: '000063', frequency: 'day', start: '2026-10-27', end: '2026-10-26' })).rejects.toThrow(/范围/)
    await expect(loadUnifiedBars({ symbol: '000063.SZ', frequency: 'day' })).rejects.toThrow(/范围/)
    expect(fetcher).not.toHaveBeenCalled()
  })

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
    expect(paths).toContain('/api/strategy-templates')
    expect(paths).not.toContain('/api/long-five-minute-research')
    expect(paths).not.toContain('/api/intraday-breadth-observation')
    expect(new Set(cacheModes)).toEqual(new Set(['no-store']))
    expect(Object.keys(snapshot.availability.failures)).toHaveLength(30)
  })

  it('times out stalled GETs and releases the queued request', async () => {
    vi.useFakeTimers()
    let active = 0
    let maximumActive = 0
    let requestCount = 0
    vi.stubGlobal('fetch', vi.fn((_: RequestInfo | URL, init?: RequestInit) => {
      requestCount += 1
      active += 1
      maximumActive = Math.max(maximumActive, active)
      if (requestCount === 7) {
        active -= 1
        return Promise.resolve({ ok: true, status: 200, json: async () => ({ symbol: '000007.SZ', bars: [] }) })
      }
      return new Promise((_, reject) => {
        init?.signal?.addEventListener('abort', () => {
          active -= 1
          reject(new DOMException('Aborted', 'AbortError'))
        }, { once: true })
      })
    }))

    const requests = Array.from({ length: 7 }, (_, index) => (
      loadMinuteBarSeries(`00000${index + 1}.SZ`).then(
        (value) => ({ status: 'fulfilled' as const, value }),
        (reason: Error) => ({ status: 'rejected' as const, reason }),
      )
    ))
    await vi.advanceTimersByTimeAsync(0)
    expect(requestCount).toBe(6)

    await vi.advanceTimersByTimeAsync(30_000)
    const results = await Promise.all(requests)

    expect(maximumActive).toBe(6)
    expect(requestCount).toBe(7)
    expect(results.slice(0, 6).every((result) => result.status === 'rejected' && result.reason.message === '请求超时：30 秒')).toBe(true)
    expect(results[6]).toMatchObject({ status: 'fulfilled' })
  })

  it('surfaces an unavailable section inside a successful minute bundle', async () => {
    vi.stubGlobal('fetch', vi.fn(async (input: RequestInfo | URL) => {
      const path = String(input)
      if (path.endsWith('/system')) {
        return { ok: true, status: 200, json: async () => ({ mode: 'PAPER', running: false, risk_state: 'HALTED' }) }
      }
      if (path.endsWith('/health')) {
        return { ok: true, status: 200, json: async () => ({ healthy: false, runtime_mode: 'PAPER', risk_state: 'HALTED', gateway_connected: false, reconciliation_status: 'UNKNOWN' }) }
      }
      if (path.endsWith('/paper-snapshot')) {
        return { ok: true, status: 200, json: async () => ({ account: { cash: '0', equity: null }, pnl: { realized: '0' }, positions: [], orders: [], trades: [], risk: { state: 'HALTED' } }) }
      }
      if (path.endsWith('/minute-research-snapshot')) {
        return {
          ok: true,
          status: 200,
          json: async () => ({
            longFiveMinuteResearch: {}, reservedTailDiagnostic: {}, developmentPolicySearch: {},
            forwardObservation: {}, forwardSourceCoverage: null, forwardSourceCoverageHistory: {},
            publicMinuteAudit: {}, intradayObservation: {}, intradayBreadth: {}, intradayContinuity: {},
            unavailable_sections: ['forwardSourceCoverage'],
          }),
        }
      }
      return { ok: false, status: 503, json: async () => ({ detail: 'optional partition unavailable' }) }
    }))

    const snapshot = await loadPlatformSnapshot()

    expect(snapshot.forwardSourceCoverage.classification).toBe('UNAVAILABLE')
    expect(snapshot.availability.failures.forwardSourceCoverage).toBe('聚合快照分区不可用')
    expect(snapshot.availability.failures.longFiveMinuteResearch).toBeUndefined()
  })

  it('retries only a network failure for the stateless intraday factor request', async () => {
    const payload: IntradayFactorBacktestRequest = {
      factor: 'opening_three_ten_minute_bullish',
      start: '2026-07-28', end: '2026-08-24', cost_bps: '5', minimum_body_return_bps: '0',
      directions: ['BULLISH', 'BULLISH', 'BULLISH'],
      combination: 'ALL',
    }
    const fetchMock = vi.fn()
      .mockRejectedValueOnce(new TypeError('Failed to fetch'))
      .mockResolvedValueOnce({ ok: true, status: 200, json: async () => ({ preview_hash: 'verified' }) })
    vi.stubGlobal('fetch', fetchMock)

    await expect(runIntradayFactorBacktest(payload)).resolves.toMatchObject({ preview_hash: 'verified' })
    expect(fetchMock).toHaveBeenCalledTimes(2)

    fetchMock.mockReset()
    fetchMock.mockResolvedValueOnce({ ok: false, status: 422, json: async () => ({ detail: 'invalid factor scope' }) })
    await expect(runIntradayFactorBacktest(payload)).rejects.toThrow('invalid factor scope')
    expect(fetchMock).toHaveBeenCalledTimes(1)
  })

  it('posts the selected bounded rules to the stateless correlation endpoint', async () => {
    const payload: IntradayFactorCorrelationRequest = {
      start: '2026-07-28',
      end: '2026-08-24',
      rules: [
        { directions: ['BULLISH', 'BULLISH', 'BULLISH'], combination: 'ALL', minimum_body_return_bps: '0' },
        { directions: ['BULLISH', 'BEARISH', 'IGNORE'], combination: 'ANY', minimum_body_return_bps: '10' },
      ],
    }
    const fetchMock = vi.fn().mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({ classification: 'AD_HOC_INTRADAY_FACTOR_CORRELATION_CURRENT_NON_PIT' }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(runIntradayFactorCorrelation(payload)).resolves.toMatchObject({
      classification: 'AD_HOC_INTRADAY_FACTOR_CORRELATION_CURRENT_NON_PIT',
    })
    expect(fetchMock).toHaveBeenCalledWith('/api/intraday-factor-correlation', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify(payload),
    }))
  })

  it('posts the fixed intraday discovery window without mutation authority', async () => {
    const payload: IntradayFactorDiscoveryRequest = {
      start: '2026-06-30',
      end: '2026-08-24',
      cost_bps: '5',
    }
    const fetchMock = vi.fn().mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        classification: 'BOUNDED_INTRADAY_FACTOR_DISCOVERY_REUSED_HISTORY_NOT_INDEPENDENT',
        state_mutation: false,
        external_order_transmission: false,
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(runIntradayFactorDiscovery(payload)).resolves.toMatchObject({
      state_mutation: false,
      external_order_transmission: false,
    })
    expect(fetchMock).toHaveBeenCalledWith('/api/intraday-factor-discovery', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify(payload),
    }))
  })

  it('posts one complete spec request to the hash-bound backtest endpoint', async () => {
    const payload: ExperimentRunSpecDraftRequest = {
      template_id: 'etf-momentum-rotation-v1', experiment_id: 'rotation-replay-001',
      git_commit: 'a'.repeat(40), seed: 7,
      split: {
        train_start: '2024-01-02', train_end: '2024-12-31',
        validation_start: '2025-01-01', validation_end: '2025-12-31',
        test_start: '2026-01-01', test_end: '2026-08-12',
      },
      backtest: {
        start: '2026-01-01', end: '2026-08-12', factor: 'momentum',
        horizons: [20, 60, 120], top_k: 3, rebalance_every: 20, reserve: '0.05',
        trend_window: null, volatility_window: null, target_annual_volatility: null,
        initial_cash: '1000000', transaction_cost_rate: '0.0015', slippage_rate: '0.0005',
      },
    }
    const fetchMock = vi.fn().mockResolvedValueOnce({
      ok: true,
      status: 200,
      json: async () => ({
        classification: 'READ_ONLY_HASH_BOUND_EXPERIMENT_PREVIEW_NOT_PERSISTED',
        state_mutation: false,
        external_order_transmission: false,
      }),
    })
    vi.stubGlobal('fetch', fetchMock)

    await expect(runExperimentRunSpecBacktest(payload)).resolves.toMatchObject({
      state_mutation: false,
      external_order_transmission: false,
    })
    expect(fetchMock).toHaveBeenCalledWith('/api/experiment-run-spec-backtest', expect.objectContaining({
      method: 'POST',
      body: JSON.stringify(payload),
    }))
  })
})
