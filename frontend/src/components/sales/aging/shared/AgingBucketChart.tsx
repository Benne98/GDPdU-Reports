import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import { fmtAmount } from '../../../../lib/fmt'
import { hasAgingChartData } from './agingDataUtils'
import type { AgingBand } from './types'

const COLORS = { grid: '#E8EDF3', axis: '#94A3B8', label: '#64748B' }

function formatAxisMoney(v: number) {
  const n = Number(v)
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (Math.abs(n) >= 1_000) return `${(n / 1_000).toFixed(0)}k`
  return fmtAmount(n)
}

function AgingTooltip({
  active,
  payload,
}: {
  active?: boolean
  payload?: { payload?: AgingBand }[]
}) {
  if (!active || !payload?.length) return null
  const row = payload[0]?.payload
  if (!row) return null
  return (
    <div
      className="rounded-xl px-4 py-3 shadow-xl text-xs"
      style={{
        background: 'linear-gradient(145deg, #1E293B 0%, #0F172A 100%)',
        border: '1px solid rgba(255,255,255,0.08)',
        minWidth: 160,
      }}
    >
      <p className="font-semibold text-sm mb-2" style={{ color: '#F8FAFC' }}>
        {row.label}
      </p>
      <div className="flex justify-between gap-6">
        <span style={{ color: '#94A3B8' }}>Open amount</span>
        <span className="font-bold tabular-nums" style={{ color: '#93C5FD' }}>
          {fmtAmount(row.amount)}
        </span>
      </div>
      <p className="text-[10px] mt-2" style={{ color: '#64748B' }}>
        Click bar for document drilldown
      </p>
    </div>
  )
}

export default function AgingBucketChart({
  series,
  totalBalance,
  selectedBand,
  onBandSelect,
  emptyMessage,
  gradIdPrefix,
  compact = false,
}: {
  series: AgingBand[]
  totalBalance: number
  selectedBand: string | null
  onBandSelect: (band: string) => void
  emptyMessage: string
  gradIdPrefix: string
  compact?: boolean
}) {
  const chartHeight = compact ? 220 : 280

  if (!hasAgingChartData(series, totalBalance)) {
    return (
      <div
        className="flex items-center justify-center text-sm"
        style={{ height: chartHeight, color: '#94A3B8' }}
      >
        {emptyMessage}
      </div>
    )
  }

  const gradBar = `${gradIdPrefix}GradBar`
  const gradBarActive = `${gradIdPrefix}GradBarActive`

  return (
    <ResponsiveContainer width="100%" height={chartHeight}>
      <BarChart
        data={series}
        margin={{ top: 4, right: 8, left: 0, bottom: 4 }}
        barCategoryGap={compact ? '14%' : '22%'}
        onClick={state => {
          const idx = state?.activeTooltipIndex
          if (idx != null && series[idx]) onBandSelect(series[idx].band)
        }}
      >
        <defs>
          <linearGradient id={gradBar} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#1E3A5F" stopOpacity={0.95} />
            <stop offset="100%" stopColor="#3B82F6" stopOpacity={0.75} />
          </linearGradient>
          <linearGradient id={gradBarActive} x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#2563EB" stopOpacity={1} />
            <stop offset="100%" stopColor="#60A5FA" stopOpacity={0.9} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={COLORS.grid} strokeDasharray="4 6" vertical={false} />
        <XAxis
          dataKey="label"
          tick={{ fontSize: 11, fill: COLORS.label, fontWeight: 500 }}
          tickLine={false}
          axisLine={{ stroke: COLORS.grid }}
          interval={0}
          dy={6}
        />
        <YAxis
          tick={{ fontSize: 11, fill: COLORS.axis }}
          tickLine={false}
          axisLine={false}
          tickFormatter={formatAxisMoney}
          width={56}
        />
        <Tooltip content={<AgingTooltip />} cursor={{ fill: 'rgba(30, 58, 95, 0.06)' }} />
        <Bar
          dataKey="amount"
          radius={[6, 6, 0, 0]}
          maxBarSize={compact ? 72 : 52}
          cursor="pointer"
        >
          {series.map(entry => (
            <Cell
              key={entry.band}
              fill={
                selectedBand === entry.band ? `url(#${gradBarActive})` : `url(#${gradBar})`
              }
              stroke={selectedBand === entry.band ? '#2563EB' : 'none'}
              strokeWidth={selectedBand === entry.band ? 1.5 : 0}
            />
          ))}
        </Bar>
      </BarChart>
    </ResponsiveContainer>
  )
}
