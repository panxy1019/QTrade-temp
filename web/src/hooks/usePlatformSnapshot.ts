import { useCallback, useEffect, useRef, useState } from 'react'
import { loadPlatformSnapshot } from '../api/client'
import type { PlatformSnapshot } from '../types'

const DEFAULT_REFRESH_MS = import.meta.env.MODE === 'vercel' ? 60_000 : 5_000
const INITIAL_RETRY_MS = 5_000

export function usePlatformSnapshot(refreshMs = DEFAULT_REFRESH_MS, enabled = true) {
  const [data, setData] = useState<PlatformSnapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const refreshInFlight = useRef(false)

  const refresh = useCallback(async () => {
    if (refreshInFlight.current) return null
    refreshInFlight.current = true
    setRefreshing(true)
    try {
      const snapshot = await loadPlatformSnapshot()
      setData(snapshot)
      setError(null)
      setLastUpdated(new Date())
      return snapshot
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '平台状态读取失败')
      return null
    } finally {
      refreshInFlight.current = false
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    if (!enabled) return
    let retry: number | null = null
    let cancelled = false
    const initial = window.setTimeout(() => {
      void refresh().then((snapshot) => {
        if (
          !cancelled
          && refreshMs > INITIAL_RETRY_MS
          && (
            snapshot === null
            || Object.keys(snapshot.availability.failures).length > 0
          )
        ) {
          retry = window.setTimeout(() => void refresh(), INITIAL_RETRY_MS)
        }
      })
    }, 0)
    const timer = window.setInterval(() => void refresh(), refreshMs)
    return () => {
      cancelled = true
      window.clearTimeout(initial)
      if (retry !== null) window.clearTimeout(retry)
      window.clearInterval(timer)
    }
  }, [refresh, refreshMs, enabled])

  return { data, error, loading, refreshing, lastUpdated, refresh }
}
