import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { Bar, BarChart, CartesianGrid, Cell, ResponsiveContainer, Tooltip, XAxis, YAxis } from 'recharts'
import { api, type FinancialsOverviewResponse, type SalesFilters, type SalesTopEntity } from '../../../lib/api'
import type { PeriodSelection } from '../../../lib/periodSelection'
import { periodAnchorYearMonth } from '../../../lib/periodSelection'
import { fmtChartKpi, fmtPct } from '../../../lib/fmt'
import { stripLegalForm } from '../../../lib/stripLegalForm'
import PlSectionHeading from '../pl-two-view/PlSectionHeading'
import {
  buildMarginInsightHeadline,
  buildMarginInsightRows,
} from './overviewBriefingUtils'

const CARD = {
  background: '#FFFFFF',
  border: '1px solid #E2E8F0',
  boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
} as const

const BAR_COLORS = ['#1E3A5F', '#3B82F6', '#60A5FA', '#93C5FD', '#BFDBFE']

type Props = {
  data: FinancialsOverviewResponse
  period: PeriodSelection
  entity?: string
}

function InsightShell({
  headline,
  children,
}: {
  headline: string
  children: ReactNode
}) {
  return (
    <div className="rounded-xl overflow-hidden flex flex-col min-h-[320px]" style={CARD}>
      <div className="px-5 pt-5 pb-3" style={{ borderBottom: '1px solid #F1F5F9' }}>
        <p className="text-sm font-semibold leading-snug m-0" style={{ color: '#0F172A' }}>
          {headline}
        </p>
      </div>
      <div className="px-3 pb-4 pt-2 flex-1 min-h-0">{children}</div>
    </div>
  )
}

function CustomerConcentrationInsight({
  rows,
  loading,
}: {
  rows: SalesTopEntity[]
  loading: boolean
}) {
  const chartRows = useMemo(() => {
    const top = rows.slice(0, 5)
    const total = top.reduce((s, r) => s + (Number(r.cm ?? 0) || 0), 0)
    return top.map((r, i) => ({
      name: stripLegalForm(r.name).slice(0, 18),
      value: Number(r.cm ?? 0) || 0,
      share: total > 0 ? ((Number(r.cm ?? 0) || 0) / total) * 100 : 0,
      color: BAR_COLORS[i % BAR_COLORS.length],
    }))
  }, [rows])

  const headline = useMemo(() => {
    if (!chartRows.length) return 'Customer concentration: no invoiced sales in this period.'
    const top3 = chartRows.slice(0, 3)
    const top3Share = top3.reduce((s, r) => s + r.share, 0)
    const leader = chartRows[0]
    return `Top 3 customers account for ${top3Share.toFixed(0)}% of top-tier CM sales; ${leader.name} leads at ${fmtChartKpi(leader.value)} kEUR.`
  }, [chartRows])

  if (loading) {
    return (
      <InsightShell headline="Loading customer concentration…">
        <div className="h-[220px] animate-pulse rounded-lg" style={{ background: '#F8FAFC' }} />
      </InsightShell>
    )
  }

  return (
    <InsightShell headline={headline}>
      {!chartRows.length ? (
        <div className="h-[220px] flex items-center justify-center text-sm" style={{ color: '#94A3B8' }}>
          No customer data for this period
        </div>
      ) : (
        <ResponsiveContainer width="100%" height={220}>
          <BarChart data={chartRows} margin={{ top: 8, right: 12, left: 4, bottom: 24 }}>
            <CartesianGrid stroke="#E8EDF3" strokeDasharray="4 6" vertical={false} />
            <XAxis
              dataKey="name"
              tick={{ fontSize: 10, fill: '#64748B' }}
              interval={0}
              angle={-18}
              textAnchor="end"
              height={48}
            />
            <YAxis
              tick={{ fontSize: 10, fill: '#94A3B8' }}
              tickFormatter={v => fmtChartKpi(Number(v))}
              width={48}
            />
            <Tooltip
              formatter={(v: number, _n, item) => {
                const share = (item?.payload as { share?: number })?.share
                return [`${fmtChartKpi(v)} kEUR${share != null ? ` (${share.toFixed(1)}%)` : ''}`, 'CM sales']
              }}
            />
            <Bar dataKey="value" radius={[6, 6, 0, 0]} maxBarSize={44}>
              {chartRows.map((row, i) => (
                <Cell key={i} fill={row.color} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
      )}
    </InsightShell>
  )
}

function MarginSnapshotInsight({ data }: { data: FinancialsOverviewResponse }) {
  const rows = useMemo(() => buildMarginInsightRows(data), [data])
  const headline = useMemo(() => buildMarginInsightHeadline(data), [data])

  return (
    <InsightShell headline={headline}>
      <ResponsiveContainer width="100%" height={220}>
        <BarChart data={rows} layout="vertical" margin={{ top: 8, right: 16, left: 8, bottom: 8 }}>
          <CartesianGrid stroke="#E8EDF3" strokeDasharray="4 6" horizontal={false} />
          <XAxis
            type="number"
            tick={{ fontSize: 10, fill: '#94A3B8' }}
            tickFormatter={v => fmtPct(Number(v))}
            domain={[0, 'auto']}
          />
          <YAxis
            type="category"
            dataKey="label"
            tick={{ fontSize: 11, fill: '#64748B' }}
            width={96}
          />
          <Tooltip formatter={(v: number) => fmtPct(v)} />
          <Bar dataKey="value" radius={[0, 6, 6, 0]} maxBarSize={28} fill="#1E3A5F" />
        </BarChart>
      </ResponsiveContainer>
    </InsightShell>
  )
}

export default function OverviewInsightsSection({ data, period, entity }: Props) {
  const [customerRows, setCustomerRows] = useState<SalesTopEntity[]>([])
  const [customersLoading, setCustomersLoading] = useState(false)

  const ent = entity === 'all' ? undefined : entity
  const filters = useMemo<SalesFilters | undefined>(
    () => (ent ? { entity: [ent] } : undefined),
    [ent],
  )
  const anchor = periodAnchorYearMonth(period)

  useEffect(() => {
    let cancelled = false
    setCustomersLoading(true)

    const opts =
      period.grain === 'week'
        ? { period_grain: 'week' as const, iso_year: period.isoYear, iso_week: period.isoWeek, limit: 20 }
        : { period_grain: 'month' as const, limit: 20 }

    void api
      .salesTopEntities(anchor.year, anchor.month, 'customer', 'cm', 'invoiced', filters, opts)
      .then(res => {
        if (!cancelled) setCustomerRows(res.rows ?? [])
      })
      .catch(() => {
        if (!cancelled) setCustomerRows([])
      })
      .finally(() => {
        if (!cancelled) setCustomersLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [anchor.year, anchor.month, period, filters])

  return (
    <div>
      <PlSectionHeading>Key insights</PlSectionHeading>
      <div className="grid grid-cols-1 lg:grid-cols-2 gap-4">
        <CustomerConcentrationInsight rows={customerRows} loading={customersLoading} />
        <MarginSnapshotInsight data={data} />
      </div>
    </div>
  )
}
