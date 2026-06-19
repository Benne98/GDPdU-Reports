import type { LucideIcon } from 'lucide-react'
import type { ReactNode } from 'react'
import KpiCard, { KpiValueVariant } from '../../cockpit/KpiCard'
import { OperationalKpiMetric } from '../../../lib/api'

export type KpiDef = {
  key: string
  title: string
  metric?: OperationalKpiMetric | null
  format?: 'currency' | 'number' | 'count' | 'percent' | 'days'
  icon?: LucideIcon
  accentColor?: string
  invertDelta?: boolean
}

function toVariant(format?: KpiDef['format']): KpiValueVariant {
  switch (format) {
    case 'count':
      return 'count'
    case 'percent':
      return 'percent'
    case 'days':
      return 'days'
    case 'currency':
    case 'number':
    default:
      return 'financial'
  }
}

export default function OperationalKpiGrid({
  items,
  className,
  trailing,
}: {
  items: KpiDef[]
  className?: string
  trailing?: ReactNode
}) {
  return (
    <div className={className ?? 'grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 xl:grid-cols-4 gap-4'}>
      {items.map((k, i) => {
        const m = k.metric
        return (
          <KpiCard
            key={k.key}
            index={i}
            title={k.title}
            value={m?.value ?? 0}
            deltaPm={m?.delta_pm ?? null}
            deltaSmly={m?.delta_smly ?? null}
            variant={toVariant(k.format)}
            neutralValue
            invertDelta={k.invertDelta}
            icon={k.icon}
            accentColor={k.accentColor ?? '#1E3A5F'}
            animateEntry={false}
          />
        )
      })}
      {trailing}
    </div>
  )
}

