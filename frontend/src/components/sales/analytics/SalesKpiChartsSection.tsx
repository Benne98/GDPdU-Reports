import { useEffect, useMemo, useState } from 'react'
import {
  api,
  SalesFilters,
  SalesCompositionBreakdownResponse,
  SalesGrossSalesTrendResponse,
  SalesProfitMarginScatterResponse,
} from '../../../lib/api'
import type { PeriodSelection } from '../../../lib/periodSelection'
import { periodAnchorYearMonth, periodQueryParams } from '../../../lib/periodSelection'
import SalesGrossSalesTrendChart from './SalesGrossSalesTrendChart'
import SalesProfitMarginScatterChart from './SalesProfitMarginScatterChart'
import SalesCompositionBreakdownChart from './SalesCompositionBreakdownChart'
import {
  loadCompositionDims,
  saveCompositionDims,
  type CompositionMetric,
} from './salesChartRegistry'
import type { SalesColumnDef } from './salesTableTypes'

type Props = {
  period: PeriodSelection
  filters: SalesFilters
}

export default function SalesKpiChartsSection({ period, filters }: Props) {
  const [trendDim, setTrendDim] = useState('end_customer_region')
  const [scatterDim, setScatterDim] = useState('end_customer_region')
  const [metric, setMetric] = useState<CompositionMetric>('gross_sales')
  const [visibleDims, setVisibleDims] = useState<SalesColumnDef[]>(() => loadCompositionDims())

  const [trend, setTrend] = useState<SalesGrossSalesTrendResponse | null>(null)
  const [scatter, setScatter] = useState<SalesProfitMarginScatterResponse | null>(null)
  const [composition, setComposition] = useState<SalesCompositionBreakdownResponse | null>(null)

  const [trendLoading, setTrendLoading] = useState(true)
  const [scatterLoading, setScatterLoading] = useState(true)
  const [compositionLoading, setCompositionLoading] = useState(true)

  const [trendError, setTrendError] = useState<string | null>(null)
  const [scatterError, setScatterError] = useState<string | null>(null)
  const [compositionError, setCompositionError] = useState<string | null>(null)

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
  const dimsParam = useMemo(
    () => visibleDims.map(c => c.field).join(','),
    [visibleDims],
  )

  useEffect(() => {
    setTrendLoading(true)
    setTrendError(null)
    api
      .salesGrossSalesTrend(anchor.year, anchor.month, trendDim, filters, periodOpts)
      .then(setTrend)
      .catch(() => {
        setTrend(null)
        setTrendError('Could not load trend data')
      })
      .finally(() => setTrendLoading(false))
  }, [anchor.year, anchor.month, trendDim, filters, periodOpts])

  useEffect(() => {
    setScatterLoading(true)
    setScatterError(null)
    api
      .salesProfitMarginScatter(anchor.year, anchor.month, scatterDim, filters, periodOpts)
      .then(setScatter)
      .catch(() => {
        setScatter(null)
        setScatterError('Could not load scatter data')
      })
      .finally(() => setScatterLoading(false))
  }, [anchor.year, anchor.month, scatterDim, filters, periodOpts])

  useEffect(() => {
    setCompositionLoading(true)
    setCompositionError(null)
    api
      .salesCompositionBreakdown(anchor.year, anchor.month, {
        ...periodOpts,
        metric,
        dims: dimsParam,
      }, filters)
      .then(setComposition)
      .catch(() => {
        setComposition(null)
        setCompositionError('Could not load breakdown data')
      })
      .finally(() => setCompositionLoading(false))
  }, [anchor.year, anchor.month, metric, dimsParam, filters, periodOpts])

  function handleDimsChange(cols: SalesColumnDef[]) {
    setVisibleDims(cols)
    saveCompositionDims(cols)
  }

  return (
    <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-stretch">
      <div className="flex flex-col gap-4 min-h-[580px]">
        <SalesGrossSalesTrendChart
          data={trend}
          dim={trendDim}
          onDimChange={setTrendDim}
          loading={trendLoading}
          error={trendError}
        />
        <SalesProfitMarginScatterChart
          data={scatter}
          dim={scatterDim}
          onDimChange={setScatterDim}
          loading={scatterLoading}
          error={scatterError}
        />
      </div>
      <div className="lg:min-h-full">
        <SalesCompositionBreakdownChart
          data={composition}
          metric={metric}
          onMetricChange={setMetric}
          visibleDims={visibleDims}
          onDimsChange={handleDimsChange}
          loading={compositionLoading}
          error={compositionError}
        />
      </div>
    </div>
  )
}
