import { useState, useEffect, useMemo } from 'react'
import {
  ComposedChart, Bar, Line, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Legend, ReferenceLine,
} from 'recharts'
import { motion } from 'framer-motion'
import { Pin } from 'lucide-react'
import { api, WcTimelineResponse, WcTimelinePoint } from '../../lib/api'
import { FinancialsDrillOpen } from './FinancialStatementTable'
import { fmtKpi } from '../../lib/fmt'
import { PAGE_CHART_ATTR } from '../../hooks/usePageChartKeyboardNav'
import { useChartLoadReporter } from '../../hooks/useChartLoadReporter'
import { useOptionalActionNotesContext } from '../action-notes/ActionNotesContext'
import { captureChartByTarget } from '../../lib/actionNotes/chartCapture'
import { STATEMENT_TOOLBAR_ICON_BTN, STATEMENT_TOOLBAR_BTN_STYLE } from './statement-two-view/statementToolbarButton'
// ─── Constants ────────────────────────────────────────────────────────────────

type Grain = 'month' | 'week' | 'day'

const GRAIN_OPTIONS: { value: Grain; label: string }[] = [
  { value: 'month', label: 'Month' },
  { value: 'week',  label: 'Week'  },
  { value: 'day',   label: 'Day'   },
]

const COLORS = {
  inventories:       '#1E3A5F',
  trade_receivables: '#3B82F6',
  trade_payables:    '#64748B',
  other_wc:          '#A78BFA',
  twc:               '#F59E0B',
  nwc:               '#10B981',
}

// Category → drill level_3 mapping
const CAT_LEVEL3: Record<string, string> = {
  inventories:       'Inventories',
  trade_receivables: 'Trade receivables',
  trade_payables:    'Trade payables',
}

// ─── Sub-period structure ─────────────────────────────────────────────────────

/**
 * Divide chartData into sub-periods for average annotation.
 * Month grain: 3 × 12  = 36 points
 * Week grain:  3 × 13  = 39 points
 * Day grain:   2 × 30  = 60 points
 */
function computeSubPeriods(n: number, grain: Grain) {
  const size = grain === 'month' ? 12 : grain === 'week' ? 13 : 30
  const periods: { startIdx: number; endIdx: number; midIdx: number }[] = []
  for (let i = 0; i < n; i += size) {
    const endIdx = Math.min(i + size - 1, n - 1)
    const midIdx = Math.round((i + endIdx) / 2)
    periods.push({ startIdx: i, endIdx, midIdx })
  }
  return periods
}

// ─── Custom tooltip ───────────────────────────────────────────────────────────

