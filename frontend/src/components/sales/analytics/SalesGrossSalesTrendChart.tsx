import {
  Bar, BarChart, CartesianGrid, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import type { SalesGrossSalesTrendResponse } from '../../../lib/api'
import { fmtChartKpi } from '../../../lib/fmt'
import SalesAnalyticsDimEditor from './SalesAnalyticsDimEditor'
import SalesAnalyticsChartShell from './SalesAnalyticsChartShell'
import { SalesChartTooltipCard, salesChartTooltipProps } from './SalesChartTooltip'
import { analyticsDimLabel } from './salesChartRegistry'
import {
  CHART_AXIS_TICK_STYLE,
  CHART_TICK_STYLE,
} from './salesChartTypography'
import {
  SALES_CHART_BODY_CLASS,
  SALES_CHART_CURSOR,
  SALES_CHART_GRID_PROPS,
  seriesColor,
} from './salesChartTheme'

type Props = {
  data: SalesGrossSalesTrendResponse | null
  dim: string
  onDimChange: (d: string) => void
  loading: boolean
  error?: string | null
}

function TrendTooltip({
  active,
  payload,
  label,
}: {
  active?: boolean
  payload?: Array<{ name: string; value: number; fill: string }>
  label?: string
}) {
  if (!active || !payload?.length) return null
  const rows = [...payload].reverse().map(p => ({
    label: p.name,
    value: `${fmtChartKpi(p.value)} kEUR`,
    color: p.fill,
  }))
  return <SalesChartTooltipCard title={String(label ?? '')} rows={rows} />
}

export default function SalesGrossSalesTrendChart({ data, dim, onDimChange, loading, error }: Props) {
  const dimLabel = analyticsDimLabel(dim)
  const rangeFrom = data?.range_label.from ?? '…'
  const rangeTo = data?.range_label.to ?? '…'
  const chartRows = data?.periods.map(p => ({ label: p.label, ...p.values })) ?? []
  const segments = data?.segments ?? []

  return (
    <SalesAnalyticsChartShell
      className="shrink-0"
      compactHeader
      title={`Gross sales development from ${rangeFrom} to ${rangeTo}`}
      subtitle={`kEUR · by ${dimLabel}`}
      actions={<SalesAnalyticsDimEditor value={dim} onChange={onDimChange} disabled={loading} />}
    >
      <div className={`${SALES_CHART_BODY_CLASS} pb-3`}>
        {loading ? (
          <div className="flex items-center justify-center h-[242px] text-xs" style={{ color: '#94A3B8' }}>Loading…</div>
        ) : error ? (
          <div className="flex items-center justify-center h-[242px] text-xs text-red-600">{error}</div>
        ) : !chartRows.length ? (
          <div className="flex items-center justify-center h-[242px] text-xs" style={{ color: '#94A3B8' }}>No data</div>
        ) : (
          <ResponsiveContainer width="100%" height={242}>
            <BarChart data={chartRows} margin={{ top: 10, right: 8, left: 0, bottom: 6 }}>
              <CartesianGrid {...SALES_CHART_GRID_PROPS} />
              <XAxis dataKey="label" tick={CHART_TICK_STYLE} axisLine={false} tickLine={false} dy={4} />
              <YAxis
                tickFormatter={v => fmtChartKpi(v)}
                tick={CHART_AXIS_TICK_STYLE}
                axisLine={false}
                tickLine={false}
                width={52}
              />
              <Tooltip
                content={<TrendTooltip />}
                cursor={SALES_CHART_CURSOR}
                {...salesChartTooltipProps}
              />
              {segments.map((seg, i) => (
                <Bar
                  key={seg}
                  dataKey={seg}
                  stackId="s"
                  fill={seriesColor(i)}
                  maxBarSize={40}
                  radius={i === segments.length - 1 ? [4, 4, 0, 0] : [0, 0, 0, 0]}
                  isAnimationActive={false}
                />
              ))}
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </SalesAnalyticsChartShell>
  )
}
