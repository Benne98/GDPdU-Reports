import { useMemo } from 'react'
import {
  Bar,
  BarChart,
  CartesianGrid,
  Cell,
  Customized,
  LabelList,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { FixedAssetNbvBridgeChart } from '../../../lib/api'
import { fmtChartKpi } from '../../../lib/fmt'
import SalesAnalyticsChartShell from '../../sales/analytics/SalesAnalyticsChartShell'
import { SalesChartTooltipCard, salesChartTooltipProps } from '../../sales/analytics/SalesChartTooltip'
import { BRAND, SALES_CHART_BODY_CLASS, SALES_CHART_GRID_PROPS } from '../../sales/analytics/salesChartTheme'
import { CHART_AXIS_TICK_STYLE } from '../../sales/analytics/salesChartTypography'
import BridgeDimensionGroupAxis from './BridgeDimensionGroupAxis'
import { bridgeChartWidth, flattenBridgeColumns, type BridgeBarPoint } from './faBridgeChartData'

const C_TOTAL = BRAND.navy
const C_ADD   = '#16A34A'
const C_DISP  = '#DC2626'
const C_DA    = '#64748B'

const CHART_HEIGHT = 320
const CHART_HEIGHT_WITH_GROUPS = 400
const LEG_X_AXIS_HEIGHT = 34
const GROUP_AXIS_BOTTOM_MARGIN = 78

function barColor(barType: BridgeBarPoint['barType']): string {
  if (barType === 'total') return C_TOTAL
  if (barType === 'add')   return C_ADD
  if (barType === 'disp')  return C_DISP
  return C_DA
}

/** Slightly darker shade for the 1 px stroke — gives the outlined look. */
function barStroke(barType: BridgeBarPoint['barType']): string {
  if (barType === 'total') return '#1e3a5f'
  if (barType === 'add')   return '#15803d'
  if (barType === 'disp')  return '#b91c1c'
  return '#334155'
}

type Props = {
  bridges: FixedAssetNbvBridgeChart[]
  loading?: boolean
  actions?: React.ReactNode
}

export default function FixedAssetsWaterfallChart({ bridges, loading, actions }: Props) {
  const bridge = bridges[0]
  const subtitle = bridge?.opening_label && bridge?.closing_label
    ? `kEUR · ${bridge.opening_label} → ${bridge.closing_label}${bridge.scope_label ? ` · ${bridge.scope_label}` : ''}`
    : 'kEUR'

  const { points, boxes } = useMemo(() => {
    if (!bridge?.columns?.length) return { points: [], boxes: [] }
    return flattenBridgeColumns(bridge.columns)
  }, [bridge?.columns])

  const hasDimensionGroups = boxes.length > 0
  const chartHeight = hasDimensionGroups ? CHART_HEIGHT_WITH_GROUPS : CHART_HEIGHT
  const chartWidth = bridgeChartWidth(points.length, boxes.length)
  const bottomMargin = hasDimensionGroups ? GROUP_AXIS_BOTTOM_MARGIN : 12

  // Only show value labels when bars are reasonably few to avoid clutter.
  const showLabels = points.length <= 20

  return (
    <SalesAnalyticsChartShell title="NBV bridge" subtitle={subtitle} actions={actions}>
      <div className={`${SALES_CHART_BODY_CLASS} overflow-x-auto`}>
        {loading ? (
          <div
            className="flex items-center justify-center text-xs"
            style={{ color: BRAND.textMuted, height: chartHeight }}
          >
            Loading…
          </div>
        ) : !points.length ? (
          <div
            className="flex items-center justify-center text-xs"
            style={{ color: BRAND.textMuted, height: chartHeight }}
          >
            No data
          </div>
        ) : (
          <div style={{ width: chartWidth, minWidth: '100%', height: chartHeight }}>
            <BarChart
              width={chartWidth}
              height={chartHeight}
              data={points}
              margin={{ top: 36, right: 12, left: 4, bottom: bottomMargin }}
              barCategoryGap="12%"
            >
              <CartesianGrid {...SALES_CHART_GRID_PROPS} />
              <XAxis
                dataKey="xKey"
                tick={CHART_AXIS_TICK_STYLE}
                axisLine={false}
                tickLine={false}
                interval={0}
                height={hasDimensionGroups ? LEG_X_AXIS_HEIGHT : 52}
                tickFormatter={(_v, idx) => points[idx]?.tickLabel ?? ''}
              />
              <YAxis
                tick={CHART_AXIS_TICK_STYLE}
                tickFormatter={v => fmtChartKpi(Number(v))}
                axisLine={false}
                tickLine={false}
                width={52}
              />
              <Tooltip
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null
                  const entry = payload.find(e => e.dataKey === 'amount') ?? payload[0]
                  const p = entry?.payload as BridgeBarPoint
                  if (!p) return null
                  const title = p.groupLabel
                    ? `${p.groupLabel} · ${p.displayLabel}`
                    : p.displayLabel
                  return (
                    <SalesChartTooltipCard
                      title={title}
                      rows={[{
                        label: p.barType === 'total' ? 'NBV' : 'kEUR',
                        value: fmtChartKpi(p.raw),
                        color: barColor(p.barType),
                      }]}
                    />
                  )
                }}
                {...salesChartTooltipProps}
              />

              {hasDimensionGroups && (
                <Customized
                  component={(chartProps: {
                    width?: number
                    height?: number
                    offset?: { top: number; right: number; bottom: number; left: number }
                    xAxisMap?: Record<string, { scale?: ((v: string) => number) & { bandwidth?: () => number } }>
                  }) => (
                    <BridgeDimensionGroupAxis
                      width={chartProps.width}
                      height={chartProps.height}
                      offset={chartProps.offset}
                      xAxisMap={chartProps.xAxisMap}
                      points={points}
                      groups={boxes}
                      legAxisHeight={LEG_X_AXIS_HEIGHT}
                    />
                  )}
                />
              )}

              <Bar
                dataKey="spacer"
                stackId="wf"
                fill="transparent"
                legendType="none"
                isAnimationActive={false}
              />

              <Bar
                dataKey="amount"
                stackId="wf"
                maxBarSize={44}
                isAnimationActive={false}
              >
                {points.map(p => (
                  <Cell
                    key={p.id}
                    fill={barColor(p.barType)}
                    stroke={barStroke(p.barType)}
                    strokeWidth={1}
                  />
                ))}
                {showLabels && (
                  <LabelList
                    position="top"
                    content={(props) => {
                      const { x, y, width, index } = props as {
                        x?: number; y?: number; width?: number; index?: number
                      }
                      const p = index != null ? points[index] : undefined
                      if (!p || x == null || y == null || width == null) return null
                      if (p.amount < 50) return null
                      return (
                        <text
                          x={Number(x) + Number(width) / 2}
                          y={Number(y) - 4}
                          textAnchor="middle"
                          fontSize={10}
                          fontWeight={600}
                          fill={barColor(p.barType)}
                        >
                          {fmtChartKpi(p.raw)}
                        </text>
                      )
                    }}
                  />
                )}
              </Bar>
            </BarChart>
          </div>
        )}
      </div>
    </SalesAnalyticsChartShell>
  )
}
