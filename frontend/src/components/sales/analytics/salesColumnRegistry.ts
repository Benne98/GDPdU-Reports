import type { SalesColumnDef } from './salesTableTypes'

const STORAGE_PREFIX = 'finssentials.sales.columns.v1'

export const TOP_ORDERS_AMOUNT_FIELD = 'amount_keur'

export const TOP_ORDERS_CATALOG: SalesColumnDef[] = [
  { id: 'invoice_number', label: 'Invoice', field: 'invoice_number' },
  { id: 'customer_name', label: 'Customer', field: 'customer_name' },
  { id: 'invoice_date', label: 'Invoice date', field: 'invoice_date' },
  { id: 'due_date', label: 'Due date', field: 'due_date' },
  { id: 'product_count', label: 'Products', field: 'product_count' },
  { id: 'line_count', label: 'Lines', field: 'line_count' },
  { id: 'contact_name', label: 'Contact', field: 'contact_name' },
  { id: 'customer_location', label: 'Location', field: 'customer_location' },
  { id: 'entity', label: 'Entity', field: 'entity' },
  { id: 'segment', label: 'Segment', field: 'segment' },
  { id: 'product_revenue_model', label: 'Revenue model', field: 'product_revenue_model' },
  { id: 'gross_profit_keur', label: 'Gross profit (kEUR)', field: 'gross_profit_keur' },
  { id: 'gross_margin_pct', label: 'Gross margin %', field: 'gross_margin_pct' },
  { id: 'supplier_invoice_number', label: 'Supplier invoice', field: 'supplier_invoice_number' },
  { id: 'supplier_invoice_date', label: 'Supplier inv. date', field: 'supplier_invoice_date' },
  { id: 'gl_reference_document', label: 'GL reference', field: 'gl_reference_document' },
  { id: 'contract_start_date', label: 'Contract start', field: 'contract_start_date' },
  { id: 'contract_end_date', label: 'Contract end', field: 'contract_end_date' },
  { id: 'amount_keur', label: 'Amount (kEUR)', field: TOP_ORDERS_AMOUNT_FIELD },
]

/** Default visible columns when user has not customized table layout. */
export const TOP_ORDERS_DEFAULT_TABLE_IDS = [
  'invoice_number',
  'customer_name',
  'invoice_date',
  'due_date',
  'gross_profit_keur',
  'gross_margin_pct',
  'entity',
  'line_count',
  'amount_keur',
] as const

export const TOP_ORDERS_REPORT_FIELDS = ['invoice_number', 'customer_name', TOP_ORDERS_AMOUNT_FIELD] as const

/** Amount column always last (table builder + saved layouts). */

export function loadTopOrdersColumns(catalog: SalesColumnDef[]): SalesColumnDef[] {
  try {
    const raw = localStorage.getItem(storageKey('top-orders'))
    if (!raw) {
      const byId = new Map(catalog.map(c => [c.id, c]))
      const defaults = TOP_ORDERS_DEFAULT_TABLE_IDS.map(id => byId.get(id)).filter((c): c is SalesColumnDef => !!c)
      return orderTopOrdersColumns(defaults.length ? defaults : catalog)
    }
  } catch {
    /* ignore */
  }
  return orderTopOrdersColumns(loadSalesColumns('top-orders', catalog))
}

export function orderTopOrdersColumns(cols: SalesColumnDef[]): SalesColumnDef[] {
  const amount = cols.filter(c => c.field === TOP_ORDERS_AMOUNT_FIELD)
  const rest = cols.filter(c => c.field !== TOP_ORDERS_AMOUNT_FIELD)
  return [...rest, ...amount]
}

export function entityColumns(colLabels: { cm: string; pm: string; py_cm: string; ytd: string }): SalesColumnDef[] {
  return [
    { id: 'name', label: 'Name', field: 'name' },
    { id: 'cm', label: colLabels.cm, field: 'cm' },
    { id: 'pm', label: colLabels.pm, field: 'pm' },
    { id: 'py_cm', label: colLabels.py_cm, field: 'py_cm' },
    { id: 'ytd', label: colLabels.ytd, field: 'ytd' },
  ]
}

