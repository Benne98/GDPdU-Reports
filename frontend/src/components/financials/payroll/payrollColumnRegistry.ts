import type { PersonnelDimension, PersonnelLayout, PersonnelSnapshotInfo } from '../../../lib/api'

import { personnelDimensionLabel } from '../../../lib/dimensionLabels'

import { defaultCompareDates } from './payrollPeriodUtils'



export type PayrollColumnKind = 'metric' | 'snapshot' | 'delta'



export type PayrollColumnDef = {

  id: string

  kind: PayrollColumnKind

  label: string

  metricId?: string

  snapshotDate?: string

  deltaFrom?: string

  deltaTo?: string

  visible: boolean

}



const COLUMNS_KEY = 'finssentials.payroll.columns.v3'

const LEGACY_COLUMNS_KEY = 'finssentials.payroll.columns.v2'

const LAYOUT_KEY = 'finssentials.payroll.layout.v1'

const COL_DIM_KEY = 'finssentials.payroll.colDim.v1'

const ROW_DIMS_KEY = 'finssentials.payroll.rowDims.v1'

const CHART_DIM_KEY = 'finssentials.payroll.chartDim.v1'

const TREND_METRIC_KEY = 'finssentials.payroll.trendMetric.v1'

const VIEW_KEY = 'finssentials.payroll.viewMode.v1'



export const DIMENSION_OPTIONS: Array<{ id: PersonnelDimension; label: string }> = (

  ['entity', 'org_unit', 'gew_ang', 'kst_name', 'bereich'] as PersonnelDimension[]

).map(id => ({ id, label: personnelDimensionLabel(id) }))



export const DEFAULT_ROW_DIMENSIONS: PersonnelDimension[] = ['bereich']



/** Table block order: Average FTEs → Avg cost/FTE → Payroll accounting → footer KPIs. */
export const TABLE_METRIC_IDS = ['fte', 'avg_cost_per_fte', 'payroll'] as const

export const DEFAULT_METRIC_IDS = [
  ...TABLE_METRIC_IDS,
  'personnel_expenses',
  'personnel_pct_output',
]

export function sortMetricIds(ids: string[]): string[] {
  const order = new Map(DEFAULT_METRIC_IDS.map((id, i) => [id, i]))
  return [...ids].sort((a, b) => (order.get(a) ?? 999) - (order.get(b) ?? 999))
}



export function deltaColumnId(from: string, to: string): string {

  return `delta-${from}-${to}`

}



export function buildDefaultDeltaColumn(from: string, to: string, fromLabel: string, toLabel: string): PayrollColumnDef {

  return {

    id: deltaColumnId(from, to),

    kind: 'delta',

    label: `Δ ${toLabel} − ${fromLabel}`,

    deltaFrom: from,

    deltaTo: to,

    visible: true,

  }

}



export function buildDefaultColumns(

  snapshots: PersonnelSnapshotInfo[],

  anchor: string,

): PayrollColumnDef[] {

  const defaultSnaps = new Set([anchor, ...defaultCompareDates(anchor, snapshots, 3)])

  const snapCols = snapshots

    .slice()

    .sort((a, b) => a.as_of_date.localeCompare(b.as_of_date))

    .map(s => ({

      id: `snap-${s.as_of_date}`,

      kind: 'snapshot' as const,

      label: s.col_label,

      snapshotDate: s.as_of_date,

      visible: defaultSnaps.has(s.as_of_date),

    }))



  const visibleDates = snapCols.filter(c => c.visible).map(c => c.snapshotDate as string)

  const lastTwo = visibleDates.slice(-2)

  const deltaCols: PayrollColumnDef[] =

    lastTwo.length === 2

      ? [buildDefaultDeltaColumn(

          lastTwo[0],

          lastTwo[1],

          snapCols.find(c => c.snapshotDate === lastTwo[0])?.label ?? lastTwo[0],

          snapCols.find(c => c.snapshotDate === lastTwo[1])?.label ?? lastTwo[1],

        )]

      : []



  return [...snapCols, ...deltaCols]

}



export function buildMetricColumns(metricIds: string[], labels: Record<string, string>): PayrollColumnDef[] {

  return metricIds.map(id => ({

    id: `metric-${id}`,

    kind: 'metric' as const,

    label: labels[id] ?? id,

    metricId: id,

    visible: DEFAULT_METRIC_IDS.includes(id),

  }))

}



