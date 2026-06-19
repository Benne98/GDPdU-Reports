import type { MonthlyRow } from '../../../../lib/api'

const CLICKABLE_KINDS = new Set<MonthlyRow['row_kind']>(['line', 'subtotal'])

export function monthlyRowToLineCode(row: MonthlyRow): string | null {
  if (!row.id.startsWith('cf-')) return null
  return row.id
}

export function isMonthlyRowClickable(row: MonthlyRow): boolean {
  if (!CLICKABLE_KINDS.has(row.row_kind)) return false
  return monthlyRowToLineCode(row) != null
}

export function monthlyPeriodKey(year: number, month: number): string {
  return `${year}-${String(month).padStart(2, '0')}`
}
