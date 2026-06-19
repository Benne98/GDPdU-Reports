import type { SalesTopEntitiesColLabels } from '../../../lib/api'

const MONTH_ABBR = [
  'Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun',
  'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec',
] as const

function priorMonth(year: number, month: number): [number, number] {
  return month > 1 ? [year, month - 1] : [year - 1, 12]
}

/** Short month labels for Top Customers / Top Suppliers (e.g. Jul25, Plan Jul25). */
export function buildTopEntityColLabelsMonth(
  year: number,
  month: number,
): SalesTopEntitiesColLabels {
  const [pmY, pmM] = priorMonth(year, month)
  const yr2 = String(year).slice(-2)
  const pmY2 = String(pmY).slice(-2)
  const py2 = String(year - 1).slice(-2)
  const cm = `${MONTH_ABBR[month - 1]}${yr2}`
  const pm = `${MONTH_ABBR[pmM - 1]}${pmY2}`
  const py = `${MONTH_ABBR[month - 1]}${py2}`
  const ytd = `YTD${MONTH_ABBR[month - 1]}${yr2}`
  const ytdPy = `YTD${MONTH_ABBR[month - 1]}${py2}`
  return {
    py_cm: py,
    pm,
    cm,
    ytd,
    ytd_py: ytdPy,
    delta_cm_pm: `Δ ${cm} − ${pm}`,
    delta_cm_py: `Δ ${cm} − ${py}`,
    delta_ytd: `Δ ${ytd} − ${ytdPy}`,
    ytd_plan: `Plan ${ytd}`,
    plan_cm: `Plan ${cm}`,
    coverage: 'Coverage',
  }
}

function looksLikeLongMonthLabel(label: string | undefined): boolean {
  if (!label) return false
  return /\s20\d{2}$/.test(label)
    || /Januar|Februar|März|April|Mai|Juni|Juli|August|September|Oktober|November|Dezember/i.test(label)
}

/**
 * Always use abbreviated headers. Month grain: derive from anchor.
 * Week grain: keep API labels when already short; otherwise fall back to month anchor labels.
 */
export function normalizeTopEntityColLabels(
  year: number,
  month: number,
  periodGrain: string,
  api?: SalesTopEntitiesColLabels,
): SalesTopEntitiesColLabels {
  if (periodGrain !== 'week') {
    return buildTopEntityColLabelsMonth(year, month)
  }
  if (api && !looksLikeLongMonthLabel(api.cm) && !looksLikeLongMonthLabel(api.mtd)) {
    return api
  }
  return { ...buildTopEntityColLabelsMonth(year, month), ...api }
}
