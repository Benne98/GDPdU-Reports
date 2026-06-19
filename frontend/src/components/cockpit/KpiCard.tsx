import { motion } from 'framer-motion'
import { LucideIcon } from 'lucide-react'
import { fmtKpi, fmtDelta, fmtPct, fmtDays } from '../../lib/fmt'

export type KpiValueVariant = 'financial' | 'count' | 'percent' | 'days'

interface KpiCardProps {
  title: string
  value: number
  deltaPm?: number | null
  deltaSmly?: number | null
  /** Invert delta colour logic — used for Cost of Materials (lower = better) */
  invertDelta?: boolean
  /** Always show the main value in neutral dark colour, ignoring sign */
  neutralValue?: boolean
  /** financial = ÷1000 (cockpit default); count/percent/days for operational KPIs */
  variant?: KpiValueVariant
  icon?: LucideIcon
  accentColor?: string
  index: number
  onClick?: () => void
  /** Staggered entrance animation — disable on dense dashboards to avoid layout shift */
  animateEntry?: boolean
  deltaPmLabel?: string
  deltaSmlyLabel?: string
}

function formatMainValue(value: number, variant: KpiValueVariant): string {
  switch (variant) {
    case 'count':
      return Math.round(value).toLocaleString('de-DE')
    case 'percent':
      return fmtPct(value <= 1 ? value * 100 : value)
    case 'days':
      return `${fmtDays(value)} days`
    default:
      return fmtKpi(value)
  }
}

function formatDeltaValue(delta: number, variant: KpiValueVariant): string {
  switch (variant) {
    case 'count': {
      const n = Math.round(delta)
      return `${n >= 0 ? '+' : ''}${n.toLocaleString('de-DE')}`
    }
    case 'percent': {
      const pp = Math.abs(delta) <= 1 ? delta * 100 : delta
      return `${pp >= 0 ? '+' : ''}${pp.toFixed(1).replace('.', ',')} pp`
    }
    case 'days':
      return `${delta >= 0 ? '+' : ''}${delta.toFixed(0)} d`
    default:
      return fmtDelta(delta)
  }
}

function DeltaRow({
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
  if (delta === null || delta === undefined) return null

  const isPositive = delta >= 0
  const isFavourable = invert ? !isPositive : isPositive
  const color = isFavourable ? '#10B981' : '#DC2626'

  return (
    <div className="flex items-center justify-between text-xs leading-snug">
      <span style={{ color: '#94A3B8' }}>{label}</span>
      <span className="font-medium tabular-nums ml-3" style={{ color }}>
        {formatDeltaValue(delta, variant)}
      </span>
    </div>
  )
}

export default function KpiCard({
  title,
  value,
  deltaPm,
  deltaSmly,
  invertDelta = false,
  neutralValue = false,
  variant = 'financial',
  icon: Icon,
  accentColor = '#1E3A5F',
  index,
  onClick,
  animateEntry = true,
  deltaPmLabel = 'Δ Month-over-month',
  deltaSmlyLabel = 'Δ Month in previous year',
}: KpiCardProps) {
  const cardStyle = {
    background: '#FFFFFF',
    border: '1px solid #E2E8F0',
    boxShadow: '0 1px 3px rgba(0,0,0,0.06), 0 4px 16px rgba(0,0,0,0.04)',
  } as const
  const cardClass = `relative flex flex-col rounded-xl p-5 ${onClick ? 'cursor-pointer hover:shadow-md transition-shadow' : ''}`

  const inner = (
    <>
      <div
        className="absolute top-0 left-0 right-0 h-[2px] rounded-t-xl"
        style={{ background: `linear-gradient(90deg, transparent, ${accentColor}, transparent)`, opacity: 0.5 }}
      />

      <div className="flex items-center justify-between mb-3">
        <span className="text-xs font-semibold tracking-wide uppercase" style={{ color: '#94A3B8' }}>
          {title}
        </span>
        {Icon && (
          <div
            className="w-6 h-6 rounded-md flex items-center justify-center"
            style={{ background: `${accentColor}12`, border: `1px solid ${accentColor}22` }}
          >
            <Icon size={12} style={{ color: accentColor }} />
          </div>
        )}
      </div>

      <div
        className="text-3xl font-bold tracking-tight tabular-nums mb-4"
        style={{ color: neutralValue ? '#111827' : value < 0 ? '#DC2626' : '#111827' }}
      >
        {formatMainValue(value, variant)}
      </div>

      <div className="flex flex-col gap-1.5 pt-3" style={{ borderTop: '1px solid #F1F5F9' }}>
        <DeltaRow label={deltaPmLabel} delta={deltaPm} invert={invertDelta} variant={variant} />
        <DeltaRow label={deltaSmlyLabel} delta={deltaSmly} invert={invertDelta} variant={variant} />
      </div>
    </>
  )

  if (!animateEntry) {
    return (
      <div className={cardClass} style={cardStyle} onClick={onClick} role={onClick ? 'button' : undefined}>
        {inner}
      </div>
    )
  }

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.4, delay: index * 0.07, ease: [0.22, 1, 0.36, 1] }}
      onClick={onClick}
      className={cardClass}
      style={cardStyle}
      whileHover={
        onClick
          ? { y: -2, boxShadow: '0 4px 20px rgba(30,58,95,0.1), 0 0 0 1px rgba(30,58,95,0.1)' }
          : undefined
      }
    >
      {inner}
    </motion.div>
  )
}
