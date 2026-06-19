import {
  Bar, BarChart, CartesianGrid, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis, Cell,
} from 'recharts'
import type { SalesCompositionBreakdownResponse } from '../../../lib/api'
import SalesColumnEditor from './SalesColumnEditor'
import SalesAnalyticsChartShell from './SalesAnalyticsChartShell'
import SalesChartMetricToggle from './SalesChartMetricToggle'
import { SalesChartTooltipCard, salesChartTooltipProps } from './SalesChartTooltip'
import type { SalesColumnDef } from './salesTableTypes'
import {
  COMPOSITION_DIM_CATALOG,
  COMPOSITION_METRIC_LABELS,
  type CompositionMetric,
} from './salesChartRegistry'
import { CHART_DATA_LABEL_FONT_SIZE, CHART_DIM_CAPTION_FONT_SIZE } from './salesChartTypography'
import {
  BRAND,
  SALES_CHART_BODY_CLASS,
  SALES_CHART_GRID_PROPS,
  segmentLabelColor,
  seriesColor,
} from './salesChartTheme'

type Props = {
  data: SalesCompositionBreakdownResponse | null
  metric: CompositionMetric
  onMetricChange: (m: CompositionMetric) => void
  visibleDims: SalesColumnDef[]
  onDimsChange: (cols: SalesColumnDef[]) => void
  loading: boolean
  error?: string | null
}

const METRICS: CompositionMetric[] = ['gross_sales', 'gross_profit', 'gross_margin']

const MIN_SEGMENT_WIDTH_PX = 30
const MIN_SHARE_PCT = 6

function SegmentPctLabel({
  x = 0,
  y = 0,
  width = 0,
  height = 0,
  sharePct = 0,
  fill = BRAND.navy,
}: {
  x?: number
  y?: number
  width?: number
  height?: number
  sharePct?: number
  fill?: string
}) {
  if (width < MIN_SEGMENT_WIDTH_PX || sharePct < MIN_SHARE_PCT) return null
  const cx = x + width / 2
  const cy = y + height / 2
  const text = `${sharePct % 1 === 0 ? sharePct.toFixed(0) : sharePct.toFixed(1)}%`
  return (
    <text
      x={cx}
      y={cy}
      textAnchor="middle"
      dominantBaseline="central"
      fontSize={CHART_DATA_LABEL_FONT_SIZE}
      fontWeight={600}
      fill={segmentLabelColor(fill)}
      pointerEvents="none"
    >
      {text}
    </text>
  )
}

function CompositionTooltip({
  active,
  payload,
  chartSegments,
}: {
  active?: boolean
  payload?: Array<{ name: string; value: number; fill: string }>
  chartSegments: Array<{ name: string; share_pct: number; value_keur: number }>
}) {
  if (!active || !payload?.length) return null
  const name = payload[0]?.name ?? ''
  const seg = chartSegments.find(s => s.name === name)
  if (!seg) return null
  return (
    <SalesChartTooltipCard
      title={name}
      rows={[
        { label: 'Share', value: `${seg.share_pct.toFixed(1)}%`, color: payload[0]?.fill },
        { label: 'Value', value: `${seg.value_keur.toFixed(1)} kEUR`, color: payload[0]?.fill },
      ]}
    />
  )
}

export default function SalesCompositionBreakdownChart({
  data,
  metric,
  onMetricChange,
  visibleDims,
  onDimsChange,
  loading,
  error,
}: Props) {
  const periodLabel = data?.period_label ?? '…'
  const metricLabel = COMPOSITION_METRIC_LABELS[metric]
  const charts = data?.charts ?? []

  return (
    <SalesAnalyticsChartShell
      className="h-full min-h-[580px]"
      bodyClassName="flex-1 flex flex-col"
      title={`${metricLabel} breakdown for ${periodLabel}`}
      subtitle="100% stacked · kEUR"
      actions={(
        <SalesColumnEditor
          tableId="sales-composition-dims"
          catalog={COMPOSITION_DIM_CATALOG}
          columns={visibleDims}
          onChange={onDimsChange}
          disabled={loading}
        />
      )}
      headerExtra={(
        <SalesChartMetricToggle
          options={METRICS.map(m => ({ value: m, label: COMPOSITION_METRIC_LABELS[m] }))}
          value={metric}
          onChange={onMetricChange}
        />
      )}
    >
      <div className={`${SALES_CHART_BODY_CLASS} flex-1 flex flex-col min-h-0 pb-3`}>
        {loading ? (
          <div className="flex items-center justify-center flex-1 text-xs" style={{ color: BRAND.textMuted }}>Loading…</div>
        ) : error ? (
          <div className="flex items-center justify-center flex-1 text-xs text-red-600">{error}</div>
        ) : !charts.length ? (
          <div className="flex items-center justify-center flex-1 text-xs" style={{ color: BRAND.textMuted }}>No data</div>
        ) : (
          <div className="flex flex-1 gap-0 items-stretch min-h-0">
            {charts.map((chart, chartIdx) => {
              const row: Record<string, string | number> = { label: chart.dim_label }
              chart.segments.forEach(s => {
                row[s.name] = s.value_keur
              })
              const segNames = chart.segments.map(s => s.name)
              return (
                <div
                  key={chart.dim}
                  className="flex-1 min-w-0 flex flex-col px-2"
                  style={chartIdx > 0 ? { borderLeft: `1px solid ${BRAND.borderLight}` } : undefined}
                >
                  <div className="flex-1 min-h-[200px]">
                    <ResponsiveContainer width="100%" height="100%">
                      <BarChart data={[row]} margin={{ top: 8, right: 6, left: 6, bottom: 4 }} stackOffset="expand">
                        <CartesianGrid {...SALES_CHART_GRID_PROPS} />
                        <XAxis dataKey="label" hide />
                        <YAxis hide domain={[0, 'auto']} />
                        <Tooltip
                          content={((tp: unknown) => (
                            <CompositionTooltip
                              active={(tp as { active?: boolean }).active}
                              payload={(tp as { payload?: Array<{ name: string; value: number; fill: string }> }).payload}
                              chartSegments={chart.segments}
                            />
                          )) as never}
                          {...salesChartTooltipProps}
                        />
                        {segNames.map((name, i) => {
                          const fill = seriesColor(i)
                          const sharePct = chart.segments.find(s => s.name === name)?.share_pct ?? 0
                          const isTop = i === segNames.length - 1
                          return (
                            <Bar
                              key={name}
                              dataKey={name}
                              stackId="stack"
                              maxBarSize={52}
                              fill={fill}
                              radius={isTop ? [5, 5, 0, 0] : [0, 0, 0, 0]}
                              isAnimationActive={false}
                            >
                              <Cell fill={fill} />
                              <LabelList
                                dataKey={name}
                                content={((props: unknown) => (
                                  <SegmentPctLabel
                                    {...(props as { x?: number; y?: number; width?: number; height?: number })}
                                    sharePct={sharePct}
                                    fill={fill}
                                  />
                                )) as never}
                              />
                            </Bar>
                          )
                        })}
                      </BarChart>
                    </ResponsiveContainer>
                  </div>
                  <p
                    className="text-xs font-semibold text-center truncate mt-2 px-1 shrink-0"
                    style={{ color: BRAND.textSecondary, fontSize: CHART_DIM_CAPTION_FONT_SIZE }}
                    title={chart.dim_label}
                  >
                    {chart.dim_label}
                  </p>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </SalesAnalyticsChartShell>
  )
}
