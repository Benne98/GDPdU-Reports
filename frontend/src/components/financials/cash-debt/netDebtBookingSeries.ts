import type { CashDebtBookingEntry } from '../../../lib/api'

export interface NetDebtMonthlyPoint {
  /** YYYY-MM sort key */
  key: string
  /** Short label, e.g. Jul 24 */
  label: string
  /** Sum of absolute booking amounts in kEUR for the month */
  amount: number
}

const MONTH_SHORT = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

function monthLabel(ym: string): string {
  const [y, m] = ym.split('-')
  const mi = Number(m)
  if (!y || mi < 1 || mi > 12) return ym
  return `${MONTH_SHORT[mi - 1]} ${y.slice(-2)}`
}

/** Aggregate raw postings into monthly payment volumes (|amount| sum, kEUR). */
export function aggregateBookingsMonthly(entries: CashDebtBookingEntry[]): NetDebtMonthlyPoint[] {
  const byMonth = new Map<string, number>()
  for (const e of entries) {
    const key = (e.posting_date ?? '').slice(0, 7)
    if (!key || key.length < 7) continue
    byMonth.set(key, (byMonth.get(key) ?? 0) + Math.abs(e.amount_keur ?? 0))
  }
  return [...byMonth.entries()]
    .sort(([a], [b]) => a.localeCompare(b))
    .map(([key, amount]) => ({
      key,
      label: monthLabel(key),
      amount: Math.round(amount * 100) / 100,
    }))
}

/** Top month share of total volume (for seasonality hint). */
export function topMonthShare(points: NetDebtMonthlyPoint[]): number | null {
  if (!points.length) return null
  const total = points.reduce((s, p) => s + p.amount, 0)
  if (total < 0.01) return null
  const top = Math.max(...points.map(p => p.amount))
  return top / total
}
