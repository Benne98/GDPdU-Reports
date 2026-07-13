import type {
  FinancialStatementColLabels,
  FinancialStatementRow,
  MonthlyPeriod,
  MonthlyResponse,
} from '../../../lib/api'
import type { PlPlanMap } from './usePlStatementData'
import { periodLabel, periodKey as pk } from './plPeriodLabels'
import { labelPlanPeriod } from '../../../lib/periodColumnLabels'

export type PlColumnKind =
  | 'py_cm'
  | 'pm'
  | 'cm'
  | 'mom'
  | 'yoy'
  | 'ytd'
  | 'ytd_py'
  | 'ytd_delta'
  | 'ytd_plan'
  | 'ytd_vs_plan'
  | 'plan_cm'
  | 'plan_vs_actual'
  | 'mtd'
  | 'mtg'
  | 'coverage_mtd'
  | 'ytg'
  | 'coverage'
  | 'month'
  | 'month_mom'
  | 'month_yoy'
  | 'month_delta'

export interface PlTableColumnDef {
  id: string
  kind: PlColumnKind
  labelLine1: string
  labelLine2?: string
  periodKey?: string
  periodKeyA?: string
  periodKeyB?: string
}

const STORAGE_KEY_V2 = 'finssentials.pl.tableColumns.v2'
const STORAGE_KEY_V1 = 'finssentials.pl.tableColumns.v1'

function tableColumnsStorageKey(statement?: string): string {
  const s = (statement || 'pl').toLowerCase()
  return `finssentials.${s}.tableColumns.v2`
}

function tableColumnsLegacyKey(statement?: string): string {
  const s = (statement || 'pl').toLowerCase()
  return `finssentials.${s}.tableColumns.v1`
}

/** KPI rows only expose % values for these column kinds (from amounts/deltas). */
export const KPI_TABLE_COLUMN_KINDS = new Set<PlColumnKind>([
  'py_cm',
  'pm',
  'cm',
  'mom',
  'yoy',
  'ytd',
  'ytd_py',
  'ytd_delta',
  'plan_cm',
  'plan_vs_actual',
])

export const DEFAULT_TABLE_COLUMN_IDS = [
  'py_cm',
  'pm',
  'cm',
  'mom',
  'yoy',
  'ytd',
  'plan_cm',
  'ytg',
  'coverage',
  'ytd_py',
  'ytd_delta',
]

/** P&L table defaults in weekly view (no py_cm / yoy by default). */
export const DEFAULT_WEEK_PL_TABLE_COLUMN_IDS = [
  'pm',
  'cm',
  'mom',
  'mtd',
  'plan_cm',
  'mtg',
  'coverage_mtd',
  'ytd',
  'ytd_plan',
  'ytd_delta',
  'ytg',
]

/** BS / WC table defaults in weekly view (no YTD columns — YTD is meaningless for balance-sheet snapshots). */
export const DEFAULT_WEEK_BS_TABLE_COLUMN_IDS = [
  'py_cm',
  'pm',
  'cm',
  'mom',
  'yoy',
  'plan_cm',
  'plan_vs_actual',
  'ytg',
]

/** BS / WC table defaults in monthly view (no YTD columns). */
export const DEFAULT_BS_TABLE_COLUMN_IDS = [
  'py_cm',
  'pm',
  'cm',
  'mom',
  'yoy',
  'plan_cm',
  'ytg',
  'coverage',
]

/**
 * Column kinds that require an active plan version (has_plan_data = true).
 * When has_plan_data is false these are hidden from defaults and saved layouts.
 */
export const PLAN_ONLY_COL_KINDS = new Set<PlColumnKind>([
  'plan_cm',
  'plan_vs_actual',
  'ytd_plan',
  'ytd_vs_plan',
  'ytg',
  'coverage',
  'coverage_mtd',
  'mtg',
])

export function priorMonthKey(key: string): string {
  const [ys, ms] = key.split('-')
  let y = parseInt(ys, 10)
  let m = parseInt(ms, 10)
  m -= 1
  if (m === 0) {
    m = 12
    y -= 1
  }
  return pk(y, m)
}

export function priorYearKey(key: string): string {
  const [ys, ms] = key.split('-')
  return pk(parseInt(ys, 10) - 1, parseInt(ms, 10))
}

function periodLabelFromKey(key: string, periods?: MonthlyPeriod[]): string {
  const hit = periods?.find(p => pk(p.year, p.month) === key)
  return hit?.label ?? periodLabel(parseInt(key.slice(0, 4), 10), parseInt(key.slice(5), 10))
}

