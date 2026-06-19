import { useId, useMemo } from 'react'
import type { LucideIcon } from 'lucide-react'
import {
  Area,
  AreaChart,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from 'recharts'
import type { OperationalKpiMetric } from '../../../../lib/api'
import { fmtChartKpi, fmtDays, fmtDelta, fmtKpi, fmtPct } from '../../../../lib/fmt'
import type { AgingTrendFormat } from './agingKpiDefs'
import type { KpiValueVariant } from '../../../cockpit/KpiCard'

const MONTH_LETTER = ['J', 'F', 'M', 'A', 'M', 'J', 'J', 'A', 'S', 'O', 'N', 'D'] as const

type TrendPoint = {
  month: number
  balance: number
  overdue: number
  dso_days?: number
  dpo_days?: number
  open_documents: number
}

function formatMainValue(value: number, variant: KpiValueVariant): string {
  switch (variant) {
    case 'count':
      return Math.round(value).toLocaleString('de-DE')
    case 'percent':
      return fmtPct(value <= 1 ? value * 100 : value)
    case 'days':
      return `${fmtDays(value)}`
    default:
      return fmtKpi(value)
  }
}

function formatTrendTooltip(value: number, format: AgingTrendFormat): string {
  if (format === 'currency') return `${fmtChartKpi(value / 1000)} kEUR`
  if (format === 'days') return `${fmtDays(value)} days`
  return Math.round(value).toLocaleString('de-DE')
}

/** Pad Y scale so stroke + area fill never clip inside the sparkline box. */
function sparklineDomain(values: number[]): [number, number] {
  if (values.length === 0) return [0, 1]
  const min = Math.min(...values)
  const max = Math.max(...values)
  if (min === max) {
    const pad = Math.max(Math.abs(min) * 0.2, 1)
    return [min - pad, max + pad]
  }
  const span = max - min
  const pad = span * 0.18
  return [min - pad, max + pad]
}

function formatDeltaCompact(delta: number, variant: KpiValueVariant): string {
  switch (variant) {
    case 'count': {
      const n = Math.round(delta)
      return `${n >= 0 ? '+' : ''}${n.toLocaleString('de-DE')}`
    }
    case 'days':
      return `${delta >= 0 ? '+' : ''}${delta.toFixed(0)} d`
    default:
      return fmtDelta(delta)
  }
}

function SparkTooltip({
  active,
  payload,
  label,
  trendFormat,
}: {
  active?: boolean
  payload?: Array<{ value: number }>
  label?: string
  trendFormat: AgingTrendFormat
}) {
  if (!active || !payload?.length) return null
  return (
    <div
      className="rounded-md border px-2 py-1 text-[10px] shadow-md"
      style={{ background: '#FFF', borderColor: '#E2E8F0' }}
    >
      <p className="font-medium" style={{ color: '#64748B' }}>{label}</p>
      <p className="tabular-nums font-bold" style={{ color: '#1E3A5F' }}>
        {formatTrendTooltip(Number(payload[0]?.value ?? 0), trendFormat)}
      </p>
    </div>
  )
}

function InlineDelta({
  label,
  delta,
  invert,
  variant,
}: {
  label: string
  delta: number | null | undefined
  invert: boolean
  variant: KpiValueVariant
}) {
  if (delta == null) {
    return (
      <div className="flex items-center justify-between text-xs leading-snug">
        <span style={{ color: '#94A3B8' }}>{label}</span>
        <span style={{ color: '#CBD5E1' }}>—</span>
      </div>
    )
  }
  const favourable = invert ? delta < 0 : delta > 0
  const flat = Math.abs(delta) < 0.05
  const color = flat ? '#64748B' : favourable ? '#10B981' : '#DC2626'
  return (
    <div className="flex items-center justify-between text-xs leading-snug gap-2">
      <span style={{ color: '#94A3B8' }}>{label}</span>
      <span className="font-medium tabular-nums shrink-0" style={{ color }}>
        {formatDeltaCompact(delta, variant)}
      </span>
    </div>
  )
}

export default function AgingKpiCard({
  title,
  metric,
  variant,
  icon: Icon,
  accentColor = '#1E3A5F',
  invertDelta = false,
  trendKey,
  trendFormat,
  trendPoints,
  trendLoading,
}: {
  title: string
  metric?: OperationalKpiMetric | null
  variant: KpiValueVariant
  icon?: LucideIcon
  accentColor?: string
  invertDelta?: boolean
  trendKey: string
  trendFormat: AgingTrendFormat
  trendPoints: TrendPoint[]
  trendLoading?: boolean
}) {
  const gradientId = useId().replace(/:/g, '')
  const value = metric?.value ?? 0

  const chartRows = useMemo(
    () =>
      trendPoints.map(p => ({
        monthLetter: MONTH_LETTER[p.month - 1] ?? '?',
        v: Number((p as Record<string, number>)[trendKey] ?? 0),
      })),
    [trendPoints, trendKey],
  )

  const hasTrend = chartRows.length > 0

  const yDomain = useMemo(
    () => sparklineDomain(chartRows.map(r => r.v)),
    [chartRows],
  )

  return (
    <div
      className="relative flex flex-col rounded-xl p-5"
      style={{
        background: '#FFFFFF',
        border: '1px solid #E2E8F0',
        boxShadow: '0 1px 3px rgba(0,0,0,0.06), 0 4px 16px rgba(0,0,0,0.04)',
        minHeight: 172,
      }}
    >
      <div
        className="absolute top-0 left-0 right-0 h-[2px] rounded-t-xl"
        style={{ background: `linear-gradient(90deg, transparent, ${accentColor}, transparent)`, opacity: 0.5 }}
      />

      <div className="flex items-center justify-between mb-2.5">
        <span className="text-xs font-semibold tracking-wide uppercase" style={{ color: '#94A3B8' }}>
          {title}
        </span>
        {Icon && (
          <div
            className="w-6 h-6 rounded-md flex items-center justify-center shrink-0"
            style={{ background: `${accentColor}12`, border: `1px solid ${accentColor}22` }}
          >
            <Icon size={12} style={{ color: accentColor }} />
          </div>
        )}
      </div>

      <div className="flex gap-2.5 mb-2.5 min-h-[76px]">
        <div className="shrink-0 flex flex-col justify-center min-w-[4.5rem]">
          <div className="flex items-baseline gap-1">
            <span className="text-3xl font-bold tracking-tight tabular-nums leading-none" style={{ color: '#111827' }}>
              {formatMainValue(value, variant)}
            </span>
            {variant === 'financial' && (
              <span className="text-[10px] font-semibold uppercase" style={{ color: '#94A3B8' }}>
                kEUR
              </span>
            )}
            {variant === 'days' && (
              <span className="text-[10px] font-medium" style={{ color: '#94A3B8' }}>
                days
              </span>
            )}
          </div>
        </div>

        <div
          className="flex-1 min-w-0 rounded-md relative flex items-stretch"
          style={{
            background: `linear-gradient(135deg, ${accentColor}08 0%, transparent 72%)`,
            border: `1px solid ${accentColor}12`,
          }}
        >
          {trendLoading && !hasTrend ? (
            <div className="w-full min-h-[76px] flex items-center justify-center px-2">
              <div className="w-full h-8 rounded animate-pulse" style={{ background: '#E2E8F0' }} />
            </div>
          ) : !hasTrend ? (
            <div className="w-full min-h-[76px] flex items-center justify-center text-[10px]" style={{ color: '#CBD5E1' }}>
              —
            </div>
          ) : (
            <div className="w-full h-[76px]">
              <ResponsiveContainer width="100%" height="100%">
                <AreaChart
                  data={chartRows}
                  margin={{ top: 8, right: 4, left: 2, bottom: 16 }}
                >
                  <defs>
                    <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
                      <stop offset="0%" stopColor={accentColor} stopOpacity={0.35} />
                      <stop offset="100%" stopColor={accentColor} stopOpacity={0.02} />
                    </linearGradient>
                  </defs>
                  <XAxis
                    dataKey="monthLetter"
                    tick={{ fontSize: 8, fill: '#94A3B8' }}
                    axisLine={false}
                    tickLine={false}
                    interval="preserveStartEnd"
                    height={14}
                    padding={{ left: 4, right: 4 }}
                  />
                  <YAxis hide domain={yDomain} width={0} />
                  <Tooltip content={<SparkTooltip trendFormat={trendFormat} />} />
                  <Area
                    type="linear"
                    dataKey="v"
                    stroke={accentColor}
                    strokeWidth={1.5}
                    fill={`url(#${gradientId})`}
                    dot={false}
                    isAnimationActive={false}
                    activeDot={{ r: 2, fill: accentColor, stroke: '#FFF', strokeWidth: 1 }}
                  />
                </AreaChart>
              </ResponsiveContainer>
            </div>
          )}
        </div>
      </div>

      <div className="flex flex-col gap-1.5 pt-3 mt-auto" style={{ borderTop: '1px solid #F1F5F9' }}>
        <InlineDelta
          label="Δ Month-over-month"
          delta={metric?.delta_pm}
          invert={invertDelta}
          variant={variant}
        />
        <InlineDelta
          label="Δ Month in previous year"
          delta={metric?.delta_smly}
          invert={invertDelta}
          variant={variant}
        />
      </div>
    </div>
  )
}
