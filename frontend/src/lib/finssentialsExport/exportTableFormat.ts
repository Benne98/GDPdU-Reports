import {
  C,
  dataBgForCell,
  exportValueTextArgb,
  isSubtotalRowKind,
  labelTextArgb,
} from './designStyles'
import type { ExportFlatRow, ExportRowKind } from './flattenTreeForExport'

export function formatExportCellValue(
  val: number | string | null | undefined,
  rowKind: ExportRowKind,
  colIdx: number,
  kpiCols?: number[],
): string {
  if (val === null || val === undefined || val === '') return ''
  if (typeof val === 'string') return val
  const isKpiVal = rowKind === 'kpi' && (kpiCols?.includes(colIdx) ?? false)
  if (isKpiVal) return val.toFixed(1)
  return new Intl.NumberFormat('de-DE', { maximumFractionDigits: 0 }).format(val)
}

export function argbToCss(argb: string): string {
  const hex = argb.replace(/^FF/i, '')
  return `#${hex}`
}

export function exportValueCssColor(
  val: number,
  colKind: string | undefined,
  rowKind: ExportRowKind,
  defaultColor: string,
): string {
  if (rowKind === 'kpi') return defaultColor
  const argb = exportValueTextArgb(val, colKind, C.dark)
  if (argb === C.green) return argbToCss(C.green)
  if (argb === C.red) return argbToCss(C.red)
  return defaultColor
}

export function exportCellCssBg(
  colIndex: number,
  colKind: string | undefined,
  rowKind: ExportRowKind,
): string {
  return argbToCss(dataBgForCell(colIndex, colKind, rowKind))
}

export function exportLabelCssColor(rowKind: ExportRowKind): string {
  return argbToCss(labelTextArgb(rowKind))
}

export function exportRowBorders(
  row: ExportFlatRow,
  next: ExportFlatRow | undefined,
): { borderTop?: string; borderBottom?: string } {
  if (!isSubtotalRowKind(row.kind)) return {}
  const border = `1px solid ${argbToCss(C.border)}`
  const borderBottom =
    next == null ||
    next.kind === 'blank' ||
    next.kind === 'kpi' ||
    next.kind === 'section' ||
    next.kind === 'title'
  return {
    borderTop: border,
    ...(borderBottom ? { borderBottom: border } : {}),
  }
}
