import ExcelJS from 'exceljs'
import type { FinancialStatementResponse, MonthlyResponse, TrialBalanceExportResponse } from '../api'
import type { PlTableColumnDef } from '../../components/financials/pl-two-view/plColumnRegistry'
import type { PlPlanMap } from '../../components/financials/pl-two-view/usePlStatementData'
import { buildStatementExportTable } from './buildStatementExportTable'
import {
  applyDataCell,
  applyHeaderCell,
  applyProjectNameStyle,
  applySubtitleStyle,
  applyTableTitleStyle,
  dataBgForCell,
  exportValueTextArgb,
  isSubtotalRowKind,
  labelTextArgb,
  PROJECT_NAME,
} from './designStyles'
import {
  buildMasterSumifsFormula,
  masterKeyFromColumn,
  writeMasterTrialBalanceSheet,
  type MasterSheetMeta,
} from './masterTrialBalanceSheets'
import { todayStr } from '../exportXlsx'

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

function walkStatementRows(
  rows: FinancialStatementResponse['rows'],
  out: FinancialStatementResponse['rows'] = [],
): FinancialStatementResponse['rows'] {
  for (const r of rows) {
    out.push(r)
    if (r.children?.length) walkStatementRows(r.children, out)
    if (r.accounts?.length) walkStatementRows(r.accounts, out)
  }
  return out
}

function writeStatementSheet(
  wb: ExcelJS.Workbook,
  sheetName: string,
  tableTitle: string,
  subtitle: string,
  model: ReturnType<typeof buildStatementExportTable>,
  columns: PlTableColumnDef[],
  flatRows: FinancialStatementResponse['rows'],
  master: MasterSheetMeta | null,
  statementType: 'PL' | 'BS',
  year: number,
  month: number,
): void {
  const ws = wb.addWorksheet(sheetName)
  const N = model.headers.length
  const kinds = model.columnKinds

  ws.addRow([PROJECT_NAME])
  applyProjectNameStyle(ws.getRow(1).getCell(1))
  ws.addRow([tableTitle])
  applyTableTitleStyle(ws.getRow(2).getCell(1))
  ws.addRow([subtitle])
  applySubtitleStyle(ws.getRow(3).getCell(1))
  ws.addRow([])

  const hRow = ws.addRow(model.headers)
  for (let c = 1; c <= N; c++) {
    applyHeaderCell(hRow.getCell(c), c, kinds[c - 1])
  }

  const flatByLineCode = new Map(
    flatRows.filter(r => r.line_code).map(r => [r.line_code, r]),
  )

  for (const row of model.rows) {
    if (row.kind === 'blank') {
      ws.addRow([])
      continue
    }

    const values: (string | number | { formula: string })[] = [row.label]
    const stmtRow = row.lineCode ? flatByLineCode.get(row.lineCode) : undefined
    const drill = stmtRow?.drill

    columns.forEach((col, colIdx) => {
      const raw = row.values[colIdx]
      if (master && drill && typeof raw === 'number') {
        const masterKey = masterKeyFromColumn(col, year, month, statementType)
        if (masterKey) {
          const formula = buildMasterSumifsFormula(master, masterKey, drill, statementType)
          if (formula) {
            values.push({ formula })
            return
          }
        }
      }
      values.push(raw === '' || raw == null ? '' : raw)
    })

    const eRow = ws.addRow(values)
    const isSubtotal = isSubtotalRowKind(row.kind)
    const bold = row.kind === 'section' || row.kind === 'title' || isSubtotal
    for (let c = 1; c <= N; c++) {
      const cell = eRow.getCell(c)
      const colKind = kinds[c - 1]
      const bg = dataBgForCell(c, colKind, row.kind)
      let numFmt: string | undefined
      let color = labelTextArgb(row.kind)
      if (c > 1) {
        const val = row.values[c - 2]
        if (typeof val === 'number') {
          const isKpiVal = row.kpiCols?.includes(c - 2) ?? false
          numFmt = isKpiVal ? '0.0' : '#,##0;(#,##0)'
          color = row.kind === 'kpi' ? color : exportValueTextArgb(val, colKind, color)
        }
      }
      applyDataCell(cell, c, { bold, bg, textArgb: color, numFmt, borderTop: isSubtotal })
    }
  }

  ws.columns = [{ width: 36 }, ...Array.from({ length: N - 1 }, () => ({ width: 13 }))]
}

