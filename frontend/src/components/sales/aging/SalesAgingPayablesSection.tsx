import { useEffect, useState } from 'react'
import {
  api,
  AgingPortfolioTableResponse,
  PayablesAgingBand,
  PayablesAgingDimension,
  PayablesSupplierRegisterRow,
  PayablesSupplierScatterRow,
  PayablesHierarchyBreakdownRow,
  OperationalKpiMetric,
  PayablesGeoRow,
  PayablesStatusSplit,
  PayablesTrendPoint,
} from '../../../lib/api'
import type { PeriodSelection } from '../../../lib/periodSelection'
import PayablesAgingKpis from './payables/PayablesAgingKpis'
import AgingPortfolioSection from './shared/AgingPortfolioSection'
import PayablesDimensionPanel from './payables/PayablesDimensionPanel'
import PayablesSupplierRiskSection from './payables/PayablesSupplierRiskSection'
import PayablesDimensionBarSection from './payables/PayablesDimensionBarSection'
import PayablesConcentrationSection from './payables/PayablesConcentrationSection'
import PayablesGeographySection from './payables/PayablesGeographySection'
import PayablesTrendSection from './payables/PayablesTrendSection'
import AgingTopGroupsTable from './shared/AgingTopGroupsTable'
import { applyFulfilled } from './shared/applyAgingSettled'
import { useAgingSectionLoad } from './shared/useAgingSectionLoad'
import {
  loadPayablesBreakdownConfig,
  needsComparePm,
  needsComparePy,
  type PayablesDimensionBreakdownConfig,
} from './shared/payablesDimensionBreakdownConfig'

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

