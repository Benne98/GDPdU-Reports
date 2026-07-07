import {
  Bar,
  BarChart,
  CartesianGrid,
  Legend,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { FixedAssetDimension } from '../../../lib/api'
import { fmtChartKpi } from '../../../lib/fmt'
import SalesAnalyticsChartShell from '../../sales/analytics/SalesAnalyticsChartShell'
import { DIMENSION_OPTIONS } from './fixedAssetsColumnRegistry'
import { BRAND, SALES_CHART_BODY_CLASS, SALES_CHART_GRID_PROPS } from '../../sales/analytics/salesChartTheme'
import { CHART_AXIS_TICK_STYLE } from '../../sales/analytics/salesChartTypography'

/**
 * Pixels per bar-group on the X-axis.
 * Wide enough for the label and the grouped bars (Additions / Disposals / D&A).
 */
const CATEGORY_SLOT_PX = 100
const CHART_HEIGHT = 330

const COLOR_ADDITIONS = '#16A34A'
const COLOR_DISPOSALS = '#DC2626'
const COLOR_DA        = BRAND.navy

type AddDispRow = {
  key: string
  label: string
  additions: number
  disposals: number
  depreciation: number
}

type Props = {
  rows: AddDispRow[]
  dimension: FixedAssetDimension
  loading?: boolean
  actions?: React.ReactNode
}

export default function FixedAssetsAddDispChart({ rows, dimension, loading, actions }: Props) {
  const dimLabel = DIMENSION_OPTIONS.find(d => d.id === dimension)?.label ?? dimension

  /*
   * Full-width or scrollable layout:
   *
   * The inner div is sized to max(100%, n * CATEGORY_SLOT_PX) via CSS:
   *   width: n*px   — natural per-bar width
   *   minWidth: 100% — ensures the chart fills its card when bars are few
   *
   * The outer div has overflow-x: auto (from SALES_CHART_BODY_CLASS + our override),
   * so horizontal scroll kicks in automatically when n*px > container width.
   *
   * ResponsiveContainer reads the rendered inner-div width and sizes the chart to match.
   *
   * No row cap — all rows are rendered (backend may return hundreds for 'asset' dimension).
   */
  const naturalWidth = Math.max(rows.length * CATEGORY_SLOT_PX, 1)

  return (
    <SalesAnalyticsChartShell
      title="Additions & disposals"
      subtitle={`kEUR · by ${dimLabel}`}
      actions={actions}
      compactHeader
    >
      <div className={`${SALES_CHART_BODY_CLASS} overflow-x-auto`}>
        {loading ? (
          <div
            className="flex items-center justify-center text-xs"
            style={{ color: BRAND.textMuted, height: CHART_HEIGHT }}
          >
            Loading…
          </div>
        ) : (
          <div style={{ width: naturalWidth, minWidth: '100%', height: CHART_HEIGHT }}>
            <ResponsiveContainer width="100%" height="100%">
              <BarChart
                data={rows}
                margin={{ top: 8, right: 12, left: 4, bottom: 4 }}
                barCategoryGap="20%"
                barGap={4}
              >
                <CartesianGrid {...SALES_CHART_GRID_PROPS} />
                <XAxis
                  dataKey="label"
                  tick={CHART_AXIS_TICK_STYLE}
                  axisLine={false}
                  tickLine={false}
                  interval={0}
                  height={48}
                />
                <YAxis
                  tick={CHART_AXIS_TICK_STYLE}
                  tickFormatter={v => fmtChartKpi(Number(v))}
                  axisLine={false}
                  tickLine={false}
                  width={52}
                />
                <Tooltip formatter={(v: number) => fmtChartKpi(v)} />
                <Legend wrapperStyle={{ fontSize: 12 }} />
                <Bar dataKey="additions"   name="Additions" fill={COLOR_ADDITIONS} radius={[4, 4, 0, 0]} maxBarSize={40} isAnimationActive={false} />
                <Bar dataKey="disposals"   name="Disposals" fill={COLOR_DISPOSALS} radius={[4, 4, 0, 0]} maxBarSize={40} isAnimationActive={false} />
                <Bar dataKey="depreciation" name="D&A"      fill={COLOR_DA}        radius={[4, 4, 0, 0]} maxBarSize={40} isAnimationActive={false} />
              </BarChart>
            </ResponsiveContainer>
          </div>
        )}
      </div>
    </SalesAnalyticsChartShell>
  )
}
