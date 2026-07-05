/**
 * Churn Bridge — waterfall chart; columns align with dimension table below.
 */
import {
  ComposedChart, Bar, XAxis, YAxis, CartesianGrid,
  Tooltip, ResponsiveContainer, Cell, LabelList,
} from 'recharts'
import { SalesChurnBridgeResponse } from '../../lib/api'
import { useChartLoadReporter } from '../../hooks/useChartLoadReporter'
import { fmtChartKpi } from '../../lib/fmt'
import SalesSideDrawer from './SalesSideDrawer'
import {
  buildChurnTableColumns,
  churnGridTemplate,
  type ChurnComponentKey,
} from './analytics/churnBridgeLayout'

const C = {
  total:      '#1E3A5F',
  new:        '#14B8A6',
  upsell:     '#3B82F6',
  cross_sell: '#84CC16',
  downsell:   '#F97316',
  lost:       '#EF4444',
}

interface WEntry {
  name:       string
  base:       number
  value:      number
  rawValue:   number
  color:      string
  isTotal:    boolean
  component?: ChurnComponentKey
  bridgeIdx?: number
}

function buildEntries(data: SalesChurnBridgeResponse): WEntry[] {
  const entries: WEntry[] = []
  const [pmLabel, cmLabel] = data.periods
  const [pmTotal, cmTotal] = data.period_totals

  // PM total bar
  entries.push({
    name: pmLabel ?? 'PM',
    base: 0,
    value: pmTotal ?? 0,
    rawValue: pmTotal ?? 0,
    color: C.total,
    isTotal: true,
  })

  // Bridge component bars (single PM→CM transition)
  const bridge = data.bridge
  if (bridge) {
    let cursor = pmTotal ?? 0
    const deltas: Array<{ key: ChurnComponentKey; v: number }> = [
      { key: 'new', v: bridge.new },
      { key: 'upsell', v: bridge.upsell },
      { key: 'cross_sell', v: bridge.cross_sell },
      { key: 'downsell', v: bridge.downsell },
      { key: 'lost', v: bridge.lost },
    ]
    deltas.forEach(({ key, v }) => {
      if (v === 0) return
      entries.push({
        name: key === 'cross_sell' ? 'Cross' : key.charAt(0).toUpperCase() + key.slice(1),
        base: v >= 0 ? cursor : cursor + v,
        value: Math.abs(v),
        rawValue: v,
        color: C[key],
        isTotal: false,
        component: key,
        bridgeIdx: 0,
      })
      cursor += v
    })
  }

  // CM total bar
  entries.push({
    name: cmLabel ?? 'CM',
    base: 0,
    value: cmTotal ?? 0,
    rawValue: cmTotal ?? 0,
    color: C.total,
    isTotal: true,
  })

  return entries
}

