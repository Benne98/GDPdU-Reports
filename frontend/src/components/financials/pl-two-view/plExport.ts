import type { FinancialStatementResponse, MonthlyResponse, PlNarrativeResponse } from '../../../lib/api'
import { api } from '../../../lib/api'
import { exportFinssentialsXlsx } from '../../../lib/finssentialsExport'
import { buildExportCheckOpen } from '../../../lib/finssentialsExport/buildExportCheckOpen'
import {
  buildStatementExportTable,
  flattenStatementForExport,
} from '../../../lib/finssentialsExport/buildStatementExportTable'
import { exportStatementTableWithMasterSheets } from '../../../lib/finssentialsExport/exportDatabookXlsx'
import { exportToXlsx, todayStr, type XlsxRow } from '../../../lib/exportXlsx'
import type { PlNarrativeBullet } from './plNarrativeEngine'
import type { PlPlanMap } from './usePlStatementData'
import type { PlTableColumnDef } from './plColumnRegistry'

export { buildStatementExportTable, flattenStatementForExport }

export async function exportPlTableView(
  data: FinancialStatementResponse,
  columns: PlTableColumnDef[],
  planMap: PlPlanMap,
  monthly: MonthlyResponse | null,
  entityLabel: string,
  checkOpen?: (id: string) => boolean,
  tableTitle = 'Income Statement — Table View',
  entityCode?: string,
) {
  const isRowOpen = checkOpen ?? buildExportCheckOpen(data.rows, data.statement)
  const filename = `${data.statement === 'bs' ? 'BS' : 'PL'}_Table_${entityLabel}_${data.year}-${String(data.month).padStart(2, '0')}_${todayStr()}.xlsx`

  if (data.statement === 'pl' || data.statement === 'bs') {
    try {
      const entity =
        entityCode && entityCode !== 'all' ? entityCode : undefined
      const trialBalance = await api.trialBalanceExport({
        year: data.year,
        month: data.month,
        entity,
      })
      await exportStatementTableWithMasterSheets({
        trialBalance,
        statement: data.statement,
        data,
        columns,
        planMap,
        monthly,
        entityLabel,
        tableTitle,
        filename,
        isRowOpen,
      })
      return
    } catch {
      /* fall back to flat export if trial balance unavailable */
    }
  }

  const model = buildStatementExportTable(data.rows, columns, planMap, monthly, isRowOpen)
  await exportFinssentialsXlsx({
    tableTitle,
    subtitle: `EURk · ${entityLabel} · ${data.col_labels.cm}`,
    headers: model.headers,
    columnKinds: model.columnKinds,
    rows: model.rows,
    collapseNonReportColumns: data.statement,
    filename: `PL_Table_${entityLabel}_${data.year}-${String(data.month).padStart(2, '0')}_${todayStr()}.xlsx`,
  })
}

export async function exportPlReportView(
  data: FinancialStatementResponse,
  miniColumns: PlTableColumnDef[],
  planMap: PlPlanMap,
  bullets: PlNarrativeBullet[],
  entityLabel: string,
  narrative?: PlNarrativeResponse | null,
  checkOpen?: (id: string) => boolean,
) {
  const isRowOpen = checkOpen ?? buildExportCheckOpen(data.rows, data.statement)
  const miniRows = flattenStatementForExport(
    data.rows.filter(r => r.row_kind !== 'kpi'),
    miniColumns,
    planMap,
    null,
    isRowOpen,
  )
  const narrativeRows: XlsxRow[] = bullets.map(b => ({
    label: `${b.index}. ${b.label}`,
    values: [b.text],
    kind: 'data' as const,
    indent: 0,
  }))
  await exportFinssentialsXlsx({
    tableTitle: 'Income Statement — Report View',
    subtitle: `EURk · ${entityLabel}`,
    headers: ['EURk', ...miniColumns.map(c => c.labelLine1)],
    columnKinds: ['', ...miniColumns.map(c => c.kind)],
    rows: miniRows,
    filename: `PL_Report_${entityLabel}_${data.year}-${String(data.month).padStart(2, '0')}_${todayStr()}.xlsx`,
  })
  if (narrativeRows.length || narrative?.headline) {
    const introRows: XlsxRow[] = []
    if (narrative?.headline) {
      introRows.push({ label: 'Headline', values: [narrative.headline], kind: 'title', indent: 0 })
    }
    if (narrative?.intro) {
      introRows.push({ label: 'Intro', values: [narrative.intro], kind: 'data', indent: 0 })
    }
    await exportToXlsx({
      title: 'P&L Narrative',
      headers: ['Position', 'Commentary'],
      rows: [...introRows, ...narrativeRows],
      filename: `PL_Narrative_${entityLabel}_${todayStr()}.xlsx`,
    })
  }
}

export async function exportPlLineDetail(
  label: string,
  accounts: Array<Record<string, unknown>>,
  _bookings: Array<Record<string, unknown>>,
) {
  const accRows: XlsxRow[] = accounts.map(a => ({
    label: String(a.account_name ?? a.gl_account_id),
    values: [
      Number(a.balance_cm ?? 0),
      Number(a.balance_pm ?? 0),
      Number(a.delta ?? 0),
    ],
    kind: 'data' as const,
  }))
  await exportToXlsx({
    title: `Detail — ${label}`,
    headers: ['Account', 'CM kEUR', 'PM kEUR', 'Δ kEUR'],
    rows: accRows,
    filename: `PL_Detail_${label.replace(/\W+/g, '_')}_${todayStr()}.xlsx`,
  })
}
