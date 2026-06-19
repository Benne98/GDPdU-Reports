import type { OperationalKpiMetric, ReceivablesTrendPoint } from '../../../lib/api'
import AgingHeadlineKpiStrip from './shared/AgingHeadlineKpiStrip'
import { RECEIVABLES_AGING_KPI_DEFS } from './shared/agingKpiDefs'

export default function ReceivablesAgingKpis({
  kpiMetrics,
  trendPoints = [],
  trendLoading,
}: {
  kpiMetrics?: Record<string, OperationalKpiMetric>
  trendPoints?: ReceivablesTrendPoint[]
  trendLoading?: boolean
}) {
  return (
    <AgingHeadlineKpiStrip
      defs={RECEIVABLES_AGING_KPI_DEFS}
      metrics={kpiMetrics}
      trendPoints={trendPoints}
      trendLoading={trendLoading}
    />
  )
}