function DrillPanel({
  title, rows, onClose,
}: {
  title: string
  rows: Array<{ name: string; from_rev_keur: number; to_rev_keur: number; delta_keur: number }>
  onClose: () => void
}) {
  return (
    <SalesSideDrawer open onClose={onClose}>
      <div className="flex items-center justify-between px-5 py-4 border-b shrink-0" style={{ borderColor: '#E2E8F0' }}>
        <h3 className="text-sm font-semibold" style={{ color: '#1E3A5F' }}>{title}</h3>
        <button type="button" onClick={onClose} className="text-xs px-2 py-1 rounded" style={{ color: '#64748B' }}>✕</button>
      </div>
      <div className="flex-1 overflow-y-auto p-4" data-drawer-scroll>
        {rows.length === 0 ? (
          <p className="text-xs text-center py-8" style={{ color: '#94A3B8' }}>No data</p>
        ) : (
          <table className="w-full text-xs">
            <thead>
              <tr style={{ background: '#F8FAFC' }}>
                <th className="text-left px-2 py-2 font-semibold" style={{ color: '#475569' }}>Customer</th>
                <th className="text-right px-2 py-2 font-semibold" style={{ color: '#475569' }}>From</th>
                <th className="text-right px-2 py-2 font-semibold" style={{ color: '#475569' }}>To</th>
                <th className="text-right px-2 py-2 font-semibold" style={{ color: '#475569' }}>Δ</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((r, i) => (
                <tr key={i} style={{ borderBottom: '1px solid #F8FAFC' }} className="hover:bg-slate-50">
                  <td className="px-2 py-1.5 font-medium" style={{ color: '#334155' }}>{r.name}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums" style={{ color: '#64748B' }}>{fmtChartKpi(r.from_rev_keur)}</td>
                  <td className="px-2 py-1.5 text-right tabular-nums" style={{ color: '#64748B' }}>{fmtChartKpi(r.to_rev_keur)}</td>
                  <td
                    className="px-2 py-1.5 text-right tabular-nums font-semibold"
                    style={{ color: r.delta_keur >= 0 ? '#15803D' : '#DC2626' }}
                  >
                    {(r.delta_keur >= 0 ? '+' : '') + fmtChartKpi(r.delta_keur)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>
    </SalesSideDrawer>
  )
}

const ChurnTooltip = ({ active, payload, label }: { active?: boolean; payload?: { payload?: WEntry }[]; label?: string }) => {
  if (!active || !payload?.length) return null
  const e = payload[0]?.payload
  if (!e) return null
  return (
    <div
      className="rounded-lg shadow-lg px-3 py-2 text-xs"
      style={{ background: '#1E2D40', border: '1px solid rgba(255,255,255,0.08)', color: '#E2E8F0', minWidth: 130 }}
    >
      <div className="font-semibold mb-1" style={{ color: '#F8FAFC' }}>{label}</div>
      <div className="flex justify-between gap-4">
        <span style={{ color: '#94A3B8' }}>{e.isTotal ? 'ARR' : 'Δ ARR'}</span>
        <span className="font-semibold tabular-nums">
          {e.isTotal ? fmtChartKpi(e.rawValue) : (e.rawValue >= 0 ? '+' : '') + fmtChartKpi(e.rawValue)} kEUR
        </span>
      </div>
    </div>
  )
}

const LEGEND_ITEMS = [
  { color: C.total, label: 'Period total' },
  { color: C.new, label: 'New' },
  { color: C.upsell, label: 'Upsell' },
  { color: C.cross_sell, label: 'Cross-sell' },
  { color: C.downsell, label: 'Downsell' },
  { color: C.lost, label: 'Lost' },
]

interface Props {
  data:            SalesChurnBridgeResponse
  loading?:        boolean
  onDrillRequest?: (component: ChurnComponentKey, bridgeIdx: number) => void
  drillRows?:      Array<{ name: string; from_rev_keur: number; to_rev_keur: number; delta_keur: number }>
  drillTitle?:     string
  drillOpen?:      boolean
  onDrillClose?:   () => void
}

export default function SalesChurnBridge({
  data, loading,
  onDrillRequest, drillRows, drillTitle, drillOpen, onDrillClose,
}: Props) {
  const hasRevenue = (data.period_totals ?? []).some(t => Math.abs(t) > 0.01)
  const entries = buildEntries(data)
  const columns = buildChurnTableColumns(data)
  const gridTemplate = churnGridTemplate(columns)
  const timedOut = useChartLoadReporter('sales-churn-bridge', !!loading, null, data.period_totals.length > 0)
  if (timedOut) return null

  const handleClick = (entry: { activePayload?: { payload?: WEntry }[] }) => {
    const e = entry?.activePayload?.[0]?.payload
    if (!e || e.isTotal || !e.component || e.bridgeIdx === undefined) return
    onDrillRequest?.(e.component, e.bridgeIdx)
  }

  return (
    <>
      <div className="px-2 pt-2 pb-2">
        {loading ? (
          <div className="flex items-center justify-center h-48 text-xs" style={{ color: '#94A3B8' }}>Loading…</div>
        ) : !hasRevenue ? (
          <div className="flex items-center justify-center h-48 text-xs text-center px-6" style={{ color: '#94A3B8' }}>
            No revenue movements for this period. Try another month or grain.
          </div>
        ) : (
          <>
            <div className="grid min-w-0" style={{ gridTemplateColumns: gridTemplate }}>
              <div aria-hidden />
              <div className="min-w-0" style={{ gridColumn: '2 / -1' }}>
                <ResponsiveContainer width="100%" height={250}>
                  <ComposedChart
                    data={entries}
                    margin={{ top: 18, right: 8, left: 0, bottom: 4 }}
                    barCategoryGap="10%"
                    onClick={handleClick}
                    style={{ cursor: onDrillRequest ? 'pointer' : 'default' }}
                  >
                    <CartesianGrid vertical={false} strokeDasharray="3 3" stroke="#F1F5F9" />
                    <XAxis
                      dataKey="name"
                      tick={{ fontSize: 10, fill: '#64748B' }}
                      axisLine={false}
                      tickLine={false}
                      interval={0}
                    />
                    <YAxis
                      tickFormatter={v => fmtChartKpi(v)}
                      tick={{ fontSize: 11, fill: '#94A3B8' }}
                      axisLine={false}
                      tickLine={false}
                      width={48}
                    />
                    <Tooltip content={<ChurnTooltip />} cursor={{ fill: 'rgba(30,58,95,0.04)' }} />
                    <Bar dataKey="base" stackId="c" fill="transparent" isAnimationActive={false} />
                    <Bar dataKey="value" stackId="c" radius={[3, 3, 0, 0]} maxBarSize={36} isAnimationActive={false}>
                      {entries.map((e, i) => (
                        <Cell key={i} fill={e.color} opacity={e.isTotal ? 1 : 0.9} />
                      ))}
                      <LabelList
                        dataKey="rawValue"
                        position="top"
                        formatter={(v: number) => {
                          if (Math.abs(v) < 0.1) return ''
                          const e = entries.find(x => x.rawValue === v)
                          if (e?.isTotal) return fmtChartKpi(v)
                          return (v >= 0 ? '+' : '') + fmtChartKpi(v)
                        }}
                        style={{ fontSize: 9, fill: '#475569' }}
                      />
                    </Bar>
                  </ComposedChart>
                </ResponsiveContainer>
              </div>
            </div>
            <div className="px-2 pt-2 pb-1 flex justify-center flex-wrap gap-x-4 gap-y-2 w-full">
              {LEGEND_ITEMS.map(l => (
                <span key={l.label} className="flex items-center gap-1.5 text-xs" style={{ color: '#64748B' }}>
                  <span className="w-2.5 h-2.5 rounded-sm inline-block shrink-0" style={{ background: l.color }} />
                  {l.label}
                </span>
              ))}
            </div>
          </>
        )}
      </div>

      {drillOpen && drillRows && onDrillClose && (
        <DrillPanel title={drillTitle ?? 'Detail'} rows={drillRows} onClose={onDrillClose} />
      )}
    </>
  )
}
