import {

  Bar,

  CartesianGrid,

  ComposedChart,

  LabelList,

  Legend,

  ResponsiveContainer,

  Tooltip,

  XAxis,

  YAxis,

} from 'recharts'

import type { ReceivablesDimensionChartRow } from '../../../lib/api'

import { monthLabelShort } from '../../../lib/periodSelection'

import { fmtAmount } from '../../../lib/fmt'

import { countryDisplayName } from '../../../lib/countryLabels'

import { SalesChartTooltipCard, salesChartTooltipProps } from '../analytics/SalesChartTooltip'

import {

  CHART_AXIS_TICK_STYLE,

  CHART_TICK_STYLE,

} from '../analytics/salesChartTypography'

import { BRAND, SALES_CHART_CURSOR, SALES_CHART_GRID_PROPS } from '../analytics/salesChartTheme'



const Y_AXIS_WIDTH = 78

const CHART_LEFT_MARGIN = 8
const MANY_BARS_THRESHOLD = 8



type ChartRow = {

  name: string

  balance: number

  prior: number | null

  delta_pm: number | null

  delta_py: number | null

  delta_pm_pct: number | null

  delta_py_pct: number | null

}



type Props = {

  rows: ReceivablesDimensionChartRow[]

  dimension: string

  comparePm: boolean

  comparePy: boolean

  year: number

  month: number

  chartHeight: number

}



function priorMonth(year: number, month: number): { year: number; month: number } {

  if (month <= 1) return { year: year - 1, month: 12 }

  return { year, month: month - 1 }

}



function deltaColor(d: number | null | undefined): string {

  if (d == null || Math.abs(d) < 0.05) return '#94A3B8'

  return d > 0 ? '#DC2626' : '#059669'

}



function fmtDeltaAbs(d: number | null | undefined): string {

  if (d == null) return ''

  const sign = d > 0 ? '+' : ''

  return `${sign}${fmtAmount(d)}`

}



function fmtDeltaPct(pct: number | null | undefined): string {

  if (pct == null) return ''

  const sign = pct > 0 ? '+' : ''

  return `${sign}${pct.toLocaleString('de-DE', { maximumFractionDigits: 1 })}%`

}



function displayLabel(label: string, dimension: string): string {

  if (dimension === 'country') return countryDisplayName(label)

  return label

}



function toChartRows(

  rows: ReceivablesDimensionChartRow[],

  dimension: string,

  comparePm: boolean,

  comparePy: boolean,

): ChartRow[] {

  return rows.map(r => {

    const prior =

      comparePm && r.pm_balance != null

        ? r.pm_balance

        : comparePy && r.py_balance != null

          ? r.py_balance

          : null

    return {

      name: displayLabel(r.label, dimension),

      balance: r.balance,

      prior,

      delta_pm: comparePm ? (r.delta_pm ?? null) : null,

      delta_py: comparePy ? (r.delta_py ?? null) : null,

      delta_pm_pct: comparePm ? (r.delta_pm_pct ?? null) : null,

      delta_py_pct: comparePy ? (r.delta_py_pct ?? null) : null,

    }

  })

}



function estimateTextWidth(text: string, fontSize: number): number {

  return Math.max(fontSize * 3.2, text.length * fontSize * 0.58)

}



function BarTopLabel({

  x = 0,

  y = 0,

  width = 0,

  value,

  payload,

  comparePm,

  comparePy,

  priorLabel,

}: {

  x?: number

  y?: number

  width?: number

  value?: number

  payload?: ChartRow

  comparePm: boolean

  comparePy: boolean

  priorLabel: string

}) {

  if (value == null) return null

  const text = fmtAmount(Number(value))

  const deltaValue = comparePm ? payload?.delta_pm : comparePy ? payload?.delta_py : null
  const deltaPct = comparePm ? payload?.delta_pm_pct : comparePy ? payload?.delta_py_pct : null
  const deltaText = deltaValue != null
    ? `${fmtDeltaAbs(deltaValue)}${deltaPct != null ? ` (${fmtDeltaPct(deltaPct)})` : ''}`
    : ''

  const cx = x + width / 2

  const fontSize = 10
  const deltaFontSize = 9

  const padX = 6

  const padY = 3

  const deltaLabelPrefix = deltaText ? `Δ ${priorLabel}: ` : ''
  const deltaFullText = deltaText ? `${deltaLabelPrefix}${deltaText}` : ''
  const boxW = Math.max(
    estimateTextWidth(text, fontSize),
    estimateTextWidth(deltaFullText, deltaFontSize),
  ) + padX * 2

  const boxH = deltaText ? fontSize + deltaFontSize + padY * 2 + 4 : fontSize + padY * 2

  const boxY = y - boxH - 6



  return (

    <g>

      <rect

        x={cx - boxW / 2}

        y={boxY}

        width={boxW}

        height={boxH}

        rx={4}

        fill="rgba(241, 245, 249, 0.92)"

        stroke="rgba(226, 232, 240, 0.85)"

        strokeWidth={0.75}

      />

      <text

        x={cx}

        y={boxY + padY + fontSize - 1}

        textAnchor="middle"

        fontSize={fontSize}

        fontWeight={600}

        fill={BRAND.navy}

      >

        {text}

      </text>

      {deltaText ? (
        <>
          <text
            x={cx}
            y={boxY + padY + fontSize + 10}
            textAnchor="end"
            fontSize={deltaFontSize}
            fontWeight={500}
            fill="#64748B"
          >
            {deltaLabelPrefix}
          </text>
          <text
            x={cx}
            y={boxY + padY + fontSize + 10}
            textAnchor="start"
            fontSize={deltaFontSize}
            fontWeight={700}
            fill={deltaColor(deltaValue)}
          >
            {deltaText}
          </text>
        </>
      ) : null}

    </g>

  )

}



