import type { OperationalKpiMetric } from '../../../../lib/api'
import type { AgingKpiDef } from './agingKpiDefs'

type TrendLike = {
  year: number
  month: number
  [key: string]: number | string | undefined
}

type AgingKpisLike = {
  overdue_pct?: number
  dso_days?: number
  dpo_days?: number
  open_documents?: number
  before_due?: number
  overdue?: number
}

/** Trend series field used to compute MoM / SMLY deltas per KPI card. */
export const AGING_KPI_TREND_FIELD: Record<string, string> = {
  total_open_gross: 'gross_balance',
  overdue_pct: 'overdue_pct',
  dso_days: 'dso_days',
  dpo_days: 'dpo_days',
  open_documents: 'open_documents',
}

/** Scalar fallbacks when kpi_metrics is missing or incomplete (common on payables). */
const KPI_FALLBACK_FIELD: Record<string, (src: AgingKpisLike, gross?: number) => number | undefined> = {
  total_open_gross: (_k, gross) => gross,
  overdue_pct: k => k.overdue_pct,
  dso_days: k => k.dso_days,
  dpo_days: k => k.dpo_days,
  open_documents: k => k.open_documents,
}

function metricValue(raw: OperationalKpiMetric | undefined): number | undefined {
  if (!raw) return undefined
  const v = raw.value
  return typeof v === 'number' && Number.isFinite(v) ? v : undefined
}

/** Merge API kpi_metrics with aging.kpis / total_open_gross before delta enrichment. */
export function resolveAgingKpiMetrics(
  apiMetrics: Record<string, OperationalKpiMetric> | undefined,
  kpis: AgingKpisLike | undefined,
  totalOpenGross: number | undefined,
  defs: AgingKpiDef[],
): Record<string, OperationalKpiMetric> {
  const out: Record<string, OperationalKpiMetric> = { ...(apiMetrics ?? {}) }
  const gross =
    totalOpenGross ??
    metricValue(out.total_open_gross) ??
    (kpis?.before_due != null && kpis?.overdue != null
      ? Math.round((kpis.before_due + kpis.overdue) * 100) / 100
      : undefined)

  for (const def of defs) {
    const fallback = KPI_FALLBACK_FIELD[def.key]?.(kpis ?? {}, gross)
    const existing = metricValue(out[def.key])
    const value = existing ?? fallback
    if (value == null) continue
    out[def.key] = { ...out[def.key], value }
  }
  return out
}

export function enrichAgingKpiMetrics(
  metrics: Record<string, OperationalKpiMetric> | undefined,
  trendPoints: TrendLike[],
  year: number,
  month: number,
  defs: AgingKpiDef[],
): Record<string, OperationalKpiMetric> {
  const out = { ...(metrics ?? {}) }

  if (!trendPoints.length) return out

  const current =
    trendPoints.find(p => p.year === year && p.month === month) ??
    trendPoints[trendPoints.length - 1]
  if (!current) return out

  const idx = trendPoints.indexOf(current)
  const prev = idx > 0 ? trendPoints[idx - 1] : undefined
  const smly = trendPoints.find(p => p.year === year - 1 && p.month === month)

  for (const def of defs) {
    const field = AGING_KPI_TREND_FIELD[def.key]
    if (!field) continue
    const base = out[def.key]
    const curVal = metricValue(base) ?? Number(current[field] ?? 0)
    out[def.key] = {
      ...base,
      value: curVal,
      delta_pm: prev != null ? roundDelta(curVal - Number(prev[field] ?? 0), def.format) : null,
      delta_smly: smly != null ? roundDelta(curVal - Number(smly[field] ?? 0), def.format) : null,
    }
  }
  return out
}

function roundDelta(delta: number, format?: AgingKpiDef['format']): number {
  if (format === 'percent') return Math.round(delta * 10) / 10
  if (format === 'days') return Math.round(delta * 10) / 10
  if (format === 'count') return Math.round(delta)
  return Math.round(delta * 100) / 100
}
