import ExcelJS from 'exceljs'

const TEMPLATE_URL = '/export/Finssentials_Excel_Design_Export.xlsx'

let templateCache: ArrayBuffer | null = null

export async function loadDesignWorkbook(): Promise<ExcelJS.Workbook> {
  const wb = new ExcelJS.Workbook()
  if (!templateCache) {
    const res = await fetch(TEMPLATE_URL)
    if (!res.ok) throw new Error(`Export template not found: ${TEMPLATE_URL}`)
    templateCache = await res.arrayBuffer()
  }
  await wb.xlsx.load(templateCache)
  return wb
}

export const DESIGN_SHEET_NAME = 'General sales table'
