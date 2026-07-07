import { useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import {
  DollarSign, TrendingUp, Users, Percent, Package, UserRound,
} from 'lucide-react'
import {
  api,
  SalesFilters,
  SalesHeadlineKpis,
  SalesTopOrder,
  SalesTopEntity,
  SalesTopEntitiesColLabels,
  type SalesPlanSource,
  SalesGeoCountry,
  SalesBreakdownResponse,
  SalesGeoTrendResponse,
} from '../../../lib/api'
import KpiCard from '../../cockpit/KpiCard'
import WorldMap from '../../sales/WorldMap'
import SalesBreakdownTable from '../../sales/analytics/SalesBreakdownTable'
import {
  buildBreakdownColumnCatalog,
  defaultBreakdownColumns,
  loadBreakdownConfig,
  sanitizeBreakdownDims,
  type BreakdownColumnDef,
  type BreakdownDimConfig,
  type BreakdownMiscConfig,
} from '../../sales/analytics/salesBreakdownRegistry'
import GeoTrendChart from '../../sales/GeoTrendChart'
import SalesLocationBubbleMap from '../../sales/SalesLocationBubbleMap'
import SalesGrossMarginSection from '../../sales/analytics/SalesGrossMarginSection'
import SalesChurnSection from '../../sales/analytics/SalesChurnSection'
import SalesTopOrdersSection from '../../sales/analytics/SalesTopOrdersSection'
import SalesTopEntitiesSection from '../../sales/analytics/SalesTopEntitiesSection'
import SalesKpiChartsSection from '../../sales/analytics/SalesKpiChartsSection'
import SalesMetricBridgeSection from '../../sales/analytics/SalesMetricBridgeSection'
import { buildSalesHeadlineCards } from '../../sales/analytics/salesHeadlineKpiCards'
import SalesAnalyticsNav from '../../sales/analytics/SalesAnalyticsNav'
import { enrichGeoLocations, hasMapCoords } from '../../../lib/salesGeoCoords'
import type { PeriodSelection } from '../../../lib/periodSelection'
import { periodAnchorYearMonth, periodCacheKey, periodQueryParams } from '../../../lib/periodSelection'
import { normalizeTopEntityColLabels } from '../../sales/analytics/salesTopEntityColLabels'
import { useOptionalActionNotesContext } from '../../action-notes/ActionNotesContext'
import { captureChartByTarget } from '../../../lib/actionNotes/chartCapture'
import { buildTopEntityNarrative, buildTopOrdersNarrative } from '../../sales/analytics/salesProfitabilityNarratives'
import { useChartLoadReporter } from '../../../hooks/useChartLoadReporter'

function filtersKey(f: SalesFilters): string {
  return JSON.stringify(f)
}

const HEADLINE_KPI_ICONS = [
  DollarSign, TrendingUp, Package, UserRound, Percent, Users,
] as const

const HEADLINE_KPI_COUNT = 6

function AnalyticsSection({ id, children }: { id: string; children: ReactNode }) {
  return (
    <div id={id} className="scroll-mt-20">
      {children}
    </div>
  )
}

interface Props {
  period: PeriodSelection
  /** Selected legal_entity_codes; empty/undefined = all entities (consolidated). */
  entities?: string[]
}

export default function GlProfitabilityTab({ period, entities }: Props) {
  const entityKey = (entities ?? []).join(',')
  const filters = useMemo<SalesFilters>(() => {
    if (!entities || entities.length === 0) return {}
    return { entity: entities }
  }, [entityKey])
  const anchor = periodAnchorYearMonth(period)
  const y = anchor.year
  const month = anchor.month
  const periodKey = periodCacheKey(period)
  const fk = filtersKey(filters)

  const periodOpts = useMemo(() => {
    const pq = periodQueryParams(period)
    return {
      period_grain: (pq.period_grain as string) ?? 'month',
      iso_year: pq.iso_year as number | undefined,
      iso_week: pq.iso_week as number | undefined,
    }
  }, [periodKey])

  const [headline, setHeadline] = useState<SalesHeadlineKpis | null>(null)
  const kpiStripRef = useRef<HTMLDivElement>(null)
  const [navMaxHeight, setNavMaxHeight] = useState<number | undefined>(undefined)
  const [headlineLoading, setHeadlineLoading] = useState(true)
  const [headlineError, setHeadlineError] = useState<string | null>(null)

  const [orders, setOrders] = useState<SalesTopOrder[]>([])
  const [ordersLoading, setOrdersLoading] = useState(true)

  const [customers, setCustomers] = useState<SalesTopEntity[]>([])
  const [suppliers, setSuppliers] = useState<SalesTopEntity[]>([])
  const [colLabels, setColLabels] = useState<SalesTopEntitiesColLabels>({
    cm: 'Current month',
    pm: 'Prior month',
    py_cm: 'Prior year',
    ytd: 'YTD',
  })
  const [custRank, setCustRank] = useState<'cm' | 'ytd' | 'mtd'>('cm')
  const [supRank, setSupRank] = useState<'cm' | 'ytd' | 'mtd'>('cm')
  const [planMix, setPlanMix] = useState<SalesPlanSource>('py_proxy')
  const [supplierPlanMix, setSupplierPlanMix] = useState<SalesPlanSource>('py_proxy')
  const [entLoading, setEntLoading] = useState(true)

  const [geoCountries, setGeoCountries] = useState<SalesGeoCountry[]>([])
  const [geoMapLoading, setGeoMapLoading] = useState(true)
  const [breakdownData, setBreakdownData] = useState<SalesBreakdownResponse | null>(null)
  const [breakdownLoading, setBreakdownLoading] = useState(true)
  const [breakdownError, setBreakdownError] = useState<string | null>(null)
  const initialBreakdown = loadBreakdownConfig({ pm: 'Prior month', cm: 'Current month', plan_cm: 'Plan' })
  const [breakdownDims, setBreakdownDims] = useState<BreakdownDimConfig>(() => initialBreakdown.dims)
  const [breakdownCols, setBreakdownCols] = useState<BreakdownColumnDef[]>(() => initialBreakdown.columns)
  const [breakdownMisc, setBreakdownMisc] = useState<BreakdownMiscConfig>(() => initialBreakdown.misc)
  const [geoTrend, setGeoTrend] = useState<SalesGeoTrendResponse>({ periods: [], regions: [] })
  const [geoGrain, setGeoGrain] = useState<'year' | 'month' | 'week'>('month')
  const [geoTrendDim, setGeoTrendDim] = useState('end_customer_region')
  const [geoTrendLoading, setGeoTrendLoading] = useState(true)
  const [selectedCountry, setSelectedCountry] = useState<string | null>(null)
  const [countryLocs, setCountryLocs] = useState<import('../../../lib/api').SalesGeoCountryLocationsResponse | null>(null)
  const [locLoading, setLocLoading] = useState(false)
  const notesCtx = useOptionalActionNotesContext()

  const pageLoading = headlineLoading || ordersLoading || entLoading
  const pageError = headlineError
  useChartLoadReporter('gl-profitability', pageLoading, pageError)

  useEffect(() => {
    if (!notesCtx) return
    notesCtx.registerChartCandidate({
      id: 'sales-geography-chart',
      label: 'Sales geography chart',
      description: 'Region/country revenue distribution',
      capture: () => captureChartByTarget('sales-geography-chart'),
      viewState: { tab: 'profitability' },
    })
    notesCtx.registerChartCandidate({
      id: 'sales-breakdown-chart',
      label: 'Sales breakdown chart',
      description: 'Region / entity / customer breakdown',
      capture: () => captureChartByTarget('sales-breakdown-chart'),
      viewState: { tab: 'profitability' },
    })
    return () => {
      notesCtx.unregisterChartCandidate('sales-geography-chart')
      notesCtx.unregisterChartCandidate('sales-breakdown-chart')
    }
  }, [notesCtx])


  useEffect(() => {
    let cancelled = false
    setHeadlineLoading(true)
    setHeadlineError(null)
    void api
      .salesHeadlineKpis(y, month, filters, periodOpts)
      .then(data => {
        if (!cancelled) setHeadline(data)
      })
      .catch((e: unknown) => {
        if (!cancelled) {
          setHeadline(null)
          setHeadlineError(e instanceof Error ? e.message : 'Failed to load KPIs')
        }
      })
      .finally(() => {
        if (!cancelled) setHeadlineLoading(false)
      })
    return () => { cancelled = true }
  }, [y, month, fk, periodKey, periodOpts.period_grain, periodOpts.iso_year, periodOpts.iso_week])

  const headlineCards = useMemo(
    () => (headline ? buildSalesHeadlineCards(headline, period) : []),
    [headline, periodKey],
  )

  useEffect(() => {
    const el = kpiStripRef.current
    if (!el) return
    const syncHeight = () => {
      const h = el.offsetHeight
      if (h > 0) setNavMaxHeight(h)
    }
    syncHeight()
    const ro = new ResizeObserver(syncHeight)
    ro.observe(el)
    window.addEventListener('resize', syncHeight)
    return () => {
      ro.disconnect()
      window.removeEventListener('resize', syncHeight)
    }
  }, [headlineLoading, headlineCards.length])

  useEffect(() => {
    let cancelled = false
    setOrdersLoading(true)
    void api
      .salesTopOrders(y, month, filters, { ...periodOpts, limit: 20 })
      .then(data => {
        if (!cancelled) setOrders(data)
      })
      .catch(() => {
        if (!cancelled) setOrders([])
      })
      .finally(() => {
        if (!cancelled) setOrdersLoading(false)
      })
    return () => { cancelled = true }
  }, [y, month, fk, periodKey, periodOpts.period_grain, periodOpts.iso_year, periodOpts.iso_week])

  useEffect(() => {
    if (period.grain === 'month' && custRank === 'mtd') setCustRank('cm')
    if (period.grain === 'month' && supRank === 'mtd') setSupRank('cm')
  }, [period.grain, custRank, supRank])

  useEffect(() => {
    let cancelled = false
    setEntLoading(true)
    void Promise.all([
      api.salesTopEntities(y, month, 'customer', custRank, 'invoiced', filters, {
        ...periodOpts,
        limit: 5000,
      }),
      api.salesTopEntities(y, month, 'supplier', supRank, 'invoiced', filters, {
        ...periodOpts,
        limit: 5000,
      }),
    ])
      .then(([c, s]) => {
        if (!cancelled) {
          setCustomers(c.rows)
          setSuppliers(s.rows)
          setColLabels(
            normalizeTopEntityColLabels(y, month, periodOpts.period_grain, c.col_labels),
          )
          setPlanMix(c.plan_mix ?? 'py_proxy')
          setSupplierPlanMix(s.plan_mix ?? 'py_proxy')
        }
      })
      .catch(() => {
        if (!cancelled) {
          setCustomers([])
          setSuppliers([])
        }
      })
      .finally(() => {
        if (!cancelled) setEntLoading(false)
      })
    return () => { cancelled = true }
  }, [y, month, custRank, supRank, fk, periodKey, periodOpts.period_grain, periodOpts.iso_year, periodOpts.iso_week])

  useEffect(() => {
    let cancelled = false
    setGeoMapLoading(true)
    void api.salesGeoCountries(y, month, 'invoiced', filters)
      .then(data => { if (!cancelled) setGeoCountries(data) })
      .catch(() => { if (!cancelled) setGeoCountries([]) })
      .finally(() => { if (!cancelled) setGeoMapLoading(false) })
    setGeoTrendLoading(true)
    void api
      .salesGeoTrend(y, month, geoGrain, geoTrendDim, 'invoiced', filters)
      .then(data => { if (!cancelled) setGeoTrend(data) })
      .catch(() => { if (!cancelled) setGeoTrend({ periods: [], regions: [] }) })
      .finally(() => { if (!cancelled) setGeoTrendLoading(false) })
    return () => { cancelled = true }
  }, [y, month, geoGrain, geoTrendDim, fk])

  useEffect(() => {
    let cancelled = false
    const dims = sanitizeBreakdownDims(breakdownDims)
    setBreakdownLoading(true)
    setBreakdownError(null)
    void api
      .salesBreakdownTable(y, month, dims, filters, periodOpts)
      .then(data => {
        if (cancelled) return
        if (!Array.isArray(data.rows)) {
          throw new Error('Unexpected breakdown response')
        }
        setBreakdownData(data)
        setBreakdownError(null)
        const catalog = buildBreakdownColumnCatalog(data.col_labels)
        setBreakdownCols(prev => {
          const ids = prev.map(c => c.id)
          const mapped = ids.map(id => catalog.find(c => c.id === id)).filter((c): c is BreakdownColumnDef => !!c)
          return mapped.length ? mapped : defaultBreakdownColumns(data.col_labels)
        })
      })
      .catch((err: unknown) => {
        if (cancelled) return
        setBreakdownData(null)
        const msg = err instanceof Error ? err.message : 'Failed to load breakdown table'
        setBreakdownError(
          msg.includes('404')
            ? 'Breakdown API not found — restart the backend (pm2 restart finssentials-api or rebuild the API container).'
            : msg,
        )
      })
      .finally(() => {
        if (!cancelled) setBreakdownLoading(false)
      })

    return () => { cancelled = true }
  }, [
    y,
    month,
    fk,
    periodKey,
    periodOpts.period_grain,
    periodOpts.iso_year,
    periodOpts.iso_week,
    breakdownDims.dim_top,
    breakdownDims.dim_mid,
    breakdownDims.dim_bottom,
    breakdownMisc,
  ])

  useEffect(() => {
    if (!selectedCountry) {
      setCountryLocs(null)
      return
    }
    setCountryLocs(null)
    setLocLoading(true)
    api.salesGeoCountryLocations(selectedCountry, y, month, filters, periodOpts)
      .then(data => {
        const locations = enrichGeoLocations(data.locations ?? [])
        setCountryLocs({
          ...data,
          locations,
          total_count: data.total_count ?? locations.length,
          mapped_count: locations.filter(hasMapCoords).length,
        })
      })
      .catch(() => setCountryLocs(null))
      .finally(() => setLocLoading(false))
  }, [selectedCountry, y, month, fk, periodKey, periodOpts.period_grain, periodOpts.iso_year, periodOpts.iso_week])

  const orderNarrative = useMemo(() => buildTopOrdersNarrative(orders), [orders])
  const customerNarrative = useMemo(
    () => buildTopEntityNarrative({
      kind: 'customer',
      rows: customers,
      rankBy: custRank,
      colLabels,
      planMix,
      periodGrain: period.grain === 'week' ? 'week' : 'month',
    }),
    [customers, custRank, colLabels, planMix, period.grain],
  )
  const supplierNarrative = useMemo(
    () => buildTopEntityNarrative({
      kind: 'supplier',
      rows: suppliers,
      rankBy: supRank,
      colLabels,
      planMix: supplierPlanMix,
      periodGrain: period.grain === 'week' ? 'week' : 'month',
    }),
    [suppliers, supRank, colLabels, supplierPlanMix, period.grain],
  )

  return (
    <div className="flex flex-col gap-6">
      {/* KPI strip (2×3) + section navigation (nav scrolls within KPI strip height) */}
      <div className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_min(300px,22rem)] gap-4 items-start">
        <div
          ref={kpiStripRef}
          className="grid grid-cols-1 sm:grid-cols-2 lg:grid-cols-3 gap-4 content-start"
        >
          {headlineLoading ? (
            Array.from({ length: HEADLINE_KPI_COUNT }).map((_, i) => (
              <div
                key={i}
                className="rounded-xl p-5 animate-pulse"
                style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', minHeight: 152 }}
              >
                <div className="h-3 w-20 rounded mb-4" style={{ background: '#E2E8F0' }} />
                <div className="h-8 w-28 rounded mb-4" style={{ background: '#E2E8F0' }} />
                <div className="h-3 w-full rounded mb-2" style={{ background: '#F1F5F9' }} />
                <div className="h-3 rounded" style={{ background: '#F1F5F9', width: '80%' }} />
              </div>
            ))
          ) : headline ? (
            headlineCards.map((card, i) => {
              const Icon = HEADLINE_KPI_ICONS[i] ?? DollarSign
              return (
                <KpiCard
                  key={card.title}
                  title={card.title}
                  value={card.value}
                  variant={card.variant}
                  invertDelta={card.invertDelta}
                  deltaPm={card.deltaPrior}
                  deltaSmly={card.deltaPriorYear}
                  deltaPmLabel={card.deltaPriorLabel}
                  deltaSmlyLabel={card.deltaPriorYearLabel}
                  icon={Icon}
                  index={i}
                  animateEntry={false}
                />
              )
            })
          ) : (
            Array.from({ length: HEADLINE_KPI_COUNT }).map((_, i) => (
              <div
                key={i}
                className="rounded-xl p-5 flex items-center justify-center text-xs"
                style={{ background: '#F8FAFC', border: '1px solid #E2E8F0', color: '#94A3B8', minHeight: 152 }}
              >
                {i === 0 ? (headlineError ?? 'No data') : ''}
              </div>
            ))
          )}
        </div>
        <div
          className="hidden xl:flex w-full min-h-0 shrink-0"
          style={
            navMaxHeight != null
              ? { height: navMaxHeight, maxHeight: navMaxHeight }
              : { maxHeight: 340 }
          }
        >
          <SalesAnalyticsNav className="flex flex-col flex-1 min-h-0 w-full" />
        </div>
      </div>
      <SalesAnalyticsNav className="xl:hidden w-full" />
      {headlineError && (
        <p className="text-xs" style={{ color: '#DC2626' }}>{headlineError}</p>
      )}

      <AnalyticsSection id="sales-analytics-kpi-charts">
        <SalesKpiChartsSection period={period} filters={filters} />
      </AnalyticsSection>

      <AnalyticsSection id="sales-analytics-metric-bridge">
        <SalesMetricBridgeSection period={period} filters={filters} />
      </AnalyticsSection>

      <AnalyticsSection id="sales-analytics-top-orders">
        <SalesTopOrdersSection
          rows={orders}
          loading={ordersLoading}
          reportIntro={orderNarrative.intro}
          bullets={orderNarrative.bullets}
        />
      </AnalyticsSection>

      <AnalyticsSection id="sales-analytics-top-customers">
      <SalesTopEntitiesSection
        title="Top Customers"
        subtitle="kEUR invoiced — tiered by cumulative gross sales"
        tableId="top-customers"
        rows={customers}
        colLabels={colLabels}
        loading={entLoading}
        rankBy={custRank}
        onRankByChange={setCustRank}
        extended
        finTableStyle
        stripLegalNames
        entityKindLabel="customers"
        periodGrain={period.grain === 'week' ? 'week' : 'month'}
        reportIntro={customerNarrative.intro}
        bullets={customerNarrative.bullets}
        exportName="Top_Customers"
      />
      </AnalyticsSection>

      <AnalyticsSection id="sales-analytics-top-suppliers">
      <SalesTopEntitiesSection
        title="Top Suppliers"
        subtitle="kEUR supplier cost — invoiced basis"
        tableId="top-suppliers"
        rows={suppliers}
        colLabels={colLabels}
        loading={entLoading}
        rankBy={supRank}
        onRankByChange={setSupRank}
        extended
        finTableStyle
        partnerLabel="Supplier"
        entityKindLabel="suppliers"
        periodGrain={period.grain === 'week' ? 'week' : 'month'}
        reportIntro={supplierNarrative.intro}
        bullets={supplierNarrative.bullets}
        exportName="Top_Suppliers"
      />
      </AnalyticsSection>

      <AnalyticsSection id="sales-analytics-geography">
      <div
        className="rounded-xl p-5"
        style={{ background: '#FFFFFF', border: '1px solid #E2E8F0' }}
        id="sales-geography-chart"
        data-expert-chart-target="sales-geography-chart"
      >
        <h3 className="text-sm font-semibold mb-1" style={{ color: '#1E3A5F' }}>Revenue by Region</h3>
        <p className="text-xs mb-4" style={{ color: '#94A3B8' }}>Click a region bar to drill into the location map — click again to close</p>
        <div className="min-h-[560px]">
          <WorldMap
            data={geoCountries}
            loading={geoMapLoading}
            selectedCountry={selectedCountry}
            onCountryClick={country => setSelectedCountry(prev => (prev === country ? null : country))}
          />
        </div>
        {selectedCountry && (
          <div className="mt-4">
            <h4 className="text-xs font-semibold mb-2" style={{ color: '#1E3A5F' }}>
              Locations in {selectedCountry}
              {countryLocs && countryLocs.total_count > 0
                ? ` (${countryLocs.mapped_count}/${countryLocs.total_count} on map)`
                : ''}
            </h4>
            <SalesLocationBubbleMap
              country={selectedCountry}
              locations={countryLocs?.locations ?? []}
              totalCount={countryLocs?.total_count ?? 0}
              loading={locLoading}
            />
          </div>
        )}
      </div>
      </AnalyticsSection>

      <AnalyticsSection id="sales-analytics-breakdown">
      <div id="sales-breakdown-chart" data-expert-chart-target="sales-breakdown-chart">
        <SalesBreakdownTable
          data={breakdownData}
          loading={breakdownLoading}
          error={breakdownError}
          dims={breakdownDims}
          misc={breakdownMisc}
          columns={breakdownCols}
          onDimsChange={d => setBreakdownDims(sanitizeBreakdownDims(d))}
          onMiscChange={setBreakdownMisc}
          onColumnsChange={setBreakdownCols}
        />
      </div>
      </AnalyticsSection>

      <AnalyticsSection id="sales-analytics-revenue-trend">
      <GeoTrendChart
        data={geoTrend}
        grain={geoGrain}
        dim={geoTrendDim}
        onGrainChange={setGeoGrain}
        onDimChange={setGeoTrendDim}
        loading={geoTrendLoading}
      />
      </AnalyticsSection>

      <AnalyticsSection id="sales-analytics-dimension-performance">
        <SalesGrossMarginSection period={period} filters={filters} />
      </AnalyticsSection>

      <AnalyticsSection id="sales-analytics-churn">
        <SalesChurnSection period={period} filters={filters} />
      </AnalyticsSection>
    </div>
  )
}
