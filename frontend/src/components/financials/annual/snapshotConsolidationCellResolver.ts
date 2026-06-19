import type {
  BsSnapshotPeriodAmounts,
  BsSnapshotPeriodKey,
  ConsolidationRow,
} from '../../../lib/api'
import type { AnnualSnapshotTableColId } from './annualSnapshotConsolidationColumns'

export type SnapshotConsolTarget =
  | { kind: 'entity'; code: string }
  | { kind: 'aggregated' }
  | { kind: 'ic' }
  | { kind: 'consolidation' }

function zeroPeriods(): BsSnapshotPeriodAmounts {
  return { dec_py2: 0, fy_py: 0, fy: 0, cm_py: 0, cm: 0 }
}

export function snapshotPeriodsForTarget(
  row: ConsolidationRow,
  target: SnapshotConsolTarget,
): BsSnapshotPeriodAmounts | null {
  switch (target.kind) {
    case 'entity':
      return row.entity_periods?.[target.code] ?? null
    case 'aggregated':
      return row.aggregated_periods ?? null
    case 'consolidation':
      return row.consolidation_periods ?? null
    case 'ic':
      return zeroPeriods()
  }
}

export function snapshotConsolidationCellValue(
  row: ConsolidationRow,
  target: SnapshotConsolTarget,
  colId: AnnualSnapshotTableColId,
): number | null {
  const isKpi = row.row_kind === 'kpi'
  const periods = snapshotPeriodsForTarget(row, target)

  if (isKpi) {
    if (periods) {
      switch (colId) {
        case 'delta_fy':
          return periods.fy - periods.fy_py
        case 'delta_cm':
          return periods.cm - periods.cm_py
        case 'cagr': {
          const start = periods.dec_py2
          if (Math.abs(start) < 1e-3) return null
          return (Math.pow(periods.fy / start, 0.5) - 1) * 100
        }
        default:
          return periods[colId as BsSnapshotPeriodKey]
      }
    }
    if (colId === 'cm') {
      switch (target.kind) {
        case 'entity': {
          const v = row.entity_amounts[target.code]
          return v == null ? null : v
        }
        case 'aggregated':
          return row.aggregated
        case 'consolidation':
          return row.consolidation
        case 'ic':
          return row.ic_eliminations
      }
    }
    return null
  }

  if (!periods) {
    if (colId === 'cm') {
      switch (target.kind) {
        case 'entity':
          const v = row.entity_amounts[target.code]
          return v == null ? null : v
        case 'aggregated':
          return row.aggregated
        case 'consolidation':
          return row.consolidation
        case 'ic':
          return row.ic_eliminations
      }
    }
    return null
  }

  switch (colId) {
    case 'delta_fy':
      return periods.fy - periods.fy_py
    case 'delta_cm':
      return periods.cm - periods.cm_py
    case 'cagr': {
      const start = periods.dec_py2
      if (Math.abs(start) < 1e-3) return null
      return (Math.pow(periods.fy / start, 0.5) - 1) * 100
    }
    default:
      return periods[colId as BsSnapshotPeriodKey]
  }
}

export function snapColToPeriodKey(colId: AnnualSnapshotTableColId): BsSnapshotPeriodKey | 'cm' {
  if (colId === 'delta_fy' || colId === 'delta_cm' || colId === 'cagr') return 'cm'
  return colId
}
