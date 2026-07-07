import { useMemo } from 'react'
import {
  Bar, CartesianGrid, Cell, ComposedChart, LabelList, ResponsiveContainer, Tooltip, XAxis, YAxis,
} from 'recharts'
import type { PersonnelWaterfallEntry } from '../../../lib/api'
import { fmtChartKpi } from '../../../lib/fmt'
import SalesAnalyticsChartShell from '../../sales/analytics/SalesAnalyticsChartShell'
import { SalesChartTooltipCard, salesChartTooltipProps } from '../../sales/analytics/SalesChartTooltip'
import { BRAND, SALES_CHART_BODY_CLASS, SALES_CHART_GRID_PROPS } from '../../sales/analytics/salesChartTheme'
import { CHART_AXIS_TICK_STYLE } from '../../sales/analytics/salesChartTypography'

const C_TOTAL = BRAND.navy
const C_POS = '#16A34A'
const C_NEG = '#DC2626'

type Entry = PersonnelWaterfallEntry & { color: string }

type Props = {
  entries: PersonnelWaterfallEntry[]
  loading?: boolean
}

export default function PayrollHeadcountBridgeChart({ entries, loading }: Props) {
  const data = useMemo(
    () => entries.map(e => ({
      ...e,
      color: e.is_total ? C_TOTAL : e.raw >= 0 ? C_POS : C_NEG,
    })),
    [entries],
  )

  return (
    <SalesAnalyticsChartShell title="Headcount bridge" subtitle="FTE · opening to closing">
      <div className={SALES_CHART_BODY_CLASS}>
        {loading ? (
          <div className="flex items-center justify-center h-[280px] text-xs" style={{ color: BRAND.textMuted }}>Loading…</div>
        ) : !data.length ? (
          <div className="flex items-center justify-center h-[280px] text-xs" style={{ color: BRAND.textMuted }}>No data</div>
        ) : (
          <ResponsiveContainer width="100%" height={330}>
            <ComposedChart data={data} margin={{ top: 24, right: 12, left: 4, bottom: 4 }} barCategoryGap="18%">
              <CartesianGrid {...SALES_CHART_GRID_PROPS} />
              <XAxis dataKey="name" tick={CHART_AXIS_TICK_STYLE} axisLine={false} tickLine={false} />
              <YAxis tick={CHART_AXIS_TICK_STYLE} tickFormatter={v => fmtChartKpi(v)} axisLine={false} tickLine={false} width={48} />
              <Tooltip
                content={({ active, payload }) => {
                  if (!active || !payload?.length) return null
                  const e = payload[0]?.payload as Entry
                  return (
                    <SalesChartTooltipCard
                      title={e.name}
                      rows={[{ label: e.is_total ? 'Total' : 'Change', value: fmtChartKpi(e.raw), color: e.color }]}
                    />
                  )
                }}
                {...salesChartTooltipProps}
              />
              <Bar dataKey="base" stackId="w" fill="transparent" isAnimationActive={false} />
              <Bar dataKey="value" stackId="w" radius={[4, 4, 0, 0]} maxBarSize={48} isAnimationActive={false}>
                {data.map((e, i) => (
                  <Cell key={`${e.name}-${i}`} fill={e.color} />
                ))}
                <LabelList
                  dataKey="raw"
                  position="top"
                  formatter={(v: number) => {
                    const e = data.find(x => x.raw === v)
                    if (!e || Math.abs(v) < 0.05) return ''
                    if (e.is_total) return fmtChartKpi(v)
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
