import { canonicalJson, sha256Hex } from './strategyIdentity'
import type { ResearchDataBinding } from './qlibWorkflow'

export interface FactorSpec {
  name: string
  factor_ref: Record<string, string>
  evaluation_ref: { evaluation_id: string; specification_sha256: string; report_manifest_sha256: string }
  signal_kind: string
  segments: Record<'train' | 'valid' | 'test', [string, string]>
  portfolio: { method: 'TopkDropout'; topk: number; n_drop: number; hold_thresh: number; risk_degree: string }
  execution: { buy_cost_bps: string; sell_cost_bps: string; min_cost: string; initial_cash: string; slippage_bps: '0' }
  parent: null | Record<string, string>
}

export interface FactorDefinition {
  schema_version: 'qtrade.factor-strategy-definition.v1'
  strategy_id: string
  strategy_version: string
  evidence_sha256: string
  spec: FactorSpec
  factor_definition: { alias: string; formula: string; factor_id: string; factor_version: string }
  evaluation_context: { data_binding: ResearchDataBinding; instruments: string[];
    decision_scope: { first_session: string; last_session: string; count: number };
    knowledge_mode: string; selected_valid_ic_days: number; limitations: string[] }
  source: { revision: string }
  execution: { backtest_implemented: false; costs_applied_to_diagnostic: false; rules_certified: false }
  signal: { adapter_implemented: false }
  research_only: true
  paper_admission: false
  model_activation: false
  live_trading_enabled: false
  external_order_transmission: false
  [key: string]: unknown
}

export interface FactorCatalog {
  schema_version: 'qtrade.factor-strategy-catalog.v1'
  worker: { heartbeat_at: string; source_revision: string; supported_kinds: string[]; implementation_sources: Record<string, string> }
  datasets: Array<{ data_binding: ResearchDataBinding; calendar: string[]; calendar_sha256: string }>
  templates: FactorDefinition[]
  limitations: string[]
  catalog_sha256: string
  evidence_sha256: string
  live_trading_enabled: false
  external_order_transmission: false
  [key: string]: unknown
}

export interface FactorPreview {
  schema_version: 'qtrade.factor-strategy-preview.v1'
  spec: FactorSpec
  source_revision: string
  strategy_definition: FactorDefinition
  expected_strategy_version: string
  expected_catalog_sha256: string
  evidence_sha256: string
  live_trading_enabled: false
  external_order_transmission: false
  [key: string]: unknown
}

function record(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === 'object' && !Array.isArray(value)
}

async function sealed(value: Record<string, unknown>): Promise<boolean> {
  const { evidence_sha256, ...body } = value
  return typeof evidence_sha256 === 'string' && evidence_sha256 === await sha256Hex(canonicalJson(body))
}

function noExecution(value: FactorDefinition): boolean {
  return value.research_only === true && value.paper_admission === false && value.model_activation === false
    && value.live_trading_enabled === false && value.external_order_transmission === false
    && value.execution?.backtest_implemented === false && value.execution.costs_applied_to_diagnostic === false
    && value.execution.rules_certified === false && value.signal?.adapter_implemented === false
}

export async function checkedFactorCatalog(value: unknown): Promise<FactorCatalog> {
  if (!record(value) || value.schema_version !== 'qtrade.factor-strategy-catalog.v1'
    || value.live_trading_enabled !== false || value.external_order_transmission !== false
    || !record(value.worker) || !Array.isArray(value.templates) || !Array.isArray(value.datasets)
    || !Array.isArray(value.limitations) || value.templates.length < 1 || value.templates.length > 16
    || !value.templates.every(row => record(row) && row.schema_version === 'qtrade.factor-strategy-definition.v1'
      && record(row.spec) && record(row.factor_definition) && record(row.evaluation_context)
      && noExecution(row as unknown as FactorDefinition)) || !await sealed(value)) {
    throw new Error('因子目录身份或权限边界不匹配')
  }
  const body = Object.fromEntries(Object.entries(value).filter(([key]) => !['evidence_sha256', 'catalog_sha256'].includes(key)))
  const worker = Object.fromEntries(Object.entries(value.worker).filter(([key]) => key !== 'heartbeat_at'))
  if (value.catalog_sha256 !== await sha256Hex(canonicalJson({ ...body, worker }))) throw new Error('因子目录内容摘要不匹配')
  return value as unknown as FactorCatalog
}

export async function checkedFactorPreview(value: unknown, spec: FactorSpec, catalog: FactorCatalog): Promise<FactorPreview> {
  if (!record(value) || value.schema_version !== 'qtrade.factor-strategy-preview.v1'
    || value.live_trading_enabled !== false || value.external_order_transmission !== false
    || value.expected_catalog_sha256 !== catalog.catalog_sha256 || value.source_revision !== catalog.worker.source_revision
    || canonicalJson(value.spec) !== canonicalJson(spec) || !record(value.strategy_definition) || !await sealed(value)) {
    throw new Error('因子预览与当前规格不一致，请重新预览')
  }
  const definition = value.strategy_definition as unknown as FactorDefinition
  const { strategy_id, strategy_version } = definition
  const body = Object.fromEntries(Object.entries(definition).filter(([key]) => !['evidence_sha256', 'strategy_id', 'strategy_version'].includes(key)))
  if (!noExecution(definition) || canonicalJson(definition.spec) !== canonicalJson(spec)
    || definition.source.revision !== catalog.worker.source_revision || !await sealed(definition)
    || strategy_version !== await sha256Hex(canonicalJson(body)) || strategy_id !== 'factor-' + strategy_version
    || value.expected_strategy_version !== strategy_version) throw new Error('因子策略冻结合同校验失败')
  return value as unknown as FactorPreview
}
