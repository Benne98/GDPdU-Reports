import type { SalesColumnDef } from './salesTableTypes'
import { loadSalesColumns, saveSalesColumns } from './salesColumnRegistry'

export const SALES_ANALYTICS_DIMS = [
  { key: 'end_customer_region', label: 'Region' },
  { key: 'end_customer_city', label: 'City' },
  { key: 'end_customer_name', label: 'Customer' },
  { key: 'entity', label: 'Entity' },
] as const

export type SalesAnalyticsDimKey = (typeof SALES_ANALYTICS_DIMS)[number]['key']

export function analyticsDimLabel(key: string): string {
  return SALES_ANALYTICS_DIMS.find(d => d.key === key)?.label ?? key
}

const COMPOSITION_TABLE_ID = 'sales-composition-dims'

export const COMPOSITION_DIM_CATALOG: SalesColumnDef[] = [
  { id: 'end_customer_region', label: 'Region', field: 'end_customer_region' },
  { id: 'end_customer_city', label: 'City', field: 'end_customer_city' },
  { id: 'end_customer_name', label: 'Customer', field: 'end_customer_name' },
  { id: 'entity', label: 'Entity', field: 'entity' },
]

export const COMPOSITION_DIM_DEFAULT_IDS = [
  'end_customer_region',
  'end_customer_city',
  'end_customer_name',
  'entity',
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
