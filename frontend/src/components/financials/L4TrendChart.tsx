import { useState, useEffect, useMemo } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, LabelList,
} from 'recharts'
import { motion } from 'framer-motion'
import { Pin } from 'lucide-react'
import {
  api, FinancialStatementResponse, FinancialStatementRow,
  L4TrendPoint,
} from '../../lib/api'
import { FinancialsDrillOpen } from './FinancialStatementTable'
import { fmtChartKpi, fmtPct } from '../../lib/fmt'
import { PAGE_CHART_ATTR } from '../../hooks/usePageChartKeyboardNav'
import { useChartLoadReporter } from '../../hooks/useChartLoadReporter'
import { useOptionalActionNotesContext } from '../action-notes/ActionNotesContext'
import { captureChartByTarget } from '../../lib/actionNotes/chartCapture'
import { STATEMENT_TOOLBAR_ICON_BTN, STATEMENT_TOOLBAR_BTN_STYLE } from './statement-two-view/statementToolbarButton'
// ─── Types ────────────────────────────────────────────────────────────────────

type Grain = 'year' | 'quarter' | 'month'

interface L4Position {
  label:   string
  level_2: string
  level_3: string
  level_4: string
}

interface ChartPoint extends L4TrendPoint {
  currentKeur:  number
  previousKeur: number
}

const GRAIN_OPTIONS: { value: Grain; label: string }[] = [
  { value: 'year',    label: 'Month' },
  { value: 'quarter', label: 'Week' },
  { value: 'month',   label: 'Day' },
]

const STATEMENT_TITLES: Record<string, string> = {
  pl: 'Income Statement',
  bs: 'Balance Sheet',
  cf: 'Cash Flow',
  wc: 'Working Capital',
}

// ─── Extract L4 positions from statement rows ─────────────────────────────────

function extractPositions(rows: FinancialStatementRow[], stmt: string): L4Position[] {
  const seen = new Set<string>()
  const result: L4Position[] = []

  function walk(rows: FinancialStatementRow[]) {
    for (const row of rows) {
      if (row.row_kind !== 'title' && row.row_kind !== 'account' && row.drill) {
        let l2 = ''
        let l3 = ''
        let l4 = ''

        if (stmt === 'cf') {
          l2 = row.drill.cf_l11_1 || ''
          l3 = row.drill.cf_l11_2 || ''
          l4 = row.drill.cf_mapping || ''
        } else {
          l2 = row.drill.level_2 || ''
          l3 = row.drill.level_3 || ''
          l4 = row.drill.level_4 || ''
        }

        if ((l3 || l4) && row.label && row.label !== '—') {
          const key = `${l2}|${l3}|${l4}`
          if (!seen.has(key)) {
            seen.add(key)
            result.push({ label: row.label, level_2: l2, level_3: l3, level_4: l4 })
          }
        }
      }
      if (row.children?.length) walk(row.children)
    }
  }

  walk(rows)
  return result
}

// ─── Custom label ─────────────────────────────────────────────────────────────

function CurrentLabel({ x = 0, y = 0, width = 0, height = 0, value = 0, index = 0, data }: any) {
  const d: ChartPoint | undefined = data[index]
  if (!d) return null
  const kv    = fmtChartKpi(value)
  const delta = d.delta_pct
  const hasD  = delta !== null && delta !== undefined && isFinite(delta)
  const dColor = !hasD ? '#94A3B8' : delta >= 0 ? '#10B981' : '#DC2626'
  const cx = x + width / 2
  // Place both labels ABOVE the bar's topmost pixel (min of the rect edges). For a
  // negative (downward) bar that is the zero line, so the label floats above the
  // x-axis — never over the bar and never below the 0-line. Positive bars keep the
  // label above the value. Using min() is robust to Recharts' rect sign convention.
  const top = Math.min(y, y + height)
  return (
    <g>
      <text x={cx} y={top - (hasD ? 20 : 8)} textAnchor="middle" fontSize={11} fontWeight="600" fill="#111827">
        {kv}
      </text>
      {hasD && (
        <text x={cx} y={top - 7} textAnchor="middle" fontSize={11} fill={dColor}>
          {fmtPct(delta)}
        </text>
      )}
    </g>
  )
}

// ─── Custom tooltip ───────────────────────────────────────────────────────────

