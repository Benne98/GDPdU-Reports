import { useMemo } from 'react'
import type { OperationalKpiMetric } from '../../../../lib/api'
import AgingKpiCard from './AgingKpiCard'
import type { AgingKpiDef } from './agingKpiDefs'
import { enrichAgingKpiMetrics, resolveAgingKpiMetrics } from './agingKpiTrendDeltas'
import type { KpiValueVariant } from '../../../cockpit/KpiCard'

type TrendPoint = {
  year: number
  month: number
  gross_balance?: number
  balance: number
  overdue: number
  overdue_pct?: number
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
  kpis?: {
    overdue_pct?: number
    dso_days?: number
    dpo_days?: number
    open_documents?: number
    before_due?: number
    overdue?: number
  }
  totalOpenGross?: number
  trendPoints: TrendPoint[]
  trendLoading?: boolean
  year: number
  month: number
}

export default function AgingHeadlineKpiStrip({
  defs,
  metrics,
  kpis,
  totalOpenGross,
  trendPoints,
  trendLoading,
  year,
  month,
}: Props) {
  const enrichedMetrics = useMemo(() => {
    const resolved = resolveAgingKpiMetrics(metrics, kpis, totalOpenGross, defs)
    return enrichAgingKpiMetrics(resolved, trendPoints, year, month, defs)
  }, [metrics, kpis, totalOpenGross, trendPoints, year, month, defs])

  const colClass = 'grid-cols-1 sm:grid-cols-2 xl:grid-cols-4'

  return (
    <div className={`grid gap-4 ${colClass}`}>
      {defs.map(def => (
        <AgingKpiCard
          key={def.key}
          title={def.title}
          metric={enrichedMetrics?.[def.key] ?? null}
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
