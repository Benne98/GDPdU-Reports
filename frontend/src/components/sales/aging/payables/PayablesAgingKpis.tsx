import type { OperationalKpiMetric, PayablesAgingKpis, PayablesTrendPoint } from '../../../../lib/api'
import AgingHeadlineKpiStrip from '../shared/AgingHeadlineKpiStrip'
import { PAYABLES_AGING_KPI_DEFS } from '../shared/agingKpiDefs'

export default function PayablesAgingKpis({
  kpiMetrics,
  kpis,
  totalOpenGross,
  trendPoints = [],
  trendLoading,
  year,
  month,
}: {
  kpiMetrics?: Record<string, OperationalKpiMetric>
  kpis?: PayablesAgingKpis
  totalOpenGross?: number
  trendPoints?: PayablesTrendPoint[]
  trendLoading?: boolean
  year: number
  month: number
}) {
  return (
    <AgingHeadlineKpiStrip
      defs={PAYABLES_AGING_KPI_DEFS}
      metrics={kpiMetrics}
      kpis={kpis}
      totalOpenGross={totalOpenGross}
      trendPoints={trendPoints}
      trendLoading={trendLoading}
      year={year}
      month={month}
    />
  )
}
