import { SALES_ANALYTICS_DIMS } from './salesChartRegistry'
import { saveSalesColumns } from './salesColumnRegistry'
import type { SalesBreakdownRow } from '../../../lib/api'

const VALID_DIM_KEYS = new Set([
  'end_customer_region',
  'end_customer_city',
  'end_customer_name',
  'entity',
])

export function sanitizeBreakdownDims(dims: BreakdownDimConfig): BreakdownDimConfig {
  const dim_top = VALID_DIM_KEYS.has(dims.dim_top) ? dims.dim_top : DEFAULT_BREAKDOWN_DIMS.dim_top
  const dim_bottom = VALID_DIM_KEYS.has(dims.dim_bottom) ? dims.dim_bottom : DEFAULT_BREAKDOWN_DIMS.dim_bottom
  let dim_mid = typeof dims.dim_mid === 'string' ? dims.dim_mid.trim() : ''
  if (dim_mid && !VALID_DIM_KEYS.has(dim_mid)) dim_mid = ''
  if (dim_mid === dim_top || dim_mid === dim_bottom) dim_mid = ''
  return { dim_top, dim_mid, dim_bottom }
}

export type BreakdownMetricBlock = 'gross_sales' | 'gross_profit' | 'gross_margin'

export type BreakdownSubColumn = 'pm' | 'cm' | 'plan' | 'delta'

export type BreakdownColumnDef = {
  id: string
  block: BreakdownMetricBlock
  sub: BreakdownSubColumn
  label: string
  field: string
  isPct?: boolean
}

export type BreakdownDimConfig = {
  dim_top: string
  dim_mid: string
  dim_bottom: string
}

export type BreakdownMiscConfig = {
  /** Collapse excess middle-dimension siblings into one Miscellaneous L2 row per L1. */
  l2_enabled: boolean
  l2_limit: number
  /** Collapse excess bottom-dimension leaves into Miscellaneous (default for customer dims). */
  l3_enabled: boolean
  l3_limit: number
}

const MISC_STORAGE_KEY = 'finssentials.sales.breakdown.misc.v1'

export function isCustomerBottomDim(dimBottom: string): boolean {
  return dimBottom === 'end_customer_name'
}

export function defaultBreakdownMisc(dimBottom: string): BreakdownMiscConfig {
  return {
    l2_enabled: false,
    l2_limit: 8,
    l3_enabled: isCustomerBottomDim(dimBottom),
    l3_limit: 10,
  }
}

export type BreakdownColLabels = {
  pm: string
  cm: string
  plan_cm: string
  delta_cm_pm?: string
}

const STORAGE_ID = 'sales-breakdown-table-v2'
const DIMS_STORAGE_KEY = 'finssentials.sales.breakdown.dims.v2'

export const BREAKDOWN_DIM_OPTIONS = SALES_ANALYTICS_DIMS.map(d => ({
  key: d.key,
  label: d.label,
}))

export const BREAKDOWN_DIM_BOTTOM_OPTIONS = BREAKDOWN_DIM_OPTIONS

export const DEFAULT_BREAKDOWN_DIMS: BreakdownDimConfig = {
  dim_top: 'end_customer_region',
  dim_mid: 'end_customer_city',
  dim_bottom: 'end_customer_name',
}

const BLOCK_LABELS: Record<BreakdownMetricBlock, string> = {
  gross_sales: 'Gross Sales',
  gross_profit: 'Gross Profit',
  gross_margin: 'Gross Margin',
}

const SUB_FIELDS: Record<BreakdownMetricBlock, Record<BreakdownSubColumn, { field: string; isPct?: boolean }>> = {
  gross_sales: {
    pm: { field: 'gs_pm' },
    cm: { field: 'gs_cm' },
    plan: { field: 'gs_plan_cm' },
    delta: { field: 'delta_gs_cm_pm' },
  },
  gross_profit: {
    pm: { field: 'gp_pm' },
    cm: { field: 'gp_cm' },
    plan: { field: 'gp_plan_cm' },
    delta: { field: 'delta_gp_cm_pm' },
  },
  gross_margin: {
    pm: { field: 'gm_pm', isPct: true },
    cm: { field: 'gm_cm', isPct: true },
    plan: { field: 'gm_plan_cm', isPct: true },
    delta: { field: 'delta_gm_cm_pm', isPct: true },
  },
}

