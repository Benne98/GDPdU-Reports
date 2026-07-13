import type { MonthlyPeriod, MonthlyRow } from '../../../lib/api'
import { monthlyRowToLineCode } from './plMonthlyLineCode'
import type { PlPlanMap } from './usePlStatementData'
import { periodLabel, periodKey as pk } from './plPeriodLabels'

export type MonthlyColumnKind = 'agg' | 'agg_vs_py' | 'agg_vs_plan'

export type MonthlyViewColumnDef = {
  id: string
  kind: MonthlyColumnKind
  labelLine1: string
  labelLine2?: string
  /** Months included in the aggregate (YYYY-MM), oldest first */
  periodKeys: string[]
}

const STORAGE_KEY_PL = 'finssentials.pl.monthly.columns.v1'

function monthlyColumnsStorageKey(statement?: string): string {
  const s = (statement || 'pl').toLowerCase()
  return `finssentials.${s}.monthly.columns.v1`
}

export function priorYearPeriodKey(key: string): string {
  const [ys, ms] = key.split('-')
  return pk(parseInt(ys, 10) - 1, parseInt(ms, 10))
}

function parseKey(key: string): { year: number; month: number } {
  const [ys, ms] = key.split('-')
  return { year: parseInt(ys, 10), month: parseInt(ms, 10) }
}

export function sortPeriodKeys(keys: string[]): string[] {
  return [...keys].sort((a, b) => {
    const pa = parseKey(a)
    const pb = parseKey(b)
    return pa.year !== pb.year ? pa.year - pb.year : pa.month - pb.month
  })
}

export function buildAggregateLabel(keys: string[], periods: MonthlyPeriod[]): string {
  const sorted = sortPeriodKeys(keys)
  if (sorted.length === 0) return 'Total'
  if (sorted.length === 1) {
    return periodLabelFromKey(sorted[0], periods)
  }
  const first = periodLabelFromKey(sorted[0], periods)
  const last = periodLabelFromKey(sorted[sorted.length - 1], periods)
  const y0 = parseKey(sorted[0]).year
  const y1 = parseKey(sorted[sorted.length - 1]).year
  if (y0 === y1 && first !== last) {
    const a = first.replace(/\d{2}$/, '')
    const b = last
    return `${a}–${b}`
  }
  return `${sorted.length}M Σ`
}

function periodLabelFromKey(key: string, periods: MonthlyPeriod[]): string {
  const hit = periods.find(p => pk(p.year, p.month) === key)
  if (hit) return periodLabel(hit.year, hit.month)
  return periodLabel(parseInt(key.slice(0, 4), 10), parseInt(key.slice(5), 10))
}

export function makeAggregateColumn(
  periodKeys: string[],
  periods: MonthlyPeriod[],
): MonthlyViewColumnDef {
  const sorted = sortPeriodKeys(periodKeys)
  const label = buildAggregateLabel(sorted, periods)
  return {
    id: `agg:${sorted.join('|')}`,
    kind: 'agg',
    labelLine1: label,
    periodKeys: sorted,
  }
}

export function makeAggregateVsPyColumn(
  periodKeys: string[],
  periods: MonthlyPeriod[],
): MonthlyViewColumnDef {
  const sorted = sortPeriodKeys(periodKeys)
  const label = buildAggregateLabel(sorted, periods)
  const pyLabel = buildAggregateLabel(
    sorted.map(priorYearPeriodKey),
    periods,
  )
  return {
    id: `agg_vs_py:${sorted.join('|')}`,
    kind: 'agg_vs_py',
    labelLine1: `Δ ${label} − ${pyLabel}`,
    periodKeys: sorted,
  }
}

export function makeAggregateVsPlanColumn(
  periodKeys: string[],
  periods: MonthlyPeriod[],
): MonthlyViewColumnDef {
  const sorted = sortPeriodKeys(periodKeys)
  const label = buildAggregateLabel(sorted, periods)
  return {
    id: `agg_vs_plan:${sorted.join('|')}`,
    kind: 'agg_vs_plan',
    labelLine1: `Δ ${label} − Plan`,
    periodKeys: sorted,
  }
}

export function loadMonthlyColumns(statement?: string): MonthlyViewColumnDef[] {
  const stmt = (statement || 'pl').toLowerCase()
  try {
    const key = monthlyColumnsStorageKey(stmt)
    let raw = localStorage.getItem(key)
    if (!raw && stmt === 'pl') {
      raw = localStorage.getItem(STORAGE_KEY_PL)
    }
    if (!raw) return []
    const parsed = JSON.parse(raw) as MonthlyViewColumnDef[]
    return Array.isArray(parsed) ? parsed : []
  } catch {
    return []
  }
}

export function saveMonthlyColumns(cols: MonthlyViewColumnDef[], statement?: string): void {
  const stmt = (statement || 'pl').toLowerCase()
  localStorage.setItem(monthlyColumnsStorageKey(stmt), JSON.stringify(cols))
}

export function reconcileMonthlyColumns(
  cols: MonthlyViewColumnDef[],
  periods: MonthlyPeriod[],
): MonthlyViewColumnDef[] {
  return cols.map(c => {
    const sorted = sortPeriodKeys(c.periodKeys)
    switch (c.kind) {
      case 'agg':
        return makeAggregateColumn(sorted, periods)
      case 'agg_vs_py':
        return makeAggregateVsPyColumn(sorted, periods)
      case 'agg_vs_plan':
        return makeAggregateVsPlanColumn(sorted, periods)
      default:
        return { ...c, periodKeys: sorted }
    }
  })
}

function sumAmounts(row: MonthlyRow, keys: string[]): number | null {
  let sum = 0
  let any = false
  for (const key of keys) {
    const v = row.amounts?.[key]
    if (v == null) continue
    any = true
    sum += v
  }
  return any ? sum : null
}

function sumPlan(lineCode: string | null, keys: string[], planByPeriod: Map<string, PlPlanMap>): number | null {
  if (!lineCode) return null
  let sum = 0
  let any = false
  for (const key of keys) {
    const plan = planByPeriod.get(key)?.[lineCode]
    if (plan?.plan_cm == null) continue
    any = true
    sum += plan.plan_cm
  }
  return any ? sum : null
}

export function resolveMonthlyColumnValue(
  row: MonthlyRow,
  col: MonthlyViewColumnDef,
  planByPeriod: Map<string, PlPlanMap>,
  lineCodeForRow: (row: MonthlyRow) => string | null = monthlyRowToLineCode,
): number | null {
  const isKpi = row.row_kind === 'kpi'
  if (isKpi) return null

  const keys = col.periodKeys
  const pyKeys = keys.map(priorYearPeriodKey)

  switch (col.kind) {
    case 'agg':
      return sumAmounts(row, keys)
    case 'agg_vs_py': {
      const cur = sumAmounts(row, keys)
      const py = sumAmounts(row, pyKeys)
      if (cur == null || py == null) return null
      return cur - py
    }
    case 'agg_vs_plan': {
      const cur = sumAmounts(row, keys)
      const lineCode = lineCodeForRow(row)
      const plan = sumPlan(lineCode, keys, planByPeriod)
      if (cur == null || plan == null) return null
      return cur - plan
    }
    default:
      return null
  }
}

/** Last period in aggregate — used for line-detail drill */
export function detailPeriodFromColumn(col: MonthlyViewColumnDef): { year: number; month: number } | null {
  const sorted = sortPeriodKeys(col.periodKeys)
  if (!sorted.length) return null
  const last = sorted[sorted.length - 1]
  return parseKey(last)
}
