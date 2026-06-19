import type { PlLineDetailResponse } from '../../../lib/api'
import DetailAnalysisLoader from '../../ui/DetailAnalysisLoader'

type OutlierFacts = {
  driver_accounts?: Array<{
    gl_account_id: string
    account_name: string
    delta_keur: number
    balance_cm_keur: number
    share_of_line_delta_pct?: number
  }>
  spike_accounts?: Array<{
    account_name: string
    cm_keur: number
    peak_month_label?: string
  }>
  top_bookings?: Array<{
    booking_line_id: number
    posting_date: string
    account_name?: string
    amount_keur: number
    counter_account?: string
    line_note?: string
  }>
}

type Props = {
  detail: PlLineDetailResponse | null
  loading: boolean
  currentPeriodLabel?: string
  priorPeriodLabel?: string
}

function fmtKeur(v: number): string {
  const sign = v >= 0 ? '+' : ''
  return `${sign}€ ${Math.abs(v).toLocaleString('de-DE', { maximumFractionDigits: 0 })}k`
}

export default function PlDetailOutlierPanel({
  detail,
  loading,
  currentPeriodLabel = 'this month',
  priorPeriodLabel = 'last month',
}: Props) {
  if (loading) {
    return (
      <DetailAnalysisLoader
        compact
        message="Analysing drivers and postings…"
      />
    )
  }

  const facts = (detail?.outlier_facts ?? {}) as OutlierFacts
  const drivers = facts.driver_accounts ?? []
  const spikes = facts.spike_accounts ?? []
  const bookings = facts.top_bookings ?? []
  const postingsNote = detail?.commentary?.postings?.trim()

  const hasDrivers = drivers.length > 0
  const hasBookings = bookings.length > 0
  const hasSpike = spikes.length > 0

  if (!hasDrivers && !hasBookings && !hasSpike && !postingsNote) {
    return (
      <p className="text-xs leading-relaxed text-slate-600">
        No unusual accounts or individual postings stood out in {currentPeriodLabel}.
      </p>
    )
  }

  return (
    <div className="space-y-3 text-xs text-slate-700">
      {hasDrivers && (
        <div>
          <p className="text-[0.65rem] font-semibold uppercase tracking-wide text-slate-500 mb-1.5">
            Main drivers vs {priorPeriodLabel}
          </p>
          <ul className="space-y-1.5">
            {drivers.slice(0, 4).map(d => (
              <li key={d.gl_account_id} className="leading-snug">
                <span className="font-medium text-slate-800">{d.account_name}</span>
                <span className="text-slate-600">
                  {' '}
                  — {fmtKeur(d.delta_keur)}
                  {d.share_of_line_delta_pct != null && (
                    <span className="text-slate-400"> ({d.share_of_line_delta_pct}% of line change)</span>
                  )}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {hasSpike && (
        <p className="text-amber-900/90 bg-amber-50/80 border border-amber-100 rounded-lg px-2.5 py-2 leading-relaxed">
          <span className="font-medium">Unusual month: </span>
          {spikes[0].account_name} was unusually high in {spikes[0].peak_month_label ?? currentPeriodLabel}.
        </p>
      )}

      {hasBookings && (
        <div>
          <p className="text-[0.65rem] font-semibold uppercase tracking-wide text-slate-500 mb-1.5">
            Notable postings
          </p>
          <ul className="space-y-1.5">
            {bookings.slice(0, 3).map(b => (
              <li key={b.booking_line_id} className="leading-snug">
                <span className="font-medium tabular-nums text-slate-800">{fmtKeur(b.amount_keur)}</span>
                <span className="text-slate-600">
                  {' '}
                  · {b.account_name ?? '—'}
                  {b.counter_account ? ` → ${b.counter_account}` : ''}
                </span>
              </li>
            ))}
          </ul>
        </div>
      )}

      {postingsNote && (
        <p className="text-slate-600 leading-relaxed border-t border-slate-100 pt-2">{postingsNote}</p>
      )}
    </div>
  )
}
