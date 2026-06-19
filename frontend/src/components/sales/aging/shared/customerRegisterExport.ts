import type { ReceivablesCustomerRegisterRow } from '../../../../lib/api'
import { exportFlatTablePptx } from '../../../../lib/finssentialsExport/exportFlatTablePptx'
import { exportToXlsx, todayStr, type XlsxRow } from '../../../../lib/exportXlsx'
import type { PlExportKind } from '../../../financials/pl-two-view/PlExportMenu'

export async function exportCustomerRegister(opts: {
  kind: PlExportKind
  rows: ReceivablesCustomerRegisterRow[]
  periodLabel: string
  filterCustomer?: string | null
}): Promise<void> {
  const { kind, rows, periodLabel, filterCustomer } = opts
  if (!rows.length) return

  const headers = [
    'Customer',
    'Balance',
    'Overdue %',
    'Days outstanding',
    'Payment terms',
    'Open documents',
    'Gross sales',
    'Contact',
  ]

  const tableRows: XlsxRow[] = rows.map(r => ({
    label: r.customer_name,
    values: [
      r.customer_name,
      r.balance,
      r.overdue_pct,
      Math.round(r.days_outstanding),
      `${r.payment_terms_days}d`,
      r.open_documents,
      r.gross_sales ?? 0,
      r.contact_name ?? '',
    ],
    kind: 'data' as const,
  }))

  const filterNote = filterCustomer ? `Filtered to ${filterCustomer}` : 'All customers'
  const base = `Customer_Register_${todayStr()}`

  if (kind === 'pptx') {
    await exportFlatTablePptx({
      fileName: `${base}.pptx`,
      pageTitle: 'Customer register',
      tableHeading: 'Open receivables by customer',
      breadcrumbCurrent: 'Aging',
      breadcrumbParent: 'Sales',
      footerRight: `${filterNote} · ${periodLabel}`,
      headers,
      rows: tableRows,
    })
    return
  }

  await exportToXlsx({
    title: 'Customer register',
    subtitle: `${periodLabel} · ${filterNote}`,
    headers,
    rows: tableRows,
    filename: `${base}.xlsx`,
  })
}
