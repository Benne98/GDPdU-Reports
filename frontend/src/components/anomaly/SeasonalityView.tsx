import { useState, useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  ComposedChart, Line, Scatter, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  BarChart, Bar, Cell, Legend,
} from 'recharts'
import { api, type AnomalyTreeResponse, type AnomalyTreeNode, type SeasonalNodePayload } from '../../lib/api'
import { AnomaliesLoading } from '../financials/AnomaliesPanel'
import AnomalyReport from './AnomalyReport'
import SensitivitySlider from './SensitivitySlider'
import { xAxisTickProps } from '../../lib/anomalyCharts'
import BookingsDrill from './BookingsDrill'

const MONTH_LABELS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec']

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function isSeasonalPayload(p: AnomalyTreeNode['payload']): p is SeasonalNodePayload {
  return 'month_index' in p
}

// ---------------------------------------------------------------------------
// Chart set for a single seasonal node
// ---------------------------------------------------------------------------

function SeasonalChartSet({ node, pointThreshold }: { node: AnomalyTreeNode; pointThreshold: number }) {
  if (!isSeasonalPayload(node.payload)) return null
  const { series, month_index, stats } = node.payload

  const resid_std = stats.resid_std_keur
  const tickProps = xAxisTickProps(series.length)

  const chartAData = series.map(p => ({
    label: p.label,
    actual: p.actual_keur,
    expected: p.expected_keur,
    bandHigh: p.expected_keur !== null ? p.expected_keur + resid_std : null,
    bandLow: p.expected_keur !== null ? p.expected_keur - resid_std : null,
    flagged: (p.signal_score ?? 0) >= pointThreshold ? p.actual_keur : null,
  }))

  const chartBData = month_index.map(m => ({
    month: MONTH_LABELS[(m.month - 1) % 12] ?? String(m.month),
    seasonal_index: m.seasonal_index,
  }))

  return (
    <div className="space-y-4">
      <div>
        <p className="text-xs font-semibold mb-2" style={{ color: '#475569' }}>Actual vs expected (with seasonal band)</p>
        <ResponsiveContainer width="100%" height={280}>
          <ComposedChart data={chartAData} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" />
            <XAxis dataKey="label" tick={{ fontSize: 10, fill: '#94A3B8' }} {...tickProps} />
            <YAxis tick={{ fontSize: 10, fill: '#94A3B8' }} unit=" k" width={48} />
            <Tooltip formatter={(v: number, name: string) => [`${v.toFixed(1)} kEUR`, name]} contentStyle={{ fontSize: 11, borderRadius: 8, border: '1px solid #E2E8F0' }} />
            <Legend wrapperStyle={{ fontSize: 10 }} />
            <Line type="monotone" dataKey="actual" stroke="#1E3A5F" strokeWidth={2} dot={false} name="Actual" />
            <Line type="monotone" dataKey="expected" stroke="#3B82F6" strokeWidth={1.5} strokeDasharray="4 3" dot={false} name="Expected" />
            <Line type="monotone" dataKey="bandHigh" stroke="#93C5FD" strokeWidth={1} strokeDasharray="2 4" dot={false} name="Band +" legendType="none" />
            <Line type="monotone" dataKey="bandLow" stroke="#93C5FD" strokeWidth={1} strokeDasharray="2 4" dot={false} name="Band −" legendType="none" />
            <Scatter dataKey="flagged" fill="#DC2626" name="Off-season" />
          </ComposedChart>
        </ResponsiveContainer>
        <p className="text-[11px] mt-2 leading-relaxed" style={{ color: '#94A3B8' }}>
          Solid = actual value. Dashed blue = what the seasonal model expected. Light blue band = normal seasonal range. Red dots = months that deviated significantly.
        </p>
      </div>
      <div>
        <p className="text-xs font-semibold mb-2" style={{ color: '#475569' }}>Monthly seasonal pattern</p>
        <ResponsiveContainer width="100%" height={200}>
          <BarChart data={chartBData} margin={{ top: 4, right: 16, bottom: 4, left: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" />
            <XAxis dataKey="month" tick={{ fontSize: 10, fill: '#94A3B8' }} />
            <YAxis tick={{ fontSize: 10, fill: '#94A3B8' }} unit=" k" width={40} />
            <Tooltip formatter={(v: number) => [`${v.toFixed(1)} kEUR`]} contentStyle={{ fontSize: 11, borderRadius: 8, border: '1px solid #E2E8F0' }} />
            <Bar dataKey="seasonal_index" name="Seasonal factor">
              {chartBData.map((entry, i) => (
                <Cell key={`cell-${i}`} fill={entry.seasonal_index >= 0 ? '#3B82F6' : '#F59E0B'} />
              ))}
            </Bar>
          </BarChart>
        </ResponsiveContainer>
        <p className="text-[11px] mt-2 leading-relaxed" style={{ color: '#94A3B8' }}>
          Blue = above the year's average for that month. Amber = below average. Factors sum to zero across all 12 months.
        </p>
      </div>
    </div>
  )
}

// ---------------------------------------------------------------------------
// NodeSection — stepwise drill (L3 → L4 → Account → bookings)
// ---------------------------------------------------------------------------

interface NodeSectionProps {
  node: AnomalyTreeNode
  threshold: number
  depth: number
}

function NodeSection({ node, threshold, depth }: NodeSectionProps) {
  const [childrenOpen, setChildrenOpen] = useState(false)
  const [showBookings, setShowBookings] = useState(false)

  if (!isSeasonalPayload(node.payload)) return null

  const { stats } = node.payload
  const bullets: string[] = [
    `${stats.n} months of data analyzed`,
    `Seasonal deviation std: ${stats.resid_std_keur.toFixed(1)} kEUR`,
  ]

  const filteredChildren = node.children
    .filter(ch => (ch.signal_score ?? 0) >= threshold)
    .sort((a, b) => (b.signal_score ?? 0) - (a.signal_score ?? 0))

  const hasChildren = filteredChildren.length > 0
  const isAccountLevel = node.level === 'account'
  const canDrillDeeper = hasChildren && !isAccountLevel
  const canShowBookings = node.account_number_group != null

  const indentStyle: React.CSSProperties =
    depth > 0
      ? { borderLeft: `2px solid ${depth === 1 ? '#CBD5E1' : '#E2E8F0'}`, paddingLeft: 16 }
      : {}

  return (
    <div style={indentStyle} className="mb-6">
      <AnomalyReport
        headline={node.label}
        bullets={bullets}
        score={node.signal_score ?? 0}
        band={node.band ?? 'low'}
        label={node.level}
      >
        <SeasonalChartSet node={node} pointThreshold={threshold} />

        <div className="mt-4 flex flex-wrap gap-2">
          {canDrillDeeper && (
            <button
              type="button"
              onClick={() => { setChildrenOpen(o => !o); setShowBookings(false) }}
              className="rounded px-3 py-1.5 text-xs font-medium transition-colors"
              style={{ background: 'rgba(30,58,95,0.08)', color: '#1E3A5F' }}
            >
              {childrenOpen ? 'Hide detail' : 'Dive deeper'}
            </button>
          )}
          {canShowBookings && (
            <button
              type="button"
              onClick={() => { setShowBookings(o => !o); setChildrenOpen(false) }}
              className="rounded px-3 py-1.5 text-xs font-medium transition-colors"
              style={{ background: 'rgba(37,99,235,0.08)', color: '#2563EB' }}
            >
              {showBookings ? 'Hide bookings' : 'Show bookings'}
            </button>
          )}
        </div>

        {showBookings && node.account_number_group && (
          <BookingsDrill
            accountNumberGroup={node.account_number_group}
            accountLabel={node.label}
          />
        )}
      </AnomalyReport>

      {childrenOpen && filteredChildren.map(child => (
        <NodeSection
          key={child.key}
          node={child}
          threshold={threshold}
          depth={depth + 1}
        />
      ))}
    </div>
  )
}

// ---------------------------------------------------------------------------
// SeasonalityView
// ---------------------------------------------------------------------------

export default function SeasonalityView() {
  const [data, setData] = useState<AnomalyTreeResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [threshold, setThreshold] = useState(70)
  const [searchParams] = useSearchParams()

  const nodeRefs = useRef<Map<string, HTMLDivElement>>(new Map())
  const scrolledRef = useRef(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.financialsAnomalySeasonality()
      .then(d => { if (!cancelled) { setData(d); setLoading(false) } })
      .catch((e: unknown) => {
        if (!cancelled) { setError(e instanceof Error ? e.message : String(e)); setLoading(false) }
      })
    return () => { cancelled = true }
  }, [])

  // Deep-link scroll: scroll to ?node= on load
  useEffect(() => {
    if (!data || scrolledRef.current) return
    const nodeKey = searchParams.get('node')
    if (!nodeKey) return
    const t = setTimeout(() => {
      const el = nodeRefs.current.get(nodeKey)
      if (el) {
        el.scrollIntoView({ behavior: 'smooth', block: 'start' })
        scrolledRef.current = true
      }
    }, 120)
    return () => clearTimeout(t)
  }, [data, searchParams])

  if (loading) return <AnomaliesLoading compact={false} />
  if (error) return (
    <div className="rounded-xl px-5 py-4 text-sm" style={{ background: 'rgba(239,68,68,0.08)', border: '1px solid rgba(220,38,38,0.3)', color: '#991B1B' }}>{error}</div>
  )
  if (!data || data.tree.length === 0) return (
    <div className="rounded-xl px-5 py-8 text-center bg-white" style={{ border: '1px solid #E2E8F0' }}>
      <p className="text-sm" style={{ color: '#94A3B8' }}>No seasonality data available.</p>
    </div>
  )

  const filteredNodes = data.tree
    .filter(n => (n.signal_score ?? 0) >= threshold)
    .sort((a, b) => (b.signal_score ?? 0) - (a.signal_score ?? 0))

  return (
    <div>
      <SensitivitySlider value={threshold} onChange={setThreshold} />
      {filteredNodes.length === 0 && (
        <p className="text-sm" style={{ color: '#94A3B8' }}>No positions above threshold. Lower the signal threshold to see more.</p>
      )}
      {filteredNodes.map(node => {
        if (!isSeasonalPayload(node.payload)) return null
        const refCallback = (el: HTMLDivElement | null) => {
          if (el) nodeRefs.current.set(node.key, el)
          else nodeRefs.current.delete(node.key)
        }
        return (
          <div key={node.key} ref={refCallback}>
            <NodeSection node={node} threshold={threshold} depth={0} />
          </div>
        )
      })}
    </div>
  )
}