export function buildDefaultColumns(
  lbl: FinancialStatementColLabels,
  periodGrain: 'month' | 'week' = 'month',
  statement?: string,
  hasPlanData = true,
): PlTableColumnDef[] {
  const catalog = buildColumnCatalog(lbl, [], periodGrain)
  const stmt = (statement || 'pl').toLowerCase()
  const ids =
    periodGrain === 'week'
      ? stmt === 'bs' || stmt === 'wc' || stmt === 'cf'
        ? DEFAULT_WEEK_BS_TABLE_COLUMN_IDS
        : DEFAULT_WEEK_PL_TABLE_COLUMN_IDS
      : stmt === 'bs' || stmt === 'wc'
        ? DEFAULT_BS_TABLE_COLUMN_IDS
        : DEFAULT_TABLE_COLUMN_IDS
  const active = hasPlanData
    ? ids
    : ids.filter(id => {
        const col = catalog.get(id)
        return !col || !PLAN_ONLY_COL_KINDS.has(col.kind)
      })
  return active.map(id => catalog.get(id)).filter(Boolean) as PlTableColumnDef[]
}

/** All addable column templates keyed by id (standard + optional period-driven). */
export function buildColumnCatalog(
  lbl: FinancialStatementColLabels,
  periods: MonthlyPeriod[],
  periodGrain: 'month' | 'week' = 'month',
): Map<string, PlTableColumnDef> {
  const map = new Map<string, PlTableColumnDef>()
  const isWeek = periodGrain === 'week'
  const hdrMom = lbl.cm && lbl.pm ? `Δ ${lbl.cm} − ${lbl.pm}` : isWeek ? 'Δ WoW' : 'Δ MoM'
  const hdrYoy = lbl.cm && lbl.py_cm ? `Δ ${lbl.cm} − ${lbl.py_cm}` : 'Δ YoY'
  const hdrYtd = lbl.ytd && lbl.ytd_py ? `Δ ${lbl.ytd} − ${lbl.ytd_py}` : 'Δ YTD vs PY'
  const hdrYtdPlan = lbl.ytd ? `Δ ${lbl.ytd} − Plan` : 'Δ YTD vs plan'
  const priorPeriod = isWeek ? 'Prior week' : 'Prior month'
  const currentPeriod = isWeek ? 'Current week' : 'Current month'
  const vsPrior = isWeek ? 'vs prior week' : 'vs prior month'
  const planLbl = lbl.plan_cm ?? labelPlanPeriod(lbl.cm ?? '')
  const mtdLbl = lbl.mtd ?? `MTD ${lbl.cm}`
  const mtgLbl = lbl.mtg ?? `MTG ${lbl.cm}`

  const standard: PlTableColumnDef[] = [
    { id: 'py_cm', kind: 'py_cm', labelLine1: lbl.py_cm, labelLine2: isWeek ? 'Prior year CW' : 'Prior year CM' },
    { id: 'pm', kind: 'pm', labelLine1: lbl.pm, labelLine2: priorPeriod },
    { id: 'cm', kind: 'cm', labelLine1: lbl.cm, labelLine2: currentPeriod },
    { id: 'mom', kind: 'mom', labelLine1: hdrMom, labelLine2: vsPrior },
    { id: 'yoy', kind: 'yoy', labelLine1: hdrYoy, labelLine2: 'vs prior year CM' },
    { id: 'ytd', kind: 'ytd', labelLine1: lbl.ytd, labelLine2: 'Year to date' },
    { id: 'ytd_py', kind: 'ytd_py', labelLine1: lbl.ytd_py, labelLine2: 'Prior-year YTD' },
    { id: 'ytd_delta', kind: 'ytd_delta', labelLine1: hdrYtd, labelLine2: 'Actual vs prior YTD' },
    { id: 'ytd_plan', kind: 'ytd_plan', labelLine1: 'Plan YTD', labelLine2: 'Budget YTD' },
    { id: 'ytd_vs_plan', kind: 'ytd_vs_plan', labelLine1: hdrYtdPlan, labelLine2: 'Actual vs plan YTD' },
    {
      id: 'plan_cm',
      kind: 'plan_cm',
      labelLine1: planLbl,
      labelLine2: isWeek ? 'Month budget' : 'Budget CM',
    },
    { id: 'plan_vs_actual', kind: 'plan_vs_actual', labelLine1: `Δ ${lbl.cm} Plan`, labelLine2: 'CM vs budget' },
    { id: 'ytg', kind: 'ytg', labelLine1: `YTG ${lbl.cm}`, labelLine2: 'Year to go' },
    { id: 'coverage', kind: 'coverage', labelLine1: 'Coverage', labelLine2: 'YTD vs budget %' },
  ]
  for (const c of standard) map.set(c.id, c)

  if (isWeek) {
    map.set('mtd', { id: 'mtd', kind: 'mtd', labelLine1: mtdLbl, labelLine2: 'Month to date' })
    map.set('mtg', { id: 'mtg', kind: 'mtg', labelLine1: mtgLbl, labelLine2: 'Month to go' })
    map.set('coverage_mtd', {
      id: 'coverage_mtd',
      kind: 'coverage_mtd',
      labelLine1: 'Coverage',
      labelLine2: 'MTD vs plan %',
    })
  }

  for (const p of periods) {
    const key = pk(p.year, p.month)
    map.set(`month:${key}`, {
      id: `month:${key}`,
      kind: 'month',
      periodKey: key,
      labelLine1: p.label,
      labelLine2: 'Month actual',
    })
    const pmKey = priorMonthKey(key)
    const pyKey = priorYearKey(key)
    map.set(`month_mom:${key}`, {
      id: `month_mom:${key}`,
      kind: 'month_mom',
      periodKey: key,
      labelLine1: `Δ ${p.label} vs ${periodLabelFromKey(pmKey, periods)}`,
      labelLine2: 'Month vs prior month',
    })
    map.set(`month_yoy:${key}`, {
      id: `month_yoy:${key}`,
      kind: 'month_yoy',
      periodKey: key,
      labelLine1: `Δ ${p.label} vs ${periodLabelFromKey(pyKey, periods)}`,
      labelLine2: 'Month vs prior year',
    })
  }

  return map
}

