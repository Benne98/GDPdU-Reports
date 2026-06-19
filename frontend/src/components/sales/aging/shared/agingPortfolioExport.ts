import type { ReceivablesAgingBand } from '../../../../lib/api'
import { exportFlatTablePptx } from '../../../../lib/finssentialsExport/exportFlatTablePptx'
import { exportToXlsx, todayStr, type XlsxRow } from '../../../../lib/exportXlsx'
import type { PlExportKind } from '../../../financials/pl-two-view/PlExportMenu'
import type { AgingPortfolioNarrative } from './agingPortfolioNarrative'
import type { AgingPortfolioSide } from './agingPortfolioDimensions'

function bucketRows(series: ReceivablesAgingBand[], total: number): XlsxRow[] {
  const rows = [...series].filter(s => s.amount > 0).sort((a, b) => b.amount - a.amount)
  return rows.map((row, i) => ({
    label: row.label,
    values: [
      i + 1,
      row.label,
      row.amount,
      total > 0 ? +((row.amount / total) * 100).toFixed(1) : 0,
    ],
    kind: 'data' as const,
  }))
}

export async function exportAgingPortfolioReport(opts: {
  kind: PlExportKind
  title: string
  periodLabel: string
  total: number
  series: ReceivablesAgingBand[]
  narrative: AgingPortfolioNarrative
  side: AgingPortfolioSide
}): Promise<void> {
  const { kind, title, periodLabel, total, series, narrative, side } = opts
  const prefix = side === 'receivables' ? 'Receivables' : 'Payables'
  const headers = ['Rank', 'Bucket', 'Amount', 'Share %']
  const tableRows = bucketRows(series, total)
  const base = `${prefix}_Portfolio_${todayStr()}`

  if (kind === 'pptx') {
    await exportFlatTablePptx({
      fileName: `${base}.pptx`,
      pageTitle: title,
      tableHeading: 'Aging buckets',
      breadcrumbCurrent: 'Aging',
      breadcrumbParent: 'Sales',
      footerRight: `${narrative.headline} · ${periodLabel}`,
      headers,
      rows: tableRows,
    })
    return
  }

  const xlsxRows: XlsxRow[] = [
    { label: 'Key metrics', values: ['Key metrics', '', '', ''], kind: 'section' },
    ...narrative.metrics.map(m => ({
      label: m.label,
      values: [m.label, m.value, m.hint ?? '', ''],
      kind: 'data' as const,
    })),
    { label: 'Aging buckets', values: ['Aging buckets', '', '', ''], kind: 'section' },
    ...tableRows,
    { label: 'Commentary', values: ['Commentary', '', '', ''], kind: 'section' },
    { label: narrative.headline, values: [narrative.headline, '', '', ''], kind: 'title' },
    { label: narrative.summary, values: [narrative.summary, '', '', ''], kind: 'data' },
    ...narrative.insights.flatMap(insight => [
      { label: insight.title, values: [insight.title, '', '', ''], kind: 'section' as const },
      { label: insight.body, values: [insight.body, '', '', ''], kind: 'data' as const },
    ]),
  ]

  await exportToXlsx({
    title,
    subtitle: `${periodLabel} · ${narrative.eyebrow}`,
    headers: ['Item', 'Value', 'Detail', 'Share %'],
    rows: xlsxRows,
    filename: `${base}.xlsx`,
  })
}
