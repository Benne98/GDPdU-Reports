/**
 * BookingsDrill — shared component for Items 6 & 7.
 *
 * Fetches bookings for a given account_number_group and renders:
 *   (a) a scatter plot: x = posting index (or date string), y = amount_keur;
 *       tooltip shows account label, counter-account name, note; large_booking flagged amber.
 *   (b) a flat-file table: date, journal #, counter-account, amount, note.
 */
import { useState, useEffect } from 'react'
import {
  ScatterChart, Scatter, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceLine,
} from 'recharts'
import { api, type AnomalyBookingRow } from '../../lib/api'

interface Props {
  accountNumberGroup: string
  accountLabel?: string
}

interface ScatterPoint {
  x: number
  y: number
  label: string
  counterAccount: string
  note: string
  large: boolean
  date: string
}

// Custom dot: amber for large_booking, blue otherwise
function ScatterDot(props: {
  cx?: number
  cy?: number
  payload?: ScatterPoint
}) {
  const { cx = 0, cy = 0, payload } = props
  const fill = payload?.large ? '#D97706' : '#1E3A5F'
  return <circle cx={cx} cy={cy} r={4} fill={fill} fillOpacity={0.8} stroke="white" strokeWidth={1} />
}

// Custom tooltip for the scatter chart
function BookingTooltip({ active, payload }: { active?: boolean; payload?: Array<{ payload: ScatterPoint }> }) {
  if (!active || !payload?.length) return null
  const p = payload[0].payload
  return (
    <div
      className="rounded-lg px-3 py-2 text-[11px] leading-snug shadow-lg"
      style={{ background: '#1E293B', color: '#F1F5F9', maxWidth: 260, pointerEvents: 'none' }}
    >
      <p className="font-semibold mb-0.5">{p.date}</p>
      {p.label && <p style={{ color: '#94A3B8' }}>{p.label}</p>}
      {p.counterAccount && (
        <p>Counter: <span className="font-medium">{p.counterAccount}</span></p>
      )}
      <p>Amount: <span className={p.y < 0 ? 'text-red-400' : 'text-green-400'}>{p.y.toFixed(1)} kEUR</span></p>
      {p.note && <p style={{ color: '#94A3B8' }} className="mt-0.5 truncate max-w-[220px]">{p.note}</p>}
      {p.large && <p className="mt-0.5 font-semibold" style={{ color: '#F59E0B' }}>Large booking</p>}
    </div>
  )
}

export default function BookingsDrill({ accountNumberGroup, accountLabel }: Props) {
  const [bookings, setBookings] = useState<AnomalyBookingRow[] | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    api.financialsAnomalyBookings(accountNumberGroup)
      .then(d => { if (!cancelled) { setBookings(d.bookings); setLoading(false) } })
      .catch((e: unknown) => {
        if (!cancelled) { setError(e instanceof Error ? e.message : String(e)); setLoading(false) }
      })
    return () => { cancelled = true }
  }, [accountNumberGroup])

  if (loading) return (
    <div className="rounded-xl bg-white p-4 mt-3" style={{ border: '1px solid #E2E8F0' }}>
      <p className="text-sm" style={{ color: '#94A3B8' }}>Loading bookings...</p>
    </div>
  )
  if (error) return (
    <div className="rounded-xl px-4 py-3 mt-3 text-sm" style={{ background: 'rgba(239,68,68,0.08)', color: '#991B1B', border: '1px solid rgba(220,38,38,0.3)' }}>{error}</div>
  )
  if (!bookings || bookings.length === 0) return (
    <div className="rounded-xl bg-white px-4 py-3 mt-3 text-sm" style={{ border: '1px solid #E2E8F0', color: '#94A3B8' }}>No bookings found.</div>
  )

  // Build scatter data: x = index, y = amount_keur
  const scatterData: ScatterPoint[] = bookings.map((b, i) => ({
    x: i,
    y: b.amount_keur,
    label: accountLabel ?? '',
    counterAccount: b.counter_account_name ?? b.counter_account_id ?? '—',
    note: b.line_note ?? '',
    large: b.large_booking,
    date: b.posting_date ?? `#${i + 1}`,
  }))

  return (
    <div className="mt-3 space-y-4">
      {/* Scatter chart — only meaningful with enough points; hidden for 1-5 bookings */}
      {bookings.length > 5 && (
        <div>
          <p className="text-xs font-semibold mb-2" style={{ color: '#475569' }}>
            Bookings scatter ({bookings.length} entries) — amber = large booking
          </p>
          <ResponsiveContainer width="100%" height={200}>
            <ScatterChart margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
              <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" />
              <XAxis
                dataKey="x"
                type="number"
                name="Index"
                tick={{ fontSize: 10, fill: '#94A3B8' }}
                tickFormatter={v => String(v + 1)}
                label={{ value: 'Booking #', position: 'insideBottom', offset: -2, fontSize: 9, fill: '#94A3B8' }}
              />
              <YAxis
                dataKey="y"
                type="number"
                name="Amount"
                tick={{ fontSize: 10, fill: '#94A3B8' }}
                unit=" k"
                width={52}
              />
              <ReferenceLine y={0} stroke="#CBD5E1" strokeWidth={1} />
              <Tooltip content={<BookingTooltip />} />
              <Scatter
                data={scatterData}
                shape={<ScatterDot />}
              />
            </ScatterChart>
          </ResponsiveContainer>
        </div>
      )}

      {/* Flat-file table */}
      <div className="overflow-x-auto rounded-xl" style={{ border: '1px solid #E2E8F0' }}>
        <table className="w-full text-xs">
          <thead>
            <tr style={{ borderBottom: '1px solid #E2E8F0', background: '#F8FAFC' }}>
              <th className="px-3 py-2 text-left font-semibold" style={{ color: '#64748B' }}>Date</th>
              <th className="px-3 py-2 text-left font-semibold" style={{ color: '#64748B' }}>Journal #</th>
              <th className="px-3 py-2 text-left font-semibold" style={{ color: '#64748B' }}>Counter account</th>
              <th className="px-3 py-2 text-right font-semibold" style={{ color: '#64748B' }}>Amount (kEUR)</th>
              <th className="px-3 py-2 text-left font-semibold" style={{ color: '#64748B' }}>Note</th>
            </tr>
          </thead>
          <tbody>
            {bookings.map(b => (
              <tr
                key={b.booking_line_id}
                style={{
                  borderBottom: '1px solid #F1F5F9',
                  background: b.large_booking ? 'rgba(217,119,6,0.05)' : undefined,
                }}
              >
                <td className="px-3 py-1.5 whitespace-nowrap" style={{ color: '#374151' }}>
                  {b.posting_date ?? '—'}
                </td>
                <td className="px-3 py-1.5 whitespace-nowrap" style={{ color: '#374151' }}>
                  {b.journal_entry_number}
                </td>
                <td className="px-3 py-1.5" style={{ color: '#374151' }}>
                  {b.counter_account_name ?? b.counter_account_id ?? '—'}
                </td>
                <td
                  className="px-3 py-1.5 text-right font-mono"
                  style={{ color: b.amount_keur < 0 ? '#DC2626' : '#374151' }}
                >
                  {b.amount_keur.toFixed(1)}
                </td>
                <td className="px-3 py-1.5 max-w-xs truncate" style={{ color: '#64748B' }}>
                  {b.line_note || '—'}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
