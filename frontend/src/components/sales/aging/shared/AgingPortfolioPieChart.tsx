import { useMemo } from 'react'
import { Cell, Label, Pie, PieChart } from 'recharts'
import type { PieLabelRenderProps } from 'recharts'
import type { ReceivablesAgingBand, ReceivablesStatusSplit } from '../../../../lib/api'
import { bucketPieColor } from './agingPortfolioColors'
import { effectiveAgingTotal, hasAgingChartData } from './agingDataUtils'

const RADIAN = Math.PI / 180
const MIN_LABEL_PCT = 0.03

function shortBucketLabel(name: string): string {
  return name
    .replace(' days overdue', 'd')
    .replace('Not yet due', 'Not due')
    .replace('>180 days overdue', '>180d')
}

function makeSliceLabelRenderer(fontSize: number) {
  return function renderSliceLabel(props: PieLabelRenderProps) {
    const {
      cx = 0,
      cy = 0,
      midAngle = 0,
      outerRadius = 0,
      percent = 0,
      name = '',
      fill = '#64748B',
    } = props
    if (percent < MIN_LABEL_PCT) return null

    const cos = Math.cos(-Number(midAngle) * RADIAN)
    const sin = Math.sin(-Number(midAngle) * RADIAN)
    const cxN = Number(cx)
    const cyN = Number(cy)
    const outerN = Number(outerRadius)
    const sx = cxN + (outerN + 2) * cos
    const sy = cyN + (outerN + 2) * sin
    const mx = cxN + (outerN + 16) * cos
    const my = cyN + (outerN + 16) * sin
    const ex = mx + (cos >= 0 ? 1 : -1) * 12
    const ey = my
    const textAnchor = cos >= 0 ? 'start' : 'end'
    const pct = Math.round(percent * 100)
    const text = `${shortBucketLabel(String(name))} ${pct}%`

    return (
      <g>
        <path
          d={`M${sx},${sy}L${mx},${my}L${ex},${ey}`}
          stroke={fill}
          strokeWidth={1}
          fill="none"
          opacity={0.65}
        />
        <text
          x={ex + (cos >= 0 ? 2 : -2)}
          y={ey}
          textAnchor={textAnchor}
          dominantBaseline="central"
          fill="#475569"
          fontSize={fontSize}
          fontWeight={600}
        >
          {text}
        </text>
      </g>
    )
  }
}

/** Donut with callout labels — no panel background. */
export default function AgingPortfolioPieChart({
  series,
  total,
  statusSplit,
  compact = false,
}: {
  series: ReceivablesAgingBand[]
  total: number
  statusSplit?: ReceivablesStatusSplit
  compact?: boolean
}) {
  const displayTotal = effectiveAgingTotal(series, total)

  const chartData = useMemo(() => {
    return series
      .filter(s => s.amount > 0)
      .map((s, i) => ({
        band: s.band,
        name: s.label,
        value: s.amount,
        pct: displayTotal > 0 ? Math.round((s.amount / displayTotal) * 100) : 0,
        color: bucketPieColor(s.band, i),
      }))
  }, [series, displayTotal])

  if (!hasAgingChartData(series, total)) {
    return (
      <p className="text-[10px] py-2 text-center" style={{ color: '#94A3B8' }}>
        No portfolio data
      </p>
    )
  }

  const size = compact ? 168 : 280
  const innerR = compact ? 36 : 62
  const outerR = compact ? 56 : 98
  const chartW = size + (compact ? 88 : 140)

  return (
    <div className="shrink-0" style={{ width: chartW, height: size + 20 }}>
      <PieChart width={chartW} height={size + 20}>
        <Pie
          data={chartData}
          dataKey="value"
          nameKey="name"
          cx={chartW / 2}
          cy={size / 2 + 6}
          innerRadius={innerR}
          outerRadius={outerR}
          paddingAngle={1.5}
          stroke="#fff"
          strokeWidth={1.5}
          label={makeSliceLabelRenderer(compact ? 8 : 11)}
          labelLine={false}
          isAnimationActive={false}
        >
          {chartData.map(d => (
            <Cell key={d.band} fill={d.color} />
          ))}
          <Label
            content={({ viewBox }) => {
              if (!viewBox || !('cx' in viewBox)) return null
              const { cx, cy } = viewBox as { cx: number; cy: number }
              return (
                <g>
                  <text x={cx} y={cy - 10} textAnchor="middle" fill="#94A3B8" fontSize={compact ? 7 : 9} fontWeight={600}>
                    OVERDUE
                  </text>
                  <text x={cx} y={cy + 6} textAnchor="middle" fill="#E85D8A" fontSize={compact ? 12 : 18} fontWeight={700}>
                    {statusSplit?.overdue_pct ?? 0}%
                  </text>
                  <text x={cx} y={cy + 20} textAnchor="middle" fill="#CBD5E1" fontSize={compact ? 6 : 8}>
                    of open
                  </text>
                </g>
              )
            }}
          />
        </Pie>
      </PieChart>
    </div>
  )
}
