import type { FinancialStatementResponse } from '../../../lib/api'

export type PlExportContext = {
  entityLabel: string
  entityDisplayName: string
}

export function generatedExportDate(): string {
  return new Date().toLocaleDateString('de-DE', { day: '2-digit', month: 'short', year: 'numeric' })
}

export function buildExportFooterLine(data: FinancialStatementResponse, ctx: PlExportContext): string {
  const period = data.col_labels?.cm ?? `${String(data.month).padStart(2, '0')}/${data.year}`
  return `Source: ERP general ledger · Period ${period} · Finssentials Financials · Group: ${ctx.entityDisplayName} · ${generatedExportDate()}`
}

export function exportPdfFilename(
  entityLabel: string,
  data: FinancialStatementResponse,
  suffix: string,
  prefix = 'PL',
): string {
  const date = new Date().toISOString().slice(0, 10)
  return `${prefix}_${suffix}_${entityLabel}_${data.year}-${String(data.month).padStart(2, '0')}_${date}.pdf`
}
