import { useEffect, useState } from 'react'
import {
  api,
  ReceivablesAgingBand,
  AgingPortfolioTableResponse,
  ReceivablesAgingDimension,
  ReceivablesCustomerRegisterRow,
  ReceivablesCustomerScatterRow,
  OperationalKpiMetric,
  ReceivablesGeoRow,
  ReceivablesHierarchyBreakdownRow,
  ReceivablesStatusSplit,
  ReceivablesTrendPoint,
} from '../../../lib/api'
import ReceivablesAgingKpis from './ReceivablesAgingKpis'
import AgingPortfolioSection from './shared/AgingPortfolioSection'
import ReceivablesConcentrationSection from './ReceivablesConcentrationSection'
import ReceivablesDimensionPanel from './ReceivablesDimensionPanel'
import ReceivablesCustomerRiskSection from './ReceivablesCustomerRiskSection'
import ReceivablesDimensionBarSection from './ReceivablesDimensionBarSection'
import ReceivablesGeographySection from './ReceivablesGeographySection'
import ReceivablesTrendSection from './ReceivablesTrendSection'
import AgingTopGroupsTable from './shared/AgingTopGroupsTable'
import { applyFulfilled } from './shared/applyAgingSettled'
import { useAgingSectionLoad } from './shared/useAgingSectionLoad'
import type { PeriodSelection } from '../../../lib/periodSelection'
import {
  loadReceivablesBreakdownConfig,
  needsComparePm,
  needsComparePy,
  type ReceivablesDimensionBreakdownConfig,
} from './shared/receivablesDimensionBreakdownConfig'
import { useOptionalActionNotesContext } from '../../action-notes/ActionNotesContext'
import { captureChartByTarget } from '../../../lib/actionNotes/chartCapture'

const MONTHS = [
  'January', 'February', 'March', 'April', 'May', 'June',
  'July', 'August', 'September', 'October', 'November', 'December',
]

