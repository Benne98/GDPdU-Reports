import type { ReceivablesAgingDimension, ReceivablesDimensionView } from '../../../../lib/api'

export type ReceivablesHierarchyDim = ReceivablesAgingDimension | 'invoice_number'

export type AgingBreakdownMetric =
  | 'total'
  | 'not_yet_due'
  | 'overdue_1_30'
  | 'overdue_31_60'
  | 'overdue_61_90'
  | 'overdue_91_180'
  | 'overdue_over_180'
  | 'before_due'
  | 'overdue'
  | 'overdue_pct'

export type AgingBreakdownSub = 'cm' | 'pm' | 'py' | 'delta_pm' | 'delta_py'

export type AgingBreakdownColumnDef = {
  id: string
  metric: AgingBreakdownMetric
  sub: AgingBreakdownSub
  label: string
  field: string
  isPct?: boolean
}

export type ReceivablesDimensionBreakdownConfig = {
  hierarchy: ReceivablesHierarchyDim[]
  view: ReceivablesDimensionView
  columns: AgingBreakdownColumnDef[]
}

const STORAGE_KEY = 'finssentials.receivables.dimension-breakdown.v2'

export const RECEIVABLES_HIERARCHY_DIM_OPTIONS: { id: ReceivablesHierarchyDim; label: string }[] = [
  { id: 'entity', label: 'Entity' },
  { id: 'segment', label: 'Segment' },
  { id: 'customer', label: 'Customer' },
  { id: 'country', label: 'Country' },
  { id: 'customer_group', label: 'Customer group' },
  { id: 'salesperson', label: 'Salesperson' },
  { id: 'invoice_number', label: 'Invoice number' },
]

export function hierarchyDimLabel(dim: ReceivablesHierarchyDim): string {
  return RECEIVABLES_HIERARCHY_DIM_OPTIONS.find(d => d.id === dim)?.label ?? dim
}

/** Page title: "Entity, country and customer" (first dim capitalized). */
export function receivablesBreakdownTitle(hierarchy: ReceivablesHierarchyDim[]): string {
  if (hierarchy.length === 0) return 'dimension'
  const parts = hierarchy.map((dim, i) =>
    i === 0 ? hierarchyDimLabel(dim) : hierarchyDimLabel(dim).toLowerCase(),
  )
  if (parts.length === 1) return parts[0]
  if (parts.length === 2) return `${parts[0]} and ${parts[1]}`
  return `${parts.slice(0, -1).join(', ')} and ${parts[parts.length - 1]}`
}

/** Table label column header: "Entity / Country / Customer". */
export function receivablesBreakdownLabelHeader(hierarchy: ReceivablesHierarchyDim[]): string {
  return hierarchy.map(hierarchyDimLabel).join(' / ')
}

/** Invoice number may only appear as the last hierarchy level. */
export function canAddHierarchyDim(
  dim: ReceivablesHierarchyDim,
  hierarchy: ReceivablesHierarchyDim[],
): boolean {
  if (hierarchy.includes(dim)) return false
  if (dim === 'invoice_number' && hierarchy.length === 0) return true
  if (dim === 'invoice_number') return true
  if (hierarchy.includes('invoice_number')) return false
  return true
}

const BUCKET_METRICS: { metric: AgingBreakdownMetric; label: string }[] = [
  { metric: 'total', label: 'Total' },
  { metric: 'not_yet_due', label: 'Not yet due' },
  { metric: 'overdue_1_30', label: '1–30 overdue' },
  { metric: 'overdue_31_60', label: '31–60 overdue' },
  { metric: 'overdue_61_90', label: '61–90 overdue' },
  { metric: 'overdue_91_180', label: '91–180 overdue' },
  { metric: 'overdue_over_180', label: '>180 overdue' },
  { metric: 'overdue_pct', label: 'Overdue %' },
]