function ChartTooltip({ active, payload, label }: any) {
  if (!active || !payload?.length) return null
  const cur  = payload.find((p: any) => p.dataKey === 'currentKeur')?.value  ?? 0
  const prev = payload.find((p: any) => p.dataKey === 'previousKeur')?.value ?? 0
  return (
    <div className="rounded-lg px-3 py-2 text-xs shadow-lg" style={{ background: '#1E3A5F', color: '#F8FAFC', minWidth: 140 }}>
      <div className="font-semibold mb-1">{label}</div>
      <div className="flex justify-between gap-4">
        <span style={{ color: '#94A3B8' }}>Current</span>
        <span className="font-medium">{fmtChartKpi(cur)}</span>
      </div>
      <div className="flex justify-between gap-4">
        <span style={{ color: '#94A3B8' }}>Prior year</span>
        <span className="font-medium">{fmtChartKpi(prev)}</span>
      </div>
    </div>
  )
}

// ─── Component ────────────────────────────────────────────────────────────────

interface L4TrendChartProps {
  statement: 'pl' | 'bs' | 'cf' | 'wc'
  year:      number
  month:     number
  entity?:   string
  data:      FinancialStatementResponse | null
  statementLoading?: boolean
  onDrill:   (d: FinancialsDrillOpen) => void
}