function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  const get = (key: string) => payload.find((p: any) => p.dataKey === key)?.value ?? 0
  return (
    <div className="rounded-lg px-3 py-2 text-xs shadow-lg" style={{ background: '#1E3A5F', color: '#F8FAFC', minWidth: 172 }}>
      <div className="font-semibold mb-1.5">{label}</div>
      <div className="mb-1" style={{ color: '#94A3B8', fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Bars</div>
      {[
        { key: 'inventories',       label: 'Inventories',        color: COLORS.inventories },
        { key: 'trade_receivables', label: 'Trade Receivables',  color: COLORS.trade_receivables },
        { key: 'trade_payables',    label: 'Trade Payables',     color: COLORS.trade_payables },
        { key: 'other_wc',          label: 'Other WC',           color: COLORS.other_wc },
      ].map(({ key, label: lbl, color }) => (
        <div key={key} className="flex justify-between gap-3">
          <span style={{ color: '#CBD5E1' }}>{lbl}</span>
          <span style={{ color, fontWeight: 600 }}>{fmtKpi(get(key))}</span>
        </div>
      ))}
      <div className="mt-1.5 mb-1" style={{ color: '#94A3B8', fontSize: 11, textTransform: 'uppercase', letterSpacing: '0.05em' }}>Lines</div>
      {[
        { key: 'twc', label: 'Trade WC', color: COLORS.twc },
        { key: 'nwc', label: 'Net WC',   color: COLORS.nwc },
      ].map(({ key, label: lbl, color }) => (
        <div key={key} className="flex justify-between gap-3">
          <span style={{ color: '#CBD5E1' }}>{lbl}</span>
          <span style={{ color, fontWeight: 600 }}>{fmtKpi(get(key))}</span>
        </div>
      ))}
    </div>
  )
}

// ─── Custom legend ────────────────────────────────────────────────────────────

function ChartLegend() {
  const bars = [
    { color: COLORS.inventories,       label: 'Inventories'       },
    { color: COLORS.trade_receivables, label: 'Trade Receivables' },
    { color: COLORS.trade_payables,    label: 'Trade Payables'    },
    { color: COLORS.other_wc,          label: 'Other WC'          },
  ]
  const lines = [
    { color: COLORS.twc, label: 'Trade WC',  dashed: false },
    { color: COLORS.nwc, label: 'Net WC',    dashed: true  },
  ]
  return (
    <div className="flex flex-wrap items-center justify-center gap-x-4 gap-y-1 pt-2" style={{ fontSize: 12, color: '#64748B' }}>
      {bars.map(({ color, label }) => (
        <span key={label} className="flex items-center gap-1">
          <span style={{ display: 'inline-block', width: 10, height: 10, borderRadius: 2, background: color, flexShrink: 0 }} />
          {label}
        </span>
      ))}
      <span style={{ width: 1, height: 12, background: '#E2E8F0', display: 'inline-block', margin: '0 2px' }} />
      {lines.map(({ color, label, dashed }) => (
        <span key={label} className="flex items-center gap-1">
          <svg width="18" height="10" style={{ flexShrink: 0 }}>
            {dashed
              ? <line x1="0" y1="5" x2="18" y2="5" stroke={color} strokeWidth="2" strokeDasharray="4 2" />
              : <line x1="0" y1="5" x2="18" y2="5" stroke={color} strokeWidth="2" />
            }
          </svg>
          {label}
        </span>
      ))}
    </div>
  )
}

// ─── Tick density helper ──────────────────────────────────────────────────────

function sparseTick(idx: number, total: number): boolean {
  if (total <= 24)  return true
  if (total <= 36)  return idx % 6 === 0    // ~6 labels for 36 months
  if (total <= 39)  return idx % 4 === 0    // ~10 labels for 39 weeks
  return idx % 7 === 0                       // ~9 labels for 60 days
}

// ─── Custom average box label ─────────────────────────────────────────────────

function AvgLabel(props: any) {
  const { viewBox, avgKeur } = props
  if (!viewBox || avgKeur == null) return null
  const { x } = viewBox
  const formatted = Math.round(avgKeur).toLocaleString('de-DE')
  const text = `Ø ${formatted}`
  const w = Math.max(text.length * 7.5 + 12, 52)
  return (
    <g>
      <rect
        x={x - w / 2} y={4} width={w} height={18} rx={3}
        fill="#F8FAFC" stroke="#CBD5E1" strokeWidth={1}
      />
      <text
        x={x} y={16} textAnchor="middle" fontSize={11} fill="#64748B"
      >
        {text}
      </text>
    </g>
  )
}

// ─── Component ────────────────────────────────────────────────────────────────

interface WcTimelineChartProps {
  year:    number
  month:   number
  entity?: string
  onDrill: (d: FinancialsDrillOpen) => void
}

const WC_TIMELINE_CHART_ID = 'wc-timeline-chart'

export default function WcTimelineChart({ year, month, entity, onDrill }: WcTimelineChartProps) {
  const [grain, setGrain]     = useState<Grain>('month')
  const [res,   setRes]       = useState<WcTimelineResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError]     = useState<string | null>(null)
  // Incremented to trigger a manual retry without changing other deps
  const [retryCount, setRetryCount] = useState(0)
  const notesCtx = useOptionalActionNotesContext()

  // ─── Chart pin registration ────────────────────────────────────────────────
  useEffect(() => {
    if (!notesCtx) return
    notesCtx.registerChartCandidate({
      id: WC_TIMELINE_CHART_ID,
      label: 'Working Capital — timeline',
      description: 'Working capital timeline chart',
      capture: () => captureChartByTarget(WC_TIMELINE_CHART_ID),
      viewState: { grain },
    })
    return () => notesCtx.unregisterChartCandidate(WC_TIMELINE_CHART_ID)
  }, [notesCtx, grain])

  useEffect(() => {
    setLoading(true)
    setError(null)
    api.financialsWcTimeline(year, month, grain, entity)
      .then(r => { setRes(r); setError(null) })
      .catch((e: unknown) => {
        setRes(null)
        setError(e instanceof Error ? e.message : 'Failed to load working capital timeline')
      })
      .finally(() => setLoading(false))
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [year, month, grain, entity, retryCount])

  const chartData = useMemo(() => {
    if (!res?.series) return []
    return res.series.map((p, i) => ({
      ...p,
      inventories:       p.inventories       / 1_000,
      trade_receivables: p.trade_receivables / 1_000,
      trade_payables:    p.trade_payables    / 1_000,
      other_wc:          p.other_wc          / 1_000,
      twc:               p.twc               / 1_000,
      nwc:               p.nwc               / 1_000,
      _idx: i,
    }))
  }, [res])

  const timedOut = useChartLoadReporter('fin-wc-timeline', loading, null)
  if (timedOut) return null

  // Sub-period averages
  const subPeriods = useMemo(() => {
    if (!chartData.length) return []
    const periods = computeSubPeriods(chartData.length, grain)
    return periods.map(({ startIdx, endIdx, midIdx }) => {
      const slice = chartData.slice(startIdx, endIdx + 1)
      const avgTwc = slice.length
        ? slice.reduce((s, p) => s + (p.twc as number), 0) / slice.length
        : 0
      return {
        startLabel: chartData[startIdx]?.label ?? '',
        endLabel:   chartData[endIdx]?.label   ?? '',
        midLabel:   chartData[midIdx]?.label   ?? '',
        avgTwcKeur: avgTwc,
      }
    })
  }, [chartData, grain])

  const yFmt = (v: number) =>
    Math.abs(v) >= 1000
      ? `${(v / 1000).toLocaleString('de-DE', { maximumFractionDigits: 1 })}M`
      : v.toLocaleString('de-DE', { maximumFractionDigits: 0 })

  const handleClick = (e: any, category?: string) => {
    if (!e?.activePayload?.length) return
    const pt: WcTimelinePoint & { _idx: number } = e.activePayload[0].payload
    const d = new Date(pt.date)
    let dateFrom: string
    let dateTo: string

    if (grain === 'month') {
      dateFrom = new Date(d.getFullYear(), d.getMonth(), 1).toISOString().split('T')[0]
      dateTo   = pt.date
    } else if (grain === 'week') {
      const day = d.getDay()
      const mon = new Date(d)
      mon.setDate(d.getDate() - ((day + 6) % 7))
      dateFrom = mon.toISOString().split('T')[0]
      dateTo   = pt.date
    } else {
      dateFrom = pt.date
      dateTo   = pt.date
    }

    const level3 = category ? CAT_LEVEL3[category] : undefined

    onDrill({
      title:         level3 ? level3 : 'Working Capital',
      statementType: 'BS',
      level3,
      dateFrom,
      dateTo,
    })
  }

  return (
    <motion.div
      {...{ [PAGE_CHART_ATTR]: '' }}
      id={WC_TIMELINE_CHART_ID}
      data-expert-chart-target={WC_TIMELINE_CHART_ID}
      tabIndex={-1}
      initial={{ opacity: 0, y: 16 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4 }}
      className="rounded-xl flex flex-col mt-4 scroll-mt-24 outline-none focus-visible:ring-2 focus-visible:ring-[rgba(30,58,95,0.35)] focus-visible:ring-offset-2"
      style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.06)' }}
    >
      {/* Header */}
      <div className="flex flex-wrap items-start justify-between gap-3 px-4 py-3" style={{ borderBottom: '1px solid #E2E8F0' }}>
        <div>
          <h3 className="text-sm font-semibold" style={{ color: '#111827' }}>Working Capital — timeline</h3>
          <p className="text-xs mt-0.5" style={{ color: '#94A3B8' }}>
            Inventories, receivables & payables over time — values in kEUR · Avg. Trade WC annotated per period
          </p>
        </div>

        <div className="flex items-center gap-2 flex-wrap">
          {/* Grain */}
          <div className="flex gap-1">
            {GRAIN_OPTIONS.map(opt => (
              <button
                key={opt.value}
                onClick={() => setGrain(opt.value)}
                className="px-2.5 py-1 rounded-md text-xs font-medium transition-all"
                style={{
                  background: grain === opt.value ? 'rgba(30,58,95,0.1)' : '#F4F6F9',
                  color:      grain === opt.value ? '#1E3A5F' : '#475569',
                  border:     `1px solid ${grain === opt.value ? 'rgba(30,58,95,0.25)' : '#E2E8F0'}`,
                }}
              >
                {opt.label}
              </button>
            ))}
          </div>

          {/* Pin */}
          {notesCtx && (
            <>
              <div className="w-px h-4" style={{ background: '#E2E8F0' }} />
              <button
                type="button"
                title="Pin chart to Action Notes"
                className={STATEMENT_TOOLBAR_ICON_BTN}
                style={STATEMENT_TOOLBAR_BTN_STYLE}
                onClick={async () => {
                  const snap = await notesCtx.pinChartById(WC_TIMELINE_CHART_ID)
                  if (snap) notesCtx.setToast('Open Action Notes to save — or use Pin chart in panel')
                  else notesCtx.setToast('No chart data to pin')
                }}
              >
                <Pin size={14} strokeWidth={1.75} />
              </button>
            </>
          )}
        </div>
      </div>
      {/* Chart */}
      <div className="px-4 pb-4 pt-4" style={{ minHeight: 300 }}>
        {loading && (
          <div className="flex items-center justify-center animate-pulse" style={{ height: 280 }}>
            <div className="h-full w-full rounded-lg" style={{ background: '#F4F6F9' }} />
          </div>
        )}
        {!loading && error && (
          <div className="flex flex-col items-center justify-center gap-3" style={{ height: 280 }}>
            <p className="text-xs font-medium text-center px-4" style={{ color: '#991B1B' }}>
              Working capital timeline could not be loaded — {error}
            </p>
            <button
              type="button"
              onClick={() => setRetryCount(c => c + 1)}
              className="rounded-md px-3 py-1 text-xs font-semibold transition-colors hover:opacity-80"
              style={{
                background: 'rgba(220,38,38,0.12)',
                color: '#B91C1C',
                border: '1px solid rgba(220,38,38,0.3)',
              }}
            >
              Retry
            </button>
          </div>
        )}
        {!loading && !error && !chartData.length && (
          <div className="flex items-center justify-center text-xs" style={{ height: 280, color: '#94A3B8' }}>
            No working capital data for this period
          </div>
        )}
        {!loading && !error && chartData.length > 0 && (
          <ResponsiveContainer width="100%" height={308}>
            <ComposedChart
              data={chartData}
              margin={{ top: 28, right: 24, left: 0, bottom: 0 }}
              barGap={0}
              barCategoryGap="15%"
              onClick={(e) => handleClick(e)}
            >
              <CartesianGrid vertical={false} stroke="#F1F5F9" />
              <XAxis
                dataKey="label"
                tick={({ x, y, payload, index }) => {
                  if (!sparseTick(index, chartData.length)) return <g />
                  return (
                    <text x={x} y={y + 12} textAnchor="middle" fontSize={11} fill="#94A3B8">
                      {payload.value}
                    </text>
                  )
                }}
                axisLine={false}
                tickLine={false}
                interval={0}
              />
              <YAxis
                tickFormatter={yFmt}
                tick={{ fontSize: 12, fill: '#94A3B8' }}
                axisLine={false}
                tickLine={false}
                width={52}
              />
              <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(30,58,95,0.04)' }} />

              {/* Average annotations — one midpoint label per sub-period */}
              {subPeriods[0] && <ReferenceLine x={subPeriods[0].midLabel} stroke="none" label={(p: any) => <AvgLabel {...p} avgKeur={subPeriods[0].avgTwcKeur} />} />}
              {subPeriods[1] && <ReferenceLine x={subPeriods[1].midLabel} stroke="none" label={(p: any) => <AvgLabel {...p} avgKeur={subPeriods[1].avgTwcKeur} />} />}
              {subPeriods[2] && <ReferenceLine x={subPeriods[2].midLabel} stroke="none" label={(p: any) => <AvgLabel {...p} avgKeur={subPeriods[2].avgTwcKeur} />} />}
              {/* One shared boundary line at each sub-period transition */}
              {subPeriods[1] && <ReferenceLine x={subPeriods[1].startLabel} stroke="#CBD5E1" strokeDasharray="4 3" strokeWidth={1} strokeOpacity={0.8} />}
              {subPeriods[2] && <ReferenceLine x={subPeriods[2].startLabel} stroke="#CBD5E1" strokeDasharray="4 3" strokeWidth={1} strokeOpacity={0.8} />}

              {/* Stacked bars */}
              <Bar dataKey="inventories"       name="Inventories"       stackId="wc" fill={COLORS.inventories}       radius={[0, 0, 0, 0]} maxBarSize={30} cursor="pointer" />
              <Bar dataKey="trade_receivables" name="Trade Receivables" stackId="wc" fill={COLORS.trade_receivables} radius={[0, 0, 0, 0]} maxBarSize={30} cursor="pointer" />
              <Bar dataKey="trade_payables"    name="Trade Payables"    stackId="wc" fill={COLORS.trade_payables}    radius={[0, 0, 0, 0]} maxBarSize={30} cursor="pointer" />
              <Bar dataKey="other_wc"          name="Other WC"          stackId="wc" fill={COLORS.other_wc}          radius={[2, 2, 0, 0]} maxBarSize={30} cursor="pointer" />

              {/* Lines */}
              <Line dataKey="twc" name="Trade WC" type="monotone" stroke={COLORS.twc} strokeWidth={2} dot={false} activeDot={{ r: 4 }} />
              <Line dataKey="nwc" name="Net WC"   type="monotone" stroke={COLORS.nwc} strokeWidth={2} dot={false} activeDot={{ r: 4 }} strokeDasharray="5 3" />

              <Legend content={<ChartLegend />} />
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>

      {!loading && chartData.length > 0 && (
        <p className="px-4 pb-3 text-xs" style={{ color: '#CBD5E1' }}>
          · Click a bar or point to drill into underlying bookings
        </p>
      )}
    </motion.div>
  )
}
