import type { FinancialStatementRow } from '../api'
import type { PlTableColumnDef } from '../../components/financials/pl-two-view/plColumnRegistry'
import { resolveCellValue } from '../../components/financials/pl-two-view/plColumnRegistry'
import type { PlPlanMap } from '../../components/financials/pl-two-view/usePlStatementData'
import type { MonthlyResponse } from '../api'
import { flattenTreeForExport, type ExportFlatRow } from './flattenTreeForExport'

export type StatementExportTableModel = {
  headers: string[]
  columnKinds: string[]
  rows: ExportFlatRow[]
  /** Rows shown in PDF/PPT (collapsed — excludes hidden children). */
  visibleRows: ExportFlatRow[]
}

export function flattenStatementForExport(
  rows: FinancialStatementRow[],
  columns: PlTableColumnDef[],
  planMap: PlPlanMap,
  monthly: MonthlyResponse | null | undefined,
  isRowOpen: (id: string) => boolean,
  options?: { excludeKpi?: boolean },
): ExportFlatRow[] {
  const source = options?.excludeKpi ? rows.filter(r => r.row_kind !== 'kpi') : rows
  return flattenTreeForExport(source as Parameters<typeof flattenTreeForExport>[0], {
    isRowOpen,
    mapRow: row => {
      const r = row as FinancialStatementRow
      if (r.row_kind === 'title') {
        return {
          label: r.label,
          values: columns.map(() => ''),
          kind: 'title' as const,
          lineCode: r.line_code,
        }
      }
      if (!r.amounts && r.row_kind !== 'kpi') return null
      const values = columns.map(col => resolveCellValue(r, col, planMap, monthly))
      return {
        label: r.label,
        values,
        kind:
          r.row_kind === 'subtotal'
            ? ('subtotal' as const)
            : r.row_kind === 'kpi'
              ? ('kpi' as const)
              : ('data' as const),
        kpiCols: r.row_kind === 'kpi' ? columns.map((_, i) => i) : undefined,
        lineCode: r.line_code,
      }
    },
  })
}

export function buildStatementExportTable(
  rows: FinancialStatementRow[],
  columns: PlTableColumnDef[],
  planMap: PlPlanMap,
  monthly: MonthlyResponse | null | undefined,
  isRowOpen: (id: string) => boolean,
  labelHeader = 'EURk',
): StatementExportTableModel {
  const flat = flattenStatementForExport(rows, columns, planMap, monthly, isRowOpen)
  return {
    headers: [labelHeader, ...columns.map(c => c.labelLine1)],
    columnKinds: ['', ...columns.map(c => c.kind)],
    rows: flat,
    visibleRows: flat.filter(r => !r.hidden && r.kind !== 'blank'),
  }
}
