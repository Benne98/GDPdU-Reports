import { useMemo } from 'react'
import {
  CartesianGrid, Cell, Customized, ResponsiveContainer, Scatter, ScatterChart, Tooltip, XAxis, YAxis, ZAxis,
} from 'recharts'
import type { SalesProfitMarginScatterResponse } from '../../../lib/api'
import { fmtChartKpi } from '../../../lib/fmt'
import SalesAnalyticsDimEditor from './SalesAnalyticsDimEditor'
import SalesAnalyticsChartShell from './SalesAnalyticsChartShell'
import { SalesChartTooltipCard, salesChartTooltipProps } from './SalesChartTooltip'
import { analyticsDimLabel } from './salesChartRegistry'
import { placeScatterLabels, type PlacedScatterLabel, type ScatterLabelPoint } from './scatterLabelPlacement'
import {
  CHART_AXIS_TICK_STYLE,
  CHART_AXIS_TITLE_FONT_SIZE,
  CHART_AXIS_TITLE_STYLE,
  CHART_DATA_LABEL_FONT_SIZE,
} from './salesChartTypography'
import {
  BRAND,
  SALES_CHART_BODY_CLASS,
  SALES_CHART_GRID_PROPS,
  seriesColor,
} from './salesChartTheme'

const CHART_MARGIN = { top: 28, right: 24, left: 8, bottom: 32 }

type Props = {
  data: SalesProfitMarginScatterResponse | null
  dim: string
  onDimChange: (d: string) => void
  loading: boolean
  error?: string | null
}

type AxisMapEntry = { scale?: (v: number) => number }

function getScale(axisMap: Record<string, AxisMapEntry> | undefined): ((v: number) => number) | null {
  if (!axisMap) return null
  const entry = Object.values(axisMap)[0]
  return typeof entry?.scale === 'function' ? entry.scale.bind(entry) : null
}

function ScatterTooltip({
  active,
  payload,
}: {
  active?: boolean
  payload?: Array<{ payload?: {
    segment: string
    gross_margin_pct: number
    gross_profit_keur: number
    gross_sales_keur: number
    fill?: string
  } }>
}) {
  if (!active || !payload?.length) return null
  const p = payload[0]?.payload
  if (!p) return null
  return (
    <SalesChartTooltipCard
      title={p.segment}
      rows={[
        { label: 'Gross margin', value: `${p.gross_margin_pct}%`, color: p.fill },
        { label: 'Gross profit', value: `${fmtChartKpi(p.gross_profit_keur)} kEUR`, color: p.fill },
        { label: 'Gross sales', value: `${fmtChartKpi(p.gross_sales_keur)} kEUR`, color: p.fill },
      ]}
    />
  )
}

function ScatterLabelsLayer({
  width = 0,
  height = 0,
  offset,
  xAxisMap,
  yAxisMap,
  points,
  colorBySegment,
}: {
  width?: number
  height?: number
  offset?: { top: number; right: number; bottom: number; left: number }
  xAxisMap?: Record<string, AxisMapEntry>
  yAxisMap?: Record<string, AxisMapEntry>
  points: ScatterLabelPoint[]
  colorBySegment: Map<string, string>
}) {
  const xScale = getScale(xAxisMap)
  const yScale = getScale(yAxisMap)
  const mTop = offset?.top ?? CHART_MARGIN.top
  const mRight = offset?.right ?? CHART_MARGIN.right
  const mBottom = offset?.bottom ?? CHART_MARGIN.bottom
  const mLeft = offset?.left ?? CHART_MARGIN.left

  const labels: PlacedScatterLabel[] = useMemo(() => {
    if (!xScale || !yScale || !width || !height || !points.length) return []
    return placeScatterLabels(points, xScale, yScale, width, height, {
      top: mTop,
      right: mRight,
      bottom: mBottom,
      left: mLeft,
    })
  }, [points, xScale, yScale, width, height, mTop, mRight, mBottom, mLeft])

  if (!labels.length) return null

  return (
    <g className="scatter-labels" pointerEvents="none">
      {labels.map(l => {
        const far = Math.hypot(l.labelX - l.pointX, l.labelY - l.pointY) > 18
        const color = colorBySegment.get(l.segment) ?? BRAND.slate
        return (
          <g key={l.segment}>
            {far && (
              <line
                x1={l.pointX}
                y1={l.pointY}
                x2={l.labelX}
                y2={l.labelY}
                stroke={color}
                strokeOpacity={0.4}
                strokeWidth={1.5}
                strokeDasharray="3 3"
              />
            )}
            <text
              x={l.labelX}
              y={l.labelY}
              textAnchor={l.anchor}
              fontSize={CHART_DATA_LABEL_FONT_SIZE}
              fontWeight={600}
              fill={BRAND.navy}
              stroke="#fff"
              strokeWidth={3}
              paintOrder="stroke"
            >
              <title>{l.segment}</title>
              {l.displayText}
            </text>
          </g>
        )
      })}
    </g>
  )
}

