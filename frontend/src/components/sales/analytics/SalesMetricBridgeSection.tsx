import { useEffect, useMemo, useState } from 'react'
import {
  api,
  SalesFilters,
  SalesMetricBridgeResponse,
} from '../../../lib/api'
import type { PeriodSelection } from '../../../lib/periodSelection'
import { periodAnchorYearMonth, periodQueryParams } from '../../../lib/periodSelection'
import SalesMetricDimensionBridgeChart from './SalesMetricDimensionBridgeChart'
import type { CompositionMetric } from './salesChartRegistry'

type Props = {
  period: PeriodSelection
  filters: SalesFilters
}

export default function SalesMetricBridgeSection({ period, filters }: Props) {
  const [metric, setMetric] = useState<CompositionMetric>('gross_sales')
  const [dim, setDim] = useState('end_customer_region')
  const [data, setData] = useState<SalesMetricBridgeResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  const anchor = periodAnchorYearMonth(period)
  const pq = periodQueryParams(period)
  const periodOpts = useMemo(
    () => ({
      period_grain: pq.period_grain as string,
      iso_year: pq.iso_year as number | undefined,
      iso_week: pq.iso_week as number | undefined,
    }),
    [pq.period_grain, pq.iso_year, pq.iso_week],
  )

  useEffect(() => {
    setLoading(true)
    setError(null)
    api
      .salesMetricBridge(anchor.year, anchor.month, dim, { ...periodOpts, metric }, filters)
      .then(setData)
      .catch(() => {
        setData(null)
        setError('Could not load bridge data')
      })
      .finally(() => setLoading(false))
  }, [anchor.year, anchor.month, dim, metric, filters, periodOpts])

  return (
    <SalesMetricDimensionBridgeChart
      data={data}
      metric={metric}
      onMetricChange={setMetric}
      dim={dim}
      onDimChange={setDim}
      loading={loading}
      error={error}
    />
  )
}