export function columnMonthDelta(aKey: string, bKey: string, periods?: MonthlyPeriod[]): PlTableColumnDef {
  const la = periodLabelFromKey(aKey, periods)
  const lb = periodLabelFromKey(bKey, periods)
  return {
    id: `month_delta:${aKey}:${bKey}`,
    kind: 'month_delta',
    periodKeyA: aKey,
    periodKeyB: bKey,
    labelLine1: `Δ ${la} − ${lb}`,
    labelLine2: 'Custom month comparison',
  }
}

export function loadSavedColumns(statement?: string): PlTableColumnDef[] | null {
  const stmt = (statement || 'pl').toLowerCase()
  try {
    const key = tableColumnsStorageKey(stmt)
    let rawV2 = localStorage.getItem(key)
    if (!rawV2 && stmt === 'pl') {
      rawV2 = localStorage.getItem(STORAGE_KEY_V2)
    }
    if (!rawV2) return null
    const parsed = JSON.parse(rawV2) as PlTableColumnDef[]
    return Array.isArray(parsed) ? parsed : null
  } catch {
    return null
  }
}

export function loadLegacyColumnIds(statement?: string): string[] | null {
  const stmt = (statement || 'pl').toLowerCase()
  try {
    const key = tableColumnsLegacyKey(stmt)
    let raw = localStorage.getItem(key)
    if (!raw && stmt === 'pl') {
      raw = localStorage.getItem(STORAGE_KEY_V1)
    }
    if (!raw) return null
    const parsed = JSON.parse(raw) as string[]
    return Array.isArray(parsed) ? parsed : null
  } catch {
    return null
  }
}

export function saveColumns(cols: PlTableColumnDef[], statement?: string): void {
  const stmt = (statement || 'pl').toLowerCase()
  localStorage.setItem(tableColumnsStorageKey(stmt), JSON.stringify(cols))
}

/** Reconcile stored columns with current labels / period catalog. */
export function reconcileColumns(
  cols: PlTableColumnDef[],
  catalog: Map<string, PlTableColumnDef>,
  periods: MonthlyPeriod[],
): PlTableColumnDef[] {
  return cols.map(c => {
    if (c.kind === 'month_delta' && c.periodKeyA && c.periodKeyB) {
      return columnMonthDelta(c.periodKeyA, c.periodKeyB, periods)
    }
    const fresh = catalog.get(c.id)
    if (fresh) return { ...fresh }
    return c
  })
}

/** Prefer non-zero plan map entry when amounts carry a placeholder 0. */
function pickPlanAmount(
  fromAmounts: number | undefined,
  fromPlanMap: number | undefined,
): number | null {
  if (fromAmounts != null && Math.abs(fromAmounts) >= 1e-6) return fromAmounts
  if (fromPlanMap != null && Math.abs(fromPlanMap) >= 1e-6) return fromPlanMap
  if (fromAmounts != null) return fromAmounts
  if (fromPlanMap != null) return fromPlanMap
  return null
}

