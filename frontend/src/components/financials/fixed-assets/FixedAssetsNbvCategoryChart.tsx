import {
  Bar, BarChart, CartesianGrid, LabelList, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { fmtChartKpi, fmtPct } from '../../../lib/fmt'
import SalesAnalyticsChartShell from '../../sales/analytics/SalesAnalyticsChartShell'
import { SalesChartTooltipCard, salesChartTooltipProps } from '../../sales/analytics/SalesChartTooltip'
import { BRAND, SALES_CHART_BODY_CLASS, SALES_CHART_GRID_PROPS } from '../../sales/analytics/salesChartTheme'
import { CHART_AXIS_TICK_STYLE, CHART_TICK_STYLE } from '../../sales/analytics/salesChartTypography'

const PRIOR_BAR_FILL = '#CBD5E1'
const CURRENT_BAR_FILL = BRAND.navy
const ROW_HEIGHT = 56
const MIN_CHART_HEIGHT = 360

type CategoryRow = {
  category: string
  nbv: number
  prior_nbv: number
  delta_pct: number | null
}

type Props = {
  rows: CategoryRow[]
  dimensionLabel: string
  anchorLabel: string
  priorLabel: string
  loading?: boolean
  actions?: React.ReactNode
}

function NbvValueLabel({ data }: { data: CategoryRow[] }) {
  return (
    <LabelList
      dataKey="nbv"
      position="right"
      content={({ x, y, width, height, index }) => {
        const row = data[index as number]
        if (!row || x == null || y == null || width == null || height == null) return null
        const labelX = Number(x) + Number(width) + 8
        const labelY = Number(y) + Number(height) / 2
        const delta = row.delta_pct
        return (
          <text x={labelX} y={labelY} dominantBaseline="middle" fontSize={10} fontWeight={600}>
            <tspan fill="#111827">{fmtChartKpi(row.nbv)}</tspan>
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

export default function FixedAssetsNbvCategoryChart({
  rows,
  dimensionLabel,
  anchorLabel,
  priorLabel,
  loading,
  actions,
}: Props) {
  const data = rows.slice(0, 8)
  const chartHeight = Math.max(MIN_CHART_HEIGHT, data.length * ROW_HEIGHT)

  return (
    <SalesAnalyticsChartShell
      title="NBV by category"
      subtitle={`kEUR · by ${dimensionLabel} · ${anchorLabel} vs ${priorLabel}`}
      compactHeader
      actions={actions}
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
                dataKey="category"
                width={148}
                tick={CHART_TICK_STYLE}
                axisLine={false}
                tickLine={false}
              />
              <Tooltip
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null
                  const r = payload[0]?.payload as CategoryRow
                  return (
                    <SalesChartTooltipCard
                      title={r.category}
                      rows={[
                        { label: anchorLabel, value: fmtChartKpi(r.nbv), color: CURRENT_BAR_FILL },
                        { label: priorLabel, value: fmtChartKpi(r.prior_nbv), color: PRIOR_BAR_FILL },
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
                dataKey="prior_nbv"
                name={priorLabel}
                fill={PRIOR_BAR_FILL}
                radius={[0, 3, 3, 0]}
                maxBarSize={32}
              >
                {/* Label inside the right end of each prior-NBV bar, kEUR formatted */}
                <LabelList
                  dataKey="prior_nbv"
                  content={(props) => {
                    const { x, y, width, height, value } = props as {
                      x?: number; y?: number; width?: number; height?: number; value?: number
                    }
                    if (value == null || x == null || y == null || width == null || height == null) return null
                    // Only render when the bar is wide enough to hold a label.
                    if (Number(width) < 36) return null
                    return (
                      <text
                        x={Number(x) + Number(width) - 5}
                        y={Number(y) + Number(height) / 2}
                        textAnchor="end"
                        dominantBaseline="middle"
                        fontSize={10}
                        fill="#475569"
                      >
                        {fmtChartKpi(Number(value))}
                      </text>
                    )
                  }}
                />
              </Bar>
              <Bar
                dataKey="nbv"
                name={anchorLabel}
                fill={CURRENT_BAR_FILL}
                radius={[0, 4, 4, 0]}
                maxBarSize={32}
              >
                <NbvValueLabel data={data} />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </SalesAnalyticsChartShell>
  )
}
