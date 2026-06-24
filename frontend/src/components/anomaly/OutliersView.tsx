import { useState, useEffect, useRef } from 'react'
import { useSearchParams } from 'react-router-dom'
import {
  ComposedChart, Line, Scatter, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, ReferenceArea, ReferenceLine, Legend,
} from 'recharts'
import { api, type AnomalyTreeResponse, type AnomalyTreeNode, type OutlierNodePayload } from '../../lib/api'
import { AnomaliesLoading } from '../financials/AnomaliesPanel'
import AnomalyReport from './AnomalyReport'
import SensitivitySlider from './SensitivitySlider'
import HistogramChart from './HistogramChart'
import { buildTrendSeries, RegressionTrendLine } from './RegressionTrend'
import { xAxisTickProps } from '../../lib/anomalyCharts'
import BookingsDrill from './BookingsDrill'

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function isOutlierPayload(p: AnomalyTreeNode['payload']): p is OutlierNodePayload {
  return 'series' in p && !('month_index' in p)
}

// ---------------------------------------------------------------------------
// Chart set for a single outlier node
// ---------------------------------------------------------------------------

function OutlierChartSet({ node, pointThreshold }: { node: AnomalyTreeNode; pointThreshold: number }) {
  if (!isOutlierPayload(node.payload)) return null
  const { series, stats, regression, histogram } = node.payload
  const { mean_keur, std_keur } = stats
  const tickProps = xAxisTickProps(series.length)

  const trendSeries = regression ? buildTrendSeries(regression, series.length) : []
  const chartData = series.map((p, i) => ({
    label: p.label,
    value: p.value_keur,
    flagged: (p.signal_score ?? 0) >= pointThreshold ? p.value_keur : null,
    trend: trendSeries[i]?.trend ?? null,
  }))

  return (
    <div className="space-y-4">
      <div>
        <p className="text-xs font-semibold mb-2" style={{ color: '#475569' }}>Monthly values with normal range and trend</p>
        <ResponsiveContainer width="100%" height={240}>
          <ComposedChart data={chartData} margin={{ top: 8, right: 16, bottom: 8, left: 8 }}>
            <CartesianGrid strokeDasharray="3 3" stroke="#F1F5F9" />
            <XAxis dataKey="label" tick={{ fontSize: 10, fill: '#94A3B8' }} {...tickProps} />
            <YAxis tick={{ fontSize: 10, fill: '#94A3B8' }} unit=" k" width={48} />
            <Tooltip
              formatter={(v: number, name: string) => [`${v.toFixed(1)} kEUR`, name]}
              contentStyle={{ fontSize: 11, borderRadius: 8, border: '1px solid #E2E8F0' }}
            />
            <Legend wrapperStyle={{ fontSize: 10 }} />
            <ReferenceArea y1={mean_keur - 1.5 * std_keur} y2={mean_keur + 1.5 * std_keur} fill="#3B82F6" fillOpacity={0.08} strokeOpacity={0} />
            <ReferenceLine y={mean_keur} stroke="#3B82F6" strokeDasharray="4 3" strokeWidth={1} />
            <Line type="monotone" dataKey="value" stroke="#1E3A5F" strokeWidth={2} dot={false} name="Actual" />
            {regression && <RegressionTrendLine r_squared={regression.r_squared} />}
            <Scatter dataKey="flagged" fill="#DC2626" name="Flagged" />
          </ComposedChart>
        </ResponsiveContainer>
        <p className="text-[11px] mt-2 leading-relaxed" style={{ color: '#94A3B8' }}>
          Solid line = monthly value. Blue band = normal range (mean ± 1.5 std). Dashed amber = linear trend. Red dots = months above the signal threshold.
        </p>
      </div>
      {histogram && histogram.length > 0 && (
        <div>
          <p className="text-xs font-semibold mb-2" style={{ color: '#475569' }}>Distribution of monthly values (kEUR)</p>
          <HistogramChart bins={histogram} height={140} />
          <p className="text-[11px] mt-1 leading-relaxed" style={{ color: '#94A3B8' }}>
            How often each value range occurred across all months.
          </p>
        </div>
      )}
    </div>
  )
}

