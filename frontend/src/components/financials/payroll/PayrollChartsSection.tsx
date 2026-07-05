import type { PersonnelDimension, PersonnelMovementsResponse } from '../../../lib/api'
import PayrollByDimensionChart from './PayrollByDimensionChart'
import PayrollFteTrendChart from './PayrollFteTrendChart'
import { BRAND, SALES_CHART_CARD_CLASS } from '../../sales/analytics/salesChartTheme'

type Props = {
  movements: PersonnelMovementsResponse | null
  chartDimension: PersonnelDimension
  onChartDimensionChange: (d: PersonnelDimension) => void
  trendMetric: string
  onTrendMetricChange: (id: string) => void
  anchorLabel: string
  priorLabel: string
  loading?: boolean
}

export default function PayrollChartsSection({
  movements,
  chartDimension,
  onChartDimensionChange,
  trendMetric,
  onTrendMetricChange,
  anchorLabel,
  priorLabel,
  loading,
}: Props) {
  if (!movements && !loading) return null

  const payrollRows = movements?.charts.payroll_by_dimension ?? []
  const fteTrend = movements?.charts.fte_trend ?? []
  const trendMetrics = movements?.charts.trend_metrics ?? []

  return (
    <div className="grid grid-cols-1 lg:grid-cols-[minmax(0,1.1fr)_minmax(0,0.9fr)] gap-4">
      <div className={SALES_CHART_CARD_CLASS} style={{ borderColor: BRAND.border }}>
        <PayrollByDimensionChart
          rows={payrollRows}
          dimension={chartDimension}
          onDimensionChange={onChartDimensionChange}
          anchorLabel={anchorLabel}
          priorLabel={priorLabel}
          loading={loading}
        />
      </div>
      <div className={SALES_CHART_CARD_CLASS} style={{ borderColor: BRAND.border }}>
        <PayrollFteTrendChart
          rows={fteTrend}
          trendMetrics={trendMetrics}
          trendMetric={trendMetric}
          onTrendMetricChange={onTrendMetricChange}
          priorYearLabel="Prior year"
          loading={loading}
        />
      </div>
    </div>
  )
}
