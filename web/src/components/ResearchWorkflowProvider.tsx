import { createContext, useCallback, useContext, useEffect, useState } from 'react'
import type { ReactNode } from 'react'
import { workflowGet } from '../research/qlibWorkflow'
import type { WorkflowCatalog } from '../research/qlibWorkflow'

interface Session { configured: boolean; authenticated: boolean; user: string | null }
interface Context {
  catalog: WorkflowCatalog | null
  session: Session
  error: string | null
  refresh: () => Promise<void>
}
const ResearchContext = createContext<Context>({
  catalog: null, session: { configured: false, authenticated: false, user: null },
  error: null, refresh: async () => {},
})

export function ResearchWorkflowProvider({ children }: { children: ReactNode }) {
  const [catalog, setCatalog] = useState<WorkflowCatalog | null>(null)
  const [session, setSession] = useState<Session>({ configured: false, authenticated: false, user: null })
  const [error, setError] = useState<string | null>(null)
  const refresh = useCallback(async () => {
    try {
      const [value, identity] = await Promise.all([
        workflowGet<WorkflowCatalog>('/catalog'), workflowGet<Session>('/session'),
      ])
      if (value.schema_version !== 'qtrade.research-workflow.v1'
        || value.live_trading_enabled !== false || value.external_order_transmission !== false) {
        throw new Error('研究目录身份或权限边界不匹配')
      }
      setCatalog(value); setSession(identity); setError(null)
    } catch (reason) {
      setCatalog(null)
      setSession({ configured: false, authenticated: false, user: null })
      setError(reason instanceof Error ? reason.message : '研究目录不可用')
    }
  }, [])
  useEffect(() => {
    const initial = window.setTimeout(() => void refresh(), 0)
    const timer = window.setInterval(() => void refresh(), 15_000)
    return () => { window.clearTimeout(initial); window.clearInterval(timer) }
  }, [refresh])
  return <ResearchContext.Provider value={{ catalog, session, error, refresh }}>{children}</ResearchContext.Provider>
}

// This hook shares data, not write authority; mutations always require server authentication.
// eslint-disable-next-line react-refresh/only-export-components
export function useResearchWorkflow() { return useContext(ResearchContext) }
