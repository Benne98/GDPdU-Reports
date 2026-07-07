/**
 * CountryRevenueChart — lightweight replacement for a choropleth world map.
 * Shows top 20 countries as horizontal bars with revenue, gross margin %, and YoY delta.
 * Uses Recharts (already bundled) — no CDN dependency, no heavy SVG topology.
 */
import { useMemo, useState } from 'react'
import {
  BarChart, Bar, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, Cell,
} from 'recharts'
import { SalesGeoCountry } from '../../lib/api'
import { fmtChartKpi } from '../../lib/fmt'

interface Props {
  data:    SalesGeoCountry[]
  loading: boolean
  selectedCountry?: string | null
  onCountryClick?: (country: string) => void
  /** Tooltip / badge label for the value metric (default: Revenue). */
  valueLabel?: string
  showGrossMargin?: boolean
  showYoY?: boolean
  emptyMessage?: string
  /** Extra tooltip lines (e.g. aging buckets on receivables map). */
  tooltipExtraRows?: (row: SalesGeoCountry) => { label: string; value: string; color?: string }[]
}

const CustomTooltip = ({
  active,
  payload,
  label,
  valueLabel = 'Revenue',
  showGrossMargin = true,
  showYoY = true,
  tooltipExtraRows,
}: any) => {
  if (!active || !payload?.length) return null
  const row = payload[0]?.payload as SalesGeoCountry
  if (!row) return null
  const extras = tooltipExtraRows?.(row) ?? []
  return (
    <div
      className="rounded-xl shadow-2xl px-4 py-3 text-xs"
      style={{ background: '#1E2D40', border: '1px solid rgba(255,255,255,0.10)', color: '#E2E8F0', minWidth: 190 }}
    >
      <div className="font-semibold text-sm mb-2" style={{ color: '#F8FAFC' }}>{label}</div>
      <div className="flex justify-between gap-6 mb-1">
        <span style={{ color: '#94A3B8' }}>{valueLabel}</span>
        <span className="font-semibold tabular-nums">{fmtChartKpi(row.revenue_keur)} kEUR</span>
      </div>
      {extras.map((line: { label: string; value: string; color?: string }) => (
        <div key={line.label} className="flex justify-between gap-6 mb-1">
          <span style={{ color: '#94A3B8' }}>{line.label}</span>
          <span className="font-semibold tabular-nums" style={{ color: line.color ?? '#F8FAFC' }}>
            {line.value}
          </span>
        </div>
      ))}
      {showGrossMargin && (
        <div className="flex justify-between gap-6 mb-1">
          <span style={{ color: '#94A3B8' }}>Gross Margin</span>
          <span className="font-semibold tabular-nums">{row.gross_margin_pct != null ? Number(row.gross_margin_pct).toFixed(1) : '—'}%</span>
        </div>
      )}
      {showYoY && (
        <div className="flex justify-between gap-6">
          <span style={{ color: '#94A3B8' }}>Δ vs PY</span>
          <span
            className="font-semibold tabular-nums"
            style={{ color: row.delta_keur >= 0 ? '#4ADE80' : '#F87171' }}
          >
            {row.delta_keur >= 0 ? '+' : ''}{fmtChartKpi(row.delta_keur)}
          </span>
        </div>
      )}
    </div>
  )
}

