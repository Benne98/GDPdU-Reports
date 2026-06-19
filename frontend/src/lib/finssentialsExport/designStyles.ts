/**
 * Finssentials Excel export palette — aligned with Excel_Oberstes Level.xlsx
 */
import type ExcelJS from 'exceljs'

export const PROJECT_NAME = 'Finssentials'

export const C = {
  navy: 'FF1E3A5F',
  navyLt: 'FFF1F5F9',
  grayBg: 'FFF8FAFC',
  grayAlt: 'FFF0F4F8',
  dark: 'FF111827',
  mid: 'FF475569',
  muted: 'FF94A3B8',
  white: 'FFFFFFFF',
  red: 'FFDC2626',
  green: 'FF2AA05F',
  border: 'FFE2E8F0',
} as const

const DELTA_COLUMN_KINDS = new Set([
  'mom',
  'yoy',
  'ytd_delta',
  'ytd_vs_plan',
  'plan_vs_actual',
  'month_mom',
  'month_yoy',
  'month_delta',
])

export function isDeltaColumnKind(kind: string | undefined): boolean {
  if (!kind) return false
  if (DELTA_COLUMN_KINDS.has(kind)) return true
  return (
    kind.includes('delta') ||
    kind.endsWith('_mom') ||
    kind.endsWith('_yoy') ||
    kind.includes('vs_plan')
  )
}

/** CM / YTD actual columns — tinted #F0F4F8 */
export function isHighlightColumnKind(kind: string | undefined): boolean {
  return kind === 'cm' || kind === 'ytd'
}

export function headerBgForColumn(_colIndex: number, kind: string | undefined): string {
  if (isHighlightColumnKind(kind)) return C.grayAlt
  return C.grayBg
}

export function dataBgForCell(
  colIndex: number,
  kind: string | undefined,
  rowKind: string,
): string {
  if (rowKind === 'kpi') return C.grayBg
  if (rowKind === 'section' || rowKind === 'title') return C.grayBg
  if (colIndex === 1) return C.white
  if (isHighlightColumnKind(kind)) return C.grayAlt
  return C.white
}

export function exportValueTextArgb(
  val: number,
  colKind: string | undefined,
  baseArgb: string,
): string {
  if (!isDeltaColumnKind(colKind)) return baseArgb
  if (val > 0) return C.green
  if (val < 0) return C.red
  return baseArgb
}

export function isSubtotalRowKind(rowKind: string): boolean {
  return rowKind === 'subtotal'
}

export function labelTextArgb(rowKind: string): string {
  if (rowKind === 'section' || rowKind === 'title') return C.navy
  if (rowKind === 'kpi') return C.mid
  return C.dark
}

export function applyProjectNameStyle(cell: ExcelJS.Cell) {
  cell.font = { name: 'Calibri', size: 24, color: { argb: C.navy } }
  cell.alignment = { horizontal: 'left', vertical: 'middle' }
}

export function applyTableTitleStyle(cell: ExcelJS.Cell) {
  cell.font = { name: 'Calibri', size: 9, bold: true, color: { argb: C.navy } }
  cell.alignment = { horizontal: 'left', vertical: 'middle' }
}

export function applySubtitleStyle(cell: ExcelJS.Cell) {
  cell.font = { name: 'Calibri', size: 8, color: { argb: C.muted } }
  cell.alignment = { horizontal: 'left', vertical: 'middle' }
}

export function applyHeaderCell(cell: ExcelJS.Cell, colIndex: number, columnKind?: string) {
  cell.fill = {
    type: 'pattern',
    pattern: 'solid',
    fgColor: { argb: headerBgForColumn(colIndex, columnKind) },
  }
  cell.font = { name: 'Calibri', size: 9, bold: true, color: { argb: C.mid } }
  cell.alignment = {
    horizontal: colIndex === 1 ? 'left' : 'right',
    vertical: 'middle',
  }
  cell.border = { bottom: { style: 'thin', color: { argb: C.border } } }
}

const thinBorder = { style: 'thin' as const, color: { argb: C.border } }

export function applyDataCell(
  cell: ExcelJS.Cell,
  colIndex: number,
  opts: {
    bold?: boolean
    italic?: boolean
    bg?: string
    textArgb?: string
    numFmt?: string
    borderTop?: boolean
    borderBottom?: boolean
  },
) {
  cell.fill = {
    type: 'pattern',
    pattern: 'solid',
    fgColor: { argb: opts.bg ?? C.white },
  }
  cell.font = {
    name: 'Calibri',
    size: 9,
    bold: opts.bold ?? false,
    italic: opts.italic ?? false,
    color: { argb: opts.textArgb ?? C.dark },
  }
  cell.alignment = {
    horizontal: colIndex === 1 ? 'left' : 'right',
    vertical: 'middle',
  }
  if (opts.numFmt) cell.numFmt = opts.numFmt
  if (opts.borderTop || opts.borderBottom) {
    cell.border = {
      ...(opts.borderTop ? { top: thinBorder } : {}),
      ...(opts.borderBottom ? { bottom: thinBorder } : {}),
    }
  }
}
