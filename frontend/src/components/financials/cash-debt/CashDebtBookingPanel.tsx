import type { CashDebtPositionBookingsResponse } from '../../../lib/api'
import { fmtFaCell } from '../fixed-assets/fixedAssetsTableFormat'
import { FA_BORDER, FA_HEADER_BG, FA_NAVY, faHeaderCellStyle } from '../fixed-assets/fixedAssetsTableTheme'

type Props = {
  data: CashDebtPositionBookingsResponse | null
  loading?: boolean
}

export default function CashDebtBookingPanel({ data, loading }: Props) {
  const headerStyle = faHeaderCellStyle()

  if (loading) {
    return <p className="text-sm text-slate-500 py-4">Loading bookings…</p>
  }
  if (!data) {
    return (
      <p className="text-sm text-slate-500 py-4">
        Select an account row to view postings over time.
      </p>
    )
  }

  return (
    <div>
      <h4 className="text-sm font-semibold mb-1" style={{ color: FA_NAVY }}>{data.account_name}</h4>
      <p className="text-xs text-slate-500 mb-3">
        Postings {data.from_date} → {data.to_date} · kEUR
      </p>
      <div className="overflow-x-auto max-h-72 overflow-y-auto border border-slate-100 rounded-lg">
        <table className="w-full border-collapse text-xs">
          <thead className="sticky top-0">
            <tr style={{ borderBottom: `1px solid ${FA_BORDER}`, background: FA_HEADER_BG }}>
              <th className="px-2 py-1.5 text-left" style={headerStyle}>Date</th>
              <th className="px-2 py-1.5 text-right" style={headerStyle}>Amount</th>
              <th className="px-2 py-1.5 text-left" style={headerStyle}>Note</th>
              <th className="px-2 py-1.5 text-left" style={headerStyle}>JE</th>
            </tr>
          </thead>
          <tbody>
            {data.entries.length === 0 ? (
              <tr>
                <td colSpan={4} className="px-2 py-4 text-center text-slate-500">No postings in range</td>
              </tr>
            ) : (
              data.entries.map((e, i) => (
                <tr key={`${e.booking_line_id ?? i}-${e.posting_date}`} className="border-b border-slate-50">
                  <td className="px-2 py-1 text-slate-700">{e.posting_date}</td>
                  <td className="px-2 py-1 text-right tabular-nums">{fmtFaCell(e.amount_keur)}</td>
                  <td className="px-2 py-1 text-slate-600 max-w-[200px] truncate" title={e.line_note}>{e.line_note || '—'}</td>
                  <td className="px-2 py-1 text-slate-500">{e.journal_entry_number || '—'}</td>
                </tr>
              ))
            )}
          </tbody>
        </table>
      </div>
    </div>
  )
}
