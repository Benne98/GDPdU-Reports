import ExcelJS from 'exceljs'
import type { TrialBalanceColumn, TrialBalanceSheetPayload } from '../../lib/api'

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
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

export async function exportTrialBalanceWorkbook(opts: {
  sheets: { name: string; payload: TrialBalanceSheetPayload }[]
  subtitle?: string
  filename: string
}): Promise<void> {
  const wb = new ExcelJS.Workbook()
  wb.creator = 'Finssentials'
  wb.created = new Date()

  for (const { name, payload } of opts.sheets) {
    const ws = wb.addWorksheet(name)
    const cols = payload.columns
    const headers = cols.map(c => c.label)
    const dimCount = cols.filter(c => c.group === 'dim').length

    ws.addRow(headers)
    const headerRow = ws.getRow(1)
    headerRow.font = { bold: true, size: 10, color: { argb: 'FF475569' } }
    headerRow.fill = {
      type: 'pattern',
      pattern: 'solid',
      fgColor: { argb: 'FFF1F5F9' },
    }
    headerRow.alignment = { vertical: 'middle', horizontal: 'left' }

    for (const row of payload.rows) {
      const values = rowValues(row, cols)
      const r = ws.addRow(values)
      for (let i = dimCount + 1; i <= values.length; i++) {
        const cell = r.getCell(i)
        if (typeof cell.value === 'number') {
          cell.numFmt = '#,##0.00'
        }
      }
    }

    ws.columns = cols.map((c, idx) => ({
      width: c.group === 'dim' ? (idx === cols.findIndex(x => x.key === 'account') ? 42 : 16) : 11,
    }))
    ws.views = [{ state: 'frozen', ySplit: 1 }]
  }

  const buf = await wb.xlsx.writeBuffer()
  downloadBlob(new Blob([buf]), opts.filename)
}

/** Format amount for on-screen preview (full EUR). */
export function fmtTrialBalanceAmount(v: number): string {
  const abs = Math.abs(v)
  const s = abs.toLocaleString('en-US', { minimumFractionDigits: 2, maximumFractionDigits: 2 })
  return v < 0 ? `(${s})` : s
}
