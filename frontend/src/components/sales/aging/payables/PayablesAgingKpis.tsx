import type { OperationalKpiMetric, PayablesTrendPoint } from '../../../../lib/api'
import AgingHeadlineKpiStrip from '../shared/AgingHeadlineKpiStrip'
import { PAYABLES_AGING_KPI_DEFS } from '../shared/agingKpiDefs'

export default function PayablesAgingKpis({
  kpiMetrics,
  trendPoints = [],
  trendLoading,
}: {
  kpiMetrics?: Record<string, OperationalKpiMetric>
  trendPoints?: PayablesTrendPoint[]
  trendLoading?: boolean
}) {
  return (
    <AgingHeadlineKpiStrip
      defs={PAYABLES_AGING_KPI_DEFS}
      metrics={kpiMetrics}
      trendPoints={trendPoints}
      trendLoading={trendLoading}
    />
  )
}