export default function SalesProfitMarginScatterChart({ data, dim, onDimChange, loading, error }: Props) {
  const dimLabel = analyticsDimLabel(dim)
  const periodLabel = data?.period_label ?? '…'
  const points = useMemo(
    () => (data?.points ?? []).map((p, i) => ({
      ...p,
      fill: seriesColor(i),
    })),
    [data?.points],
  )

  const colorBySegment = useMemo(
    () => new Map(points.map(p => [p.segment, p.fill])),
    [points],
  )

  const scatterPoints = useMemo(
    () => points.map(({ segment, gross_margin_pct, gross_profit_keur, gross_sales_keur }) => ({
      segment,
      gross_margin_pct,
      gross_profit_keur,
      gross_sales_keur,
    })),
    [points],
  )

  return (
    <SalesAnalyticsChartShell
      className="flex-1 min-h-[360px]"
      title={`Gross profit and gross margin by ${dimLabel} for ${periodLabel}`}
      subtitle="Bubble size = gross sales (kEUR)"
      actions={<SalesAnalyticsDimEditor value={dim} onChange={onDimChange} disabled={loading} />}
      bodyClassName="flex-1"
    >
      <div className={`${SALES_CHART_BODY_CLASS} flex-1 flex flex-col min-h-[280px] pb-4`}>
        {loading ? (
          <div className="flex flex-1 items-center justify-center text-xs" style={{ color: BRAND.textMuted }}>Loading…</div>
        ) : error ? (
          <div className="flex flex-1 items-center justify-center text-xs text-red-600">{error}</div>
        ) : !points.length ? (
          <div className="flex flex-1 items-center justify-center text-xs" style={{ color: BRAND.textMuted }}>No data</div>
        ) : (
          <div className="flex-1 flex items-center w-full min-h-[280px]">
            <div className="w-full h-[308px]">
              <ResponsiveContainer width="100%" height="100%">
                <ScatterChart margin={CHART_MARGIN}>
                  <CartesianGrid {...SALES_CHART_GRID_PROPS} />
                  <XAxis
                    type="number"
                    dataKey="gross_margin_pct"
                    name="Gross margin"
                    unit="%"
                    tick={CHART_AXIS_TICK_STYLE}
                    axisLine={false}
                    tickLine={false}
                    label={{
                      value: 'Gross margin %',
                      position: 'bottom',
                      offset: 6,
                      fontSize: CHART_AXIS_TITLE_FONT_SIZE,
                      ...CHART_AXIS_TITLE_STYLE,
                    }}
                  />
                  <YAxis
                    type="number"
                    dataKey="gross_profit_keur"
                    name="Gross profit"
                    tick={CHART_AXIS_TICK_STYLE}
                    tickFormatter={v => fmtChartKpi(v)}
                    axisLine={false}
                    tickLine={false}
                    label={{
                      value: 'Gross profit (kEUR)',
                      angle: -90,
                      position: 'insideLeft',
                      fontSize: CHART_AXIS_TITLE_FONT_SIZE,
                      ...CHART_AXIS_TITLE_STYLE,
                    }}
                  />
                  <ZAxis type="number" dataKey="gross_sales_keur" range={[64, 480]} />
                  <Tooltip content={<ScatterTooltip />} {...salesChartTooltipProps} />
                  <Scatter data={points} fillOpacity={0.9} stroke="#fff" strokeWidth={2}>
                    {points.map(entry => (
                      <Cell key={entry.segment} fill={entry.fill} />
                    ))}
                  </Scatter>
                  <Customized
                    component={function ScatterLabelsCustomized(chartProps: Record<string, unknown>) {
                      return (
                        <ScatterLabelsLayer
                          {...chartProps}
                          points={scatterPoints}
                          colorBySegment={colorBySegment}
                        />
                      )
                    }}
                  />
                </ScatterChart>
              </ResponsiveContainer>
            </div>
          </div>
        )}
      </div>
    </SalesAnalyticsChartShell>
  )
}
