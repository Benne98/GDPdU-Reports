const MONTH_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export function periodLabel(year: number, month: number): string {
  return `${MONTH_ABBR[month - 1]}${String(year).slice(-2)}`
}

export function priorPeriod(year: number, month: number): { year: number; month: number } {
  return month > 1 ? { year, month: month - 1 } : { year: year - 1, month: 12 }
}

export type PeriodDef = {
  year: number
  month: number
  label: string
  key: string
}

export function periodKey(year: number, month: number): string {
  return `${year}-${String(month).padStart(2, '0')}`
}

/** Last 12 months ending at (year, month), oldest first. */
export function last12PeriodDefs(year: number, month: number): PeriodDef[] {
  const out: PeriodDef[] = []
  let y = year
  let m = month
  for (let i = 0; i < 12; i++) {
    out.unshift({
      year: y,
      month: m,
      label: periodLabel(y, m),
      key: periodKey(y, m),
    })
    m -= 1
    if (m === 0) {
      m = 12
      y -= 1
    }
  }
  return out
}
