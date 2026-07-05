import type { OperationalKpiMetric, ReceivablesAgingKpis, ReceivablesTrendPoint } from '../../../lib/api'
import AgingHeadlineKpiStrip from './shared/AgingHeadlineKpiStrip'
import { RECEIVABLES_AGING_KPI_DEFS } from './shared/agingKpiDefs'

export default function ReceivablesAgingKpis({
  kpiMetrics,
  kpis,
  totalOpenGross,
  trendPoints = [],
  trendLoading,
  year,
  month,
}: {
  kpiMetrics?: Record<string, OperationalKpiMetric>
  kpis?: ReceivablesAgingKpis
  totalOpenGross?: number
  trendPoints?: ReceivablesTrendPoint[]
  trendLoading?: boolean
  year: number
  month: number
}) {
  return (
    <AgingHeadlineKpiStrip
      defs={RECEIVABLES_AGING_KPI_DEFS}
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