export const ENTITY_REPORT_FIELDS = ['name', 'cm', 'pm', 'ytd'] as const

type EntityColLabels = import('../../../lib/api').SalesTopEntitiesColLabels

function lbl(colLabels: EntityColLabels, key: keyof EntityColLabels, fallback: string): string {
  return (colLabels[key] as string | undefined) ?? fallback
}

/** Fin-style report columns (monthly) — Top Customers & Top Suppliers. */
export function entityReportColumnsMonth(
  colLabels: EntityColLabels,
  partnerLabel = 'Customer',
): SalesColumnDef[] {
  return [
    { id: 'name', label: partnerLabel, field: 'name' },
    { id: 'cm', label: colLabels.cm, field: 'cm' },
    { id: 'plan_cm', label: lbl(colLabels, 'plan_cm', 'Plan'), field: 'plan_cm' },
    { id: 'delta_cm_pm', label: lbl(colLabels, 'delta_cm_pm', 'Δ CM−PM'), field: 'delta_cm_pm' },
    { id: 'ytd', label: colLabels.ytd, field: 'ytd' },
    { id: 'ytd_plan', label: lbl(colLabels, 'ytd_plan', 'YTD Plan'), field: 'ytd_plan' },
    { id: 'coverage', label: lbl(colLabels, 'coverage', 'Coverage'), field: 'coverage' },
  ]
}

/** Fin-style report columns (weekly). */
export function entityReportColumnsWeek(
  colLabels: EntityColLabels,
  partnerLabel = 'Customer',
): SalesColumnDef[] {
  return [
    { id: 'name', label: partnerLabel, field: 'name' },
    { id: 'cm', label: colLabels.cm, field: 'cm' },
    { id: 'delta_cm_pm', label: lbl(colLabels, 'delta_cm_pm', 'Δ CW'), field: 'delta_cm_pm' },
    { id: 'mtd', label: lbl(colLabels, 'mtd', 'MTD'), field: 'mtd' },
    { id: 'plan_cm', label: lbl(colLabels, 'plan_cm', 'Plan'), field: 'plan_cm' },
    { id: 'coverage', label: lbl(colLabels, 'coverage', 'Coverage'), field: 'coverage' },
  ]
}

/** Fin-style full table (monthly). */
export function entityTableColumnsMonth(
  colLabels: EntityColLabels,
  partnerLabel = 'Customer',
): SalesColumnDef[] {
  return [
    { id: 'name', label: partnerLabel, field: 'name' },
    { id: 'py_cm', label: colLabels.py_cm, field: 'py_cm' },
    { id: 'pm', label: colLabels.pm, field: 'pm' },
    { id: 'cm', label: colLabels.cm, field: 'cm' },
    { id: 'plan_cm', label: lbl(colLabels, 'plan_cm', 'Plan'), field: 'plan_cm' },
    { id: 'delta_cm_pm', label: lbl(colLabels, 'delta_cm_pm', 'Δ'), field: 'delta_cm_pm' },
    { id: 'delta_cm_py', label: lbl(colLabels, 'delta_cm_py', 'Δ PY'), field: 'delta_cm_py' },
    { id: 'ytd_py', label: lbl(colLabels, 'ytd_py', 'YTD PY'), field: 'ytd_py' },
    { id: 'ytd', label: colLabels.ytd, field: 'ytd' },
    { id: 'delta_ytd', label: lbl(colLabels, 'delta_ytd', 'Δ YTD'), field: 'delta_ytd' },
    { id: 'ytd_plan', label: lbl(colLabels, 'ytd_plan', 'YTD Plan'), field: 'ytd_plan' },
    { id: 'coverage', label: lbl(colLabels, 'coverage', 'Coverage'), field: 'coverage' },
  ]
}