export default function SalesAgingReceivablesSection({
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

  const [series, setSeries] = useState<ReceivablesAgingBand[]>([])
  const [total, setTotal] = useState(0)
  const [kpiMetrics, setKpiMetrics] = useState<Record<string, OperationalKpiMetric> | undefined>()
  const [statusSplit, setStatusSplit] = useState<ReceivablesStatusSplit | undefined>()
  const [reconciliation, setReconciliation] = useState<'subledger' | 'scaled' | 'synthetic'>('subledger')
  const [breakdownConfig, setBreakdownConfig] = useState<ReceivablesDimensionBreakdownConfig>(() =>
    loadReceivablesBreakdownConfig(),
  )
  const [dimRows, setDimRows] = useState<ReceivablesHierarchyBreakdownRow[]>([])
  const [dimLoading, setDimLoading] = useState(false)
  const [dimError, setDimError] = useState<string | null>(null)
  const [scatter, setScatter] = useState<ReceivablesCustomerScatterRow[]>([])
  const [register, setRegister] = useState<ReceivablesCustomerRegisterRow[]>([])
  const [geo, setGeo] = useState<ReceivablesGeoRow[]>([])
  const [trend, setTrend] = useState<ReceivablesTrendPoint[]>([])
  const [selectedCustomer, setSelectedCustomer] = useState<string | null>(null)
  const [selectedCustomerId, setSelectedCustomerId] = useState<string | null>(null)
  const [portfolioDimension, setPortfolioDimension] = useState<ReceivablesAgingDimension>('customer')
  const [portfolioTable, setPortfolioTable] = useState<AgingPortfolioTableResponse | null>(null)
  const [portfolioTableLoading, setPortfolioTableLoading] = useState(false)
  const [secondaryLoading, setSecondaryLoading] = useState(false)
  const [concentrationTrendOpen, setConcentrationTrendOpen] = useState(false)
  const notesCtx = useOptionalActionNotesContext()

  useEffect(() => {
    if (!notesCtx) return
    notesCtx.registerChartCandidate({
      id: 'receivables-customer-risk-matrix',
      label: 'Customer risk matrix',
      description: 'Receivables customer risk and register section',
      capture: () => captureChartByTarget('receivables-customer-risk-matrix'),
      viewState: { tab: 'aging', view_mode: 'receivables' },
    })
    return () => notesCtx.unregisterChartCandidate('receivables-customer-risk-matrix')
  }, [notesCtx])

  const { initialLoading, refreshing, error, loadedOnce } = useAgingSectionLoad(
    async ({ isCurrent, markReady }) => {
      setSecondaryLoading(true)
      try {
        const settled = await Promise.allSettled([
          api.salesReceivablesAging(year, month, entity),
          api.salesReceivablesCustomers(year, month, entity, 50),
          api.salesReceivablesGeo(year, month, entity, 50),
          api.salesReceivablesTrend(year, month, entity, 12, 'month'),
        ])
        if (!isCurrent()) return

        applyFulfilled(settled[0], isCurrent, aging => {
          setSeries(aging.series)
          setTotal(aging.total_receivables)
          setKpiMetrics(aging.kpi_metrics)
          setStatusSplit(aging.status_split)
          setReconciliation(aging.reconciliation_mode)
          setSelectedCustomer(null)
          setSelectedCustomerId(null)
        })
        applyFulfilled(settled[1], isCurrent, v => {
          setScatter(v.scatter)
          setRegister(v.register)
        })
        applyFulfilled(settled[2], isCurrent, v => setGeo(v.rows))
        applyFulfilled(settled[3], isCurrent, v => setTrend(v.points ?? []))
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
      .salesReceivablesByDimensionHierarchy(breakdownConfig.hierarchy, year, month, entity, {
        view: breakdownConfig.view,
        comparePm: needsComparePm(breakdownConfig.columns),
        comparePy: needsComparePy(breakdownConfig.columns),
      })
      .then(r => {
        if (!cancelled) setDimRows(r.rows)
      })
      .catch(err => {
        if (!cancelled) {
          setDimRows([])
          setDimError(err instanceof Error ? err.message : 'Could not load breakdown')
        }
      })
      .finally(() => {
        if (!cancelled) setDimLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [loadedOnce, breakdownConfig, year, month, entity])

  useEffect(() => {
    if (!loadedOnce) return
    let cancelled = false
    setPortfolioTableLoading(true)
    api
      .salesReceivablesPortfolioTable(portfolioDimension, year, month, entity, 25)
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

  const dsoDays = kpiMetrics?.dso_days?.value

  if (initialLoading) {
    return (
      <p className="text-sm py-8 text-center" style={{ color: '#94A3B8' }}>
        Loading receivables aging…
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

      <section id="ar-portfolio" className="space-y-6 scroll-mt-20">
        <ReceivablesAgingKpis
          kpiMetrics={kpiMetrics}
          trendPoints={trend}
          trendLoading={secondaryLoading}
        />

        <AgingPortfolioSection
          side="receivables"
          title="Receivables portfolio"
          periodLabel={periodLabel}
          total={total}
          series={series}
          statusSplit={statusSplit}
          overdueDays={dsoDays}
          reconciliation={reconciliation}
          reconciliationGlLabel="trade receivables"
          dimension={portfolioDimension}
          onDimensionChange={d => setPortfolioDimension(d as ReceivablesAgingDimension)}
          portfolioTable={portfolioTable}
          tableLoading={portfolioTableLoading}
          viewStorageKey="finssentials.sales.arPortfolio.viewMode.v1"
        />
      </section>

      <section
        id="ar-customers"
        className="space-y-6 scroll-mt-20"
        data-expert-chart-target="receivables-customer-risk-matrix"
      >
        <ReceivablesCustomerRiskSection
          scatter={scatter}
          register={register}
          selectedCustomer={selectedCustomer}
          selectedCustomerId={selectedCustomerId}
          onSelect={(name, customerId) => {
            setSelectedCustomer(name)
            setSelectedCustomerId(customerId ?? null)
          }}
          year={year}
          month={month}
          periodLabel={periodLabel}
          entity={entity}
          loading={secondaryLoading}
        />

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-4 items-stretch">
          <ReceivablesDimensionBarSection
            year={year}
            month={month}
            entity={entity}
            trendExpanded={concentrationTrendOpen}
          />
          <ReceivablesConcentrationSection
            period={period}
            year={year}
            month={month}
            entity={entity}
            onTrendVisibleChange={setConcentrationTrendOpen}
          />
        </div>
      </section>

      <section id="ar-dimensions" className="space-y-6 scroll-mt-20">
        <ReceivablesGeographySection
          year={year}
          month={month}
          entity={entity}
          rows={geo}
          loading={secondaryLoading}
        />
      </section>

      <section
        id="ar-trends"
        className="space-y-6 scroll-mt-20"
        data-expert-chart-target="receivables-customer-register"
      >
        <ReceivablesTrendSection year={year} month={month} entity={entity} />
      </section>

      <section id="ar-tables" className="space-y-6 scroll-mt-20">
        <ReceivablesDimensionPanel
          config={breakdownConfig}
          onConfigChange={setBreakdownConfig}
          rows={dimRows}
          loading={dimLoading}
          error={dimError}
        />
        <AgingTopGroupsTable side="receivables" year={year} month={month} entity={entity} />
      </section>
    </div>
  )
}
