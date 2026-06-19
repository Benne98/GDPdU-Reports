import type { SalesDimensionPerformanceMetric, SalesDimensionPeriodScope } from '../../../lib/api'

export const DIMENSION_PERF_METRICS: { value: SalesDimensionPerformanceMetric; label: string }[] = [
  { value: 'gross_sales', label: 'Gross sales' },
  { value: 'gross_profit', label: 'Gross profit' },
  { value: 'units_sold', label: 'Units sold' },
]

export const DIMENSION_PERIOD_SCOPES: { value: SalesDimensionPeriodScope; label: string; hint: string }[] = [
  {
    value: 'month',
    label: 'Current month',
    hint: 'Compare to prior month and plan',
  },
  {
    value: 'ytd',
    label: 'YTD',
    hint: 'Year-to-date vs prior-year YTD and plan',
  },
  {
    value: 'py_month',
    label: 'Prior-year month',
    hint: 'Same month prior year vs its prior month',
  },
]

export function dimensionPerfMetricLabel(metric: SalesDimensionPerformanceMetric): string {
  return DIMENSION_PERF_METRICS.find(m => m.value === metric)?.label ?? metric
}
