import { useCallback, useEffect, useState } from 'react'
import { loadPlatformSnapshot } from '../api/client'
import type { PlatformSnapshot } from '../types'

export function usePlatformSnapshot(refreshMs = 5000) {
  const [data, setData] = useState<PlatformSnapshot | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)

  const refresh = useCallback(async () => {
    try {
      setData(await loadPlatformSnapshot())
      setError(null)
    } catch (reason) {
      setError(reason instanceof Error ? reason.message : '平台状态读取失败')
    } finally {
      setLoading(false)
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

  return { data, error, loading, refresh }
}
