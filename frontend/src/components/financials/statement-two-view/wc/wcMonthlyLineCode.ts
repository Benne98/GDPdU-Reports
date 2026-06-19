import type { MonthlyRow } from '../../../../lib/api'

const WC_AGGREGATE_BOLD_LABELS = new Set([
  'Trade Working Capital',
  'Other Working Capital',
  'Net working capital',
])

/** Only TWC / OWC / NWC are emphasised in the WC monthly table. */
export function isWcMonthlyBoldRow(row: MonthlyRow): boolean {
  if (row.id === 'wc-net-total') return true
  const lbl = row.label?.trim() ?? ''
  return WC_AGGREGATE_BOLD_LABELS.has(lbl)
}

/** Mapping lines only — same rule as narrative markers (no TWC/OWC/NWC/KPI). */
export function monthlyRowToLineCode(row: MonthlyRow): string | null {
  if (!row.id.startsWith('wc-')) return null
  if (row.row_kind !== 'line') return null
  if (row.id === 'wc-net-total') return null
  return row.id
}

export function isMonthlyRowClickable(row: MonthlyRow): boolean {
  return monthlyRowToLineCode(row) != null
}

export function monthlyPeriodKey(year: number, month: number): string {
  return `${year}-${String(month).padStart(2, '0')}`
}