function ChartTooltip({

  active,

  payload,

  label,

  comparePm,

  comparePy,

  currentLabel,

  priorLabel,

}: {

  active?: boolean

  payload?: Array<{ name: string; value: number; color: string; dataKey: string; payload?: ChartRow }>

  label?: string

  comparePm: boolean

  comparePy: boolean

  currentLabel: string

  priorLabel: string

}) {

  if (!active || !payload?.length) return null

  const row = payload[0]?.payload as ChartRow | undefined

  const rows = payload

    .filter(p => p.value != null)

    .map(p => ({

      label:

        p.dataKey === 'balance'

          ? currentLabel

          : p.dataKey === 'prior'

            ? priorLabel

            : String(p.name),

      value: fmtAmount(p.value),

      color: p.color,

    }))



  if (row?.delta_pm != null && comparePm) {

    const pct = row.delta_pm_pct != null ? ` (${fmtDeltaPct(row.delta_pm_pct)})` : ''

    rows.push({

      label: `Δ vs ${priorLabel}`,

      value: `${fmtDeltaAbs(row.delta_pm)}${pct}`,

      color: deltaColor(row.delta_pm),

    })

  }

  if (row?.delta_py != null && comparePy && !comparePm) {

    const pct = row.delta_py_pct != null ? ` (${fmtDeltaPct(row.delta_py_pct)})` : ''

    rows.push({

      label: `Δ vs ${priorLabel}`,

      value: `${fmtDeltaAbs(row.delta_py)}${pct}`,

      color: deltaColor(row.delta_py),

    })

  }



  return <SalesChartTooltipCard title={String(label ?? '')} rows={rows} />

}



export default function ReceivablesDimensionBarChart({

  rows,

  dimension,

  comparePm,

  comparePy,

  year,

  month,

  chartHeight,

}: Props) {

  const chartRows = toChartRows(rows, dimension, comparePm, comparePy)

  const showPrior = chartRows.some(r => r.prior != null)

  const currentLabel = monthLabelShort(year, month)

  const pm = priorMonth(year, month)

  const priorLabel = comparePm

    ? monthLabelShort(pm.year, pm.month)

    : comparePy

      ? monthLabelShort(year - 1, month)

      : 'Prior'

  const topMargin = 36
  const showValueLabels = chartRows.length <= MANY_BARS_THRESHOLD
  const showYAxisTicks = !showValueLabels



  return (

    <div style={{ width: '100%', height: chartHeight, minHeight: chartHeight }}>

      <ResponsiveContainer width="100%" height={chartHeight}>

        <ComposedChart

          data={chartRows}

          margin={{ top: topMargin, right: 12, left: CHART_LEFT_MARGIN, bottom: 44 }}

          barGap={showPrior ? -18 : 4}

        >

          <CartesianGrid {...SALES_CHART_GRID_PROPS} />

          <XAxis

            dataKey="name"

            tick={CHART_TICK_STYLE}

            axisLine={false}

            tickLine={false}

            interval={0}

            angle={0}

            textAnchor="middle"

            height={40}

          />

          <YAxis

            tick={CHART_AXIS_TICK_STYLE}

            axisLine={false}

            tickLine={false}

            width={showYAxisTicks ? Y_AXIS_WIDTH : 0}

            tickFormatter={v => fmtAmount(Number(v))}

            hide={!showYAxisTicks}

          />

          <Tooltip

            content={

              <ChartTooltip

                comparePm={comparePm}

                comparePy={comparePy}

                currentLabel={currentLabel}

                priorLabel={priorLabel}

              />

            }

            cursor={SALES_CHART_CURSOR}

            {...salesChartTooltipProps}

          />

          {showPrior && (

            <Legend

              wrapperStyle={{ fontSize: 12, paddingTop: 4 }}

              iconType="square"

              iconSize={9}

            />

          )}

          {showPrior && (

            <Bar

              dataKey="prior"

              name={priorLabel}

              fill={BRAND.navy}

              fillOpacity={0.22}

              maxBarSize={40}

              radius={[4, 4, 0, 0]}

              isAnimationActive={false}

            />

          )}

          <Bar

            dataKey="balance"

            name={currentLabel}

            fill={BRAND.navy}

            maxBarSize={40}

            radius={[4, 4, 0, 0]}

            isAnimationActive={false}

          >

            {showValueLabels && (
              <LabelList
                dataKey="balance"
                position="top"
                content={(props: { x?: string | number; y?: string | number; width?: string | number; value?: string | number; payload?: ChartRow }) => (
                  <BarTopLabel
                    x={typeof props.x === 'number' ? props.x : Number(props.x)}
                    y={typeof props.y === 'number' ? props.y : Number(props.y)}
                    width={typeof props.width === 'number' ? props.width : Number(props.width)}
                    value={typeof props.value === 'number' ? props.value : Number(props.value)}
                    payload={props.payload}
                    comparePm={comparePm}
                    comparePy={comparePy}
                    priorLabel={priorLabel}
                  />
                )}
              />
            )}

          </Bar>

        </ComposedChart>

      </ResponsiveContainer>

    </div>

  )

}



export function buildDimensionBarCompareSubtitle(

  year: number,

  month: number,

  comparePm: boolean,

  comparePy: boolean,

): string {

  const current = monthLabelShort(year, month)

  if (!comparePm && !comparePy) {

    return `Open balance · ${current}`

  }

  const pm = priorMonth(year, month)

  const prior = comparePm ? monthLabelShort(pm.year, pm.month) : monthLabelShort(year - 1, month)

  const priorDesc = comparePm ? 'prior month as transparent bar' : 'prior year as transparent bar'

  return `${current} vs ${prior} · ${priorDesc} · Δ green when balance falls, red when it rises`

}


