/** AR/AP aging sub-tab (Balance sheet) — GL-derived from fact_ar / fact_ap. */
import { useEffect, useState } from 'react'
import {
  BarChart,
  Bar,
  XAxis,
  YAxis,
  Tooltip,
  ResponsiveContainer,
  Cell,
} from 'recharts'
import {
  api,
  type ReceivablesAgingResponse,
  type PayablesAgingResponse,
} from '../../../lib/api'
import type { PeriodSelection } from '../../../lib/periodSelection'
import { periodAnchorYearMonth } from '../../../lib/periodSelection'
import { useChartLoadReporter } from '../../../hooks/useChartLoadReporter'

type Props = {
  period: PeriodSelection
  entity?: string
}

const AR_COLORS = ['#1E3A5F', '#2C5F8A', '#4A6FA5', '#6B8FC7', '#94A3B8', '#CBD5E1']
const AP_COLORS = ['#7C2D12', '#B45309', '#D97706', '#F59E0B', '#FBBF24', '#FDE68A']

function fmtKeur(v: number): string {
  return `€${Math.round(v)}k`
}

function AgingPanel({
  title,
  data,
  colors,
}: {
  title: string
  data: ReceivablesAgingResponse | PayablesAgingResponse | null
  colors: string[]
}) {
  if (!data) return null
  const chartData = data.series.map((s) => ({ name: s.label, amount: s.amount, band: s.band }))
  const total = data.series.reduce((a, s) => a + s.amount, 0)
  const kpis = data.kpis as { before_due?: number; overdue?: number; overdue_pct?: number }

  return (
    <div
      className="rounded-xl overflow-hidden"
      style={{ background: '#FFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.05)' }}
    >
      <div className="px-4 py-3 border-b border-slate-100 flex flex-wrap items-end justify-between gap-3">
        <div>
          <h3 className="text-sm font-semibold text-slate-900">{title}</h3>
          <p className="text-xs text-slate-500 mt-0.5">As of {data.as_of} · kEUR</p>
        </div>
        <div className="flex gap-4 text-xs">
          <span className="text-slate-600">
            Open <strong className="text-slate-900">{fmtKeur(total)}</strong>
          </span>
          <span className="text-slate-600">
            Overdue <strong className="text-red-700">{fmtKeur(kpis.overdue ?? 0)}</strong>
            {kpis.overdue_pct != null ? ` (${kpis.overdue_pct}%)` : ''}
          </span>
        </div>
      </div>
      <div className="p-4 h-64">
        <ResponsiveContainer width="100%" height="100%">
          <BarChart data={chartData} margin={{ top: 8, right: 8, left: 0, bottom: 48 }}>
            <XAxis dataKey="name" tick={{ fontSize: 10 }} angle={-28} textAnchor="end" height={56} interval={0} />
            <YAxis tick={{ fontSize: 10 }} tickFormatter={(v) => `${v}k`} />
            <Tooltip formatter={(v: number) => [fmtKeur(v), 'Amount']} />
            <Bar dataKey="amount" radius={[4, 4, 0, 0]}>
              {chartData.map((_, i) => (
                <Cell key={i} fill={colors[i % colors.length]} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      </div>
    </div>
  )
}

export default function GlAgingTab({ period, entity }: Props) {
  const anchor = periodAnchorYearMonth(period)
  const [ar, setAr] = useState<ReceivablesAgingResponse | null>(null)
  const [ap, setAp] = useState<PayablesAgingResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    const ent = entity === 'all' ? undefined : entity
    Promise.all([
      api.salesReceivablesAging(anchor.year, anchor.month, ent),
      api.salesPayablesAging(anchor.year, anchor.month, ent),
    ])
      .then(([arRes, apRes]) => {
        if (cancelled) return
        setAr(arRes)
        setAp(apRes)
      })
      .catch((e: unknown) => {
        if (!cancelled) setError(e instanceof Error ? e.message : String(e))
      })
      .finally(() => {
        if (!cancelled) setLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [anchor.year, anchor.month, entity, period])

  useChartLoadReporter('gl-aging', loading, error)

  if (loading) {
    return <p className="mt-4 text-center text-sm text-slate-400 animate-pulse py-16">Loading AR/AP aging…</p>
  }

  if (error) {
    return (
      <div className="mt-4 rounded-xl border border-red-200 bg-red-50 p-6 text-sm text-red-800" role="alert">
        {error}
      </div>
    )
  }

  return (
    <div className="space-y-6 mt-2">
      <AgingPanel title="Trade receivables aging" data={ar} colors={AR_COLORS} />
      <AgingPanel title="Trade payables aging" data={ap} colors={AP_COLORS} />
    </div>
  )
}
