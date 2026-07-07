import { useState, useEffect } from 'react'
import { useNavigate } from 'react-router-dom'
import {
  ComposedChart, Line, Scatter, XAxis, YAxis, Tooltip,
  ResponsiveContainer, Legend,
} from 'recharts'
import { api, type AnomalyOverviewCard, type AnomalyOverviewResponse } from '../../lib/api'
import { AnomaliesLoading } from '../financials/AnomaliesPanel'
import AnomalyReport from './AnomalyReport'
import SensitivitySlider from './SensitivitySlider'
import { xAxisTickProps } from '../../lib/anomalyCharts'

const FLAG_NAV_LABELS: Record<string, string> = {
  outliers: 'View Outliers',
  seasonality: 'View Seasonality',
  forensic: 'View Forensic',
}

// ---------------------------------------------------------------------------
// SparkLine — actual line + dashed expected reference + red flagged dots
// ---------------------------------------------------------------------------

interface SparkPoint {
  label: string
  value: number
  expected: number | null
  signal_score: number
}

interface SparkLineProps {
  data: SparkPoint[]
  threshold: number
}

function SparkLine({ data, threshold }: SparkLineProps) {
  const tickProps = xAxisTickProps(data.length)

  const chartData = data.map(p => ({
    label: p.label,
    value: p.value,
    expected: p.expected,
    // Scatter dataKey: value when flagged, null otherwise — Recharts Scatter needs non-null to plot
    flagged: (p.signal_score ?? 0) >= threshold ? p.value : null,
  }))

  return (
    <div style={{ height: 132 }}>
      <ResponsiveContainer width="100%" height="100%">
        <ComposedChart data={chartData} margin={{ top: 6, right: 6, bottom: 4, left: 4 }}>
          <XAxis
            dataKey="label"
            tick={{ fontSize: 10, fill: '#94A3B8' }}
            {...tickProps}
          />
          <YAxis hide />
          <Tooltip
            labelFormatter={(_label, payload) => {
              // Use the data point's own label field (e.g. "Jul24") — append "A" for Actual
              const point = payload?.[0]?.payload as { label?: string } | undefined
              const monthLabel = point?.label ?? String(_label)
              return `${monthLabel}A`
            }}
            formatter={(v: number, name: string) => [`${(v).toFixed(1)} kEUR`, name]}
            contentStyle={{ fontSize: 11, borderRadius: 6, border: '1px solid #E2E8F0' }}
          />
          <Legend wrapperStyle={{ fontSize: 10 }} />
          <Line
            type="monotone"
            dataKey="value"
            stroke="#1E3A5F"
            strokeWidth={1.5}
            dot={false}
            name="Actual"
          />
          <Line
            type="monotone"
            dataKey="expected"
            stroke="#3B82F6"
            strokeWidth={1.2}
            strokeDasharray="4 3"
            dot={false}
            name="Expected"
            connectNulls
          />
          <Scatter dataKey="flagged" fill="#DC2626" name="Flagged" />
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}

// ---------------------------------------------------------------------------
// CardView
// ---------------------------------------------------------------------------

function CardView({ card, threshold }: { card: AnomalyOverviewCard; threshold: number }) {
  const navigate = useNavigate()
  const activeFlags = (['outliers', 'seasonality', 'forensic'] as const).filter(
    k => (card.flags[k].score ?? 0) > 0
  )

  return (
    <AnomalyReport
      headline={card.headline ?? card.label}
      bullets={card.bullets ?? []}
      score={card.signal_score ?? 0}
      band={card.band ?? 'low'}
      label={card.label}
      sublabel={`${card.level_0} · ${card.level_3}`}
    >
      {card.spark_series && card.spark_series.length > 0 && (
        <div className="mb-3">
          <p className="text-[12px] mb-1" style={{ color: '#94A3B8' }}>Monthly trend</p>
          <SparkLine data={card.spark_series} threshold={threshold} />
        </div>
      )}
      {activeFlags.length > 0 && (
        <div className="flex flex-wrap gap-2 mt-2">
          {activeFlags.map(key => (
            <button
              key={key}
              type="button"
              onClick={() => navigate(card.flags[key].deep_link)}
              className="rounded px-2.5 py-1 text-xs font-medium transition-colors"
              style={{ background: 'rgba(30,58,95,0.08)', color: '#1E3A5F' }}
            >
              {FLAG_NAV_LABELS[key]}
            </button>
          ))}
        </div>
      )}
    </AnomalyReport>
  )
}

// ---------------------------------------------------------------------------
// Section
// ---------------------------------------------------------------------------

function Section({
  title,
  cards,
  keys,
  threshold,
}: {
  title: string
  cards: AnomalyOverviewCard[]
  keys: string[]
  threshold: number
}) {
  const sectionCards = keys
    .map(k => cards.find(c => c.key === k))
    .filter((c): c is AnomalyOverviewCard => c !== undefined && (c.signal_score ?? 0) >= threshold)
    .sort((a, b) => (b.signal_score ?? 0) - (a.signal_score ?? 0))

  return (
    <div className="mb-8">
      <div className="flex items-center gap-3 mb-4">
        <h2 className="text-base font-semibold" style={{ color: '#1E3A5F' }}>{title}</h2>
        <span
          className="rounded-full px-2 py-0.5 text-[12px] font-semibold"
          style={{ background: 'rgba(30,58,95,0.08)', color: '#1E3A5F' }}
        >
          {sectionCards.length}
        </span>
      </div>
      {sectionCards.length === 0 ? (
        <p className="text-sm" style={{ color: '#94A3B8' }}>No positions above threshold.</p>
      ) : (
        <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
          {sectionCards.map(card => <CardView key={card.key} card={card} threshold={threshold} />)}
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// AnomalyOverview
// ---------------------------------------------------------------------------

export default function AnomalyOverview() {
  const [data, setData] = useState<AnomalyOverviewResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [threshold, setThreshold] = useState(70)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    setError(null)
    api.financialsAnomalyOverview()
      .then(d => { if (!cancelled) { setData(d); setLoading(false) } })
      .catch((e: unknown) => {
        if (!cancelled) { setError(e instanceof Error ? e.message : String(e)); setLoading(false) }
      })
    return () => { cancelled = true }
  }, [])

  if (loading) return <AnomaliesLoading compact={false} />
  if (error) return (
    <div className="rounded-xl px-5 py-4 text-sm" style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(220,38,38,0.3)', color: '#991B1B' }}>{error}</div>
  )
  if (!data || data.cards.length === 0) return (
    <div className="rounded-xl px-5 py-8 text-center bg-white" style={{ border: '1px solid #E2E8F0' }}>
      <p className="text-sm" style={{ color: '#94A3B8' }}>No overview data available.</p>
    </div>
  )

  return (
    <div>
      <SensitivitySlider value={threshold} onChange={setThreshold} />
      <Section title="Income Statement (P&L)" cards={data.cards} keys={data.groups.pl} threshold={threshold} />
      <Section title="Balance Sheet" cards={data.cards} keys={data.groups.bs} threshold={threshold} />
    </div>
  )
}
