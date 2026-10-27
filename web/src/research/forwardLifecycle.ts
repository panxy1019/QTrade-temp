export interface ForwardModels {
  publication_available: boolean
  worker_fresh: boolean
  published_at?: string
  active_model: string | null
  models: Array<{ state: string; definition: { model_id: string } }>
}

export type BookRole = 'BASELINE' | 'ACTIVE_MODEL' | 'RETIRED_MODEL' | 'UNKNOWN'
export const bookRoleNames: Record<BookRole, string> = {
  BASELINE: '当前基线', ACTIVE_MODEL: '当前研究模型', RETIRED_MODEL: '退休模型历史账本', UNKNOWN: '模型状态待核验',
}

export function bookRole(strategy: string, catalog: ForwardModels | null, now = Date.now()): BookRole {
  if (!strategy.startsWith('approved_model_')) return 'BASELINE'
  const id = strategy.slice('approved_model_'.length)
  const stamp = Date.parse(catalog?.published_at ?? '')
  if (!/^[a-f0-9]{64}$/.test(id) || !catalog?.publication_available || !catalog.worker_fresh
    || !Number.isFinite(stamp) || stamp > now || now - stamp > 720_000 || !Array.isArray(catalog.models)) return 'UNKNOWN'
  const matches = catalog.models.filter(model => model && model.definition?.model_id === id)
  if (matches.length !== 1) return 'UNKNOWN'
  if (matches[0].state === 'ACTIVE' && catalog.active_model === id) return 'ACTIVE_MODEL'
  if (matches[0].state === 'RETIRED' && catalog.active_model !== id) return 'RETIRED_MODEL'
  return 'UNKNOWN'
}
