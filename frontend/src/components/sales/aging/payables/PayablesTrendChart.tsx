import {
  CartesianGrid,
  ComposedChart,
  Label,
  Legend,
  Line,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { PayablesTrendPoint } from '../../../../lib/api'
import { fmtAmount } from '../../../../lib/fmt'
import {
  CHART_AXIS_TICK_STYLE,
  CHART_TICK_STYLE,
} from '../../analytics/salesChartTypography'
import { BRAND, SALES_CHART_CURSOR, SALES_CHART_GRID_PROPS } from '../../analytics/salesChartTheme'
import { SalesChartTooltipCard, salesChartTooltipProps } from '../../analytics/SalesChartTooltip'
import {
  PAYABLES_TREND_METRICS,
  payablesTrendMetricDef,
  payablesTrendMetricField,
  type PayablesTrendMetricId,
} from './shared/payablesTrendChartConfig'

const COLORS = { grid: '#E8EDF3', axis: '#94A3B8', label: '#64748B' }

function formatAxisMoney(v: number) {
  const n = Number(v)
  const abs = Math.abs(n)
  if (abs >= 1_000_000) return `${(n / 1_000_000).toLocaleString('de-DE', { maximumFractionDigits: 1 })}M`
  if (abs >= 1_000) return `${(n / 1_000).toLocaleString('de-DE', { maximumFractionDigits: 0 })}k`
  return n.toLocaleString('de-DE', { maximumFractionDigits: 0 })
}

function formatMetricValue(id: PayablesTrendMetricId, value: number): string {
  if (id === 'mom_variance_pct' || id === 'overdue_pct') {
    return `${value}%`
  }
  return fmtAmount(value)
}

function TrendTooltip({
  active,
  payload,
  label,
  activeMetrics,
}: {
  active?: boolean
  payload?: { dataKey?: string; value?: number; color?: string }[]
  label?: string
  activeMetrics: PayablesTrendMetricId[]
}) {
  if (!active || !payload?.length) return null
  const defs = new Map(PAYABLES_TREND_METRICS.map(m => [m.id, m]))
  const rows = activeMetrics.flatMap(id => {
    const dataKey = payablesTrendMetricField(id)
    const p = payload.find(x => x.dataKey === dataKey)
    if (p?.value == null) return []
    const def = defs.get(id)
    return [{
      label: def?.label ?? id,
      value: formatMetricValue(id, Number(p.value)),
      color: def?.color ?? p.color,
    }]
  })
  if (!rows.length) return null
  return <SalesChartTooltipCard title={label ?? ''} rows={rows} />
}

export default function PayablesTrendChart({
  points,
  metrics,
  chartHeight,
}: {
  points: PayablesTrendPoint[]
  metrics: PayablesTrendMetricId[]
  chartHeight: number
}) {
  const chartData = points.map(p => ({
    ...p,
    cost_of_materials: p.cost_of_materials ?? p.procurement_spend,
  }))

  if (!chartData.length || !metrics.length) {
    return (
      <div className="flex items-center justify-center text-sm" style={{ color: '#94A3B8', height: chartHeight }}>
        {!metrics.length ? 'Select at least one metric in chart settings' : 'No trend data'}
      </div>
    )
  }

  const activeDefs = metrics.map(id => ({
    ...payablesTrendMetricDef(id),
    dataKey: payablesTrendMetricField(id),
  }))
  const showAmountAxis = activeDefs.some(d => d.axis === 'amount')
  const showRatioAxis = activeDefs.some(d => d.axis === 'ratio')
  const leftWidth = showAmountAxis ? 72 : 8
  const rightWidth = showRatioAxis ? 48 : 8

  return (
    <div style={{ width: '100%', height: chartHeight, minHeight: chartHeight }}>
      <ResponsiveContainer width="100%" height={chartHeight}>
        <ComposedChart
          data={chartData}
          margin={{ top: 16, right: showRatioAxis ? 12 : 8, left: 4, bottom: showAmountAxis || showRatioAxis ? 36 : 16 }}
        >
          <CartesianGrid {...SALES_CHART_GRID_PROPS} stroke={COLORS.grid} vertical={false} />
          <XAxis
            dataKey="label"
            tick={CHART_TICK_STYLE}
            tickLine={false}
            axisLine={{ stroke: COLORS.grid }}
            interval="preserveStartEnd"
            angle={0}
            textAnchor="middle"
            height={36}
          />
          {showAmountAxis && (
            <YAxis
              yAxisId="amount"
              tick={CHART_AXIS_TICK_STYLE}
              tickLine={false}
              axisLine={false}
              width={leftWidth}
              tickFormatter={formatAxisMoney}
              domain={['auto', 'auto']}
            >
              <Label
                value="Amount (EUR)"
                angle={-90}
                position="insideLeft"
                offset={8}
                style={{ fontSize: 11, fontWeight: 600, fill: BRAND.textSecondary, textAnchor: 'middle' }}
              />
            </YAxis>
          )}
          {showRatioAxis && (
            <YAxis
              yAxisId="ratio"
              orientation="right"
              tick={{ fontSize: 10, fill: '#D97706' }}
              tickLine={false}
              axisLine={false}
              width={rightWidth}
              tickFormatter={v => `${Number(v).toLocaleString('de-DE', { maximumFractionDigits: 0 })}%`}
              domain={['auto', 'auto']}
            >
              <Label
                value="Days / %"
                angle={90}
                position="insideRight"
                offset={8}
                style={{ fontSize: 11, fontWeight: 600, fill: BRAND.textSecondary, textAnchor: 'middle' }}
              />
            </YAxis>
          )}
          <Tooltip
            content={<TrendTooltip activeMetrics={metrics} />}
            {...salesChartTooltipProps}
            cursor={SALES_CHART_CURSOR}
          />
          <Legend
            wrapperStyle={{ fontSize: 11, paddingTop: 8 }}
            iconType="plainline"
            iconSize={14}
          />
          {activeDefs.map(def => (
            <Line
              key={def.id}
              yAxisId={def.axis}
              type="monotone"
              dataKey={def.dataKey}
              name={def.label}
              stroke={def.color}
              strokeWidth={def.id === 'balance' ? 2.5 : 2}
              strokeDasharray={def.strokeDasharray}
              dot={def.axis === 'ratio' ? { r: 2, fill: def.color } : false}
              activeDot={{ r: 4 }}
              isAnimationActive={false}
            />
          ))}
        </ComposedChart>
      </ResponsiveContainer>
    </div>
  )
}
