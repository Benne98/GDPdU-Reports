import type { MonthlyRow } from '../../../lib/api'

const CLICKABLE_KINDS = new Set<MonthlyRow['row_kind']>(['line', 'subtotal'])

/** Map monthly row id to pl-line-detail line_code. */
export function monthlyRowToLineCode(row: MonthlyRow): string | null {
  if (!row.id.startsWith('pl-')) return null
  const l4Match = row.id.match(/^pl-(.+)-l4-\d+$/)
  if (l4Match) {
    const base = l4Match[1]
    const l4 = (row.label || '').trim()
    return l4 ? `${base}::${l4}` : base
  }
  if (row.id === 'pl-kpi-header') return null
  const code = row.id.slice(3)
  return code || null
}

export function isMonthlyRowClickable(row: MonthlyRow): boolean {
  if (!CLICKABLE_KINDS.has(row.row_kind)) return false
  return monthlyRowToLineCode(row) != null
}

export function monthlyPeriodKey(year: number, month: number): string {
  return `${year}-${String(month).padStart(2, '0')}`
}