function refreshSnapshotLabels(cols: PayrollColumnDef[], snapshots: PersonnelSnapshotInfo[]): PayrollColumnDef[] {

  const snapByDate = new Map(snapshots.map(s => [s.as_of_date, s]))

  return cols

    .map(c => {

      if (c.kind === 'snapshot' && c.snapshotDate) {

        const snap = snapByDate.get(c.snapshotDate)

        return snap ? { ...c, label: snap.col_label } : c

      }

      if (c.kind === 'delta' && c.deltaFrom && c.deltaTo) {

        const fromLbl = snapByDate.get(c.deltaFrom)?.col_label ?? c.deltaFrom

        const toLbl = snapByDate.get(c.deltaTo)?.col_label ?? c.deltaTo

        return { ...c, label: `Δ ${toLbl} − ${fromLbl}` }

      }

      return c

    })

    .filter(c => {

      if (c.kind === 'snapshot' && c.snapshotDate) return snapByDate.has(c.snapshotDate)

      if (c.kind === 'delta' && c.deltaFrom && c.deltaTo) {

        return snapByDate.has(c.deltaFrom) && snapByDate.has(c.deltaTo)

      }

      return true

    })

}



export function mergePayrollColumns(

  saved: PayrollColumnDef[] | null,

  snapshots: PersonnelSnapshotInfo[],

  anchor: string,

): PayrollColumnDef[] {

  if (!saved?.length) return buildDefaultColumns(snapshots, anchor)

  const merged = refreshSnapshotLabels(saved, snapshots)

  const hasDelta = merged.some(c => c.kind === 'delta')

  if (hasDelta) {

    return merged.sort((a, b) => columnSortKey(a).localeCompare(columnSortKey(b)))

  }

  const snapCols = merged.filter(c => c.kind === 'snapshot')

  const visibleDates = snapCols.filter(c => c.visible).map(c => c.snapshotDate as string).sort()

  const lastTwo = visibleDates.slice(-2)

  if (lastTwo.length === 2) {

    const fromLbl = snapCols.find(c => c.snapshotDate === lastTwo[0])?.label ?? lastTwo[0]

    const toLbl = snapCols.find(c => c.snapshotDate === lastTwo[1])?.label ?? lastTwo[1]

    merged.push(buildDefaultDeltaColumn(lastTwo[0], lastTwo[1], fromLbl, toLbl))

  }

  return merged.sort((a, b) => columnSortKey(a).localeCompare(columnSortKey(b)))

}



function columnSortKey(c: PayrollColumnDef): string {

  if (c.kind === 'snapshot') return `0-${c.snapshotDate ?? ''}`

  if (c.kind === 'delta') return `1-${c.deltaTo ?? ''}`

  return `2-${c.id}`

}



export function loadPayrollColumns(): PayrollColumnDef[] | null {

  try {

    let raw = localStorage.getItem(COLUMNS_KEY)

    if (!raw) {

      raw = localStorage.getItem(LEGACY_COLUMNS_KEY)

    }

    if (!raw) return null

    return JSON.parse(raw) as PayrollColumnDef[]

  } catch {

    return null

  }

}



export function savePayrollColumns(cols: PayrollColumnDef[]): void {

  try {

    localStorage.setItem(COLUMNS_KEY, JSON.stringify(cols))

  } catch {

    /* ignore */

  }

}



export function mergeMetricColumnsFromApi(
  columns: PayrollColumnDef[],
  metrics: Array<{ id: string; label: string }>,
): PayrollColumnDef[] {
  if (!metrics.length) return columns
  const labels = Object.fromEntries(metrics.map(m => [m.id, m.label]))
  const existing = new Map(columns.filter(c => c.kind === 'metric').map(c => [c.metricId, c]))
  const metricCols = sortMetricIds(metrics.map(m => m.id)).map(id => {
    const m = metrics.find(x => x.id === id)!
    const prev = existing.get(id)
    return prev ?? {
      id: `metric-${id}`,
      kind: 'metric' as const,
      label: labels[id] ?? m.label,
      metricId: id,
      visible: DEFAULT_METRIC_IDS.includes(id),
    }
  })
  return [
    ...columns.filter(c => c.kind !== 'metric'),
    ...metricCols,
  ].sort((a, b) => columnSortKey(a).localeCompare(columnSortKey(b)))
}

export function toggleColumnVisibility(columns: PayrollColumnDef[], id: string): PayrollColumnDef[] {
  const idx = columns.findIndex(c => c.id === id)
  if (idx >= 0) {
    return columns.map(c => (c.id === id ? { ...c, visible: !c.visible } : c))
  }
  return columns
}