// ---------------------------------------------------------------------------
// NodeSection — one level in the stepwise vertical drill
// ---------------------------------------------------------------------------

interface NodeSectionProps {
  node: AnomalyTreeNode
  threshold: number
  /** depth: 0=L3, 1=L4, 2=account */
  depth: number
}

function NodeSection({ node, threshold, depth }: NodeSectionProps) {
  const [childrenOpen, setChildrenOpen] = useState(false)
  const [showBookings, setShowBookings] = useState(false)

  const headline = node.label
  const bullets: string[] = []
  if (isOutlierPayload(node.payload)) {
    const s = node.payload.stats
    bullets.push(`${s.n} months of data — mean ${s.mean_keur.toFixed(1)} kEUR, std ${s.std_keur.toFixed(1)} kEUR`)
    if (node.payload.regression) {
      const r = node.payload.regression
      bullets.push(`Linear trend: ${r.slope >= 0 ? '+' : ''}${r.slope.toFixed(2)} kEUR/month (R²=${r.r_squared.toFixed(2)})`)
    }
  }

  const filteredChildren = node.children
    .filter(ch => (ch.signal_score ?? 0) >= threshold)
    .sort((a, b) => (b.signal_score ?? 0) - (a.signal_score ?? 0))

  const hasChildren = filteredChildren.length > 0
  const isAccountLevel = node.level === 'account'
  // Show "Dive deeper" if has children and we're at L3 or L4 (not account)
  const canDrillDeeper = hasChildren && !isAccountLevel
  // Show "Show bookings" if account level with a group key
  const canShowBookings = node.account_number_group != null

  const indentStyle: React.CSSProperties =
    depth > 0
      ? { borderLeft: `2px solid ${depth === 1 ? '#CBD5E1' : '#E2E8F0'}`, paddingLeft: 16 }
      : {}

  return (
    <div style={indentStyle} className="mb-6">
      <AnomalyReport
        headline={headline}
        bullets={bullets}
        score={node.signal_score ?? 0}
        band={node.band ?? 'low'}
        label={node.level}
      >
        <OutlierChartSet node={node} pointThreshold={threshold} />

        {/* Dive deeper / Show bookings buttons */}
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

        {/* Bookings drill */}
        {showBookings && node.account_number_group && (
          <BookingsDrill
            accountNumberGroup={node.account_number_group}
            accountLabel={node.label}
          />
        )}
      </AnomalyReport>

      {/* Stepwise children — each child renders its own NodeSection */}
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
// OutliersView
// ---------------------------------------------------------------------------

export default function OutliersView() {
  const [data, setData] = useState<AnomalyTreeResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)
  const [threshold, setThreshold] = useState(70)
  const [searchParams] = useSearchParams()

  // Refs map: key → div ref for deep-link scroll
  const nodeRefs = useRef<Map<string, HTMLDivElement>>(new Map())
  const scrolledRef = useRef(false)

  useEffect(() => {
    let cancelled = false
    setLoading(true)
    api.financialsAnomalyOutliers()
      .then(d => { if (!cancelled) { setData(d); setLoading(false) } })
      .catch((e: unknown) => {
        if (!cancelled) { setError(e instanceof Error ? e.message : String(e)); setLoading(false) }
      })
    return () => { cancelled = true }
  }, [])

  // Deep-link scroll: once data loads and refs are attached, scroll to ?node=
  useEffect(() => {
    if (!data || scrolledRef.current) return
    const nodeKey = searchParams.get('node')
    if (!nodeKey) return
    // Small timeout to let DOM paint
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
      <p className="text-sm" style={{ color: '#94A3B8' }}>No outlier data available.</p>
    </div>
  )

  const filteredNodes = data.tree
    .filter(n => (n.signal_score ?? 0) >= threshold)
    .sort((a, b) => (b.signal_score ?? 0) - (a.signal_score ?? 0))

  return (
    <div>
      <SensitivitySlider value={threshold} onChange={v => { setThreshold(v) }} />

      {filteredNodes.length === 0 && (
        <p className="text-sm" style={{ color: '#94A3B8' }}>No positions above threshold. Lower the signal threshold to see more.</p>
      )}

      {filteredNodes.map(node => {
        // Build a stable ref callback for this node key
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
