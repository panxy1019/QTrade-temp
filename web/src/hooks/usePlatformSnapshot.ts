import { useCallback, useEffect, useRef, useState } from 'react'
import { loadPlatformSnapshot } from '../api/client'
import type { PlatformSnapshot } from '../types'

const DEFAULT_REFRESH_MS = import.meta.env.MODE === 'vercel' ? 60_000 : 5_000

export function usePlatformSnapshot(refreshMs = DEFAULT_REFRESH_MS) {
  const [data, setData] = useState<PlatformSnapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [refreshing, setRefreshing] = useState(false)
  const [lastUpdated, setLastUpdated] = useState<Date | null>(null)
  const refreshInFlight = useRef(false)

  const refresh = useCallback(async () => {
    if (refreshInFlight.current) return
    refreshInFlight.current = true
    setRefreshing(true)
    try {
      setData(await loadPlatformSnapshot())
      setError(null)
      setLastUpdated(new Date())
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '平台状态读取失败')
    } finally {
      refreshInFlight.current = false
      setLoading(false)
      setRefreshing(false)
    }
  }, [])

  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0)
    const timer = window.setInterval(() => void refresh(), refreshMs)
    return () => {
      window.clearTimeout(initial)
      window.clearInterval(timer)
    }
  }, [refresh, refreshMs])

  return { data, error, loading, refreshing, lastUpdated, refresh }
}
