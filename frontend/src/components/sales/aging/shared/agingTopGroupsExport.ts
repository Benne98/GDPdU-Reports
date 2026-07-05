/**
 * Export helper for AgingTopGroupsTable (clustered by top customer/supplier groups).
 * Mirrors the signature pattern of customerRegisterExport.ts / supplierRegisterExport.ts.
 * Values in the table are stored as kEUR; the export emits raw kEUR numbers so the
 * spreadsheet consumer can format as needed.
 */
import type { ReceivablesDimensionRowBuckets } from '../../../../lib/api'
import { exportFlatTablePptx } from '../../../../lib/finssentialsExport/exportFlatTablePptx'
import { exportToXlsx, todayStr, type XlsxRow } from '../../../../lib/exportXlsx'
import type { PlExportKind } from '../../../financials/pl-two-view/PlExportMenu'

const BUCKET_COLS: { key: keyof ReceivablesDimensionRowBuckets; label: string }[] = [
  { key: 'not_yet_due', label: 'Not due' },
  { key: 'overdue_1_30', label: '1–30 days' },
  { key: 'overdue_31_60', label: '31–60 days' },
  { key: 'overdue_61_90', label: '61–90 days' },
  { key: 'overdue_91_180', label: '91–180 days' },
  { key: 'overdue_over_180', label: '>180 days' },
]

type BandSection = {
  bandLabel: string
  subtotal: ReceivablesDimensionRowBuckets
  items: ReceivablesDimensionRowBuckets[]
}

export async function exportAgingTopGroups(opts: {
  kind: PlExportKind
  side: 'receivables' | 'payables'
  sections: BandSection[]
  grand: ReceivablesDimensionRowBuckets
  periodLabel: string
}): Promise<void> {
  const { kind, side, sections, grand, periodLabel } = opts
  if (!sections.length) return

  const prefix = side === 'receivables' ? 'Receivables' : 'Payables'
  const partnerCol = side === 'receivables' ? 'Customer' : 'Supplier'
  const headers = [partnerCol, ...BUCKET_COLS.map(c => c.label), 'Total (kEUR)']
  const base = `${prefix}_Aging_Groups_${todayStr()}`
  const title = `${prefix} aging — clustered by top ${partnerCol.toLowerCase()} groups`

  const xlsxRows: XlsxRow[] = []
  for (const section of sections) {
    xlsxRows.push({
      label: section.bandLabel,
      values: [
        section.bandLabel,
        ...BUCKET_COLS.map(c => section.subtotal[c.key] as number),
        section.subtotal.total,
      ],
      kind: 'section',
    })
    for (const item of section.items) {
      xlsxRows.push({
        label: item.label,
        values: [
          item.label,
          ...BUCKET_COLS.map(c => item[c.key] as number),
          item.total,
        ],
        kind: 'data',
        indent: 1,
      })
    }
  }
  xlsxRows.push({
    label: prefix,
    values: [
      prefix,
      ...BUCKET_COLS.map(c => grand[c.key] as number),
      grand.total,
    ],
    kind: 'title',
  })

  if (kind === 'pptx') {
    await exportFlatTablePptx({
      fileName: `${base}.pptx`,
      pageTitle: title,
      tableHeading: `${prefix} aging buckets`,
      breadcrumbCurrent: 'Aging',
      breadcrumbParent: 'Sales',
      footerRight: periodLabel,
      headers,
      rows: xlsxRows,
    })
    return
  }

  await exportToXlsx({
    title,
    subtitle: `${periodLabel} · amounts in kEUR`,
    headers,
    rows: xlsxRows,
    filename: `${base}.xlsx`,
  })
}
