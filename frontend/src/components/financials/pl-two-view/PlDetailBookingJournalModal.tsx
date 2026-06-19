import { useEffect, useState } from 'react'
import { X } from 'lucide-react'
import { api } from '../../../lib/api'

export type JournalEntryPayload = {
  legal_entity_code: string
  fiscal_year: number
  journal_entry_number: string
  posting_date: string
  reference?: string | null
  entry_note?: string | null
  debits: Array<{
    booking_line_id: number
    gl_account_id: string
    account_name: string
    amount_keur: number
    line_note?: string | null
  }>
  credits: Array<{
    booking_line_id: number
    gl_account_id: string
    account_name: string
    amount_keur: number
    line_note?: string | null
  }>
}

type Props = {
  bookingLineId: number | null
  onClose: () => void
}

function fmtAmount(v: number): string {
  return `€ ${Math.abs(v).toLocaleString('de-DE', { maximumFractionDigits: 2 })}k`
}

export default function PlDetailBookingJournalModal({ bookingLineId, onClose }: Props) {
  const [data, setData] = useState<JournalEntryPayload | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (bookingLineId == null) {
      setData(null)
      return
    }
    let cancelled = false
    setLoading(true)
    setError(null)
    void api
      .financialsJournalEntryByBooking(bookingLineId)
      .then(res => {
        if (!cancelled) {
          setData(res)
          setLoading(false)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setError('Could not load journal entry.')
          setLoading(false)
        }
      })
    return () => { cancelled = true }
  }, [bookingLineId])

  if (bookingLineId == null) return null

  const debitTotal = (data?.debits ?? []).reduce((s, l) => s + Math.abs(l.amount_keur), 0)
  const creditTotal = (data?.credits ?? []).reduce((s, l) => s + Math.abs(l.amount_keur), 0)

  return (
    <div
      className="fixed inset-0 z-[70] flex items-center justify-center p-4"
      style={{ background: 'rgba(15, 23, 42, 0.45)' }}
      onClick={onClose}
      role="dialog"
      aria-modal="true"
    >
      <div
        className="w-full max-w-md rounded-xl bg-white shadow-xl border border-slate-200 overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        <div className="flex items-center justify-between px-4 py-3 border-b border-slate-200">
          <div>
            <h3 className="text-sm font-semibold text-slate-900">Journal entry</h3>
            <p className="text-xs text-slate-500 mt-0.5">
              #{bookingLineId}
              {data?.journal_entry_number && ` · ${data.journal_entry_number}`}
            </p>
          </div>
          <button type="button" onClick={onClose} className="p-1.5 rounded-lg hover:bg-slate-100" aria-label="Close">
            <X size={18} />
          </button>
        </div>

        <div className="p-4 max-h-[min(70vh,420px)] overflow-y-auto">
          {loading && <p className="text-xs text-slate-500">Loading…</p>}
          {error && <p className="text-xs text-rose-600">{error}</p>}
          {data && !loading && (
            <>
              <p className="text-xs text-slate-500 mb-3">
                {data.posting_date}
                {data.reference ? ` · Ref. ${data.reference}` : ''}
              </p>
              <div className="grid grid-cols-2 gap-3">
                <div>
                  <p className="text-[0.65rem] font-semibold uppercase tracking-wide text-slate-500 mb-2">Soll (debit)</p>
                  <ul className="space-y-2">
                    {data.debits.map(line => (
                      <li key={line.booking_line_id} className="text-xs rounded-lg bg-slate-50 px-2.5 py-2 border border-slate-100">
                        <p className="font-medium text-slate-800 truncate">{line.account_name}</p>
                        <p className="text-slate-500 font-mono text-[0.65rem]">{line.gl_account_id}</p>
                        <p className="tabular-nums font-semibold text-slate-900 mt-0.5">{fmtAmount(line.amount_keur)}</p>
                        {line.line_note && <p className="text-slate-500 mt-1 line-clamp-2">{line.line_note}</p>}
                      </li>
                    ))}
                    {data.debits.length === 0 && <p className="text-xs text-slate-400">—</p>}
                  </ul>
                  <p className="text-xs font-semibold text-slate-700 mt-2 tabular-nums">Σ {fmtAmount(debitTotal)}</p>
                </div>
                <div>
                  <p className="text-[0.65rem] font-semibold uppercase tracking-wide text-slate-500 mb-2">Haben (credit)</p>
                  <ul className="space-y-2">
                    {data.credits.map(line => (
                      <li key={line.booking_line_id} className="text-xs rounded-lg bg-slate-50 px-2.5 py-2 border border-slate-100">
                        <p className="font-medium text-slate-800 truncate">{line.account_name}</p>
                        <p className="text-slate-500 font-mono text-[0.65rem]">{line.gl_account_id}</p>
                        <p className="tabular-nums font-semibold text-slate-900 mt-0.5">{fmtAmount(line.amount_keur)}</p>
                        {line.line_note && <p className="text-slate-500 mt-1 line-clamp-2">{line.line_note}</p>}
                      </li>
                    ))}
                    {data.credits.length === 0 && <p className="text-xs text-slate-400">—</p>}
                  </ul>
                  <p className="text-xs font-semibold text-slate-700 mt-2 tabular-nums">Σ {fmtAmount(creditTotal)}</p>
                </div>
              </div>
              {data.entry_note && (
                <p className="text-xs text-slate-600 mt-3 pt-3 border-t border-slate-100">{data.entry_note}</p>
              )}
            </>
          )}
        </div>
      </div>
    </div>
  )
}
