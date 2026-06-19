/**
 * Finssentials-branded XLSX export (layout from Excel Design_Export.xlsx).
 */
import ExcelJS from 'exceljs'
import {
  PROJECT_NAME,
  applyDataCell,
  applyHeaderCell,
  applyProjectNameStyle,
  applySubtitleStyle,
  applyTableTitleStyle,
  dataBgForCell,
  exportValueTextArgb,
  isSubtotalRowKind,
  labelTextArgb,
} from './designStyles'
import type { ExportFlatRow } from './flattenTreeForExport'
import type { FinStatementKind } from '../../components/financials/statement-two-view/statementTypes'
import { collapsedExcelColumnsForKinds } from './reportViewColumns'

export type FinssentialsXlsxConfig = {
  tableTitle: string
  subtitle?: string
  headers: string[]
  /** Parallel to headers — used for CM / YTD column tint (e.g. `cm`, `ytd`). */
  columnKinds?: string[]
  rows: ExportFlatRow[]
  filename: string
  sheetName?: string
  projectName?: string
  /**
   * Hide (collapse) value columns not shown in Report View.
   * Pass statement kind or `true` (defaults to `pl` kinds).
   */
  collapseNonReportColumns?: boolean | FinStatementKind
  /** Explicit 1-based Excel column indices to hide; overrides auto collapse when set. */
  collapsedColumnIndices?: number[]
}

export async function exportFinssentialsXlsx(cfg: FinssentialsXlsxConfig): Promise<void> {
  const wb = new ExcelJS.Workbook()
  wb.creator = PROJECT_NAME
  wb.created = new Date()
  const ws = wb.addWorksheet(cfg.sheetName ?? 'Export')
  const N = cfg.headers.length
  const project = cfg.projectName ?? PROJECT_NAME

  ws.properties.outlineProperties = {
    summaryBelow: true,
    summaryRight: true,
  }

  ws.columns = [
    { width: 36 },
    ...Array.from({ length: N - 1 }, () => ({ width: 13 })),
  ]

  const r1 = ws.addRow([project])
  r1.height = 28
  applyProjectNameStyle(r1.getCell(1))

  const r2 = ws.addRow([cfg.tableTitle])
  r2.height = 20
  applyTableTitleStyle(r2.getCell(1))

  if (cfg.subtitle) {
    const r3 = ws.addRow([cfg.subtitle])
    applySubtitleStyle(r3.getCell(1))
  } else {
    ws.addRow([])
  }

  const kinds = cfg.columnKinds ?? []
  const hRow = ws.addRow(cfg.headers)
  hRow.height = 18
  for (let c = 1; c <= N; c++) {
    applyHeaderCell(hRow.getCell(c), c, kinds[c - 1])
  }

  for (let ri = 0; ri < cfg.rows.length; ri++) {
    const row = cfg.rows[ri]
    if (row.kind === 'blank') {
      ws.addRow([])
      continue
    }

    const next = cfg.rows[ri + 1]
    const isSubtotal = isSubtotalRowKind(row.kind)
    const borderBottom =
      isSubtotal && (next?.kind === 'kpi' || next?.kind === 'blank' || next == null)

    const eRow = ws.addRow([row.label, ...row.values])
    eRow.height = 15
    eRow.outlineLevel = row.outlineLevel
    eRow.hidden = row.hidden

    const bold = row.kind === 'section' || row.kind === 'title' || isSubtotal
    const italic = row.kind === 'kpi'
    const baseText = labelTextArgb(row.kind)

    for (let c = 1; c <= N; c++) {
      const cell = eRow.getCell(c)
      let numFmt: string | undefined
      let color: string = baseText
      const colKind = kinds[c - 1]
      const bg = dataBgForCell(c, colKind, row.kind)

      if (c > 1) {
        const valIdx = c - 2
        const val = row.values[valIdx]
        if (typeof val === 'number') {
          const isKpiVal = row.kpiCols?.includes(valIdx) ?? false
          numFmt = isKpiVal ? '0.0' : '#,##0;(#,##0)'
          color = row.kind === 'kpi'
            ? baseText
            : exportValueTextArgb(val, colKind, baseText)
        }
      }

      applyDataCell(cell, c, {
        bold,
        italic,
        bg,
        textArgb: color,
        numFmt,
        borderTop: isSubtotal,
        borderBottom,
      })
    }
  }

  const collapsed =
    cfg.collapsedColumnIndices ??
    (cfg.collapseNonReportColumns
      ? collapsedExcelColumnsForKinds(
          kinds.slice(1),
          cfg.collapseNonReportColumns === true ? 'pl' : cfg.collapseNonReportColumns,
        )
      : undefined)
  if (collapsed?.length) {
    ws.properties.outlineLevelCol = Math.max(ws.properties.outlineLevelCol ?? 0, 1)
    for (const colIdx of collapsed) {
      const col = ws.getColumn(colIdx)
      col.hidden = true
      col.outlineLevel = 1
    }
  }

  const buf = await wb.xlsx.writeBuffer()
  const blob = new Blob([buf as BlobPart], {
    type: 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
  })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = cfg.filename
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
