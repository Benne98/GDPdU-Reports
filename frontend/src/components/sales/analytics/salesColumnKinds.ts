export type SalesColumnKind = 'label' | 'amount' | 'delta' | 'pct'

export function salesColumnKind(field: string): SalesColumnKind {
  if (field === 'name') return 'label'
  if (field === 'coverage') return 'pct'
  if (field.startsWith('delta_')) return 'delta'
  return 'amount'
}

/** CM / YTD (month) or CM / MTD (week) — mirrors Income Statement period highlight. */
export function isSalesHighlightColumn(field: string, periodGrain: 'month' | 'week'): boolean {
  if (periodGrain === 'week') return field === 'cm' || field === 'mtd'
  return field === 'cm' || field === 'ytd'
}

export function isDeltaField(field: string): boolean {
  return salesColumnKind(field) === 'delta'
}
