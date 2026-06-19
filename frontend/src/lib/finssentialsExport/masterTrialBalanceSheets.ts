import ExcelJS from 'exceljs'
import type { TrialBalanceColumn, TrialBalanceSheetPayload } from '../api'

function excelCol(n: number): string {
  let s = ''
  let x = n
  while (x > 0) {
    const m = (x - 1) % 26
    s = String.fromCharCode(65 + m) + s
    x = Math.floor((x - 1) / 26)
  }
  return s
}

function rowValues(
  row: TrialBalanceSheetPayload['rows'][0],
  columns: TrialBalanceColumn[],
): (string | number | null)[] {
  return columns.map(col => {
    if (col.group === 'spacer') return null
    if (col.key === 'entity') return row.entity
    if (col.key === 'level_1') return row.level_1 ?? ''
    if (col.key === 'level_2') return row.level_2 ?? ''
    if (col.key === 'level_3') return row.level_3 ?? ''
    if (col.key === 'level_4') return row.level_4 ?? ''
    if (col.key === 'account') return row.account
    return row.amounts[col.key] ?? 0
  })
}

export type MasterSheetMeta = {
  sheetName: string
  payload: TrialBalanceSheetPayload
  headerRow: number
  dataStartRow: number
  dataEndRow: number
  amountColKeys: string[]
  amountColLetters: Record<string, string>
}

/** Write PL_all / BS_all account-level sheets and return row/column metadata for SUMIFS. */
export function writeMasterTrialBalanceSheet(
  wb: ExcelJS.Workbook,
  sheetName: string,
  payload: TrialBalanceSheetPayload,
  opts?: { addBalanceCheck?: boolean },
): MasterSheetMeta {
  const ws = wb.addWorksheet(sheetName)
  const cols = payload.columns
  const headers = cols.map(c => c.label)
  const dimCount = cols.filter(c => c.group === 'dim').length

  ws.addRow(headers)
  const headerRow = 1
  ws.getRow(headerRow).font = { bold: true, size: 10, color: { argb: 'FF475569' } }
  ws.getRow(headerRow).fill = {
    type: 'pattern',
    pattern: 'solid',
    fgColor: { argb: 'FFF1F5F9' },
  }

  const dataStartRow = 2
  for (const row of payload.rows) {
    const values = rowValues(row, cols)
    const r = ws.addRow(values)
    for (let i = dimCount + 1; i <= values.length; i++) {
      const cell = r.getCell(i)
      if (typeof cell.value === 'number') cell.numFmt = '#,##0.00'
    }
  }

  let dataEndRow = dataStartRow + payload.rows.length - 1
  if (opts?.addBalanceCheck && payload.rows.length > 0) {
    const checkRow = ws.addRow(cols.map((col, colIdx) => {
      if (colIdx === 0) return 'CHECK (sum must be 0)'
      if (col.group === 'spacer' || col.group === 'dim') return null
      const letter = excelCol(colIdx + 1)
      return { formula: `SUM(${letter}${dataStartRow}:${letter}${dataEndRow})` }
    }))
    checkRow.font = { bold: true, color: { argb: 'FFDC2626' } }
    dataEndRow += 1
  }

  ws.columns = cols.map(c => ({
    width: c.group === 'dim' ? (c.key === 'account' ? 42 : 16) : 11,
  }))
  ws.views = [{ state: 'frozen', ySplit: 1 }]

  const amountColKeys = cols.filter(c => c.group === 'summary' || c.group === 'monthly').map(c => c.key)
  const amountColLetters: Record<string, string> = {}
  cols.forEach((c, idx) => {
    if (c.group === 'summary' || c.group === 'monthly') {
      amountColLetters[c.key] = excelCol(idx + 1)
    }
  })

  return {
    sheetName,
    payload,
    headerRow,
    dataStartRow,
    dataEndRow,
    amountColKeys,
    amountColLetters,
  }
}

