import { useState } from 'react'
import type { PlLineDetailResponse } from '../../../lib/api'
import PlDetailBookingJournalModal from './PlDetailBookingJournalModal'

type Props = {
  bookings: PlLineDetailResponse['top_bookings']
}

export default function PlDetailBookingsTable({ bookings }: Props) {
  const [selectedBookingId, setSelectedBookingId] = useState<number | null>(null)

  if (!bookings.length) {
    return <p className="text-xs text-slate-500">No postings in the current month.</p>
  }

  return (
    <>
      <div className="overflow-auto rounded-xl border border-slate-200/80 max-h-[280px] bg-white">
        <table className="w-full text-[10px]">
          <thead className="sticky top-0 bg-slate-50/95 backdrop-blur-sm z-[1]">
            <tr className="border-b border-slate-200">
              <th className="px-2 py-1.5 text-left font-semibold text-slate-600">ID</th>
              <th className="px-2 py-1.5 text-left font-semibold text-slate-600">Date</th>
              <th className="px-2 py-1.5 text-left font-semibold text-slate-600">Account</th>
              <th className="px-2 py-1.5 text-right font-semibold text-slate-600">€ k</th>
              <th className="px-2 py-1.5 text-left font-semibold text-slate-600">Booking text</th>
            </tr>
          </thead>
          <tbody>
            {bookings.map(b => (
              <tr key={b.booking_line_id} className="border-b border-slate-100 last:border-0 hover:bg-slate-50/60">
                <td className="px-2 py-1.5 tabular-nums">
                  <button
                    type="button"
                    onClick={() => setSelectedBookingId(b.booking_line_id)}
                    className="text-[#1E3A5F] font-medium hover:underline"
                  >
                    {b.booking_line_id}
                  </button>
                </td>
                <td className="px-2 py-1.5 whitespace-nowrap text-slate-600">{b.posting_date}</td>
                <td className="px-2 py-1.5 max-w-[110px] truncate text-slate-700" title={b.account_name ?? b.gl_account_id}>
                  {b.account_name ?? b.gl_account_id}
                </td>
                <td className="px-2 py-1.5 text-right tabular-nums font-medium text-slate-800">
                  {b.amount.toLocaleString('de-DE', { maximumFractionDigits: 0 })}
                </td>
                <td className="px-2 py-1.5 max-w-[130px] truncate text-slate-500" title={b.line_note}>
                  {b.line_note || '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <PlDetailBookingJournalModal
        bookingLineId={selectedBookingId}
        onClose={() => setSelectedBookingId(null)}
      />
    </>
  )
}
