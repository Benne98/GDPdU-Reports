import { useMemo } from 'react'
import {
  Bar, CartesianGrid, ComposedChart, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis, Cell,
} from 'recharts'
import type { SalesMetricBridgeResponse } from '../../../lib/api'
import { fmtChartKpi } from '../../../lib/fmt'
import SalesAnalyticsChartShell from './SalesAnalyticsChartShell'
import SalesAnalyticsDimEditor from './SalesAnalyticsDimEditor'
import SalesChartMetricToggle from './SalesChartMetricToggle'
import { SalesChartTooltipCard, salesChartTooltipProps } from './SalesChartTooltip'
import {
  analyticsDimLabel,
  COMPOSITION_METRIC_LABELS,
  type CompositionMetric,
} from './salesChartRegistry'
import {
  CHART_AXIS_TICK_STYLE,
  CHART_TICK_STYLE,
} from './salesChartTypography'
import { BRAND, SALES_CHART_BODY_CLASS, SALES_CHART_GRID_PROPS } from './salesChartTheme'
import { buildMetricBridgeEntries, type WaterfallEntry } from './salesMetricBridgeChart'

const METRICS: CompositionMetric[] = ['gross_sales', 'gross_profit', 'gross_margin']

const TICK_MAX_CHARS = 14

/** Split long segment names into up to two horizontal lines for the X axis. */
function bridgeXAxisLines(label: string): string[] {
  const text = label.trim()
  if (text.length <= TICK_MAX_CHARS) return [text]

  const spaceIdx = text.lastIndexOf(' ', TICK_MAX_CHARS)
  if (spaceIdx > 4) {
    const line1 = text.slice(0, spaceIdx)
    const rest = text.slice(spaceIdx + 1).trim()
    if (rest.length <= TICK_MAX_CHARS) return [line1, rest]
    return [line1, `${rest.slice(0, TICK_MAX_CHARS - 1)}…`]
  }

  if (text.length <= TICK_MAX_CHARS * 2) {
    return [text.slice(0, TICK_MAX_CHARS), text.slice(TICK_MAX_CHARS)]
  }
  return [`${text.slice(0, TICK_MAX_CHARS - 1)}…`]
}

function BridgeXAxisTick({
  x = 0,
  y = 0,
  payload,
}: {
  x?: number
  y?: number
  payload?: { value?: string }
}) {
  const lines = bridgeXAxisLines(String(payload?.value ?? ''))
  return (
    <g transform={`translate(${x},${y})`}>
      <text
        textAnchor="middle"
        fill={CHART_TICK_STYLE.fill}
        fontSize={CHART_TICK_STYLE.fontSize}
        fontWeight={CHART_TICK_STYLE.fontWeight}
      >
        {lines.map((line, i) => (
          <tspan key={i} x={0} dy={i === 0 ? 14 : 13}>
            {line}
          </tspan>
        ))}
      </text>
    </g>
  )
}

type Props = {
  data: SalesMetricBridgeResponse | null
  metric: CompositionMetric
  onMetricChange: (m: CompositionMetric) => void
  dim: string
  onDimChange: (d: string) => void
  loading: boolean
  error?: string | null
}

function formatValue(v: number, isTotal: boolean, displayLabel?: string): string {
  if (isTotal && displayLabel) return displayLabel
  return isTotal ? fmtChartKpi(v) : (v >= 0 ? '+' : '') + fmtChartKpi(v)
}

function BridgeTooltip({
  active,
  payload,
  metric,
}: {
  active?: boolean
  payload?: Array<{ payload?: WaterfallEntry }>
  metric: CompositionMetric
}) {
  if (!active || !payload?.length) return null
  const e = payload[0]?.payload
  if (!e) return null
  const totalLabel = metric === 'gross_margin' ? 'Gross margin' : 'Total'
  const rows = e.isTotal
    ? [{ label: totalLabel, value: formatValue(e.raw, true, e.segmentKey), color: e.color }]
    : [{ label: 'Change', value: formatValue(e.raw, false), color: e.color }]
  return <SalesChartTooltipCard title={e.name} rows={rows} />
}