export async function exportDatabookXlsx(opts: {
  trialBalance: TrialBalanceExportResponse
  pl?: {
    data: FinancialStatementResponse
    columns: PlTableColumnDef[]
    planMap: PlPlanMap
    monthly: MonthlyResponse | null
    entityLabel: string
  }
  bs?: {
    data: FinancialStatementResponse
    columns: PlTableColumnDef[]
    planMap: PlPlanMap
    monthly: MonthlyResponse | null
    entityLabel: string
  }
  filename: string
  isRowOpen?: (id: string) => boolean
}): Promise<void> {
  const wb = new ExcelJS.Workbook()
  wb.creator = PROJECT_NAME
  wb.created = new Date()

  const plMaster = writeMasterTrialBalanceSheet(wb, 'PL_all', opts.trialBalance.pl)
  const bsMaster = writeMasterTrialBalanceSheet(wb, 'BS_all', opts.trialBalance.bs, { addBalanceCheck: true })

  const { year, month } = opts.trialBalance.anchor
  const subtitle = `${opts.pl?.entityLabel ?? opts.bs?.entityLabel ?? 'All entities'} · ${String(month).padStart(2, '0')}/${year}`

  if (opts.pl) {
    const checkOpen = opts.isRowOpen ?? (() => true)
    const model = buildStatementExportTable(
      opts.pl.data.rows,
      opts.pl.columns,
      opts.pl.planMap,
      opts.pl.monthly,
      checkOpen,
    )
    writeStatementSheet(
      wb,
      'PL',
      'Income statement — Table view',
      subtitle,
      model,
      opts.pl.columns,
      walkStatementRows(opts.pl.data.rows),
      plMaster,
      'PL',
      year,
      month,
    )
  }

  if (opts.bs) {
    const checkOpen = opts.isRowOpen ?? (() => true)
    const model = buildStatementExportTable(
      opts.bs.data.rows,
      opts.bs.columns,
      opts.bs.planMap,
      opts.bs.monthly,
      checkOpen,
    )
    writeStatementSheet(
      wb,
      'BS',
      'Balance sheet — Table view',
      subtitle,
      model,
      opts.bs.columns,
      walkStatementRows(opts.bs.data.rows),
      bsMaster,
      'BS',
      year,
      month,
    )
  }

  const buf = await wb.xlsx.writeBuffer()
  downloadBlob(new Blob([buf as BlobPart]), opts.filename)
}

export async function exportStatementTableWithMasterSheets(opts: {
  trialBalance: TrialBalanceExportResponse
  statement: 'pl' | 'bs'
  data: FinancialStatementResponse
  columns: PlTableColumnDef[]
  planMap: PlPlanMap
  monthly: MonthlyResponse | null
  entityLabel: string
  tableTitle: string
  filename: string
  isRowOpen?: (id: string) => boolean
}): Promise<void> {
  const wb = new ExcelJS.Workbook()
  wb.creator = PROJECT_NAME
  wb.created = new Date()

  const plMaster = writeMasterTrialBalanceSheet(wb, 'PL_all', opts.trialBalance.pl)
  const bsMaster = writeMasterTrialBalanceSheet(wb, 'BS_all', opts.trialBalance.bs, { addBalanceCheck: true })

  const { year, month } = opts.trialBalance.anchor
  const subtitle = `${opts.entityLabel} · ${String(month).padStart(2, '0')}/${year}`
  const checkOpen = opts.isRowOpen ?? (() => true)
  const model = buildStatementExportTable(
    opts.data.rows,
    opts.columns,
    opts.planMap,
    opts.monthly,
    checkOpen,
  )

  const sheetName = opts.statement === 'pl' ? 'PL' : 'BS'
  const master = opts.statement === 'pl' ? plMaster : bsMaster
  const stType = opts.statement === 'pl' ? 'PL' : 'BS'

  writeStatementSheet(
    wb,
    sheetName,
    opts.tableTitle,
    subtitle,
    model,
    opts.columns,
    walkStatementRows(opts.data.rows),
    master,
    stType,
    year,
    month,
  )

  const buf = await wb.xlsx.writeBuffer()
  downloadBlob(new Blob([buf as BlobPart]), opts.filename)
}

export function databookFilename(
  entityLabel: string,
  year: number,
  month: number,
  scope: 'monthly' | 'annual',
): string {
  const safe = entityLabel.replace(/\W+/g, '_')
  return `Finssentials_Databook_${scope}_${safe}_${year}-${String(month).padStart(2, '0')}_${todayStr()}.xlsx`
}
