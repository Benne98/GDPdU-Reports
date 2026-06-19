import {
  Area,
  CartesianGrid,
  ComposedChart,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { PayablesTrendPoint } from '../../../../lib/api'
import { fmtAmount } from '../../../../lib/fmt'

const COLORS = { grid: '#E8EDF3', axis: '#94A3B8', label: '#64748B' }

function formatAxisMoney(v: number) {
  const n = Number(v)
  if (Math.abs(n) >= 1_000_000) return `${(n / 1_000_000).toFixed(1)}M`
  if (Math.abs(n) >= 1_000) return `${(n / 1_000).toFixed(0)}k`
  return fmtAmount(n)
}

function TrendTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean
  payload?: { dataKey?: string; value?: number; color?: string }[]
  label?: string
}) {
  if (!active || !payload?.length) return null
  const labels: Record<string, string> = {
    balance: 'Balance',
    overdue: 'Overdue',
    dpo_days: 'DPO',
    procurement_spend: 'Procurement spend',
    mom_variance_pct: 'MoM %',
  }
  return (
    <div
      className="rounded-xl px-4 py-3 shadow-xl text-xs"
      style={{
        background: 'linear-gradient(145deg, #1E293B 0%, #0F172A 100%)',
        border: '1px solid rgba(255,255,255,0.08)',
        minWidth: 180,
      }}
    >
      <p className="font-semibold text-sm mb-2" style={{ color: '#F8FAFC' }}>
        {label}
      </p>
      {payload.map(p => (
        <div key={p.dataKey} className="flex justify-between gap-4 mb-1">
          <span style={{ color: '#94A3B8' }}>{labels[p.dataKey ?? ''] ?? p.dataKey}</span>
          <span className="font-bold tabular-nums" style={{ color: p.color ?? '#93C5FD' }}>
            {p.dataKey === 'dpo_days'
              ? `${p.value} d`
              : p.dataKey === 'mom_variance_pct'
                ? `${p.value}%`
                : fmtAmount(Number(p.value ?? 0))}
          </span>
        </div>
      ))}
    </div>
  )
}

export default function PayablesAgingTrend({
  points,
  chartHeight = 260,
}: {
  points: PayablesTrendPoint[]
  chartHeight?: number
}) {
  if (!points.length) {
    return (
      <div className="flex items-center justify-center text-sm" style={{ color: '#94A3B8', height: chartHeight }}>
        No trend data
      </div>
    )
  }

  return (
    <ResponsiveContainer width="100%" height={chartHeight}>
      <ComposedChart data={points} margin={{ top: 12, right: 48, left: 4, bottom: 8 }}>
        <defs>
          <linearGradient id="recvTrendArea" x1="0" y1="0" x2="0" y2="1">
            <stop offset="0%" stopColor="#3B82F6" stopOpacity={0.25} />
            <stop offset="100%" stopColor="#3B82F6" stopOpacity={0.02} />
          </linearGradient>
        </defs>
        <CartesianGrid stroke={COLORS.grid} strokeDasharray="4 6" vertical={false} />
        <XAxis
          dataKey="label"
          tick={{ fontSize: 10, fill: COLORS.label }}
          tickLine={false}
          axisLine={{ stroke: COLORS.grid }}
        />
        <YAxis
          yAxisId="left"
          tick={{ fontSize: 10, fill: COLORS.axis }}
          tickLine={false}
          axisLine={false}
          tickFormatter={formatAxisMoney}
          width={52}
        />
        <YAxis
          yAxisId="right"
          orientation="right"
          tick={{ fontSize: 10, fill: '#D97706' }}
          tickLine={false}
          axisLine={false}
          width={40}
          tickFormatter={v => (String(v).includes('.') && Number(v) < 100 ? `${v}%` : `${v}d`)}
        />
        <Tooltip content={<TrendTooltip />} />
        <Area
          yAxisId="left"
          type="monotone"
          dataKey="balance"
          fill="url(#recvTrendArea)"
          stroke="#2563EB"
          strokeWidth={2}
          name="balance"
        />
        <Line
          yAxisId="left"
          type="monotone"
          dataKey="overdue"
          stroke="#F59E0B"
          strokeWidth={2}
          dot={false}
          name="overdue"
        />
        <Line
          yAxisId="left"
          type="monotone"
          dataKey="procurement_spend"
          stroke="#94A3B8"
          strokeWidth={1.5}
          strokeDasharray="5 5"
          dot={false}
          name="procurement_spend"
        />
        <Line
          yAxisId="right"
          type="monotone"
          dataKey="dpo_days"
          stroke="#64748B"
          strokeWidth={1.5}
          strokeDasharray="4 4"
          dot={{ r: 2, fill: '#64748B' }}
          name="dpo_days"
        />
        <Line
          yAxisId="right"
          type="monotone"
          dataKey="mom_variance_pct"
          stroke="#10B981"
          strokeWidth={1.5}
          dot={false}
          name="mom_variance_pct"
        />
      </ComposedChart>
    </ResponsiveContainer>
  )
}



