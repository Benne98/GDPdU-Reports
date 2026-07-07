import {
  Bar,
  CartesianGrid,
  ComposedChart,
  LabelList,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { SalesDimensionPerformanceResponse } from '../../../lib/api'
import { fmtChartKpi } from '../../../lib/fmt'
import { SalesChartTooltipCard, salesChartTooltipProps } from './SalesChartTooltip'
import {
  CHART_AXIS_TICK_STYLE,
  CHART_TICK_STYLE,
} from './salesChartTypography'
import {
  BRAND,
  SALES_CHART_CURSOR,
  SALES_CHART_GRID_PROPS,
} from './salesChartTheme'

type ChartRow = {
  name: string
  actual: number
  prior: number
  plan: number | null
  delta_prior: number | null
  delta_plan: number | null
  has_plan: boolean
}

type Props = {
  data: SalesDimensionPerformanceResponse
}

function fmtValue(v: number, unit: 'keur' | 'units'): string {
  if (unit === 'units') return v.toLocaleString('de-DE', { maximumFractionDigits: 0 })
  return fmtChartKpi(v)
}

function deltaColor(d: number | null | undefined): string {
  if (d == null || Math.abs(d) < 0.05) return '#94A3B8'
  return d > 0 ? '#059669' : '#DC2626'
}

function fmtDeltaSmall(d: number | null | undefined, unit: 'keur' | 'units'): string {
  if (d == null) return ''
  const sign = d > 0 ? '+' : ''
  const abs = unit === 'units'
    ? d.toLocaleString('de-DE', { maximumFractionDigits: 0 })
    : fmtChartKpi(d)
  return `${sign}${abs}`
}

function BarTopLabel({
  x = 0,
  y = 0,
  width = 0,
  value,
  index = 0,
  payload,
  chartRows,
  valueUnit,
  priorShortLabel,
}: {
  x?: number
  y?: number
  width?: number
  value?: number
  index?: number
  payload?: ChartRow
  chartRows: ChartRow[]
  valueUnit: 'keur' | 'units'
  priorShortLabel: string
}) {
  const row = payload ?? chartRows[index ?? 0]
  if (!row || value == null) return null
  const cx = x + width / 2
  const lines: { text: string; color: string }[] = [
    { text: fmtValue(Number(value), valueUnit), color: BRAND.navy },
  ]
  if (row.delta_prior != null) {
    lines.push({
      text: `Δ ${fmtDeltaSmall(row.delta_prior, valueUnit)} vs ${priorShortLabel}`,
      color: deltaColor(row.delta_prior),
    })
  }
  if (row.has_plan && row.delta_plan != null) {
    lines.push({
      text: `Δ ${fmtDeltaSmall(row.delta_plan, valueUnit)} vs Plan`,
      color: deltaColor(row.delta_plan),
    })
  }
  return (
    <g>
      {lines.map((ln, i) => (
        <text
          key={i}
          x={cx}
          y={y - 8 - (lines.length - 1 - i) * 11}
          textAnchor="middle"
          fontSize={i === 0 ? 10 : 8}
          fontWeight={i === 0 ? 600 : 500}
          fill={ln.color}
        >
          {ln.text}
        </text>
      ))}
    </g>
  )
}

function ChartTooltip({
  active,
  payload,
  label,
  valueUnit,
  priorLabel,
  periodLabel,
}: {
  active?: boolean
  payload?: Array<{ name: string; value: number; color: string; dataKey: string }>
  label?: string
  valueUnit: 'keur' | 'units'
  priorLabel: string
  periodLabel: string
}) {
  if (!active || !payload?.length) return null
  const rows = payload
    .filter(p => p.value != null && (p.dataKey !== 'plan' || p.value > 0))
    .map(p => ({
      label: p.dataKey === 'actual' ? `Ist (${periodLabel})` : p.dataKey === 'prior' ? priorLabel : 'Plan',
      value: fmtValue(p.value, valueUnit) + (valueUnit === 'keur' ? ' kEUR' : ''),
      color: p.color,
    }))
  return <SalesChartTooltipCard title={String(label ?? '')} rows={rows} />
}

export default function SalesDimensionPerformanceChart({ data }: Props) {
  const chartRows: ChartRow[] = data.chart.segments.map(s => ({
    name: s.name,
    actual: s.actual,
    prior: s.prior,
    plan: s.has_plan && s.plan != null ? s.plan : null,
    delta_prior: s.delta_prior,
    delta_plan: s.delta_plan,
    has_plan: s.has_plan,
  }))

  const hasPlan = chartRows.some(r => r.has_plan && r.plan != null)
  const unit = data.value_unit
  const priorLabel = data.prior_label
  const periodLabel = data.period_label

  return (
    <div style={{ width: '100%', height: 330, minHeight: 330 }}>
      <ResponsiveContainer width="100%" height={330}>
        <ComposedChart data={chartRows} margin={{ top: 44, right: 12, left: 4, bottom: 52 }} barGap={-18}>
          <CartesianGrid {...SALES_CHART_GRID_PROPS} />
          <XAxis
            dataKey="name"
            tick={CHART_TICK_STYLE}
            axisLine={false}
            tickLine={false}
            interval={0}
            angle={-32}
            textAnchor="end"
            height={56}
          />
          <YAxis
            tick={CHART_AXIS_TICK_STYLE}
            axisLine={false}
            tickLine={false}
            width={52}
            tickFormatter={v => fmtValue(v, unit)}
          />
          <Tooltip
            content={(
              <ChartTooltip
                valueUnit={unit}
                priorLabel={priorLabel}
                periodLabel={periodLabel}
              />
            )}
            cursor={SALES_CHART_CURSOR}
            {...salesChartTooltipProps}
          />
          <Legend
            wrapperStyle={{ fontSize: 12, paddingTop: 4 }}
            iconType="square"
            iconSize={9}
            formatter={(val: string) => val}
          />
          <Bar
            dataKey="prior"
            name={priorLabel}
            fill={BRAND.navy}
            fillOpacity={0.22}
            maxBarSize={36}
            radius={[4, 4, 0, 0]}
            isAnimationActive={false}
          />
          <Bar
            dataKey="actual"
            name={`Ist (${periodLabel})`}
            fill={BRAND.navy}
            maxBarSize={36}
            radius={[4, 4, 0, 0]}
            isAnimationActive={false}
          >
            <LabelList
              dataKey="actual"
              position="top"
              content={((props: unknown) => (
                <BarTopLabel
                  {...(props as object)}
                  chartRows={chartRows}
                  valueUnit={unit}
                  priorShortLabel={priorLabel}
                />
              )) as never}
            />
          </Bar>
          {hasPlan && (
            <Line
              type="monotone"
              dataKey="plan"
              name="Plan"
              stroke={BRAND.teal}
              strokeWidth={2.5}
              dot={{ r: 4, fill: BRAND.teal, strokeWidth: 0 }}
              connectNulls={false}
              isAnimationActive={false}
            />
          )}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}
