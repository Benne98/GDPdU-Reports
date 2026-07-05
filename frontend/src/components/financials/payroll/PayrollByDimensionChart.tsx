import {
  Bar, BarChart, CartesianGrid, LabelList, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import type { PersonnelDimension, PersonnelMovementsResponse } from '../../../lib/api'
import { fmtChartKpi, fmtPct } from '../../../lib/fmt'
import SalesAnalyticsChartShell from '../../sales/analytics/SalesAnalyticsChartShell'
import { SalesChartTooltipCard, salesChartTooltipProps } from '../../sales/analytics/SalesChartTooltip'
import { BRAND, SALES_CHART_BODY_CLASS, SALES_CHART_GRID_PROPS } from '../../sales/analytics/salesChartTheme'
import { CHART_AXIS_TICK_STYLE, CHART_TICK_STYLE } from '../../sales/analytics/salesChartTypography'
import { DIMENSION_OPTIONS } from './payrollColumnRegistry'

const PRIOR_BAR_FILL = '#CBD5E1'
const CURRENT_BAR_FILL = BRAND.navy
const ROW_HEIGHT = 56
const MIN_CHART_HEIGHT = 280

type Row = PersonnelMovementsResponse['charts']['payroll_by_dimension'][number]

type Props = {
  rows: Row[]
  dimension: PersonnelDimension
  onDimensionChange: (d: PersonnelDimension) => void
  anchorLabel: string
  priorLabel: string
  loading?: boolean
}

function PayrollValueLabel({ data }: { data: Row[] }) {
  return (
    <LabelList
      dataKey="anchor"
      position="right"
      content={({ x, y, width, height, index }) => {
        const row = data[index as number]
        if (!row || x == null || y == null || width == null || height == null) return null
        const labelX = Number(x) + Number(width) + 8
        const labelY = Number(y) + Number(height) / 2
        const delta = row.delta_pct
        return (
          <text x={labelX} y={labelY} dominantBaseline="middle" fontSize={10} fontWeight={600}>
            <tspan fill="#111827">{fmtChartKpi(row.anchor)}</tspan>
            {delta != null && (
              <tspan fill={delta >= 0 ? '#16A34A' : '#DC2626'} dx={8}>
                {delta >= 0 ? `+${fmtPct(delta)}` : fmtPct(delta)}
              </tspan>
            )}
          </text>
        )
      }}
    />
  )
}

export default function PayrollByDimensionChart({
  rows, dimension, onDimensionChange, anchorLabel, priorLabel, loading,
}: Props) {
  const data = rows.slice(0, 10)
  const dimLabel = DIMENSION_OPTIONS.find(d => d.id === dimension)?.label ?? dimension
  const chartHeight = Math.max(MIN_CHART_HEIGHT, data.length * ROW_HEIGHT)

  return (
    <SalesAnalyticsChartShell
      title="Payroll by dimension"
      subtitle={`kEUR · by ${dimLabel} · ${anchorLabel} vs ${priorLabel}`}
      actions={(
        <select
          className="text-xs border border-slate-200 rounded-md px-2 py-1 bg-white"
          value={dimension}
          onChange={e => onDimensionChange(e.target.value as PersonnelDimension)}
        >
          {DIMENSION_OPTIONS.map(o => (
            <option key={o.id} value={o.id}>{o.label}</option>
          ))}
        </select>
      )}
      compactHeader
    >
      <div className={SALES_CHART_BODY_CLASS}>
        {loading ? (
          <div
            className="flex items-center justify-center text-xs"
            style={{ color: BRAND.textMuted, height: chartHeight }}
          >
            Loading…
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={chartHeight}>
            <BarChart
              layout="vertical"
              data={data}
              margin={{ left: 4, right: 108, top: 12, bottom: 8 }}
              barCategoryGap="18%"
              barGap={6}
            >
              <CartesianGrid {...SALES_CHART_GRID_PROPS} horizontal={false} />
              <XAxis
                type="number"
                tick={CHART_AXIS_TICK_STYLE}
                axisLine={false}
                tickLine={false}
                tickFormatter={v => fmtChartKpi(Number(v))}
              />
              <YAxis
                type="category"
                dataKey="label"
                width={130}
                tick={CHART_TICK_STYLE}
                axisLine={false}
                tickLine={false}
              />
              <Tooltip
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null
                  const r = payload[0]?.payload as Row
                  return (
                    <SalesChartTooltipCard
                      title={r.label}
                      rows={[
                        { label: anchorLabel, value: fmtChartKpi(r.anchor), color: CURRENT_BAR_FILL },
                        { label: priorLabel, value: fmtChartKpi(r.prior), color: PRIOR_BAR_FILL },
                        ...(r.delta_pct != null
                          ? [{
                              label: 'YoY',
                              value: r.delta_pct >= 0 ? `+${fmtPct(r.delta_pct)}` : fmtPct(r.delta_pct),
                              color: r.delta_pct >= 0 ? '#16A34A' : '#DC2626',
                            }]
                          : []),
                      ]}
                    />
                  )
                }}
                {...salesChartTooltipProps}
              />
              <Legend
                wrapperStyle={{ fontSize: 11, paddingTop: 4 }}
                formatter={value => <span style={{ color: BRAND.textSecondary }}>{value}</span>}
              />
              <Bar
                dataKey="prior"
                name={priorLabel}
                fill={PRIOR_BAR_FILL}
                radius={[0, 3, 3, 0]}
                maxBarSize={32}
              />
              <Bar
                dataKey="anchor"
                name={anchorLabel}
                fill={CURRENT_BAR_FILL}
                radius={[0, 4, 4, 0]}
                maxBarSize={32}
              >
                <PayrollValueLabel data={data} />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </SalesAnalyticsChartShell>
  )
}
