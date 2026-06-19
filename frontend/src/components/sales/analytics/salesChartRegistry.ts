import type { SalesColumnDef } from './salesTableTypes'
import { loadSalesColumns, saveSalesColumns } from './salesColumnRegistry'

export const SALES_ANALYTICS_DIMS = [
  { key: 'end_customer_region', label: 'End customer region' },
  { key: 'segment', label: 'Segment' },
  { key: 'product_revenue_model', label: 'Product revenue model' },
  { key: 'entity', label: 'Entity' },
  { key: 'customer_group', label: 'Customer group' },
  { key: 'product_family', label: 'Product family' },
  { key: 'product_line', label: 'Product line' },
] as const

export type SalesAnalyticsDimKey = (typeof SALES_ANALYTICS_DIMS)[number]['key']

export function analyticsDimLabel(key: string): string {
  return SALES_ANALYTICS_DIMS.find(d => d.key === key)?.label ?? key
}

const COMPOSITION_TABLE_ID = 'sales-composition-dims'

export const COMPOSITION_DIM_CATALOG: SalesColumnDef[] = [
  { id: 'entity', label: 'Entity', field: 'entity' },
  { id: 'end_customer_region', label: 'End customer region', field: 'end_customer_region' },
  { id: 'product_family', label: 'Product family', field: 'product_family' },
  { id: 'top_customers', label: 'Top customers', field: 'top_customers' },
  { id: 'segment', label: 'Segment', field: 'segment' },
  { id: 'product_revenue_model', label: 'Product revenue model', field: 'product_revenue_model' },
  { id: 'customer_group', label: 'Customer group', field: 'customer_group' },
  { id: 'product_line', label: 'Product line', field: 'product_line' },
]

export const COMPOSITION_DIM_DEFAULT_IDS = [
  'entity',
  'end_customer_region',
  'product_family',
  'top_customers',
]

function defaultCompositionDims(): SalesColumnDef[] {
  return COMPOSITION_DIM_DEFAULT_IDS.map(
    id => COMPOSITION_DIM_CATALOG.find(c => c.id === id)!,
  )
}

export function loadCompositionDims(): SalesColumnDef[] {
  const loaded = loadSalesColumns(COMPOSITION_TABLE_ID, COMPOSITION_DIM_CATALOG)
  const isUnconfigured =
    loaded.length === COMPOSITION_DIM_CATALOG.length
    && loaded.every((c, i) => c.id === COMPOSITION_DIM_CATALOG[i]?.id)
  try {
    const raw = localStorage.getItem(`finssentials.sales.columns.v1.${COMPOSITION_TABLE_ID}`)
    if (!raw || isUnconfigured) return defaultCompositionDims()
  } catch {
    return defaultCompositionDims()
  }
  return loaded.slice(0, 6)
}

export function saveCompositionDims(cols: SalesColumnDef[]): void {
  saveSalesColumns(COMPOSITION_TABLE_ID, cols)
}

export type CompositionMetric = 'gross_sales' | 'gross_profit' | 'gross_margin'

export const COMPOSITION_METRIC_LABELS: Record<CompositionMetric, string> = {
  gross_sales: 'Gross sales',
  gross_profit: 'Gross profit',
  gross_margin: 'Gross margin',
}
