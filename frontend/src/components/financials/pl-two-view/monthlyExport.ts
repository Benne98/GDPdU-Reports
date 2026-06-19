import type { MonthlyResponse, MonthlyRow } from '../../../lib/api'
import { exportFinssentialsXlsx, flattenTreeForExport } from '../../../lib/finssentialsExport'
import { buildMonthlyPptxConfig } from '../../../lib/finssentialsExport/buildPptxExportConfig'
import { exportFinssentialsPptx } from '../../../lib/finssentialsExport/pptx/exportFinssentialsPptx'
import { buildExportCheckOpen } from '../../../lib/finssentialsExport/buildExportCheckOpen'
import type { PlPlanMap } from './usePlStatementData'
import {
  resolveMonthlyColumnValue,
  type MonthlyViewColumnDef,
} from './monthlyColumnRegistry'
import { monthlyPeriodKey } from './plMonthlyLineCode'
import { periodLabel } from './plPeriodLabels'

function flattenMonthlyForExport(
  rows: MonthlyRow[],
  periodKeys: string[],
  extraColumns: MonthlyViewColumnDef[],
  planByPeriod: Map<string, PlPlanMap>,
  isRowOpen: (id: string) => boolean,
) {
  return flattenTreeForExport(rows, {
    isRowOpen,
    mapRow: row => {
      const r = row as MonthlyRow
      const values: (number | null)[] = []
      for (const pk of periodKeys) {
        const v = r.amounts?.[pk]
        values.push(v != null ? Math.round(v / (r.row_kind === 'kpi' ? 1 : 1000)) : null)
      }
      for (const col of extraColumns) {
        const v = resolveMonthlyColumnValue(r, col, planByPeriod)
        if (v == null) values.push(null)
        else if (r.row_kind === 'kpi') values.push(+v.toFixed(1))
        else values.push(Math.round(v / 1000))
      }
      const kind =
        r.row_kind === 'title'
          ? ('section' as const)
          : r.row_kind === 'subtotal'
            ? ('subtotal' as const)
            : r.row_kind === 'kpi'
              ? ('kpi' as const)
              : ('data' as const)
      return {
        label: r.label,
        values,
        kind,
        kpiCols: r.row_kind === 'kpi' ? periodKeys.map((_, i) => i) : undefined,
      }
    },
  })
}

export async function exportMonthlyTableXlsx(
  data: MonthlyResponse,
  extraColumns: MonthlyViewColumnDef[],
  planByPeriod: Map<string, PlPlanMap>,
  title: string,
  checkOpen?: (id: string) => boolean,
): Promise<void> {
  const periodKeys = data.periods.map(p => monthlyPeriodKey(p.year, p.month))
  const headers = [
    'EURk',
    ...data.periods.map(p => periodLabel(p.year, p.month)),
    ...extraColumns.map(c => c.labelLine1),
  ]
  const isRowOpen =
    checkOpen ??
    buildExportCheckOpen(data.rows as unknown as import('../../../lib/api').FinancialStatementRow[], data.statement)
  const rows = flattenMonthlyForExport(data.rows, periodKeys, extraColumns, planByPeriod, isRowOpen)
  const stamp = monthlyPeriodKey(data.year, data.month)
  await exportFinssentialsXlsx({
    tableTitle: title,
    subtitle: 'Monthly view — amounts in EURk',
    headers,
    rows,
    filename: `Monthly_${data.statement}_${stamp}.xlsx`,
  })
}

export async function exportMonthlyTablePptx(
  data: MonthlyResponse,
  extraColumns: MonthlyViewColumnDef[],
  planByPeriod: Map<string, PlPlanMap>,
  title: string,
  footerRight: string,
  checkOpen?: (id: string) => boolean,
): Promise<void> {
  const stmtNames: Record<string, string> = {
    pl: 'Income statement',
    bs: 'Balance sheet',
    cf: 'Cash flow',
    wc: 'Working capital',
  }
  const cfg = buildMonthlyPptxConfig(
    data,
    extraColumns,
    planByPeriod,
    title,
    footerRight,
    {
      breadcrumbCurrent: stmtNames[data.statement] ?? data.statement,
      pageTitle: title,
    },
    checkOpen,
  )
  await exportFinssentialsPptx(cfg)
}
