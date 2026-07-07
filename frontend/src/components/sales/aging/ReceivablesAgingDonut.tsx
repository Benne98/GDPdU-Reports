import { useMemo } from 'react'
import { Cell, Label, Pie, PieChart, ResponsiveContainer, Tooltip } from 'recharts'
import type { ReceivablesAgingBand } from '../../../lib/api'
import { fmtAmount } from '../../../lib/fmt'
import { agingDonutSize } from './shared/agingChartSizes'
import { effectiveAgingTotal, hasAgingChartData } from './shared/agingDataUtils'

const SLICE_COLORS = ['#1E3A5F', '#2563EB', '#3B82F6', '#60A5FA', '#93C5FD']

function DonutTooltip({
  active,
  payload,
  total,
}: {
  active?: boolean
  payload?: { name?: string; value?: number }[]
  total: number
}) {
  if (!active || !payload?.length) return null
  const p = payload[0]
  const v = Number(p.value ?? 0)
  const pct = total > 0 ? Math.round((v / total) * 100) : 0
  return (
    <div
      className="rounded-xl px-3 py-2 shadow-xl text-xs"
      style={{
        background: 'linear-gradient(145deg, #1E293B 0%, #0F172A 100%)',
        border: '1px solid rgba(255,255,255,0.08)',
      }}
    >
      <p className="font-medium mb-1" style={{ color: '#F8FAFC' }}>
        {p.name}
      </p>
      <p className="font-bold tabular-nums" style={{ color: '#93C5FD' }}>
        {fmtAmount(v)} · {pct}%
      </p>
    </div>
  )
}

export default function ReceivablesAgingDonut({
  series,
  total,
  compact = false,
}: {
  series: ReceivablesAgingBand[]
  total: number
  compact?: boolean
}) {
  const { width, height, inner, outer } = agingDonutSize(compact)
  const data = useMemo(() => {
    const positive = series.filter(s => s.amount > 0)
    return positive.length > 0 ? positive : series
  }, [series])

  const displayTotal = effectiveAgingTotal(series, total)

  if (!hasAgingChartData(series, total)) {
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
    <div className="mx-auto" style={{ width }}>
      <ResponsiveContainer width={width} height={height}>
        <PieChart>
          <Pie
            data={data}
            dataKey="amount"
            nameKey="label"
            cx="50%"
            cy="50%"
            innerRadius={inner}
            outerRadius={outer}
            paddingAngle={2}
            stroke="#fff"
            strokeWidth={2}
          >
            {data.map((_, i) => (
              <Cell key={i} fill={SLICE_COLORS[i % SLICE_COLORS.length]} />
            ))}
            <Label
              content={({ viewBox }) => {
                if (!viewBox || !('cx' in viewBox)) return null
                const { cx, cy } = viewBox as { cx: number; cy: number }
                return (
                  <g>
                    <text
                      x={cx}
                      y={cy - 4}
                      textAnchor="middle"
                      fill="#1E3A5F"
                      fontSize={compact ? 11 : 14}
                      fontWeight={700}
                    >
                      {fmtAmount(displayTotal)}
                    </text>
                    {!compact && (
                      <text x={cx} y={cy + 12} textAnchor="middle" fill="#94A3B8" fontSize={11}>
                        total open
                      </text>
                    )}
                  </g>
                )
              }}
            />
          </Pie>
          <Tooltip content={<DonutTooltip total={displayTotal} />} />
        </PieChart>
      </ResponsiveContainer>
      {!compact && (
        <ul className="flex flex-wrap justify-center gap-x-4 gap-y-1 mt-2">
          {data.map((s, i) => (
            <li key={s.band} className="flex items-center gap-1.5 text-[10px]" style={{ color: '#64748B' }}>
              <span
                className="w-2 h-2 rounded-sm shrink-0"
                style={{ background: SLICE_COLORS[i % SLICE_COLORS.length] }}
              />
              {s.label}
            </li>
          ))}
        </ul>
      )}
    </div>
  )
}
