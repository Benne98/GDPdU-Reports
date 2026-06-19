import ExcelJS from 'exceljs'
import type { TrialBalanceSheetPayload } from '../../lib/api'
import { writeMasterTrialBalanceSheet } from '../../lib/finssentialsExport/masterTrialBalanceSheets'

function downloadBlob(blob: Blob, filename: string): void {
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()
  URL.revokeObjectURL(url)
}

export type { TrialBalanceSheetPayload }

export async function exportTrialBalanceXlsx(
  pl: TrialBalanceSheetPayload,
  bs: TrialBalanceSheetPayload,
  opts: { entityLabel: string; year: number; month: number },
): Promise<void> {
  const wb = new ExcelJS.Workbook()
  wb.creator = 'Finssentials'
  wb.created = new Date()
  writeMasterTrialBalanceSheet(wb, 'PL_all', pl)
  writeMasterTrialBalanceSheet(wb, 'BS_all', bs, { addBalanceCheck: true })
  const buf = await wb.xlsx.writeBuffer()
  downloadBlob(
    new Blob([buf as BlobPart]),
    `Trial_Balance_PL_BS_${opts.year}-${String(opts.month).padStart(2, '0')}.xlsx`,
  )
}
