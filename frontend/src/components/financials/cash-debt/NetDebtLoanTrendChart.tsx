import { useEffect } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { Pin } from 'lucide-react'
import type { CashDebtPositionBookingsResponse } from '../../../lib/api'
import { fmtChartKpi } from '../../../lib/fmt'
import { FA_NAVY } from '../fixed-assets/fixedAssetsTableTheme'
import { aggregateBookingsMonthly, topMonthShare } from './netDebtBookingSeries'
import { useOptionalActionNotesContext } from '../../action-notes/ActionNotesContext'
import { captureChartByTarget } from '../../../lib/actionNotes/chartCapture'
import { STATEMENT_TOOLBAR_ICON_BTN, STATEMENT_TOOLBAR_BTN_STYLE } from '../statement-two-view/statementToolbarButton'

const CHART_ID = 'net-debt-loan-trend-chart'

type Props = {
  data: CashDebtPositionBookingsResponse | null
  loading?: boolean
  loanLabel?: string
  /** Omit outer title when embedded in NetDebtReport panel (has its own header). */
  compact?: boolean
}

export default function NetDebtLoanTrendChart({ data, loading, loanLabel, compact }: Props) {
  // Hooks must be unconditional — before any early returns
  const notesCtx = useOptionalActionNotesContext()

  useEffect(() => {
    if (!notesCtx || !data) return
    notesCtx.registerChartCandidate({
      id: CHART_ID,
      label: `Net Debt — ${loanLabel ?? data.account_name}`,
      description: 'Monthly loan payment seasonality',
      capture: () => captureChartByTarget(CHART_ID),
    })
    return () => notesCtx.unregisterChartCandidate(CHART_ID)
  }, [notesCtx, data, loanLabel])

  if (loading) {
    return <p className="text-sm text-slate-500 py-2">Loading payment history…</p>
  }
  if (!data) {
    return (
      <p className="text-sm text-slate-500 py-2">
        Click a loan row to view monthly payment seasonality.
      </p>
    )
  }

  const points = aggregateBookingsMonthly(data.entries)
  const share = topMonthShare(points)
  const title = loanLabel ?? data.account_name

  if (points.length === 0) {
    return (
      <div id={CHART_ID} data-expert-chart-target={CHART_ID}>
        {!compact && <h4 className="text-sm font-semibold mb-1" style={{ color: FA_NAVY }}>{title}</h4>}
        <p className="text-xs text-slate-500">No postings in {data.from_date} → {data.to_date}.</p>
      </div>
    )
  }

  const seasonalityHint =
    share != null && share >= 0.45
      ? `Payments concentrate in peak months (${Math.round(share * 100)}% in the busiest month).`
      : share != null && share >= 0.25
        ? 'Moderate monthly clustering — several months drive most of the volume.'
        : 'Payments are spread relatively evenly across months.'

  const handlePin = async () => {
    if (!notesCtx) return
    const snap = await notesCtx.pinChartById(CHART_ID)
    if (snap) notesCtx.setToast('Open Action Notes to save — or use Pin chart in panel')
    else notesCtx.setToast('No chart data to pin')
  }

  return (
    <div id={CHART_ID} data-expert-chart-target={CHART_ID}>
      {!compact && (
        <>
          <div className="flex items-center justify-between mb-0.5">
            <h4 className="text-sm font-semibold" style={{ color: FA_NAVY }}>{title}</h4>
            {notesCtx && (
              <button
                type="button"
                title="Pin chart to Action Notes"
                className={STATEMENT_TOOLBAR_ICON_BTN}
                style={STATEMENT_TOOLBAR_BTN_STYLE}
                onClick={handlePin}
              >
                <Pin size={14} strokeWidth={1.75} />
              </button>
            )}
          </div>
          <p className="text-xs text-slate-500 mb-3">
            Monthly payment volume · kEUR · {data.from_date} → {data.to_date}
          </p>
        </>
      )}
      {compact && (
        <div className="flex items-center justify-between mb-2">
          <p className="text-xs text-slate-500">kEUR · {data.from_date} → {data.to_date}</p>
          {notesCtx && (
            <button
              type="button"
              title="Pin chart to Action Notes"
              className={STATEMENT_TOOLBAR_ICON_BTN}
              style={STATEMENT_TOOLBAR_BTN_STYLE}
              onClick={handlePin}
            >
              <Pin size={14} strokeWidth={1.75} />
            </button>
          )}
        </div>
      )}
      <div className="h-52 w-full">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={points} margin={{ top: 8, right: 8, left: 0, bottom: 4 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" vertical={false} />
            <XAxis
              dataKey="label"
              tick={{ fontSize: 10, fill: '#64748B' }}
              interval="preserveStartEnd"
              minTickGap={24}
            />
            <YAxis
              tick={{ fontSize: 10, fill: '#64748B' }}
              width={44}
              tickFormatter={(v: number) => fmtChartKpi(v)}
            />
            <Tooltip
              formatter={(v: number) => [`${fmtChartKpi(v)} kEUR`, 'Payments']}
              labelFormatter={(l: string) => l}
              contentStyle={{ fontSize: 12, borderRadius: 8, border: '1px solid #E2E8F0' }}
            />
            <Bar dataKey="amount" fill={FA_NAVY} radius={[3, 3, 0, 0]} maxBarSize={28} />
          </BarChart>
        </ResponsiveContainer>
      </div>
      <p className="text-[12px] text-slate-500 mt-2 leading-snug">{seasonalityHint}</p>
    </div>
  )
}