export default function SalesMetricDimensionBridgeChart({
  data,
  metric,
  onMetricChange,
  dim,
  onDimChange,
  loading,
  error,
}: Props) {
  const metricLabel = COMPOSITION_METRIC_LABELS[metric]
  const dimLabel = analyticsDimLabel(dim)

  const entries = useMemo(() => (data ? buildMetricBridgeEntries(data) : []), [data])
  const rangeFrom = data?.range_label.from ?? '…'
  const rangeTo = data?.range_label.to ?? '…'
  const periodLabel = data?.period_label ?? '…'

  const subtitle =
    metric === 'gross_margin'
      ? `Period totals: margin % · bridge steps: gross profit change (kEUR) · by ${dimLabel}`
      : `kEUR · by ${dimLabel}`

  return (
    <SalesAnalyticsChartShell
      title={`${metricLabel} by ${dimLabel} for ${periodLabel}`}
      subtitle={subtitle}
      actions={(
        <div className="flex items-center gap-2 shrink-0 flex-wrap justify-end">
          <SalesChartMetricToggle
            inline
            options={METRICS.map(m => ({ value: m, label: COMPOSITION_METRIC_LABELS[m] }))}
            value={metric}
            onChange={onMetricChange}
          />
          <SalesAnalyticsDimEditor value={dim} onChange={onDimChange} disabled={loading} />
        </div>
      )}
    >
      <p className="text-[12px] -mt-1 mb-1 px-1" style={{ color: BRAND.textMuted }}>
        {rangeFrom} → {rangeTo}
        {data?.period_grain === 'week' ? ' · last 3 weeks' : ' · last 3 months'}
      </p>
      <div className={`${SALES_CHART_BODY_CLASS} pb-3`}>
        {loading ? (
          <div className="flex items-center justify-center h-[300px] text-xs" style={{ color: BRAND.textMuted }}>
            Loading…
          </div>
        ) : error ? (
          <div className="flex items-center justify-center h-[300px] text-xs text-red-600">{error}</div>
        ) : !entries.length ? (
          <div className="flex items-center justify-center h-[300px] text-xs" style={{ color: BRAND.textMuted }}>
            No data
          </div>
        ) : (
          <ResponsiveContainer width="100%" height={352}>
            <ComposedChart
              data={entries}
              margin={{ top: 22, right: 12, left: 4, bottom: 4 }}
              barCategoryGap="20%"
            >
              <CartesianGrid {...SALES_CHART_GRID_PROPS} />
              <XAxis
                dataKey="name"
                tick={<BridgeXAxisTick />}
                axisLine={false}
                tickLine={false}
                interval={0}
                height={44}
              />
              <YAxis
                tick={CHART_AXIS_TICK_STYLE}
                tickFormatter={v => fmtChartKpi(v)}
                axisLine={false}
                tickLine={false}
                width={52}
              />
              <Tooltip
                content={<BridgeTooltip metric={metric} />}
                {...salesChartTooltipProps}
              />
              <Bar dataKey="base" stackId="w" fill="transparent" isAnimationActive={false} />
              <Bar dataKey="value" stackId="w" radius={[4, 4, 0, 0]} maxBarSize={52} isAnimationActive={false}>
                {entries.map((e, i) => (
                  <Cell key={`${e.name}-${i}`} fill={e.color} />
                ))}
                <LabelList
                  dataKey="raw"
                  position="top"
                  formatter={(v: number) => {
                    if (Math.abs(v) < 0.05) return ''
                    const e = entries.find(x => x.raw === v)
                    if (!e) return ''
                    if (e.isTotal && e.segmentKey) return e.segmentKey
                    if (e.isTotal) return fmtChartKpi(v)
                    return (v >= 0 ? '+' : '') + fmtChartKpi(v)
                  }}
                  style={{ fontSize: 12, fill: BRAND.textSecondary, fontWeight: 600 }}
                />
              </Bar>
            </ComposedChart>
          </ResponsiveContainer>
        )}
      </div>
    </SalesAnalyticsChartShell>
  )
}
