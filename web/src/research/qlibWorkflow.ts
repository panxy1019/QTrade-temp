export interface ResearchSpec {
  name: string
  dataset_id: string
  model: 'linear' | 'lightgbm'
  features: 'Alpha158'
  segments: { train: [string, string]; valid: [string, string]; test: [string, string] }
  topk: number
  n_drop: number
  hold_thresh: number
  risk_degree: number
  buy_cost_bps: number
  sell_cost_bps: number
  min_cost: number
  initial_cash: number
  seed: number
  universe_policy: 'supported_a_share'
  source_snapshot_sha256?: string
}

export interface ResearchAudit {
  universe: { status: string; historical_pit_certified: boolean; selection?: string }
  limitations: string[]
  promotion: string
  [key: string]: unknown
}

export interface WorkflowExperiment {
  experiment_id: string
  name: string
  model: string
  engine: 'QLIB'
  state: string
  stage?: string
  updated_at?: string
  completed_at?: string
  spec: ResearchSpec | null
  spec_sha256?: string
  source_revision?: string
  run_id?: string
  metrics?: Record<string, number | null>
  audit: ResearchAudit
  error_code?: string
  comparison_protocol?: string
  events?: Array<{ at: string; state: string }>
}

export interface WorkflowCatalog {
  schema_version: 'qtrade.research-workflow.v1'
  publication_available: boolean
  worker_fresh?: boolean
  expected_complete_day?: string | null
  frozen_data_current?: boolean | null
  source_snapshot_sha256?: string | null
  published_at?: string
  live_trading_enabled: false
  external_order_transmission: false
  models: Array<{ id: string; name: string; parameters: Record<string, unknown> }>
  feature_sets: string[]
  portfolio_methods: string[]
  datasets: Array<{ id: string; symbols: number; rows: number; start: string; end: string; status: string; audit: ResearchAudit }>
  default_spec: ResearchSpec
  default_linear_spec: ResearchSpec
  experiments: WorkflowExperiment[]
  worker: { state: string; heartbeat_at?: string; concurrency?: number }
}

export interface WorkflowResult {
  experiment_id: string
  run_id: string
  state: 'COMPLETE'
  engine: 'QLIB'
  model: string
  spec: ResearchSpec | null
  source_revision: string
  dataset_sha256: string
  source_snapshot_sha256?: string | null
  data_end: string
  symbols: number
  feature_count: number
  metrics: Record<string, number | null>
  curve: Array<{ date: string; equity: number; benchmark_equity: number; drawdown: number; turnover: number; cost: number }>
  audit: ResearchAudit
  limitations: string[]
  comparison_protocol: string
  signal_evaluation: { start: string; end: string; days: number; label: string }
  portfolio_evaluation: { start: string; end: string; days: number; benchmark: string }
  evidence_sha256: string
}

export interface ArchivedResearchTemplate {
  schema_version: 'qtrade.research-template.v1'
  base_experiment_id: string
  base_run_id: string
  base_result_sha256: string
  source_revision: string
  dataset_sha256: string
  source_snapshot_sha256: string
  comparison_protocol: string
  spec: ResearchSpec
  live_trading_enabled: false
  external_order_transmission: false
  point_in_time: false
  real_data_paper_readiness: false
  promotion: 'NOT_ELIGIBLE'
}

export const sourceBoundProtocol = 'ALIGNED_SUPPORTED_UNIVERSE_SOURCE_BOUND_CONSTRAINTS_V3'
const sourceBoundProtocols = new Set([sourceBoundProtocol, 'ALIGNED_SUPPORTED_UNIVERSE_SOURCE_BOUND_CONSTRAINTS_V2'])

export function canReuseArchivedResult(result: WorkflowResult): boolean {
  return result.comparison_protocol === sourceBoundProtocol && !!result.spec &&
    /^[a-f0-9]{64}$/.test(result.source_snapshot_sha256 ?? '') &&
    result.spec.source_snapshot_sha256 === result.source_snapshot_sha256
}

export function comparisonDraft(
  template: ArchivedResearchTemplate, expectedId: string, target?: 'linear' | 'lightgbm',
): ResearchSpec {
  if (template.schema_version !== 'qtrade.research-template.v1' ||
    template.base_experiment_id !== expectedId || template.live_trading_enabled !== false ||
    template.external_order_transmission !== false || template.point_in_time !== false ||
    template.real_data_paper_readiness !== false || template.promotion !== 'NOT_ELIGIBLE' ||
    template.comparison_protocol !== sourceBoundProtocol || !template.spec ||
    !/^[a-f0-9]{64}$/.test(template.source_snapshot_sha256) ||
    template.spec.source_snapshot_sha256 !== template.source_snapshot_sha256 ||
    !/^[a-f0-9]{64}$/.test(template.base_result_sha256) ||
    !/^[a-f0-9]{64}$/.test(template.dataset_sha256) ||
    !/^[a-f0-9]{40}$/.test(template.source_revision) ||
    !['linear', 'lightgbm'].includes(template.spec.model)) {
    throw new Error('归档配置或来源身份不完整，不能以当前默认值替代')
  }
  const model = target ?? (template.spec.model === 'linear' ? 'lightgbm' : 'linear')
  return { ...structuredClone(template.spec), model,
    name: template.spec.name.slice(0, 40) + '-vs-' + model }
}