export default function SalesAgingPayablesSection({
  period,
  year,
  month,
  legalEntity,
}: {
  period: PeriodSelection
  year: number
  month: number
  legalEntity: string
}) {
  const entity = legalEntity === 'all' ? undefined : legalEntity
  const periodLabel = `${MONTHS[month - 1]} ${year}`

  const [series, setSeries] = useState<PayablesAgingBand[]>([])
  const [total, setTotal] = useState(0)
  const [kpiMetrics, setKpiMetrics] = useState<Record<string, OperationalKpiMetric> | undefined>()
  const [statusSplit, setStatusSplit] = useState<PayablesStatusSplit | undefined>()
  const [reconciliation, setReconciliation] = useState<'subledger' | 'scaled' | 'synthetic'>('subledger')
  const [trend, setTrend] = useState<PayablesTrendPoint[]>([])
  const [dimConfig, setDimConfig] = useState<PayablesDimensionBreakdownConfig>(() => loadPayablesBreakdownConfig())
  const [dimRows, setDimRows] = useState<PayablesHierarchyBreakdownRow[]>([])
  const [dimError, setDimError] = useState<string | null>(null)
  const [dimLoading, setDimLoading] = useState(false)
  const [scatter, setScatter] = useState<PayablesSupplierScatterRow[]>([])
  const [register, setRegister] = useState<PayablesSupplierRegisterRow[]>([])
  const [geo, setGeo] = useState<PayablesGeoRow[]>([])
  const [selectedSupplier, setSelectedSupplier] = useState<string | null>(null)
  const [concentrationTrendOpen, setConcentrationTrendOpen] = useState(false)
  const [portfolioDimension, setPortfolioDimension] = useState<PayablesAgingDimension>('supplier')
  const [portfolioTable, setPortfolioTable] = useState<AgingPortfolioTableResponse | null>(null)
  const [portfolioTableLoading, setPortfolioTableLoading] = useState(false)
  const [secondaryLoading, setSecondaryLoading] = useState(false)

  const { initialLoading, refreshing, error, loadedOnce } = useAgingSectionLoad(
    async ({ isCurrent, markReady }) => {
      setSecondaryLoading(true)
      try {
        const settled = await Promise.allSettled([
          api.salesPayablesAging(year, month, entity),
          api.salesPayablesTrend(year, month, entity, 12, 'month'),
          api.salesPayablesSuppliers(year, month, entity, 50),
          api.salesPayablesGeo(year, month, entity, 20),
        ])
        if (!isCurrent()) return

        applyFulfilled(settled[0], isCurrent, aging => {
          setSeries(aging.series)
          setTotal(aging.total_payables)
          setKpiMetrics(aging.kpi_metrics)
          setStatusSplit(aging.status_split)
          setReconciliation(aging.reconciliation_mode)
          setSelectedSupplier(null)
        })
        applyFulfilled(settled[1], isCurrent, v => setTrend(v.points))
        applyFulfilled(settled[2], isCurrent, v => {
          setScatter(v.scatter)
          setRegister(v.register)
        })
        applyFulfilled(settled[3], isCurrent, v => setGeo(v.rows))
        markReady()
      } finally {
        if (isCurrent()) setSecondaryLoading(false)
      }
    },
    [year, month, entity],
  )

  useEffect(() => {
    if (!loadedOnce) return
    let cancelled = false
    setDimLoading(true)
    setDimError(null)
    api
      .salesPayablesByDimensionHierarchy(dimConfig.hierarchy, year, month, entity, {
        view: dimConfig.view,
        comparePm: needsComparePm(dimConfig.columns),
        comparePy: needsComparePy(dimConfig.columns),
        limit: 1500,
      })
      .then(r => {
        if (!cancelled) setDimRows(r.rows)
      })
      .catch(err => {
        if (!cancelled) {
          setDimRows([])
          setDimError(err instanceof Error ? err.message : 'Failed to load breakdown')
        }
      })
      .finally(() => {
        setDimLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [loadedOnce, dimConfig, year, month, entity])

  useEffect(() => {
    if (!loadedOnce) return
    let cancelled = false
    setPortfolioTableLoading(true)
    api
      .salesPayablesPortfolioTable(portfolioDimension, year, month, entity, 25)
      .then(r => {
        if (!cancelled) setPortfolioTable(r)
      })
      .catch(() => {
        if (!cancelled) setPortfolioTable(null)
      })
      .finally(() => {
        if (!cancelled) setPortfolioTableLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [loadedOnce, portfolioDimension, year, month, entity])

  const dpoDays = kpiMetrics?.dpo_days?.value

  if (initialLoading) {
    return (
      <p className="text-sm py-8 text-center" style={{ color: '#94A3B8' }}>
        Loading payables aging…
      </p>
    )
  }

  if (error && !loadedOnce) {
    return (
      <p className="text-sm py-8 text-center" style={{ color: '#DC2626' }}>
        {error}
      </p>
    )
  }

  if (!loadedOnce) return null

  return (
    <div className={refreshing ? 'space-y-8 opacity-60 pointer-events-none transition-opacity' : 'space-y-8'}>
      {error && (
        <p className="text-xs text-center" style={{ color: '#D97706' }}>
          Could not refresh: {error}
        </p>
      )}

      <section id="ap-portfolio" className="space-y-6 scroll-mt-20">
        <PayablesAgingKpis
          kpiMetrics={kpiMetrics}
          trendPoints={trend}
          trendLoading={secondaryLoading}
        />

        <AgingPortfolioSection
          side="payables"
          title="Payables portfolio"
          periodLabel={periodLabel}
          total={total}
          series={series}
          statusSplit={statusSplit}
          overdueDays={dpoDays}
          reconciliation={reconciliation}
          reconciliationGlLabel="trade payables"
          dimension={portfolioDimension}
          onDimensionChange={d => setPortfolioDimension(d as PayablesAgingDimension)}
          portfolioTable={portfolioTable}
          tableLoading={portfolioTableLoading}
          viewStorageKey="finssentials.sales.apPortfolio.viewMode.v1"
        />
      </section>

      <section id="ap-suppliers" className="space-y-6 scroll-mt-20">
        <PayablesSupplierRiskSection
          scatter={scatter}
          register={register}
          selectedSupplier={selectedSupplier}
          onSelect={setSelectedSupplier}
          year={year}
          month={month}
          periodLabel={periodLabel}
          entity={entity}
          loading={secondaryLoading}
        />
      </section>

      <section id="ap-overview-pair" className="space-y-6 scroll-mt-20">
        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-stretch">
          <PayablesDimensionBarSection
            year={year}
            month={month}
            entity={entity}
            trendExpanded={concentrationTrendOpen}
          />
          <PayablesConcentrationSection
            period={period}
            year={year}
            month={month}
            entity={entity}
            onTrendVisibleChange={setConcentrationTrendOpen}
          />
        </div>
      </section>

      <section id="ap-dimensions" className="space-y-6 scroll-mt-20">
        <PayablesGeographySection year={year} month={month} entity={entity} rows={geo} loading={secondaryLoading} />
      </section>

      <section id="ap-trends" className="space-y-6 scroll-mt-20">
        <PayablesTrendSection year={year} month={month} entity={entity} />
      </section>

      <section id="ap-tables" className="space-y-6 scroll-mt-20">
        <PayablesDimensionPanel config={dimConfig} onConfigChange={setDimConfig} rows={dimRows} loading={dimLoading} error={dimError} />
        <AgingTopGroupsTable side="payables" year={year} month={month} entity={entity} />
      </section>
    </div>
  )
}