export function setDeltaColumnVisible(columns: PayrollColumnDef[], visible: boolean): PayrollColumnDef[] {
  const hasDelta = columns.some(c => c.kind === 'delta')
  if (!hasDelta) return columns
  return columns.map(c => (c.kind === 'delta' ? { ...c, visible } : c))
}

export type PayrollBreakdownPreset = 'flat' | 'rows_entity' | 'rows_org_unit' | 'split_entity'

export function breakdownFromLayout(
  layout: PersonnelLayout,
  _columnDimension: PersonnelDimension,
  rowDimensions: PersonnelDimension[],
): PayrollBreakdownPreset {
  if (layout === 'column_split') return 'split_entity'
  if (layout === 'row_hierarchy' && rowDimensions[0] === 'org_unit') return 'rows_org_unit'
  if (layout === 'row_hierarchy' && rowDimensions[0] === 'entity') return 'rows_entity'
  return 'flat'
}

export function layoutFromBreakdown(preset: PayrollBreakdownPreset): {
  layout: PersonnelLayout
  columnDimension: PersonnelDimension
  rowDimensions: PersonnelDimension[]
} {
  if (preset === 'split_entity') {
    return { layout: 'column_split', columnDimension: 'entity', rowDimensions: ['bereich'] }
  }
  if (preset === 'rows_entity') {
    return { layout: 'row_hierarchy', columnDimension: 'entity', rowDimensions: ['entity', 'bereich'] }
  }
  if (preset === 'rows_org_unit') {
    return { layout: 'row_hierarchy', columnDimension: 'org_unit', rowDimensions: ['org_unit', 'bereich'] }
  }
  return { layout: 'flat', columnDimension: 'entity', rowDimensions: ['bereich'] }
}

export function loadLayout(): PersonnelLayout {

  try {

    const v = localStorage.getItem(LAYOUT_KEY)

    if (v === 'column_split' || v === 'row_hierarchy') return v

  } catch { /* ignore */ }

  return 'flat'

}



export function saveLayout(layout: PersonnelLayout): void {

  try {

    localStorage.setItem(LAYOUT_KEY, layout)

  } catch {

    /* ignore */

  }

}



export function loadColumnDimension(): PersonnelDimension {

  try {

    const v = localStorage.getItem(COL_DIM_KEY) as PersonnelDimension

    if (DIMENSION_OPTIONS.some(d => d.id === v)) return v

  } catch { /* ignore */ }

  return 'entity'

}



export function saveColumnDimension(v: PersonnelDimension): void {

  try {

    localStorage.setItem(COL_DIM_KEY, v)

  } catch {

    /* ignore */

  }

}



export function loadRowDimensions(): PersonnelDimension[] {

  try {

    const raw = localStorage.getItem(ROW_DIMS_KEY)

    if (!raw) return DEFAULT_ROW_DIMENSIONS

    const parsed = JSON.parse(raw) as PersonnelDimension[]

    return parsed.length ? parsed.slice(0, 3) : DEFAULT_ROW_DIMENSIONS

  } catch {

    return DEFAULT_ROW_DIMENSIONS

  }

}



export function saveRowDimensions(dims: PersonnelDimension[]): void {

  try {

    localStorage.setItem(ROW_DIMS_KEY, JSON.stringify(dims.slice(0, 3)))

  } catch {

    /* ignore */

  }

}



export function loadChartDimension(): PersonnelDimension {

  try {

    const v = localStorage.getItem(CHART_DIM_KEY) as PersonnelDimension

    if (DIMENSION_OPTIONS.some(d => d.id === v)) return v

  } catch { /* ignore */ }

  return 'bereich'

}



export function saveChartDimension(v: PersonnelDimension): void {

  try {

    localStorage.setItem(CHART_DIM_KEY, v)

  } catch {

    /* ignore */

  }

}



export function loadTrendMetric(): string {

  try {

    const v = localStorage.getItem(TREND_METRIC_KEY)

    if (v) return v

  } catch { /* ignore */ }

  return 'fte'

}



export function saveTrendMetric(v: string): void {

  try {

    localStorage.setItem(TREND_METRIC_KEY, v)

  } catch {

    /* ignore */

  }

}



export function loadViewMode(): 'report' | 'table' {

  try {

    return localStorage.getItem(VIEW_KEY) === 'table' ? 'table' : 'report'

  } catch {

    return 'report'

  }

}



export function saveViewMode(mode: 'report' | 'table'): void {

  try {

    localStorage.setItem(VIEW_KEY, mode)

  } catch {

    /* ignore */

  }

}