export default function WorldMap({
  data,
  loading,
  selectedCountry,
  onCountryClick,
  valueLabel = 'Revenue',
  showGrossMargin = true,
  showYoY = true,
  emptyMessage = 'No regional revenue data for the selected period',
  tooltipExtraRows,
}: Props) {
  const [showAll, setShowAll] = useState(false)

  const sorted = useMemo(
    () => [...data].sort((a, b) => b.revenue_keur - a.revenue_keur),
    [data],
  )
  const maxRev  = useMemo(() => sorted[0]?.revenue_keur ?? 1, [sorted])
  const visible = showAll ? sorted : sorted.slice(0, 15)
  const top3    = sorted.slice(0, 3)
  /** Fixed chart height so the page does not jump when country data loads */
  const chartHeight = 480

  const barColor = (rev: number): string => {
    const intensity = Math.pow(rev / maxRev, 0.45)
    const light = [203, 213, 225]
    const dark  = [30, 58, 95]
    const rgb   = light.map((lv, i) => Math.round(lv + (dark[i] - lv) * intensity))
    return `rgb(${rgb[0]},${rgb[1]},${rgb[2]})`
  }

  if (loading) {
    return (
      <div className="flex items-center justify-center text-xs" style={{ color: '#94A3B8', minHeight: chartHeight + 120 }}>
        Loading…
      </div>
    )
  }

  if (data.length === 0) {
    return (
      <div className="flex items-center justify-center text-xs" style={{ color: '#94A3B8', minHeight: chartHeight + 120 }}>
        {emptyMessage}
      </div>
    )
  }

  return (
    <div className="flex flex-col gap-4">
      {/* Top-3 highlight badges */}
      <div className="flex flex-wrap gap-2">
        {top3.map((c, i) => (
          <div
            key={c.country}
            className="flex items-center gap-2 px-3 py-2 rounded-xl text-xs"
            style={{
              background: i === 0 ? 'rgba(30,58,95,0.08)' : '#F4F6F9',
              border: `1px solid ${i === 0 ? 'rgba(30,58,95,0.30)' : '#E2E8F0'}`,
            }}
          >
            <span
              className="font-bold text-xs w-5 h-5 flex items-center justify-center rounded-full shrink-0"
              style={{ background: '#1E3A5F', color: '#FFFFFF', fontSize: 10 }}
            >
              {i + 1}
            </span>
            <div className="flex flex-col">
              <span className="font-semibold" style={{ color: '#1E3A5F' }}>{c.country}</span>
              <div className="flex items-center gap-2 mt-0.5">
                <span className="tabular-nums font-semibold" style={{ color: '#334155' }}>
                  {fmtChartKpi(c.revenue_keur)} kEUR
                </span>
                <span style={{ color: '#CBD5E1' }}>·</span>
                {showGrossMargin && (
                  <>
                    <span style={{ color: '#64748B' }}>GM {c.gross_margin_pct != null ? Number(c.gross_margin_pct).toFixed(1) : '—'}%</span>
                    <span style={{ color: '#CBD5E1' }}>·</span>
                  </>
                )}
                {showYoY && (
                  <span
                    className="font-medium"
                    style={{ color: c.delta_keur >= 0 ? '#15803D' : '#DC2626' }}
                  >
                    {c.delta_keur >= 0 ? '+' : ''}{fmtChartKpi(c.delta_keur)} YoY
                  </span>
                )}
              </div>
            </div>
          </div>
        ))}
      </div>

      {/* Horizontal bar chart — fixed height; scroll when showing all countries */}
      <div style={{ height: chartHeight, overflowY: showAll && sorted.length > 15 ? 'auto' : 'hidden' }}>
      <ResponsiveContainer width="100%" height={showAll ? Math.max(sorted.length * 32, chartHeight) : chartHeight}>
        <BarChart
          data={visible}
          layout="vertical"
          margin={{ top: 0, right: 80, left: 8, bottom: 0 }}
          barCategoryGap="20%"
        >
          <CartesianGrid horizontal={false} strokeDasharray="3 3" stroke="#F1F5F9" />
          <XAxis
            type="number"
            tickFormatter={v => fmtChartKpi(v)}
            tick={{ fontSize: 11, fill: '#94A3B8' }}
            axisLine={false} tickLine={false}
          />
          <YAxis
            type="category"
            dataKey="country"
            width={100}
            tick={{ fontSize: 12, fill: '#475569' }}
            axisLine={false} tickLine={false}
          />
          <Tooltip
            content={
              <CustomTooltip
                valueLabel={valueLabel}
                showGrossMargin={showGrossMargin}
                showYoY={showYoY}
                tooltipExtraRows={tooltipExtraRows}
              />
            }
            cursor={{ fill: 'rgba(30,58,95,0.04)' }}
          />
          <Bar
            dataKey="revenue_keur"
            radius={[0, 4, 4, 0]}
            maxBarSize={24}
            isAnimationActive={false}
            cursor={onCountryClick ? 'pointer' : undefined}
            onClick={(barData: { country?: string }) => {
              if (barData?.country && onCountryClick) onCountryClick(barData.country)
            }}
            label={{
              position: 'right',
              formatter: (v: number) => fmtChartKpi(v),
              style: { fontSize: 11, fill: '#64748B' },
            }}
          >
            {visible.map((entry, i) => (
              <Cell
                key={i}
                fill={
                  selectedCountry === entry.country
                    ? '#0F766E'
                    : barColor(entry.revenue_keur)
                }
                stroke={selectedCountry === entry.country ? '#0F766E' : undefined}
                strokeWidth={selectedCountry === entry.country ? 2 : 0}
              />
            ))}
          </Bar>
        </BarChart>
      </ResponsiveContainer>
      </div>

      {/* Show more button */}
      {sorted.length > 15 && (
        <button
          type="button"
          onClick={() => setShowAll(v => !v)}
          className="self-center text-xs font-medium px-4 py-1.5 rounded-lg transition-colors"
          style={{ background: '#F4F6F9', color: '#475569', border: '1px solid #E2E8F0' }}
        >
          {showAll ? 'Show fewer' : `Show all ${sorted.length} regions`}
        </button>
      )}

      {/* Legend */}
      <div className="flex items-center gap-2">
        <span className="text-xs" style={{ color: '#94A3B8' }}>Low</span>
        <div className="flex-1 h-1.5 rounded-full" style={{ background: 'linear-gradient(to right, #CBD5E1, #1E3A5F)' }} />
        <span className="text-xs" style={{ color: '#94A3B8' }}>High</span>
        <span className="text-xs font-medium ml-3" style={{ color: '#64748B' }}>
          Max: {fmtChartKpi(maxRev)} kEUR
        </span>
      </div>
    </div>
  )
}