const DUE_METRICS: { metric: AgingBreakdownMetric; label: string }[] = [
  { metric: 'total', label: 'Total' },
  { metric: 'before_due', label: 'Not yet due' },
  { metric: 'overdue', label: 'Overdue' },
  { metric: 'overdue_pct', label: 'Overdue %' },
]

const SUB_OPTIONS: { sub: AgingBreakdownSub; title: string }[] = [
  { sub: 'cm', title: 'Current period' },
  { sub: 'pm', title: 'Prior month' },
  { sub: 'py', title: 'Prior year (same month)' },
  { sub: 'delta_pm', title: 'Delta vs prior month' },
  { sub: 'delta_py', title: 'Delta vs prior year' },
]

function fieldFor(metric: AgingBreakdownMetric, sub: AgingBreakdownSub): string {
  if (sub === 'cm') return metric
  if (sub === 'pm') return `pm_${metric}`
  if (sub === 'py') return `py_${metric}`
  if (sub === 'delta_pm') return `delta_pm_${metric}`
  return `delta_py_${metric}`
}

function subLabel(sub: AgingBreakdownSub, metricLabel: string): string {
  if (sub === 'cm') return metricLabel
  if (sub === 'pm') return `${metricLabel} (PM)`
  if (sub === 'py') return `${metricLabel} (PY)`
  if (sub === 'delta_pm') return `Δ ${metricLabel} vs PM`
  return `Δ ${metricLabel} vs PY`
}

export function metricsForView(view: ReceivablesDimensionView) {
  return view === 'due_overdue' ? DUE_METRICS : BUCKET_METRICS
}

export function buildColumnCatalog(view: ReceivablesDimensionView): AgingBreakdownColumnDef[] {
  const metrics = metricsForView(view)
  const out: AgingBreakdownColumnDef[] = []
  for (const m of metrics) {
    for (const s of SUB_OPTIONS) {
      out.push({
        id: `${m.metric}_${s.sub}`,
        metric: m.metric,
        sub: s.sub,
        label: subLabel(s.sub, m.label),
        field: fieldFor(m.metric, s.sub),
        isPct: m.metric === 'overdue_pct',
      })
    }
  }
  return out
}

const DEFAULT_BUCKET_COLUMN_METRICS: AgingBreakdownMetric[] = [
  'not_yet_due',
  'overdue_1_30',
  'overdue_31_60',
  'overdue_61_90',
  'overdue_91_180',
  'overdue_over_180',
  'overdue_pct',
  'total',
]

export function defaultBreakdownColumns(view: ReceivablesDimensionView): AgingBreakdownColumnDef[] {
  const catalog = buildColumnCatalog(view)
  const pick = (metric: AgingBreakdownMetric, sub: AgingBreakdownSub = 'cm') =>
    catalog.find(c => c.metric === metric && c.sub === sub)!
  if (view === 'due_overdue') {
    return [pick('before_due'), pick('overdue'), pick('overdue_pct'), pick('total')]
  }
  return DEFAULT_BUCKET_COLUMN_METRICS.map(m => pick(m))
}

export const DEFAULT_RECEIVABLES_BREAKDOWN_CONFIG: ReceivablesDimensionBreakdownConfig = {
  hierarchy: ['entity', 'country', 'customer'],
  view: 'buckets',
  columns: defaultBreakdownColumns('buckets'),
}

function sanitizeHierarchy(raw: unknown): ReceivablesHierarchyDim[] {
  const valid = new Set(RECEIVABLES_HIERARCHY_DIM_OPTIONS.map(d => d.id))
  const list = Array.isArray(raw)
    ? raw.filter((d): d is ReceivablesHierarchyDim => typeof d === 'string' && valid.has(d as ReceivablesHierarchyDim))
    : []
  if (!list.length) return DEFAULT_RECEIVABLES_BREAKDOWN_CONFIG.hierarchy
  const invIdx = list.indexOf('invoice_number')
  if (invIdx >= 0 && invIdx !== list.length - 1) {
    const without = list.filter(d => d !== 'invoice_number')
    return [...without, 'invoice_number']
  }
  return list
}

