import type { GlLine } from '../../lib/api'
import { exportToXlsx } from '../../lib/exportXlsx'
import { fmtAmountWhole } from '../../lib/fmt'

function fmtDate(iso: string): string {
  const [y, m, d] = iso.split('-')
  if (!y || !m || !d) return iso
  return `${d}.${m}.${y}`
}

export async function exportAccountStatementRows(
  rows: GlLine[],
  opts: {
    entityLabel: string
    dateFrom: string
    dateTo: string
    entityDisplayName?: (code: string) => string
  },
): Promise<void> {
  const headers = [
    'Entity',
    'Booking number',
    'Amount (EUR)',
    'Account number',
    'Account',
    'Posting date',
    'Booking text',
    'PL/BS item',
    'Statement',
  ]

  const entityLabel = opts.entityDisplayName ?? ((code: string) => code)

  const xlsxRows = rows.map(r => ({
    label: r.journal_entry_number,
    kind: 'data' as const,
    values: [
      entityLabel(r.legal_entity_code),
      r.journal_entry_number,
      r.amount_signed,
      r.gl_account_id,
      r.account_name,
      fmtDate(r.posting_date),
      r.booking_text ?? '',
      [r.level_2, r.level_3].filter(Boolean).join(' / '),
      r.statement_type ?? '',
    ],
  }))

  const stamp = new Date().toISOString().slice(0, 10)
  await exportToXlsx({
    title: 'Export — bookings',
    subtitle: `${opts.entityLabel} · ${fmtDate(opts.dateFrom)} – ${fmtDate(opts.dateTo)} · ${rows.length} lines`,
    headers,
    rows: xlsxRows,
    filename: `Account_Statement_${stamp}.xlsx`,
  })
}

/** Table display — kEUR with parentheses for negatives */
export function displayBookingAmount(amountSigned: number): string {
  return fmtAmountWhole(amountSigned)
}