export function masterColumnKeyForPlKind(
  kind: string,
  year: number,
  month: number,
): string | null {
  if (kind === 'cm') return `${year}-${String(month).padStart(2, '0')}`
  if (kind === 'ytd') return `YTD${year}`
  if (kind === 'py_cm') return `${year - 1}-${String(month).padStart(2, '0')}`
  if (kind === 'pm') {
    const pm = month === 1 ? 12 : month - 1
    const py = month === 1 ? year - 1 : year
    return `${py}-${String(pm).padStart(2, '0')}`
  }
  if (kind === 'ytd_py') return `FY${year - 1}`
  const monthMatch = /^month_(\d{4})_(\d{1,2})$/.exec(kind)
  if (monthMatch) {
    return `${monthMatch[1]}-${String(Number(monthMatch[2])).padStart(2, '0')}`
  }
  return null
}

export function masterColumnKeyForBsKind(
  kind: string,
  year: number,
  month: number,
): string | null {
  if (kind === 'cm' || kind === 'ytd') return `CM${year}-${String(month).padStart(2, '0')}`
  if (kind === 'py_cm') return `DEC${year - 1}`
  if (kind === 'pm') {
    const pm = month === 1 ? 12 : month - 1
    const py = month === 1 ? year - 1 : year
    return `${py}-${String(pm).padStart(2, '0')}`
  }
  if (kind === 'ytd_py') return `DEC${year - 1}`
  const plKey = masterColumnKeyForPlKind(kind, year, month)
  return plKey
}

export function buildMasterSumifsFormula(
  master: MasterSheetMeta,
  masterColKey: string,
  drill: { level_1?: string | null; level_2?: string | null; level_3?: string | null; level_4?: string | null },
  statementType: 'PL' | 'BS',
): string | null {
  const colLetter = master.amountColLetters[masterColKey]
  if (!colLetter) return null

  const amountRange = `${master.sheetName}!$${colLetter}$${master.dataStartRow}:$${colLetter}$${master.dataEndRow}`
  const esc = (s: string) => s.replace(/"/g, '""')

  if (statementType === 'PL') {
    if (!drill.level_2) return null
    const parts = [
      `SUMIFS(${amountRange}`,
      `${master.sheetName}!$B$${master.dataStartRow}:$B$${master.dataEndRow},"${esc(drill.level_2)}"`,
    ]
    if (drill.level_3) {
      parts.push(
        `${master.sheetName}!$C$${master.dataStartRow}:$C$${master.dataEndRow},"${esc(drill.level_3)}"`,
      )
    }
    if (drill.level_4) {
      parts.push(
        `${master.sheetName}!$D$${master.dataStartRow}:$D$${master.dataEndRow},"${esc(drill.level_4)}"`,
      )
    }
    return `${parts.join(',')})`
  }

  if (!drill.level_2) return null
  const parts = [
    `SUMIFS(${amountRange}`,
    `${master.sheetName}!$C$${master.dataStartRow}:$C$${master.dataEndRow},"${esc(drill.level_2)}"`,
  ]
  if (drill.level_1) {
    parts.splice(1, 0, `${master.sheetName}!$B$${master.dataStartRow}:$B$${master.dataEndRow},"${esc(drill.level_1)}"`)
  }
  if (drill.level_3) {
    parts.push(
      `${master.sheetName}!$D$${master.dataStartRow}:$D$${master.dataEndRow},"${esc(drill.level_3)}"`,
    )
  }
  if (drill.level_4) {
    parts.push(
      `${master.sheetName}!$E$${master.dataStartRow}:$E$${master.dataEndRow},"${esc(drill.level_4)}"`,
    )
  }
  return `${parts.join(',')})`
}

export function masterKeyFromColumn(
  col: { id: string; kind: string },
  year: number,
  month: number,
  statementType: 'PL' | 'BS',
): string | null {
  if (col.kind === 'month' && col.id.startsWith('month:')) return col.id.slice(6)
  if (statementType === 'PL') return masterColumnKeyForPlKind(col.kind, year, month)
  return masterColumnKeyForBsKind(col.kind, year, month)
}
