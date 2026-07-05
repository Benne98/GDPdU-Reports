import { fmtChartKpi } from '../../../lib/fmt'

export function fmtFaCell(value: number | null | undefined): string {
  if (value == null || Number.isNaN(value)) return '—'
  if (Math.abs(value) < 0.0001) return '-'
  return fmtChartKpi(value)
}