const ROW_METRIC_FIELDS: (keyof SalesBreakdownRow)[] = [
  'gs_pm', 'gs_cm', 'gp_pm', 'gp_cm',
  'gs_plan_cm', 'gp_plan_cm',
  'delta_gs_cm_pm', 'delta_gp_cm_pm', 'delta_gm_cm_pm',
]

const ZERO_EPS = 1e-6

export function breakdownRowHasMetrics(row: SalesBreakdownRow): boolean {
  for (const key of ROW_METRIC_FIELDS) {
    const v = row[key]
    if (typeof v === 'number' && Math.abs(v) > ZERO_EPS) return true
  }
  return false
}

export function filterNonZeroBreakdownRows(rows: SalesBreakdownRow[]): SalesBreakdownRow[] {
  return rows.filter(breakdownRowHasMetrics)
}

export function buildBreakdownColumnCatalog(colLabels: BreakdownColLabels): BreakdownColumnDef[] {
  const deltaLabel = colLabels.delta_cm_pm ?? `${colLabels.cm} - ${colLabels.pm}`
  const out: BreakdownColumnDef[] = []
  for (const block of Object.keys(BLOCK_LABELS) as BreakdownMetricBlock[]) {
    for (const sub of ['pm', 'cm', 'plan', 'delta'] as BreakdownSubColumn[]) {
      const meta = SUB_FIELDS[block][sub]
      const subLabel =
        sub === 'pm' ? colLabels.pm
        : sub === 'cm' ? colLabels.cm
        : sub === 'plan' ? colLabels.plan_cm
        : deltaLabel
      out.push({
        id: `${block}_${sub}`,
        block,
        sub,
        label: subLabel,
        field: meta.field,
        isPct: meta.isPct,
      })
    }
  }
  return out
}

/** Default visible columns: PM, CM, Δ per block — no plan. */
export function defaultBreakdownColumns(colLabels: BreakdownColLabels): BreakdownColumnDef[] {
  return buildBreakdownColumnCatalog(colLabels).filter(c => c.sub !== 'plan')
}

export function loadBreakdownMisc(dimBottom: string): BreakdownMiscConfig {
  const base = defaultBreakdownMisc(dimBottom)
  try {
    const raw = localStorage.getItem(MISC_STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as BreakdownMiscConfig
      return {
        l2_enabled: !!parsed.l2_enabled,
        l2_limit: Math.max(1, Number(parsed.l2_limit) || base.l2_limit),
        l3_enabled: parsed.l3_enabled ?? base.l3_enabled,
        l3_limit: Math.max(1, Number(parsed.l3_limit) || base.l3_limit),
      }
    }
  } catch { /* ignore */ }
  return base
}

export function saveBreakdownMisc(misc: BreakdownMiscConfig): void {
  try {
    localStorage.setItem(MISC_STORAGE_KEY, JSON.stringify(misc))
  } catch { /* ignore */ }
}

export function loadBreakdownConfig(colLabels: BreakdownColLabels): {
  dims: BreakdownDimConfig
  columns: BreakdownColumnDef[]
  misc: BreakdownMiscConfig
} {
  const catalog = buildBreakdownColumnCatalog(colLabels)
  let dims = { ...DEFAULT_BREAKDOWN_DIMS }
  try {
    const raw = localStorage.getItem(DIMS_STORAGE_KEY)
    if (raw) {
      const parsed = JSON.parse(raw) as BreakdownDimConfig
      dims = sanitizeBreakdownDims({ ...dims, ...parsed })
    }
  } catch { /* ignore */ }

  let columns = defaultBreakdownColumns(colLabels)
  try {
    const raw = localStorage.getItem(`finssentials.sales.columns.v1.${STORAGE_ID}`)
    if (raw) {
      const ids: string[] = JSON.parse(raw)
      if (Array.isArray(ids) && ids.length) {
        const byId = new Map(catalog.map(c => [c.id, c]))
        const ordered = ids.map(id => byId.get(id)).filter((c): c is BreakdownColumnDef => !!c)
        if (ordered.length) columns = sortBreakdownColumns(ordered)
      }
    }
  } catch { /* ignore */ }

  const misc = loadBreakdownMisc(dims.dim_bottom)
  return { dims, columns, misc }
}

