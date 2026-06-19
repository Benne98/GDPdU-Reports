import type { MonthlyRow } from '../../../../lib/api'

/** Mapping lines only — same rule as narrative markers (no Assets / Current assets). */
export function monthlyRowToLineCode(row: MonthlyRow): string | null {
  if (!row.id.startsWith('bs-')) return null
  if (row.id === 'bs-kpi-equity-ratio') return 'bs-kpi-equity-ratio'
  if (row.row_kind !== 'line') return null
  return row.id
}

export function isMonthlyRowClickable(row: MonthlyRow): boolean {
  return monthlyRowToLineCode(row) != null
}

export function monthlyPeriodKey(year: number, month: number): string {
  return `${year}-${String(month).padStart(2, '0')}`
}
