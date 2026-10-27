import { useEffect, useState } from 'react'

// Each mounted page owns one bounded poll per source. A failed poll removes the
// prior response; callers never inherit a successful value from another source.
export function useReadOnlySnapshot<T>(load: (signal: AbortSignal) => Promise<T>, interval: number, refresh: number) {
  const [value, setValue] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  useEffect(() => {
    let active = true
    let inFlight = false
    let controller: AbortController | null = null
    const poll = async () => {
      if (inFlight) return
      inFlight = true
      const request = new AbortController()
      controller = request
      let timeout = 0
      try {
        const deadline = new Promise<never>((_, reject) => {
          timeout = window.setTimeout(() => { request.abort(); reject(new Error('请求超时，当前状态不可用')) }, 15_000)
        })
        const result = await Promise.race([load(request.signal), deadline])
        if (active) { setValue(result); setError(null) }
      } catch (reason) {
        if (active) { setValue(null); setError(reason instanceof Error ? reason.message : '状态不可用') }
      } finally { window.clearTimeout(timeout); inFlight = false }
    }
    void poll()
    const timer = window.setInterval(() => void poll(), interval)
    return () => { active = false; controller?.abort(); window.clearInterval(timer) }
  }, [load, interval, refresh])
  return { value, error }
}
