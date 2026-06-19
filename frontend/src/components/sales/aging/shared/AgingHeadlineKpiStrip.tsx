import type { OperationalKpiMetric } from '../../../../lib/api'
import AgingKpiCard from './AgingKpiCard'
import type { AgingKpiDef } from './agingKpiDefs'
import type { KpiValueVariant } from '../../../cockpit/KpiCard'

type TrendPoint = {
  month: number
  balance: number
  overdue: number
  dso_days?: number
  dpo_days?: number
  open_documents: number
}

function toVariant(format?: AgingKpiDef['format']): KpiValueVariant {
  switch (format) {
    case 'count':
      return 'count'
    case 'percent':
      return 'percent'
    case 'days':
      return 'days'
    default:
      return 'financial'
  }
}

type Props = {
  defs: AgingKpiDef[]
  metrics?: Record<string, OperationalKpiMetric>
  trendPoints: TrendPoint[]
  trendLoading?: boolean
}

export default function AgingHeadlineKpiStrip({
  defs,
  metrics,
  trendPoints,
  trendLoading,
}: Props) {
  return (
    <div className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-4 gap-4">
      {defs.map(def => (
        <AgingKpiCard
          key={def.key}
          title={def.title}
          metric={metrics?.[def.key] ?? null}
          variant={toVariant(def.format)}
          icon={def.icon}
          accentColor={def.accentColor ?? '#1E3A5F'}
          invertDelta={def.invertDelta}
          trendKey={def.trendKey}
          trendFormat={def.trendFormat}
          trendPoints={trendPoints}
          trendLoading={trendLoading}
        />
      ))}
    </div>
  )
}