export default function L4TrendChart({
  statement, year, month, entity, data, statementLoading = false, onDrill,
}: L4TrendChartProps) {
  const [grain, setGrain] = useState<Grain>('year')
  const [chartData, setChartData] = useState<ChartPoint[]>([])
  const [loading, setLoading] = useState(false)
  const [prevLabel, setPrevLabel] = useState('')
  const positions = useMemo(
    () => (data?.rows ? extractPositions(data.rows, statement) : []),
    [data, statement],
  )

  const [selectedIdx, setSelectedIdx] = useState(0)
  const pos = positions[selectedIdx] ?? null
  const notesCtx = useOptionalActionNotesContext()
  const chartTargetId = `${statement}-l4-trend-chart`

  // Reset selection when tab/data changes
  useEffect(() => { setSelectedIdx(0) }, [statement, data])

  // ─── Chart pin registration ────────────────────────────────────────────────
  useEffect(() => {
    if (!notesCtx) return
    if (!positions.length) {
      notesCtx.unregisterChartCandidate(chartTargetId)
      return
    }
    notesCtx.registerChartCandidate({
      id: chartTargetId,
      label: `${STATEMENT_TITLES[statement] ?? statement.toUpperCase()} — position trend`,
      description: 'Position trend chart',
      capture: () => captureChartByTarget(chartTargetId),
      viewState: { grain, selected_position: pos?.label },
    })
    return () => notesCtx.unregisterChartCandidate(chartTargetId)
  }, [notesCtx, chartTargetId, positions.length, grain, pos?.label, statement])

  const endpointMap: Record<string, string> = {
    pl: 'pl-statement', bs: 'balance-sheet', cf: 'cash-flow', wc: 'working-capital',
  }
  const stmtPath = endpointMap[statement]

  useEffect(() => {
    if (!pos) { setChartData([]); return }
    setLoading(true)
    api.financialsL4Trend(
      stmtPath, year, month, grain,
      pos.level_2, pos.level_3, pos.level_4, entity,
    ).then(res => {
      setPrevLabel(res.prev_label)
      setChartData(res.series.map(p => ({
        ...p,
        currentKeur:  p.current  / 1_000,
        previousKeur: p.previous / 1_000,
      })))
    }).catch(() => setChartData([]))
      .finally(() => setLoading(false))
  }, [stmtPath, year, month, grain, pos, entity])

  // Subheader: describe the delta for the last data point
  const subheader = useMemo(() => {
    if (!pos || !chartData.length) return null
    const last = chartData[chartData.length - 1]
    if (last.delta_pct === null || last.delta_pct === undefined) return null
    const dir = last.delta_pct >= 0 ? 'up' : 'down'
    const abs = Math.abs(last.delta_pct)
    return `${pos.label} is ${dir} ${abs.toFixed(1)}% vs prior year${prevLabel ? ` (${prevLabel})` : ''}`
  }, [pos, chartData, prevLabel])

  const yFmt = (v: number) =>
    Math.abs(v) >= 1000
      ? `${(v / 1000).toLocaleString('de-DE', { maximumFractionDigits: 1 })}M`
      : v.toLocaleString('de-DE', { maximumFractionDigits: 0 })

  const renderLabel = (props: any) => <CurrentLabel {...props} data={chartData} />

  const handleBarClick = (chartEventData: any) => {
    if (!pos || !chartEventData?.activePayload?.length) return
    const pt: ChartPoint = chartEventData.activePayload[0].payload
    onDrill({
      title: pos.label,
      statementType: statement === 'cf' ? 'PL' : (statement === 'pl' ? 'PL' : 'BS'),
      level2:  statement !== 'cf' ? pos.level_2 || undefined : undefined,
      level3:  statement !== 'cf' ? pos.level_3 || undefined : undefined,
      level4:  statement !== 'cf' ? pos.level_4 || undefined : undefined,
      dateFrom: pt.date_from,
      dateTo:   pt.date_to,
    })
  }

  const chartEnabled = statementLoading || positions.length > 0
  const timedOut = useChartLoadReporter(
    `fin-l4-${statement}`,
    statementLoading || (positions.length > 0 && loading),
    null,
    chartEnabled,
  )
  if (!positions.length || timedOut) return null

  return (
    <motion.div
      {...{ [PAGE_CHART_ATTR]: '' }}
      id={chartTargetId}
      data-expert-chart-target={chartTargetId}
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
          <h3 className="text-sm font-semibold" style={{ color: '#111827' }}>
            {STATEMENT_TITLES[statement]} — position trend
          </h3>
          {subheader ? (
            <p className="text-xs mt-0.5" style={{ color: '#64748B' }}>{subheader}</p>
          ) : (
            <p className="text-xs mt-0.5" style={{ color: '#94A3B8' }}>Values in kEUR vs same period prior year</p>
          )}
        </div>

        <div className="flex flex-wrap items-center gap-2">
          {/* Position dropdown */}
          <select
            value={selectedIdx}
            onChange={e => setSelectedIdx(Number(e.target.value))}
            className="text-xs rounded-lg px-2 py-1.5 pr-6 border appearance-none"
            style={{
              background: '#F8FAFC', color: '#1E3A5F',
              border: '1px solid #E2E8F0', maxWidth: 220,
            }}
          >
            {positions.map((p, i) => (
              <option key={i} value={i}>{p.label}</option>
            ))}
          </select>

          <div className="w-px h-4" style={{ background: '#E2E8F0' }} />

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
                  const snap = await notesCtx.pinChartById(chartTargetId)
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
      <div className="flex-1 px-4 pb-4 pt-5" style={{ minHeight: 220 }}>
        {loading && (
          <div className="h-full flex items-center justify-center animate-pulse">
            <div className="h-44 w-full rounded-lg" style={{ background: '#F4F6F9' }} />
          </div>
        )}
        {!loading && chartData.length === 0 && (
          <div className="h-44 flex items-center justify-center text-xs" style={{ color: '#94A3B8' }}>
            No data for this position and period
          </div>
        )}
        {!loading && chartData.length > 0 && (
          <ResponsiveContainer width="100%" height={260}>
            <BarChart
              data={chartData}
              margin={{ top: 34, right: 8, left: 0, bottom: 0 }}
              barGap={4}
              barCategoryGap="28%"
              onClick={handleBarClick}
            >
              <CartesianGrid vertical={false} stroke="#F1F5F9" />
              <XAxis
                dataKey="label"
                tick={{ fontSize: 12, fill: '#94A3B8' }}
                axisLine={false}
                tickLine={false}
                interval={grain === 'month' ? 4 : 0}
              />
              <YAxis
                tickFormatter={yFmt}
                tick={{ fontSize: 12, fill: '#94A3B8' }}
                axisLine={false}
                tickLine={false}
                width={48}
              />
              <Tooltip content={<ChartTooltip />} cursor={{ fill: 'rgba(30,58,95,0.04)' }} />

              <Bar dataKey="previousKeur" name="Prior year" fill="#CBD5E1" fillOpacity={0.75} radius={[2, 2, 0, 0]} maxBarSize={32} cursor="pointer" />
              <Bar dataKey="currentKeur" name="Current" fill="#1E3A5F" radius={[2, 2, 0, 0]} maxBarSize={26} cursor="pointer">
                <LabelList dataKey="currentKeur" content={renderLabel} />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>

      {!loading && chartData.length > 0 && (
        <div className="flex items-center gap-4 px-4 pb-3 text-xs" style={{ color: '#94A3B8' }}>
          <span className="flex items-center gap-1.5">
            <span className="inline-block w-3 h-2 rounded-sm" style={{ background: '#CBD5E1', opacity: 0.75 }} />
            Prior year
          </span>
          <span className="flex items-center gap-1.5">
            <span className="inline-block w-3 h-2 rounded-sm" style={{ background: '#1E3A5F' }} />
            Current period
          </span>
          <span style={{ color: '#CBD5E1' }}>· Click a bar to drill into bookings</span>
        </div>
      )}
    </motion.div>
  )
}