export const finalStates = new Set(['COMPLETE', 'FAILED', 'CANCELLED', 'INTERRUPTED', 'REJECTED'])
export function completedExperiments(rows: WorkflowExperiment[]): WorkflowExperiment[] {
  return rows.filter(row => row.state === 'COMPLETE').sort((a, b) =>
    Date.parse(b.completed_at ?? '1970-01-01') - Date.parse(a.completed_at ?? '1970-01-01'))
}
export const stageLabels: Record<string, string> = {
  AWAITING_PUBLICATION: '提交获确认，等待集群状态发布',
  QUEUED: '等待集群接收', RUNNING: '执行中', BUILDING_PROVIDER: '校验并转换数据',
  COMPUTING_ALPHA158: '计算特征', TRAINING_MODEL: '训练模型', BACKTESTING: '组合回测',
  COMPLETE: '归档完成', FAILED: '运行失败', CANCELLED: '已取消',
  INTERRUPTED: '异常中断，需人工重新提交', REJECTED: '执行器拒绝',
}

export const limitationLabels: Record<string, string> = {
  CURRENT_LIQUIDITY_UNIVERSE_SURVIVORSHIP_AND_SELECTION_BIAS: '当前流动性样本回投历史，幸存者与筛选偏差尚未消除',
  POST_HOC_VIEWED_HISTORY_NOT_UNSEEN_FORWARD_EVIDENCE: '已查看历史的事后研究，不是新的未见前向证据',
  HISTORICAL_ST_AND_EXACT_LIMIT_PRICES_NOT_FULLY_CERTIFIED: '历史ST及精确涨跌停尚未完整认证',
  MISSING_QUOTE_NOT_CERTIFIED_SUSPENSION_REASON: '缺行情不等于已认证停牌原因',
  VENDOR_ADJUSTMENTS_NOT_INDEPENDENT_CORPORATE_ACTION_RECONCILIATION: '供应商复权尚未与独立公司行为账本对账',
  OPEN_PRICE_AND_PREVIOUS_VOLUME_CAP_NOT_AUCTION_REPLAY: '开盘价及前日量容量是研究近似，未重放集合竞价',
  BACKFILLED_STATE_RESTRICTIONS_ARE_POST_HOC_NOT_PIT: '回填状态只增加研究限制，历史可得时间未认证；ST禁买是研究政策',
}

export function comparisonIssue(left: WorkflowResult, right: WorkflowResult): string | null {
  if (!left.spec || !right.spec || left.comparison_protocol !== right.comparison_protocol) {
    return '实验协议不同；原始固定基线仅供参考，不能与新协议直接归因比较'
  }
  if (left.dataset_sha256 !== right.dataset_sha256) return '数据版本不同，请先统一输入'
  if (left.source_revision !== right.source_revision) return '代码版本不同，请先统一实现版本'
  if ((left.source_snapshot_sha256 ?? null) !== (right.source_snapshot_sha256 ?? null)) return '历史状态来源快照不同，请先统一来源证据'
  if (sourceBoundProtocols.has(left.comparison_protocol) &&
    !/^[a-f0-9]{64}$/.test(left.source_snapshot_sha256 ?? '')) return '历史状态来源快照身份缺失，不能归因比较'
  const comparable = (spec: ResearchSpec) => {
    const { name, model, ...rest } = spec
    void name
    void model
    return JSON.stringify(rest, Object.keys(rest).concat(['train', 'valid', 'test']).sort())
  }
  if (comparable(left.spec) !== comparable(right.spec)) return '时间切分、种子、组合或成本不一致；请仅改变模型后重跑'
  if (left.curve.map(r => r.date).join() !== right.curve.map(r => r.date).join()) return '评价日期未对齐'
  return null
}

const ROOT = '/api/research'
export async function workflowGet<T>(path: string): Promise<T> {
  const response = await fetch(ROOT + path, { cache: 'no-store', credentials: 'same-origin' })
  if (!response.ok) {
    const body = await response.json().catch(() => null) as { detail?: unknown } | null
    throw new Error(typeof body?.detail === 'string' ? body.detail.slice(0, 300)
      : '研究接口读取失败：' + response.status)
  }
  return response.json() as Promise<T>
}

export async function workflowPost<T>(path: string, body: object): Promise<T> {
  const response = await fetch(ROOT + path, {
    method: 'POST', credentials: 'same-origin',
    headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
  })
  const result = await response.json() as T & { detail?: string }
  if (!response.ok) throw new Error(result.detail ?? '研究操作未完成：' + response.status)
  return result
}