export function saveBreakdownDims(dims: BreakdownDimConfig): void {
  try {
    localStorage.setItem(DIMS_STORAGE_KEY, JSON.stringify(dims))
  } catch { /* ignore */ }
}

export function saveBreakdownColumns(columns: BreakdownColumnDef[]): void {
  saveSalesColumns(
    STORAGE_ID,
    columns.map(c => ({ id: c.id, label: c.label, field: c.field })),
  )
}

export function visibleBlocks(columns: BreakdownColumnDef[]): BreakdownMetricBlock[] {
  const blocks: BreakdownMetricBlock[] = []
  for (const b of ['gross_sales', 'gross_profit', 'gross_margin'] as BreakdownMetricBlock[]) {
    if (columns.some(c => c.block === b)) blocks.push(b)
  }
  return blocks
}

export function columnsForBlock(columns: BreakdownColumnDef[], block: BreakdownMetricBlock): BreakdownColumnDef[] {
  const order: BreakdownSubColumn[] = ['pm', 'cm', 'plan', 'delta']
  return order.map(sub => columns.find(c => c.block === block && c.sub === sub)).filter((c): c is BreakdownColumnDef => !!c)
}

const BLOCK_ORDER: BreakdownMetricBlock[] = ['gross_sales', 'gross_profit', 'gross_margin']
const SUB_ORDER: BreakdownSubColumn[] = ['pm', 'cm', 'plan', 'delta']

export function sortBreakdownColumns(columns: BreakdownColumnDef[]): BreakdownColumnDef[] {
  const rank = (c: BreakdownColumnDef) => {
    const bi = BLOCK_ORDER.indexOf(c.block)
    const si = SUB_ORDER.indexOf(c.sub)
    return bi * 10 + si
  }
  return [...columns].sort((a, b) => rank(a) - rank(b))
}

export function blockHasColumn(columns: BreakdownColumnDef[], block: BreakdownMetricBlock): boolean {
  return columns.some(c => c.block === block)
}

export function blockHasSubColumn(
  columns: BreakdownColumnDef[],
  block: BreakdownMetricBlock,
  sub: BreakdownSubColumn,
): boolean {
  return columns.some(c => c.block === block && c.sub === sub)
}

/** Toggle a single period column within a metric block; keeps PM→CM→Plan→Δ order. */
export function toggleBreakdownSubColumn(
  columns: BreakdownColumnDef[],
  catalog: BreakdownColumnDef[],
  block: BreakdownMetricBlock,
  sub: BreakdownSubColumn,
  on: boolean,
): BreakdownColumnDef[] {
  const def = catalog.find(c => c.block === block && c.sub === sub)
  if (!def) return columns
  if (on) {
    if (columns.some(c => c.id === def.id)) return columns
    return sortBreakdownColumns([...columns, def])
  }
  const next = columns.filter(c => c.id !== def.id)
  return next.length ? next : columns
}

/** Enable/disable all columns for a metric block (on → default PM+CM+Δ). */
export function toggleBreakdownBlock(
  columns: BreakdownColumnDef[],
  catalog: BreakdownColumnDef[],
  block: BreakdownMetricBlock,
  on: boolean,
): BreakdownColumnDef[] {
  if (on) {
    const defaults: BreakdownSubColumn[] = ['pm', 'cm', 'delta']
    let next = columns.filter(c => c.block !== block)
    for (const sub of defaults) {
      const def = catalog.find(c => c.block === block && c.sub === sub)
      if (def) next.push(def)
    }
    return sortBreakdownColumns(next)
  }
  const next = columns.filter(c => c.block !== block)
  return next.length ? next : columns
}

/** Page title: "Entity, Product Family and End Customer". */
export function breakdownTitle(
  dims: BreakdownDimConfig,
  dimLabels: { top: string; mid?: string | null; bottom: string },
): string {
  const parts: string[] = [dimLabels.top]
  if (dims.dim_mid && dimLabels.mid) parts.push(dimLabels.mid)
  parts.push(dimLabels.bottom)
  if (parts.length === 1) return parts[0]
  if (parts.length === 2) return `${parts[0]} and ${parts[1]}`
  return `${parts[0]}, ${parts[1]} and ${parts[2]}`
}