/** Fin-style full table (weekly). */
export function entityTableColumnsWeek(
  colLabels: EntityColLabels,
  partnerLabel = 'Customer',
): SalesColumnDef[] {
  return [
    { id: 'name', label: partnerLabel, field: 'name' },
    { id: 'py_cm', label: colLabels.py_cm, field: 'py_cm' },
    { id: 'pm', label: colLabels.pm, field: 'pm' },
    { id: 'cm', label: colLabels.cm, field: 'cm' },
    { id: 'delta_cm_py', label: lbl(colLabels, 'delta_cm_py', 'Δ PY'), field: 'delta_cm_py' },
    { id: 'delta_cm_pm', label: lbl(colLabels, 'delta_cm_pm', 'Δ PW'), field: 'delta_cm_pm' },
    { id: 'mtd_py', label: lbl(colLabels, 'mtd_py', 'MTD PY'), field: 'mtd_py' },
    { id: 'mtd_pm', label: lbl(colLabels, 'mtd_pm', 'MTD PM'), field: 'mtd_pm' },
    { id: 'mtd', label: lbl(colLabels, 'mtd', 'MTD'), field: 'mtd' },
    { id: 'delta_mtd', label: lbl(colLabels, 'delta_mtd', 'Δ MTD'), field: 'delta_mtd' },
    { id: 'plan_cm', label: lbl(colLabels, 'plan_cm', 'Plan'), field: 'plan_cm' },
    { id: 'coverage', label: lbl(colLabels, 'coverage', 'Coverage'), field: 'coverage' },
  ]
}

/** Keep partner name fixed first; table body always renders name separately from data columns. */
export function normalizeEntityColumns(
  cols: SalesColumnDef[],
  catalog: SalesColumnDef[],
): SalesColumnDef[] {
  const nameCol =
    catalog.find(c => c.field === 'name') ?? cols.find(c => c.field === 'name')
  const data = cols.filter(c => c.field !== 'name')
  return nameCol ? [nameCol, ...data] : data
}

export type SalesColumnGroup = { title: string; fieldIds: string[] }

export const ENTITY_TABLE_GROUPS_MONTH: SalesColumnGroup[] = [
  { title: 'Period amounts', fieldIds: ['py_cm', 'pm', 'cm', 'ytd_py', 'ytd'] },
  { title: 'Plan & coverage', fieldIds: ['plan_cm', 'ytd_plan', 'coverage'] },
  { title: 'Variance', fieldIds: ['delta_cm_pm', 'delta_cm_py', 'delta_ytd'] },
]

export const ENTITY_TABLE_GROUPS_WEEK: SalesColumnGroup[] = [
  { title: 'Period amounts', fieldIds: ['py_cm', 'pm', 'cm', 'mtd_py', 'mtd_pm', 'mtd'] },
  { title: 'Plan & coverage', fieldIds: ['plan_cm', 'coverage'] },
  { title: 'Variance', fieldIds: ['delta_cm_py', 'delta_cm_pm', 'delta_mtd'] },
]

function storageKey(tableId: string): string {
  return `${STORAGE_PREFIX}.${tableId}`
}

export function loadSalesColumns(tableId: string, catalog: SalesColumnDef[]): SalesColumnDef[] {
  try {
    const raw = localStorage.getItem(storageKey(tableId))
    if (!raw) return catalog
    const ids: string[] = JSON.parse(raw)
    if (!Array.isArray(ids) || !ids.length) return catalog
    const byId = new Map(catalog.map(c => [c.id, c]))
    const ordered = ids.map(id => byId.get(id)).filter((c): c is SalesColumnDef => !!c)
    const rest = catalog.filter(c => !ids.includes(c.id))
    return [...ordered, ...rest]
  } catch {
    return catalog
  }
}

export function saveSalesColumns(tableId: string, columns: SalesColumnDef[]): void {
  try {
    localStorage.setItem(storageKey(tableId), JSON.stringify(columns.map(c => c.id)))
  } catch {
    /* ignore quota */
  }
}

export function reconcileSalesColumns(saved: SalesColumnDef[], catalog: SalesColumnDef[]): SalesColumnDef[] {
  const byId = new Map(catalog.map(c => [c.id, c]))
  const out: SalesColumnDef[] = []
  for (const c of saved) {
    const fresh = byId.get(c.id)
    if (fresh) out.push(fresh)
  }
  for (const c of catalog) {
    if (!out.some(x => x.id === c.id)) out.push(c)
  }
  return out
}
