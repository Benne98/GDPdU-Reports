import { Cell, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'
import type { ReceivablesStatusSplit } from '../../../lib/api'
import { fmtAmount } from '../../../lib/fmt'
import { agingStatusDonutSize } from './shared/agingChartSizes'

const SEGMENTS = [
  { key: 'before_due' as const, label: 'Not yet due', color: '#3B82F6' },
  { key: 'overdue' as const, label: 'Overdue', color: '#F59E0B' },
]

export default function ReceivablesStatusDonut({
  split,
  compact = false,
}: {
  split: ReceivablesStatusSplit
  compact?: boolean
}) {
  const { width, height, inner, outer } = agingStatusDonutSize(compact)
  const total = split.before_due + split.overdue
  const data = SEGMENTS.map(s => ({
    name: s.label,
    value: split[s.key],
    color: s.color,
  })).filter(d => d.value > 0)

  if (total <= 0 || !data.length) {
    return (
      <div
        className="flex items-center justify-center text-sm mx-auto"
        style={{ width, height, color: '#94A3B8' }}
      >
        No data
      </div>
    )
  }

  return (
    <div className="relative mx-auto" style={{ width, height }}>
      <ResponsiveContainer width={width} height={height}>
        <PieChart>
          <Pie
            data={data}
            dataKey="value"
            nameKey="name"
            cx="50%"
            cy="50%"
            innerRadius={inner}
            outerRadius={outer}
            paddingAngle={2}
          >
            {data.map((d, i) => (
              <Cell key={i} fill={d.color} stroke="none" />
            ))}
          </Pie>
          <Tooltip
            formatter={(v: number) => fmtAmount(v)}
            contentStyle={{
              background: '#1E293B',
              border: '1px solid rgba(255,255,255,0.08)',
              borderRadius: 12,
              fontSize: 11,
            }}
          />
        </PieChart>
      </ResponsiveContainer>
      <div className="absolute inset-0 flex flex-col items-center justify-center pointer-events-none">
        <span className="text-[10px] uppercase tracking-wider" style={{ color: '#94A3B8' }}>
          Overdue
        </span>
        <span
          className="font-bold tabular-nums"
          style={{ color: '#D97706', fontSize: compact ? 14 : 18 }}
        >
          {split.overdue_pct}%
        </span>
      </div>
    </div>
  )
}
