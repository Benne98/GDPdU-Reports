import {
  Bar, BarChart, CartesianGrid, LabelList, Legend, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import { fmtChartKpi } from '../../../lib/fmt'
import SalesAnalyticsChartShell from '../../sales/analytics/SalesAnalyticsChartShell'
import { SalesChartTooltipCard, salesChartTooltipProps } from '../../sales/analytics/SalesChartTooltip'
import { BRAND, SALES_CHART_BODY_CLASS, SALES_CHART_GRID_PROPS } from '../../sales/analytics/salesChartTheme'
import { CHART_AXIS_TICK_STYLE } from '../../sales/analytics/salesChartTypography'

/** English fallback labels for trend metrics — used when the API omits `trend_metrics`. */
export const TREND_METRIC_CATALOG: Record<string, { label: string; unit: string }> = {
  fte: { label: 'Average FTE', unit: 'count' },
  avg_cost_per_fte: { label: 'Avg cost / FTE (kEUR)', unit: 'keur' },
  payroll: { label: 'Payroll (kEUR)', unit: 'keur' },
  personnel_expenses: { label: 'Personnel expenses (kEUR)', unit: 'keur' },
  personnel_pct_output: { label: 'Personnel % of output', unit: 'pct' },
}

const PRIOR_BAR_FILL = '#CBD5E1'
const CURRENT_BAR_FILL = BRAND.navy

type TrendRow = {
  period: string
  value: number
  prior_value: number
  fte: number
  prior_fte: number
  delta_pct: number | null
}

type TrendMetric = { id: string; label: string; unit: string }

type Props = {
  rows: TrendRow[]
  trendMetrics: TrendMetric[]
  trendMetric: string
  onTrendMetricChange: (id: string) => void
  priorYearLabel?: string
  loading?: boolean
}

function formatValue(metricId: string, v: number): string {
  if (metricId === 'fte') return String(Math.round(v))
  return fmtChartKpi(v)
}

function TrendBarLabel({ metricId }: { metricId: string }) {
  return (
    <LabelList
      dataKey="value"
      position="top"
      formatter={(v: number) => formatValue(metricId, v)}
      style={{ fontSize: 10, fontWeight: 600, fill: '#111827' }}
    />
  )
}

export default function PayrollFteTrendChart({
  rows,
  trendMetrics,
  trendMetric,
  onTrendMetricChange,
  priorYearLabel = 'Prior year',
  loading,
}: Props) {
  // Resolve display metrics: prefer API-supplied list, fall back to static catalog
  const displayMetrics: TrendMetric[] = trendMetrics.length > 0
    ? trendMetrics.map(m => ({
        id: m.id,
        label: m.label || TREND_METRIC_CATALOG[m.id]?.label || m.id,
        unit: m.unit || TREND_METRIC_CATALOG[m.id]?.unit || 'keur',
      }))
    : Object.entries(TREND_METRIC_CATALOG).map(([id, { label, unit }]) => ({ id, label, unit }))

  const metricMeta = displayMetrics.find(m => m.id === trendMetric) ?? displayMetrics[0]
  const metricLabel = metricMeta?.label ?? TREND_METRIC_CATALOG[trendMetric]?.label ?? trendMetric
  const unit = metricMeta?.unit ?? 'count'
  const subtitle = unit === 'count' ? 'FTE · vs prior year' : `kEUR · ${metricLabel} · vs prior year`

  const isEmpty = !loading && rows.length === 0

  return (
    <SalesAnalyticsChartShell
      title="Metric trend"
      subtitle={subtitle}
      compactHeader
      actions={(
        <select
          className="text-xs border border-slate-200 rounded-md px-2 py-1 bg-white max-w-[200px]"
          value={trendMetric}
          onChange={e => onTrendMetricChange(e.target.value)}
        >
          {displayMetrics.map(m => (
            <option key={m.id} value={m.id}>{m.label}</option>
          ))}
        </select>
      )}
    >
      <div className={SALES_CHART_BODY_CLASS}>
        {loading ? (
          <div className="flex items-center justify-center h-[280px] text-xs" style={{ color: BRAND.textMuted }}>
            Loading…
          </div>
        ) : isEmpty ? (
          <div className="flex items-center justify-center h-[280px] text-xs" style={{ color: BRAND.textMuted }}>
            No trend data for this period — restart the API and reload.
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={280}>
            <BarChart data={rows} margin={{ top: 24, right: 12, left: 4, bottom: 4 }} barGap={4} barCategoryGap="20%">
              <CartesianGrid {...SALES_CHART_GRID_PROPS} />
              <XAxis
                dataKey="period"
                tick={CHART_AXIS_TICK_STYLE}
                axisLine={false}
                tickLine={false}
                interval={0}
              />
              <YAxis
                tick={CHART_AXIS_TICK_STYLE}
                axisLine={false}
                tickLine={false}
                width={48}
                tickFormatter={v => formatValue(trendMetric, Number(v))}
              />
              <Tooltip
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null
                  const r = payload[0]?.payload as TrendRow
                  const value = r.value ?? r.fte
                  const prior = r.prior_value ?? r.prior_fte
                  return (
                    <SalesChartTooltipCard
                      title={r.period}
                      rows={[
                        { label: metricLabel, value: formatValue(trendMetric, value), color: CURRENT_BAR_FILL },
                        { label: priorYearLabel, value: formatValue(trendMetric, prior), color: PRIOR_BAR_FILL },
                        ...(r.delta_pct != null
                          ? [{
                              label: 'YoY',
                              value: `${r.delta_pct >= 0 ? '+' : ''}${r.delta_pct.toFixed(1)}%`,
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
                dataKey="prior_value"
                name={priorYearLabel}
                fill={PRIOR_BAR_FILL}
                radius={[4, 4, 0, 0]}
                maxBarSize={36}
              />
              <Bar
                dataKey="value"
                name={metricLabel}
                fill={CURRENT_BAR_FILL}
                radius={[4, 4, 0, 0]}
                maxBarSize={36}
              >
                <TrendBarLabel metricId={trendMetric} />
              </Bar>
            </BarChart>
          </ResponsiveContainer>
        )}
      </div>
    </SalesAnalyticsChartShell>
  )
}