function sanitizeColumns(raw: unknown, view: ReceivablesDimensionView): AgingBreakdownColumnDef[] {
  const catalog = buildColumnCatalog(view)
  const byId = new Map(catalog.map(c => [c.id, c]))
  if (!Array.isArray(raw)) return defaultBreakdownColumns(view)
  const cols = raw
    .map(item => (typeof item === 'object' && item && 'id' in item ? byId.get(String((item as { id: string }).id)) : undefined))
    .filter((c): c is AgingBreakdownColumnDef => !!c)
  return cols.length ? cols : defaultBreakdownColumns(view)
}

export function loadReceivablesBreakdownConfig(): ReceivablesDimensionBreakdownConfig {
  try {
    const raw = localStorage.getItem(STORAGE_KEY)
    if (!raw) return { ...DEFAULT_RECEIVABLES_BREAKDOWN_CONFIG }
    const parsed = JSON.parse(raw) as Partial<ReceivablesDimensionBreakdownConfig>
    const view: ReceivablesDimensionView = parsed.view === 'due_overdue' ? 'due_overdue' : 'buckets'
    return {
      hierarchy: sanitizeHierarchy(parsed.hierarchy),
      view,
      columns: sanitizeColumns(parsed.columns, view),
    }
  } catch {
    return { ...DEFAULT_RECEIVABLES_BREAKDOWN_CONFIG }
  }
}

export function saveReceivablesBreakdownConfig(cfg: ReceivablesDimensionBreakdownConfig): void {
  localStorage.setItem(STORAGE_KEY, JSON.stringify(cfg))
}

export function metricHasSubColumn(
  columns: AgingBreakdownColumnDef[],
  metric: AgingBreakdownMetric,
  sub: AgingBreakdownSub,
): boolean {
  return columns.some(c => c.metric === metric && c.sub === sub)
}

export function toggleMetricSubColumn(
  columns: AgingBreakdownColumnDef[],
  catalog: AgingBreakdownColumnDef[],
  metric: AgingBreakdownMetric,
  sub: AgingBreakdownSub,
  on: boolean,
): AgingBreakdownColumnDef[] {
  const def = catalog.find(c => c.metric === metric && c.sub === sub)
  if (!def) return columns
  const has = columns.some(c => c.id === def.id)
  if (on && !has) {
    return [...columns, def]
  }
  if (!on && has) return columns.filter(c => c.id !== def.id)
  return columns
}

export function metricEnabled(columns: AgingBreakdownColumnDef[], metric: AgingBreakdownMetric): boolean {
  return columns.some(c => c.metric === metric)
}

export function toggleMetricBlock(
  columns: AgingBreakdownColumnDef[],
  catalog: AgingBreakdownColumnDef[],
  metric: AgingBreakdownMetric,
  on: boolean,
): AgingBreakdownColumnDef[] {
  if (on) {
    const cm = catalog.find(c => c.metric === metric && c.sub === 'cm')
    if (!cm || columns.some(c => c.id === cm.id)) return columns
    return toggleMetricSubColumn(columns, catalog, metric, 'cm', true)
  }
  return columns.filter(c => c.metric !== metric)
}

export function needsComparePm(columns: AgingBreakdownColumnDef[]): boolean {
  return columns.some(c => c.sub === 'pm' || c.sub === 'delta_pm')
}

export function needsComparePy(columns: AgingBreakdownColumnDef[]): boolean {
  return columns.some(c => c.sub === 'py' || c.sub === 'delta_py')
}

export const SUB_OPTIONS_UI = SUB_OPTIONS

export function moveBreakdownColumn(
  columns: AgingBreakdownColumnDef[],
  columnId: string,
  dir: -1 | 1,
): AgingBreakdownColumnDef[] {
  const idx = columns.findIndex(c => c.id === columnId)
  if (idx < 0) return columns
  const swap = idx + dir
  if (swap < 0 || swap >= columns.length) return columns
  const next = [...columns]
  ;[next[idx], next[swap]] = [next[swap], next[idx]]
  return next
}