export function resolveCellValue(
  row: FinancialStatementRow,
  col: PlTableColumnDef,
  planMap: PlPlanMap,
  monthly?: MonthlyResponse | null,
): number | null {
  const isKpi = row.row_kind === 'kpi'
  const isAccount = row.row_kind === 'account'
  const isPlanCol =
    col.kind === 'plan_cm' ||
    col.kind === 'plan_vs_actual' ||
    col.kind === 'ytd_plan' ||
    col.kind === 'ytd_vs_plan' ||
    col.kind === 'ytg' ||
    col.kind === 'coverage'
  if (isAccount && isPlanCol) return null
  if (isKpi && !KPI_TABLE_COLUMN_KINDS.has(col.kind)) return null

  const am = row.amounts
  const d = row.deltas
  const plan = isKpi ? undefined : planMap[row.line_code]

  const monthAmount = (key: string): number | null => {
    if (isKpi || !monthly) return null
    const mrow = findMonthlyRow(monthly.rows, row)
    const v = mrow?.amounts?.[key]
    return v != null ? v : null
  }

  switch (col.kind) {
    case 'py_cm':
      return am?.py_cm ?? null
    case 'pm':
      return am?.pm ?? null
    case 'cm':
      return am?.cm ?? null
    case 'mom':
      return d?.mom ?? null
    case 'yoy':
      return d?.yoy ?? null
    case 'ytd':
      return am?.ytd ?? null
    case 'ytd_py':
      return am?.ytd_py ?? null
    case 'ytd_delta':
      return d?.ytd ?? null
    case 'ytd_plan':
      return plan?.ytd_plan ?? null
    case 'ytd_vs_plan': {
      const ytd = am?.ytd
      const yp = plan?.ytd_plan
      if (ytd == null || yp == null) return null
      return ytd - yp
    }
    case 'plan_cm':
      return pickPlanAmount(am?.plan_cm, plan?.plan_cm)
    case 'plan_vs_actual': {
      const pcm = pickPlanAmount(am?.plan_cm, plan?.plan_cm)
      const cm = am?.cm
      if (pcm != null && cm != null) return cm - pcm
      return pickPlanAmount(am?.plan_vs_actual, plan?.plan_vs_actual)
    }
    case 'ytg': {
      const ytg = plan?.ytg
      // Return null when there is no plan data (ytg is 0 or absent); the
      // renderer will display "—" so the user sees a clear data-gap marker.
      if (ytg == null || Math.abs(ytg) < 1e-6) return null
      return ytg
    }
    case 'coverage':
      return plan?.coverage_pct ?? null
    case 'mtd':
      return am?.mtd ?? null
    case 'mtg':
      return am?.mtg ?? null
    case 'coverage_mtd':
      return am?.coverage_mtd ?? null
    case 'month':
      return col.periodKey ? monthAmount(col.periodKey) : null
    case 'month_mom': {
      if (!col.periodKey) return null
      const cur = monthAmount(col.periodKey)
      const prev = monthAmount(priorMonthKey(col.periodKey))
      if (cur == null || prev == null) return null
      return cur - prev
    }
    case 'month_yoy': {
      if (!col.periodKey) return null
      const cur = monthAmount(col.periodKey)
      const prev = monthAmount(priorYearKey(col.periodKey))
      if (cur == null || prev == null) return null
      return cur - prev
    }
    case 'month_delta': {
      if (!col.periodKeyA || !col.periodKeyB) return null
      const a = monthAmount(col.periodKeyA)
      const b = monthAmount(col.periodKeyB)
      if (a == null || b == null) return null
      return a - b
    }
    default:
      return null
  }
}

function findMonthlyRow(rows: MonthlyResponse['rows'], row: FinancialStatementRow): MonthlyResponse['rows'][0] | null {
  const targetId = row.id || `pl-${row.line_code}`
  for (const r of rows) {
    if (r.id === targetId || r.label === row.label) return r
    if (r.children?.length) {
      const c = findMonthlyRow(r.children, row)
      if (c) return c
    }
  }
  return null
}

/** @deprecated Use buildColumnCatalog groups in the column editor */
export const COLUMN_PALETTE: Array<{ kind: PlColumnKind; label: string }> = [
  { kind: 'py_cm', label: 'Prior year CM' },
  { kind: 'pm', label: 'Prior month' },
  { kind: 'cm', label: 'Current month' },
  { kind: 'mom', label: 'Δ vs prior month (CM)' },
  { kind: 'yoy', label: 'Δ vs prior year (CM)' },
  { kind: 'ytd', label: 'YTD actual' },
  { kind: 'ytd_py', label: 'YTD prior year' },
  { kind: 'ytd_delta', label: 'Δ YTD vs prior year' },
  { kind: 'ytd_plan', label: 'YTD plan' },
  { kind: 'ytd_vs_plan', label: 'Δ YTD vs plan' },
  { kind: 'plan_cm', label: 'Plan CM' },
  { kind: 'plan_vs_actual', label: 'Δ CM vs plan' },
  { kind: 'ytg', label: 'Year to go' },
  { kind: 'coverage', label: 'Coverage %' },
]

export const STANDARD_KINDS: PlColumnKind[] = COLUMN_PALETTE.map(p => p.kind)
