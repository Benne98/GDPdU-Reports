/** Column label suffixes: A = actual, P = plan, F = forecast. */

export function labelActual(label: string): string {
  const s = String(label || '').trim()
  if (!s) return s
  if (s.length > 1 && /[APF]$/i.test(s)) return s
  return `${s}A`
}

export function labelPlanPeriod(label: string): string {
  const s = String(label || '').trim()
  if (!s) return s
  const base = s.length > 1 && /[APF]$/i.test(s) ? s.slice(0, -1) : s
  return `${base}P`
}

export function labelPlanFy(year: number): string {
  return `FY${String(year + 1).slice(-2)}P`
}

export function labelForecastFy(year: number): string {
  return `FY${String(year).slice(-2)}F`
}

/** Annual flow forecast column (`fy_f` in API) = full-year forecast (YTD + plan YTG). */
export function resolveAnnualForecastColumnLabel(year: number, apiLabel?: string | null): string {
  const s = String(apiLabel ?? '').trim()
  if (s && /F$/i.test(s)) return s
  return labelForecastFy(year)
}

/** Consolidation table period header — avoids double A/P/F suffixes. */
export function formatConsolidationPeriodLabel(colLabel: string | undefined | null): string {
  const s = String(colLabel ?? '').trim()
  if (!s) return 'CM'
  if (s.length > 1 && /[APF]$/i.test(s)) return s
  return labelActual(s)
}
