/** Shared period grain for Cockpit, Financials, Sales, Inventory, Benchmark. */

export type PeriodGrain = 'month' | 'week' | 'year'

export type MonthPeriod = { grain: 'month'; year: number; month: number }

export type WeekPeriod = { grain: 'week'; isoYear: number; isoWeek: number }

/** Year grain: year = anchor fiscal year, month = YTD/LTM anchor month. */
export type YearPeriod = { grain: 'year'; year: number; month: number }

export type PeriodSelection = MonthPeriod | WeekPeriod | YearPeriod

export type LatestPeriodInfo = {
  period: string
  year: number | null
  month: number | null
  iso_year: number | null
  iso_week: number | null
}

const MONTH_ABBR = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

export function monthLabelShort(year: number, month: number): string {
  return `${MONTH_ABBR[month - 1]}${String(year).slice(-2)}`
}

export function weekLabelShort(isoYear: number, isoWeek: number): string {
  return `CW${String(isoWeek).padStart(2, '0')}'${String(isoYear).slice(-2)}`
}

/** Returns `count` fiscal years ascending up to endYear, each with month=anchorMonth. */
export function rollingYears(endYear: number, anchorMonth: number, count = 4): YearPeriod[] {
  const out: YearPeriod[] = []
  for (let i = count - 1; i >= 0; i--) {
    out.push({ grain: 'year', year: endYear - i, month: anchorMonth })
  }
  return out
}

export function rollingMonths(endYear: number, endMonth: number, count = 5): MonthPeriod[] {
  const out: MonthPeriod[] = []
  let y = endYear
  let m = endMonth
  for (let i = 0; i < count; i++) {
    out.unshift({ grain: 'month', year: y, month: m })
    if (m === 1) {
      y -= 1
      m = 12
    } else {
      m -= 1
    }
  }
  return out
}

export function priorIsoWeek(isoYear: number, isoWeek: number): { isoYear: number; isoWeek: number } {
  const d = isoWeekMonday(isoYear, isoWeek)
  const prev = new Date(d)
  prev.setUTCDate(prev.getUTCDate() - 7)
  return isoPartsFromDate(prev)
}

export function isoWeekMonday(isoYear: number, isoWeek: number): Date {
  const jan4 = new Date(Date.UTC(isoYear, 0, 4))
  const day = jan4.getUTCDay() || 7
  const mondayWeek1 = new Date(jan4)
  mondayWeek1.setUTCDate(jan4.getUTCDate() - day + 1)
  const monday = new Date(mondayWeek1)
  monday.setUTCDate(mondayWeek1.getUTCDate() + (isoWeek - 1) * 7)
  return monday
}

function isoPartsFromDate(d: Date): { isoYear: number; isoWeek: number } {
  const tmp = new Date(Date.UTC(d.getUTCFullYear(), d.getUTCMonth(), d.getUTCDate()))
  tmp.setUTCDate(tmp.getUTCDate() + 4 - (tmp.getUTCDay() || 7))
  const yearStart = new Date(Date.UTC(tmp.getUTCFullYear(), 0, 1))
  const week = Math.ceil(((tmp.getTime() - yearStart.getTime()) / 86400000 + 1) / 7)
  return { isoYear: tmp.getUTCFullYear(), isoWeek: week }
}

export function rollingIsoWeeks(endIsoYear: number, endIsoWeek: number, count = 12): WeekPeriod[] {
  const out: WeekPeriod[] = []
  let y = endIsoYear
  let w = endIsoWeek
  for (let i = 0; i < count; i++) {
    out.unshift({ grain: 'week', isoYear: y, isoWeek: w })
    const prev = priorIsoWeek(y, w)
    y = prev.isoYear
    w = prev.isoWeek
  }
  return out
}

export function defaultPeriodFromLatest(lp: LatestPeriodInfo): PeriodSelection {
  if (lp.year != null && lp.month != null) {
    return { grain: 'month', year: lp.year, month: lp.month }
  }
  if (lp.iso_year != null && lp.iso_week != null) {
    return { grain: 'week', isoYear: lp.iso_year, isoWeek: lp.iso_week }
  }
  return { grain: 'month', year: 2025, month: 7 }
}

/** Overview / P&L default: annual slice anchored at latest YTD month. */
export function defaultAnnualPeriodFromLatest(lp: LatestPeriodInfo): PeriodSelection {
  if (lp.year != null && lp.month != null) {
    return { grain: 'year', year: lp.year, month: lp.month }
  }
  return defaultPeriodFromLatest(lp)
}

export function periodQueryParams(p: PeriodSelection): Record<string, string | number | undefined> {
  if (p.grain === 'week') {
    const anchor = periodAnchorYearMonth(p)
    return {
      period_grain: 'week',
      iso_year: p.isoYear,
      iso_week: p.isoWeek,
      // Anchor month for endpoints that still require year/month alongside week grain
      year: anchor.year,
      month: anchor.month,
    }
  }
  if (p.grain === 'year') {
    return { period_grain: 'year', year: p.year, month: p.month }
  }
  return { period_grain: 'month', year: p.year, month: p.month }
}

/** Params for financial statement GET endpoints (includes optional entity). */
export function finStatementPeriodParams(
  p: PeriodSelection,
  entity?: string,
): Record<string, string | number | undefined> {
  return { ...periodQueryParams(p), entity }
}

export function periodCacheKey(p: PeriodSelection): string {
  if (p.grain === 'week') return `w-${p.isoYear}-${p.isoWeek}`
  if (p.grain === 'year') return `y-${p.year}-${p.month}`
  return `m-${p.year}-${p.month}`
}

/** Calendar month containing the ISO week's Sunday (charts still anchored monthly until week APIs exist). */
export function periodAnchorYearMonth(p: PeriodSelection): { year: number; month: number } {
  if (p.grain === 'month') return { year: p.year, month: p.month }
  if (p.grain === 'year') return { year: p.year, month: p.month }
  const mon = isoWeekMonday(p.isoYear, p.isoWeek)
  const sun = new Date(mon)
  sun.setUTCDate(mon.getUTCDate() + 6)
  return { year: sun.getUTCFullYear(), month: sun.getUTCMonth() + 1 }
}

export function periodDateRange(p: PeriodSelection): { dateFrom: string; dateTo: string } {
  if (p.grain === 'month') {
    const dateFrom = `${p.year}-${String(p.month).padStart(2, '0')}-01`
    const dateTo = new Date(p.year, p.month, 0).toISOString().slice(0, 10)
    return { dateFrom, dateTo }
  }
  if (p.grain === 'year') {
    const dateFrom = `${p.year}-01-01`
    const dateTo = new Date(p.year, p.month, 0).toISOString().slice(0, 10)
    return { dateFrom, dateTo }
  }
  const mon = isoWeekMonday(p.isoYear, p.isoWeek)
  const sun = new Date(mon)
  sun.setUTCDate(mon.getUTCDate() + 6)
  const dateFrom = mon.toISOString().slice(0, 10)
  const dateTo = sun.toISOString().slice(0, 10)
  return { dateFrom, dateTo }
}

export function periodTitleLabel(p: PeriodSelection): string {
  if (p.grain === 'month') return monthLabelShort(p.year, p.month)
  if (p.grain === 'year') return `FY${String(p.year).slice(-2)}`
  return weekLabelShort(p.isoYear, p.isoWeek)
}
