import type { PayablesSupplierRegisterRow } from '../../../../lib/api'
import { exportFlatTablePptx } from '../../../../lib/finssentialsExport/exportFlatTablePptx'
import { exportToXlsx, todayStr, type XlsxRow } from '../../../../lib/exportXlsx'
import type { PlExportKind } from '../../../financials/pl-two-view/PlExportMenu'

export async function exportSupplierRegister(opts: {
  kind: PlExportKind
  rows: PayablesSupplierRegisterRow[]
  periodLabel: string
  filterSupplier?: string | null
}): Promise<void> {
  const { kind, rows, periodLabel, filterSupplier } = opts
  if (!rows.length) return

  const headers = [
    'Supplier',
    'Balance',
    'Overdue %',
    'Days outstanding',
    'Payment terms',
    'Open documents',
    'Cost of materials',
  ]

  const tableRows: XlsxRow[] = rows.map(r => ({
    label: r.supplier_name,
    values: [
      r.supplier_name,
      r.balance,
      r.overdue_pct,
      Math.round(r.days_outstanding),
      `${r.payment_terms_days}d`,
      r.open_documents,
      r.cost_of_materials ?? r.procurement_spend ?? 0,
    ],
    kind: 'data' as const,
  }))

  const filterNote = filterSupplier ? `Filtered to ${filterSupplier}` : 'All suppliers'
  const base = `Supplier_Register_${todayStr()}`

  if (kind === 'pptx') {
    await exportFlatTablePptx({
      fileName: `${base}.pptx`,
      pageTitle: 'Supplier register',
      tableHeading: 'Open payables by supplier',
      breadcrumbCurrent: 'Aging',
      breadcrumbParent: 'Sales',
      footerRight: `${filterNote} · ${periodLabel}`,
      headers,
      rows: tableRows,
    })
    return
  }

  await exportToXlsx({
    title: 'Supplier register',
    subtitle: `${periodLabel} · ${filterNote}`,
    headers,
    rows: tableRows,
    filename: `${base}.xlsx`,
  })
}
