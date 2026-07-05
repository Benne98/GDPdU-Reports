/**
 * GeoTrendChart — stacked bar chart of revenue over time, segmented by a selectable dimension.
 */
import {
  Bar, BarChart, CartesianGrid, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { SalesGeoTrendResponse } from '../../lib/api'
import { fmtChartKpi } from '../../lib/fmt'
import SalesAnalyticsChartShell from './analytics/SalesAnalyticsChartShell'
import SalesGeoTrendEditor, { geoTrendGrainLabel, type GeoTrendGrain } from './analytics/SalesGeoTrendEditor'
import { SalesChartTooltipCard, salesChartTooltipProps } from './analytics/SalesChartTooltip'
import { analyticsDimLabel } from './analytics/salesChartRegistry'
import {
  CHART_AXIS_TICK_STYLE,
  CHART_TICK_STYLE,
} from './analytics/salesChartTypography'
import {
  SALES_CHART_BODY_CLASS,
  SALES_CHART_CURSOR,
  SALES_CHART_GRID_PROPS,
  seriesColor,
} from './analytics/salesChartTheme'

interface Props {
  data: SalesGeoTrendResponse
  grain: GeoTrendGrain
  dim: string
  onGrainChange: (g: GeoTrendGrain) => void
  onDimChange: (d: string) => void
  loading: boolean
}

type LegacyGeoRegion = { name: string; values: number[] }

/** Accept legacy API shape `{ periods: string[], regions: {name, values}[] }`. */
function normalizeGeoTrend(data: SalesGeoTrendResponse): SalesGeoTrendResponse {
  if (!data?.periods?.length) return { periods: [], regions: [] }
  const firstRegion = data.regions?.[0]
  if (
    firstRegion != null
    && typeof firstRegion === 'object'
    && 'name' in firstRegion
    && 'values' in firstRegion
  ) {
    const legacyRegions = data.regions as unknown as LegacyGeoRegion[]
    const labels = data.periods as unknown as string[]
    const regionNames = legacyRegions.map(r => r.name)
    const periods = labels.map((label, i) => {
      const row: Record<string, number | string> = { label }
      for (const region of legacyRegions) {
        row[region.name] = region.values[i] ?? 0
      }
      return row
    })
    return { periods, regions: regionNames }
  }
  return data
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

export default function GeoTrendChart({
  data,
  grain,
  dim,
  onGrainChange,
  onDimChange,
  loading,
}: Props) {
  const chartData = normalizeGeoTrend(data)
  const dimLabel = analyticsDimLabel(dim)
  const segments = chartData.regions ?? []

  return (
    <SalesAnalyticsChartShell
      compactHeader
      title={`Revenue by ${dimLabel}`}
      subtitle={`kEUR · ${geoTrendGrainLabel(grain)} view`}
      actions={(
        <SalesGeoTrendEditor
          grain={grain}
          dim={dim}
          onGrainChange={onGrainChange}
          onDimChange={onDimChange}
          disabled={loading}
        />
      )}
    >
      <div className={SALES_CHART_BODY_CLASS}>
        {loading ? (
          <div className="flex items-center justify-center h-[250px] text-xs" style={{ color: '#94A3B8' }}>
            Loading…
          </div>
        ) : !chartData.periods.length ? (
          <div className="flex items-center justify-center h-[250px] text-xs" style={{ color: '#94A3B8' }}>
            No data
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={250}>
            <BarChart data={chartData.periods} margin={{ top: 8, right: 12, left: 0, bottom: 4 }}>
              <CartesianGrid {...SALES_CHART_GRID_PROPS} />
              <XAxis dataKey="label" tick={CHART_TICK_STYLE} axisLine={false} tickLine={false} />
              <YAxis
                tickFormatter={v => fmtChartKpi(v)}
                tick={CHART_AXIS_TICK_STYLE}
                axisLine={false}
                tickLine={false}
                width={48}
              />
              <Tooltip
                content={<TrendTooltip />}
                cursor={SALES_CHART_CURSOR}
                {...salesChartTooltipProps}
              />
              <Legend wrapperStyle={{ fontSize: 11, paddingTop: 8 }} iconType="square" iconSize={9} />
              {segments.map((seg, i) => (
                <Bar
                  key={seg}
                  dataKey={seg}
                  stackId="s"
                  fill={seriesColor(i)}
                  maxBarSize={40}
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
