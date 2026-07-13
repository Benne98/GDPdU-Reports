/**
 * Shared statements page — faithful port of the legacy FinancialsPage,
 * fixed to a single statement kind per route (GDPdU target nav §5).
 * All section components are the verbatim legacy components.
 */
import { useState, useEffect, useCallback, useRef, Component, ReactNode } from 'react'
import { motion } from 'framer-motion'
import { SlidersHorizontal, ChevronDown } from 'lucide-react'
import {
  api,
  Entity,
  type FinPeriodParams,
  FinancialStatementResponse,
  ConsolidationResponse,
  MonthlyResponse,
  type ErFlowResponse,
  type ErSnapshotResponse,
  type WeeklyBreakdownResponse,
} from '../lib/api'
import ModulePeriodFilterBar from '../components/ui/ModulePeriodFilterBar'
import EntityMultiSelect from '../components/ui/EntityMultiSelect'
import {
  defaultAnnualPeriodFromLatest,
  periodAnchorYearMonth,
  periodCacheKey,
  periodTitleLabel,
  type LatestPeriodInfo,
  type PeriodGrain,
  type PeriodSelection,
} from '../lib/periodSelection'
import ErFlowTable from '../components/financials/annual/ErFlowTable'
import ErSnapshotTable from '../components/financials/annual/ErSnapshotTable'
import AnnualConsolidationSection from '../components/financials/annual/AnnualConsolidationSection'
import AnnualSnapshotConsolidationSection from '../components/financials/annual/AnnualSnapshotConsolidationSection'
import WeeklyTable from '../components/financials/WeeklyTable'
import type { FinancialsDrillAnchor, FinancialsDrillOpen } from '../components/financials/FinancialStatementTable'
import FinancialsDrillSlot from '../components/financials/FinancialsDrillSlot'
import {
  StatementSection,
  getStatementConfig,
  type FinStatementKind,
} from '../components/financials/statement-two-view'
import PlConsolidationSection from '../components/financials/pl-two-view/PlConsolidationSection'
import StatementConsolidationSection from '../components/financials/statement-two-view/consolidation/StatementConsolidationSection'
import FloatingAssistants from '../components/action-notes/FloatingAssistants'
import MonthlyTable from '../components/financials/MonthlyTable'
import L4TrendChart from '../components/financials/L4TrendChart'
import WcTimelineChart from '../components/financials/WcTimelineChart'
import { usePageChartKeyboardNav } from '../hooks/usePageChartKeyboardNav'
import AnalyticsPageShell from '../components/ui/AnalyticsPageShell'
import ErrorBoundary from '../components/ErrorBoundary'
import { ChartLoadReporter } from '../hooks/useChartLoadReporter'
import { stripLegalForm } from '../lib/stripLegalForm'
import { IS_OVERVIEW_V2 } from '../lib/overviewV2Mode'

// ─── Error boundary for WcTimelineChart ──────────────────────────────────────

class ChartErrorBoundary extends Component<
  { children: ReactNode },
  { hasError: boolean; message: string }
> {
  constructor(props: { children: ReactNode }) {
    super(props)
    this.state = { hasError: false, message: '' }
  }
  static getDerivedStateFromError(err: Error) {
    return { hasError: true, message: err.message ?? 'Unknown error' }
  }
  render() {
    if (this.state.hasError) {
      return (
        <div
          className="rounded-xl mt-4 px-4 py-6 text-center text-xs"
          style={{ background: '#FEF2F2', border: '1px solid #FECACA', color: '#B91C1C' }}
        >
          <p className="font-semibold mb-1">Chart could not be rendered</p>
          <p style={{ color: '#DC2626' }}>{this.state.message}</p>
        </div>
      )
    }
    return this.props.children
  }
}

export type StatementSubTab = {
  id: string
  label: string
  disabled?: boolean
  note?: string
  /** Optional per-sub-page subheading; supports the {period} placeholder. Falls back to the page description. */
  description?: string
}

export type StatementSubTabRenderContext = {
  period: PeriodSelection
  /** Selected legal_entity_codes for entity-scoped sub-pages; empty = all (consolidated). */
  entities: string[]
  periodReady: boolean
}

type Props = {
  statement: FinStatementKind
  kicker: string
  title: string
  description: string
  /** Sub-page tab bar under the page title (PLAN §5), e.g. P&L statement · Profitability. */
  subTabs?: StatementSubTab[]
  activeSubTab?: string
  onSubTabChange?: (id: string) => void
  /** Rendered instead of the statement content when a non-default sub tab is active. */
  subTabContent?: ReactNode
  /** Factory for sub-tab content that needs period / entity from this page. */
  renderSubTabContent?: (ctx: StatementSubTabRenderContext) => ReactNode
  /** Sub-tab ids that get the multi-select entity filter (Phase 7): Profitability /
   *  Payroll / Fixed-Assets / OPOS aging. The IS/BS/CF/WC statements themselves are
   *  always consolidated and never show an entity selector. */
  entityFilterSubTabs?: string[]
  /** Sub-tab ids that support ANNUAL grain only (e.g. fixed-assets — year-end
   *  snapshots): Monthly/Weekly pills are disabled and grain is forced to year. */
  annualOnlySubTabs?: string[]
}

function finPeriodFromSelection(p: PeriodSelection, entity?: string): FinPeriodParams {
  if (p.grain === 'week') {
    return { period_grain: 'week', iso_year: p.isoYear, iso_week: p.isoWeek, entity }
  }
  if (p.grain === 'year') {
    return { period_grain: 'year', year: p.year, month: p.month, entity }
  }
  return { period_grain: 'month', year: p.year, month: p.month, entity }
}

async function fetchStatementForTab(
  tab: FinStatementKind,
  period: PeriodSelection,
  ent?: string,
): Promise<FinancialStatementResponse> {
  const fp = finPeriodFromSelection(period, ent)
  if (tab === 'pl') {
    const [pl, plan] = await Promise.all([
      api.financialsPlStatementPeriod(fp),
      api.financialsPlPlan(
        period.grain === 'month' ? period.year : periodAnchorYearMonth(period).year,
        period.grain === 'month' ? period.month : periodAnchorYearMonth(period).month,
        ent,
      ).catch(() => null),
    ])
    return plan ? { ...pl, plan } : pl
  }
  const planYear = period.grain === 'month' ? period.year : periodAnchorYearMonth(period).year
  const planMonth = period.grain === 'month' ? period.month : periodAnchorYearMonth(period).month

  if (tab === 'bs') {
    if (IS_OVERVIEW_V2) {
      const [bs, plan] = await Promise.all([
        api.financialsBalanceSheetPeriod(fp),
        api.financialsBsPlan(planYear, planMonth, ent).catch(() => null),
      ])
      return plan ? { ...bs, plan } : bs
    }
    return api.financialsBalanceSheetPeriod(fp)
  }
  if (tab === 'cf') {
    if (IS_OVERVIEW_V2) {
      const [cf, plan] = await Promise.all([
        api.financialsCashFlowPeriod(fp),
        api.financialsCfPlan(planYear, planMonth, ent).catch(() => null),
      ])
      return plan ? { ...cf, plan } : cf
    }
    return api.financialsCashFlowPeriod(fp)
  }
  // wc
  if (IS_OVERVIEW_V2) {
    const [wc, plan] = await Promise.all([
      api.financialsWorkingCapitalPeriod(fp),
      api.financialsWcPlan(planYear, planMonth, ent).catch(() => null),
    ])
    return plan ? { ...wc, plan } : wc
  }
  return api.financialsWorkingCapitalPeriod(fp)
}

// ─── Financial query concurrency limiter ─────────────────────────────────────
// Prevents saturating DB parallel workers when BS / WC / consolidation / monthly
// all fire simultaneously on mount. Max 2 concurrent heavy financial queries.
let _finQueryRunning = 0
const _finQueryQueue: Array<() => void> = []
const FIN_QUERY_MAX_CONCURRENT = 2

async function acquireFinSlot(): Promise<() => void> {
  if (_finQueryRunning < FIN_QUERY_MAX_CONCURRENT) {
    _finQueryRunning++
  } else {
    await new Promise<void>(resolve => _finQueryQueue.push(resolve))
    // Slot was passed to us by the previous holder — running count unchanged
  }
  return function releaseFinSlot() {
    const next = _finQueryQueue.shift()
    if (next) {
      // Pass slot directly to next waiter — running count stays the same
      next()
    } else {
      _finQueryRunning--
    }
  }
}

// ─── Section-level error card with Retry ─────────────────────────────────────

function FinSectionErrorCard({
  label,
  error,
  onRetry,
}: {
  label: string
  error: string
  onRetry: () => void
}) {
  return (
    <div
      className="mt-4 mb-2 rounded-xl px-5 py-4 text-sm"
      style={{
        background: 'rgba(239,68,68,0.08)',
        border: '1.5px solid rgba(220,38,38,0.35)',
        color: '#991B1B',
      }}
      role="alert"
    >
      <p className="font-medium mb-2">{label} could not be loaded — {error}</p>
      <button
        type="button"
        onClick={onRetry}
        className="rounded-md px-3 py-1 text-xs font-semibold transition-colors hover:opacity-80"
        style={{
          background: 'rgba(220,38,38,0.12)',
          color: '#B91C1C',
          border: '1px solid rgba(220,38,38,0.3)',
        }}
      >
        Retry
      </button>
    </div>
  )
}

export default function StatementsPage({
  statement: tab,
  kicker,
  title,
  description,
  subTabs,
  activeSubTab,
  subTabContent,
  renderSubTabContent,
  entityFilterSubTabs,
  annualOnlySubTabs,
}: Props) {
  const [entities, setEntities] = useState<Entity[]>([])
  // Phase 7: the IS/BS/CF/WC statements are ALWAYS consolidated — no per-statement
  // entity selector. Only the entity-scoped sub-pages carry a multi-select.
  const entity = 'all'
  const [selectedEntities, setSelectedEntities] = useState<string[]>([])
  const [grain, setGrain] = useState<PeriodGrain>('year')
  const [period, setPeriod] = useState<PeriodSelection>({ grain: 'year', year: 2025, month: 7 })
  const [latest, setLatest] = useState<LatestPeriodInfo | null>(null)
  const [periodReady, setPeriodReady] = useState(false)

  const [data, setData] = useState<FinancialStatementResponse | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  const [drill, setDrill] = useState<FinancialsDrillOpen | null>(null)
  const openDrill = (anchor: FinancialsDrillAnchor) => (d: FinancialsDrillOpen) =>
    setDrill({ ...d, anchor })
  const [conslData, setConslData] = useState<ConsolidationResponse | null>(null)
  const [conslLoading, setConslLoading] = useState(false)
  const [monthlyData, setMonthlyData] = useState<MonthlyResponse | null>(null)
  const [monthlyLoading, setMonthlyLoading] = useState(false)
  const [weeklyData, setWeeklyData] = useState<WeeklyBreakdownResponse | null>(null)
  const [weeklyLoading, setWeeklyLoading] = useState(false)
  const [annualData, setAnnualData] = useState<ErFlowResponse | null>(null)
  const [annualSnapData, setAnnualSnapData] = useState<ErSnapshotResponse | null>(null)
  const [annualLoading, setAnnualLoading] = useState(false)
  const [annualError, setAnnualError] = useState<string | null>(null)
  const [annualConsol, setAnnualConsol] = useState<ConsolidationResponse | null>(null)
  const [annualConsolLoading, setAnnualConsolLoading] = useState(false)
  // Section-level errors — silently swallowed before; now surfaced with Retry
  const [conslError, setConslError] = useState<string | null>(null)
  const [monthlyError, setMonthlyError] = useState<string | null>(null)
  const [weeklyError, setWeeklyError] = useState<string | null>(null)
  const [annualConsolError, setAnnualConsolError] = useState<string | null>(null)
  const [filtersOpen, setFiltersOpen] = useState(false)
  const pageContentRef = useRef<HTMLDivElement>(null)
  const statementLoadId = useRef(0)
  const conslLoadId = useRef(0)
  const monthlyLoadId = useRef(0)
  const weeklyLoadId = useRef(0)
  const annualLoadId = useRef(0)
  const annualConsolLoadId = useRef(0)

  const ent = entity === 'all' ? undefined : entity
  const anchor = periodAnchorYearMonth(period)
  const periodLabel = periodTitleLabel(period)

  const defaultSubTabId = subTabs?.[0]?.id
  const altSubActive = Boolean(
    subTabs?.length && activeSubTab && defaultSubTabId && activeSubTab !== defaultSubTabId,
  )
  // Page title + subheading reflect the active sub-page (e.g. "Profitability"); the kicker
  // keeps the main-page header (e.g. "Income statement"). Both fall back to the page props.
  const activeSub = subTabs?.find(t => t.id === activeSubTab)
  const pageTitle = activeSub?.label ?? title
  const pageDescription = activeSub?.description ?? description
  const altContent = altSubActive
    ? (renderSubTabContent?.({ period, periodReady, entities: selectedEntities })
      ?? subTabContent)
    : undefined
  const statementActive = !altSubActive
  // The multi-select entity filter appears only on the entity-scoped sub-pages.
  const showEntityFilter = Boolean(
    altSubActive && activeSubTab && entityFilterSubTabs?.includes(activeSubTab),
  )
  // Fixed-assets (and any annual-only sub-page): year-end snapshots only → force
  // Annual grain and disable the Monthly/Weekly pills.
  const annualOnly = Boolean(
    activeSubTab && annualOnlySubTabs?.includes(activeSubTab),
  )
  useEffect(() => {
    if (annualOnly && grain !== 'year') {
      setGrain('year')
    }
  }, [annualOnly, grain])

  usePageChartKeyboardNav(pageContentRef, { enabled: periodReady && !loading && statementActive })

  useEffect(() => {
    let cancelled = false

    async function boot() {
      try {
        const [entList, lp] = await Promise.all([api.entities(), api.latestPeriod()])
        if (cancelled) return
        setEntities(entList)
        const info: LatestPeriodInfo = {
          period: lp.period ?? '',
          year: lp.year ?? null,
          month: lp.month ?? null,
          iso_year: lp.iso_year ?? null,
          iso_week: lp.iso_week ?? null,
        }
        setLatest(info)
        setPeriod(defaultAnnualPeriodFromLatest(info))
      } catch (e: unknown) {
        if (!cancelled) {
          const msg = e instanceof Error ? e.message : String(e)
          setError(
            `${msg} — Check Postgres and backend/.env (DB_*). API quick test: GET /api/v1/health`,
          )
        }
      } finally {
        if (!cancelled) setPeriodReady(true)
      }
    }

    void boot()
    return () => {
      cancelled = true
    }
  }, [])

  const loadStatement = useCallback(async () => {
    if (!periodReady || !statementActive) return
    const loadId = ++statementLoadId.current
    setLoading(true)
    setDrill(null)
    let releaseSlot: (() => void) | null = null
    try {
      releaseSlot = await acquireFinSlot()
      if (loadId !== statementLoadId.current) return
      const res = await fetchStatementForTab(tab, period, ent)
      if (loadId !== statementLoadId.current) return
      setData(res)
      setError(null)
    } catch (e: unknown) {
      if (loadId !== statementLoadId.current) return
      setData(null)
      setError(e instanceof Error ? e.message : 'Failed to load')
    } finally {
      releaseSlot?.()
      if (loadId === statementLoadId.current) setLoading(false)
    }
  }, [tab, period, ent, periodReady, statementActive])

  useEffect(() => {
    loadStatement()
  }, [loadStatement])

  const loadConsolidation = useCallback(async () => {
    if (!periodReady || !statementActive) return
    const loadId = ++conslLoadId.current
    setConslLoading(true)
    setConslError(null)
    const fp = finPeriodFromSelection(period, ent)
    let releaseSlot: (() => void) | null = null
    try {
      releaseSlot = await acquireFinSlot()
      if (loadId !== conslLoadId.current) return
      let res: ConsolidationResponse
      if (tab === 'pl')      res = await api.financialsPlConsolidation(fp)
      else if (tab === 'bs') res = await api.financialsBsConsolidation(fp)
      else if (tab === 'cf') res = await api.financialsCfConsolidation(fp)
      else                   res = await api.financialsWcConsolidation(fp)
      if (loadId !== conslLoadId.current) return
      setConslData(res)
    } catch (e: unknown) {
      if (loadId !== conslLoadId.current) return
      setConslData(null)
      setConslError(e instanceof Error ? e.message : 'Failed to load consolidation data')
    } finally {
      releaseSlot?.()
      if (loadId === conslLoadId.current) setConslLoading(false)
    }
  }, [tab, period, ent, periodReady, statementActive])

  useEffect(() => {
    loadConsolidation()
  }, [loadConsolidation])

  const loadMonthly = useCallback(async () => {
    if (!periodReady || !statementActive) return
    const loadId = ++monthlyLoadId.current
    setMonthlyLoading(true)
    setMonthlyError(null)
    const fp = finPeriodFromSelection(period, ent)
    const useSpan = period.grain === 'month' || period.grain === 'year' || period.grain === 'week'
    let releaseSlot: (() => void) | null = null
    try {
      releaseSlot = await acquireFinSlot()
      if (loadId !== monthlyLoadId.current) return
      let res: MonthlyResponse
      if (tab === 'pl') {
        res = await api.financialsPlMonthly(fp, useSpan ? { span: 'fy3' } : undefined)
      } else if (tab === 'bs') {
        res = await api.financialsBsMonthly(anchor.year, anchor.month, ent, useSpan ? { span: 'fy3' } : undefined)
      } else if (tab === 'cf') {
        res = await api.financialsCfMonthly(
          anchor.year,
          anchor.month,
          ent,
          useSpan ? { span: 'fy3' } : undefined,
        )
      } else {
        res = await api.financialsWcMonthly(
          anchor.year,
          anchor.month,
          ent,
          useSpan ? { span: 'fy3' } : undefined,
        )
      }
      if (loadId !== monthlyLoadId.current) return
      setMonthlyData(res)
    } catch (e: unknown) {
      if (loadId !== monthlyLoadId.current) return
      setMonthlyData(null)
      setMonthlyError(e instanceof Error ? e.message : 'Failed to load monthly trend data')
    } finally {
      releaseSlot?.()
      if (loadId === monthlyLoadId.current) setMonthlyLoading(false)
    }
  }, [tab, period, anchor.year, anchor.month, ent, periodReady, statementActive])

  const loadWeekly = useCallback(async () => {
    if (!periodReady || !statementActive || period.grain !== 'week') {
      setWeeklyData(null)
      return
    }
    if (tab !== 'pl' && tab !== 'cf') {
      setWeeklyData(null)
      return
    }
    const loadId = ++weeklyLoadId.current
    setWeeklyLoading(true)
    setWeeklyError(null)
    let releaseSlot: (() => void) | null = null
    try {
      releaseSlot = await acquireFinSlot()
      if (loadId !== weeklyLoadId.current) return
      const res = tab === 'pl'
        ? await api.financialsPlWeekly(period.isoYear, period.isoWeek, ent)
        : await api.financialsCfWeekly(period.isoYear, period.isoWeek, ent)
      if (loadId !== weeklyLoadId.current) return
      setWeeklyData(res)
    } catch (e: unknown) {
      if (loadId !== weeklyLoadId.current) return
      setWeeklyData(null)
      setWeeklyError(e instanceof Error ? e.message : 'Failed to load weekly breakdown data')
    } finally {
      releaseSlot?.()
      if (loadId === weeklyLoadId.current) setWeeklyLoading(false)
    }
  }, [tab, period, ent, periodReady, statementActive])

  useEffect(() => {
    loadMonthly()
  }, [loadMonthly])

  useEffect(() => {
    loadWeekly()
  }, [loadWeekly])

  const loadAnnual = useCallback(async () => {
    if (!periodReady || !statementActive || period.grain !== 'year') return
    const loadId = ++annualLoadId.current
    setAnnualLoading(true)
    setAnnualError(null)
    let releaseSlot: (() => void) | null = null
    try {
      releaseSlot = await acquireFinSlot()
      if (loadId !== annualLoadId.current) return
      let res: ErFlowResponse | ErSnapshotResponse
      if (tab === 'pl') {
        res = await api.exitReadinessPlStatement(anchor.year, anchor.month, ent)
        if (loadId !== annualLoadId.current) return
        setAnnualData(res as ErFlowResponse)
        setAnnualSnapData(null)
      } else if (tab === 'cf') {
        res = await api.exitReadinessCashFlow(anchor.year, anchor.month, ent)
        if (loadId !== annualLoadId.current) return
        setAnnualData(res as ErFlowResponse)
        setAnnualSnapData(null)
      } else if (tab === 'bs') {
        res = await api.exitReadinessBalanceSheet(anchor.year, anchor.month, ent)
        if (loadId !== annualLoadId.current) return
        setAnnualSnapData(res as ErSnapshotResponse)
        setAnnualData(null)
      } else {
        res = await api.exitReadinessWorkingCapital(anchor.year, anchor.month, ent)
        if (loadId !== annualLoadId.current) return
        setAnnualSnapData(res as ErSnapshotResponse)
        setAnnualData(null)
      }
    } catch (e: unknown) {
      if (loadId !== annualLoadId.current) return
      setAnnualData(null)
      setAnnualSnapData(null)
      setAnnualError(e instanceof Error ? e.message : 'Failed to load annual data')
    } finally {
      releaseSlot?.()
      if (loadId === annualLoadId.current) setAnnualLoading(false)
    }
  }, [tab, period.grain, anchor.year, anchor.month, ent, periodReady, statementActive])

  useEffect(() => {
    loadAnnual()
  }, [loadAnnual])

  const loadAnnualConsol = useCallback(async () => {
    if (!periodReady || !statementActive || tab !== 'pl' || period.grain !== 'year') return
    const loadId = ++annualConsolLoadId.current
    setAnnualConsolLoading(true)
    setAnnualConsolError(null)
    let releaseSlot: (() => void) | null = null
    try {
      releaseSlot = await acquireFinSlot()
      if (loadId !== annualConsolLoadId.current) return
      const res = await api.exitReadinessPlConsolidation(anchor.year, anchor.month, ent)
      if (loadId !== annualConsolLoadId.current) return
      setAnnualConsol(res)
    } catch (e: unknown) {
      if (loadId !== annualConsolLoadId.current) return
      setAnnualConsol(null)
      setAnnualConsolError(e instanceof Error ? e.message : 'Failed to load annual consolidation data')
    } finally {
      releaseSlot?.()
      if (loadId === annualConsolLoadId.current) setAnnualConsolLoading(false)
    }
  }, [tab, period.grain, anchor.year, anchor.month, ent, periodReady, statementActive])

  useEffect(() => {
    loadAnnualConsol()
  }, [loadAnnualConsol])

  useEffect(() => {
    setGrain(period.grain)
  }, [period.grain])

  const bootLoading =
    loading ||
    (period.grain === 'year' && annualLoading)

  return (
    <div className="min-h-screen" style={{ background: '#F4F6F9' }}>
      <AnalyticsPageShell
        resetKey={`fin-${tab}-${periodCacheKey(period)}-${entity}`}
        bootReady={periodReady}
        bootLoading={statementActive ? bootLoading : false}
        message="Loading financials…"
        submessage="Statements, trends, and report narratives are loading."
      >
      <ChartLoadReporter
        chartId={`fin-tab-${tab}`}
        loading={statementActive ? bootLoading : false}
        error={error}
      />
      <div ref={pageContentRef} className="max-w-[1920px] mx-auto px-6 lg:px-8 py-8">
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.4 }}
          className="mb-6 flex items-start justify-between gap-4"
        >
          <div className="min-w-0">
            <div className="text-xs font-semibold uppercase tracking-widest mb-1.5" style={{ color: '#1E3A5F' }}>
              {kicker}
            </div>
            <h1 className="text-2xl font-bold tracking-tight" style={{ color: '#111827' }}>
              {pageTitle}
            </h1>
            <p className="text-sm mt-1" style={{ color: '#94A3B8' }}>
              {pageDescription.replace('{period}', periodLabel)}
            </p>
          </div>

          {/* Filters trigger — aligned to the page title, top-right */}
          <button
            type="button"
            onClick={() => setFiltersOpen(v => !v)}
            className="shrink-0 mt-1 flex items-center gap-1.5 px-3.5 py-2 rounded-lg text-sm font-medium transition-colors"
            style={{
              background: filtersOpen ? 'rgba(30,58,95,0.1)' : '#FFFFFF',
              color: '#1E3A5F',
              border: `1px solid ${filtersOpen ? 'rgba(30,58,95,0.25)' : '#E2E8F0'}`,
              boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
            }}
            title={filtersOpen ? 'Hide period filters' : 'Show period filters'}
            aria-expanded={filtersOpen}
            aria-label={filtersOpen ? 'Hide filters' : 'Show filters'}
          >
            <SlidersHorizontal size={15} strokeWidth={2} aria-hidden />
            <span className="hidden sm:inline">Filters</span>
            <ChevronDown
              size={14}
              className="transition-transform duration-200"
              style={{ transform: filtersOpen ? 'rotate(180deg)' : 'none' }}
              aria-hidden
            />
          </button>
        </motion.div>

        {/* Period filter window — opens beneath the title row. The IS/BS/CF/WC
            statements are always consolidated (no entity selector); only the
            entity-scoped sub-pages get a multi-select entity filter. */}
        {filtersOpen && (
          <>
            {showEntityFilter && (
              <div
                className="rounded-xl mb-3 px-5 py-4"
                style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.05)' }}
              >
                <EntityMultiSelect
                  entities={entities}
                  selected={selectedEntities}
                  onChange={setSelectedEntities}
                />
              </div>
            )}
            <ModulePeriodFilterBar
              grain={grain}
              onGrainChange={setGrain}
              period={period}
              onPeriodChange={p => {
                setPeriod(p)
                setDrill(null)
              }}
              latest={latest}
              loading={loading}
              onRefresh={loadStatement}
              showYearGrain={true}
              annualOnly={annualOnly}
            />
          </>
        )}

        {error && statementActive && (
          <div
            className="mb-4 rounded-xl px-5 py-4 text-sm font-medium"
            style={{ background: 'rgba(239,68,68,0.1)', border: '2px solid rgba(220,38,38,0.45)', color: '#991B1B' }}
            role="alert"
            aria-live="assertive"
          >
            {error}
          </div>
        )}
        {conslError && statementActive && (
          <FinSectionErrorCard label="Consolidation breakdown" error={conslError} onRetry={loadConsolidation} />
        )}
        {monthlyError && statementActive && (
          <FinSectionErrorCard label="Monthly trend" error={monthlyError} onRetry={loadMonthly} />
        )}
        {weeklyError && statementActive && (
          <FinSectionErrorCard label="Weekly breakdown" error={weeklyError} onRetry={loadWeekly} />
        )}
        {annualConsolError && statementActive && tab === 'pl' && (
          <FinSectionErrorCard label="Annual consolidation" error={annualConsolError} onRetry={loadAnnualConsol} />
        )}

        {/* Sub-page navigation moved to the top-bar hover menu; the active sub-page is
            driven by ?sub=… so no in-page sub-tab bar is rendered here anymore. */}

        {!statementActive ? (
          altContent
        ) : period.grain === 'year' ? (
          <>
            {/* 1 — Annual statement table */}
            {tab === 'pl' || tab === 'cf' ? (
              <ErFlowTable
                key={`annual-${tab}-${periodCacheKey(period)}-${entity}`}
                data={annualData}
                loading={annualLoading}
                error={annualError}
                year={anchor.year}
                month={anchor.month}
                entity={ent}
                periodSelection={period}
                entityDisplayName={
                  entity !== 'all'
                    ? stripLegalForm(entities.find(e => e.legal_entity_code === entity)?.entity_name ?? entity)
                    : undefined
                }
                onDrill={openDrill('statement')}
                pinId={`annual-${tab}-${periodCacheKey(period)}`}
                pinLabel={
                  tab === 'pl'
                    ? `Income Statement Annual — FY${String(anchor.year).slice(-2)}`
                    : `Cash Flow Annual — FY${String(anchor.year).slice(-2)}`
                }
              />
            ) : (
              <ErSnapshotTable
                key={`annual-${tab}-${periodCacheKey(period)}-${entity}`}
                data={annualSnapData}
                loading={annualLoading}
                error={annualError}
                year={anchor.year}
                month={anchor.month}
                entity={ent}
                periodSelection={period}
                entityDisplayName={
                  entity !== 'all'
                    ? stripLegalForm(entities.find(e => e.legal_entity_code === entity)?.entity_name ?? entity)
                    : undefined
                }
                onDrill={openDrill('statement')}
                pinId={`${tab}-annual-group`}
                pinLabel={
                  tab === 'bs'
                    ? `Balance Sheet Annual — FY${String(anchor.year).slice(-2)}`
                    : `Working Capital Annual — FY${String(anchor.year).slice(-2)}`
                }
              />
            )}
            {/* 2 — Statement drill */}
            <FinancialsDrillSlot
              anchor="statement"
              drill={drill}
              entity={ent}
              onClose={() => setDrill(null)}
            />
            {/* 3 — L4 position trend chart */}
            <L4TrendChart
              key={`annual-l4trend-${tab}-${periodCacheKey(period)}-${entity}`}
              statement={tab}
              year={anchor.year}
              month={anchor.month}
              entity={ent}
              data={
                tab === 'pl' || tab === 'cf'
                  ? (annualData as unknown as FinancialStatementResponse)
                  : tab === 'bs' || tab === 'wc'
                    ? (annualSnapData as unknown as FinancialStatementResponse)
                    : data
              }
              statementLoading={
                tab === 'pl' || tab === 'cf' || tab === 'bs' || tab === 'wc'
                  ? annualLoading
                  : loading
              }
              onDrill={openDrill('l4')}
            />
            <FinancialsDrillSlot
              anchor="l4"
              drill={drill}
              entity={ent}
              onClose={() => setDrill(null)}
            />
            {tab === 'wc' && (
              <ChartErrorBoundary key={`annual-wctimeline-err-${periodCacheKey(period)}-${entity}`}>
                <WcTimelineChart
                  key={`annual-wctimeline-${periodCacheKey(period)}-${entity}`}
                  year={anchor.year}
                  month={anchor.month}
                  entity={ent}
                  onDrill={openDrill('wc-timeline')}
                />
              </ChartErrorBoundary>
            )}
            {tab === 'wc' && (
              <FinancialsDrillSlot
                anchor="wc-timeline"
                drill={drill}
                entity={ent}
                onClose={() => setDrill(null)}
              />
            )}
            {/* 4 — Entity consolidation breakdown */}
            {tab === 'pl' ? (
              <AnnualConsolidationSection
                consol={annualConsol}
                loading={annualConsolLoading}
                year={anchor.year}
                month={anchor.month}
                periodSelection={period}
                onDrill={openDrill('consolidation')}
              />
            ) : tab === 'bs' || tab === 'wc' ? (
              <AnnualSnapshotConsolidationSection
                statement={tab}
                consol={conslData}
                loading={conslLoading}
                year={anchor.year}
                month={anchor.month}
                periodSelection={period}
                onDrill={openDrill('consolidation')}
              />
            ) : (
              <StatementConsolidationSection
                key={`annual-consl-${tab}-${periodCacheKey(period)}`}
                statement={tab as 'cf'}
                consol={conslData}
                monthly={monthlyData}
                loading={conslLoading}
                year={anchor.year}
                month={anchor.month}
                periodSelection={period}
                onDrill={openDrill('consolidation')}
              />
            )}
            <FinancialsDrillSlot
              anchor="consolidation"
              drill={drill}
              entity={ent}
              onClose={() => setDrill(null)}
            />
            {/* 5 — Monthly table (full fiscal year span) */}
            <MonthlyTable
              key={`annual-monthly-${tab}-${periodCacheKey(period)}-${entity}`}
              data={monthlyData}
              loading={monthlyLoading}
              entity={ent}
              enableCellDetail={getStatementConfig(tab).features.monthlyCellDetail}
              showColumnEditor={getStatementConfig(tab).features.columnEditor}
              annualGrain
            />
          </>
        ) : (
          <>
        <ErrorBoundary>
          <StatementSection
            key={`${tab}-${periodCacheKey(period)}-${entity}`}
            statement={tab}
            data={data}
            monthly={monthlyData}
            loading={loading}
            error={error}
            year={anchor.year}
            month={anchor.month}
            entity={ent}
            entityLabel={entity === 'all' ? 'all' : entity}
            entityDisplayName={
              entity !== 'all'
                ? stripLegalForm(entities.find(e => e.legal_entity_code === entity)?.entity_name ?? entity)
                : tab === 'pl' || tab === 'bs' || tab === 'wc'
                  ? 'All entities (consolidated)'
                  : undefined
            }
            periodSelection={period}
            onDrill={openDrill('statement')}
          />
        </ErrorBoundary>

        <FinancialsDrillSlot
          anchor="statement"
          drill={drill}
          entity={ent}
          onClose={() => setDrill(null)}
        />

        <L4TrendChart
          key={`l4trend-${tab}-${periodCacheKey(period)}-${entity}`}
          statement={tab}
          year={anchor.year}
          month={anchor.month}
          entity={ent}
          data={data}
          statementLoading={loading}
          onDrill={openDrill('l4')}
        />

        <FinancialsDrillSlot
          anchor="l4"
          drill={drill}
          entity={ent}
          onClose={() => setDrill(null)}
        />

        {tab === 'wc' && (
          <ChartErrorBoundary key={`wctimeline-err-${periodCacheKey(period)}-${entity}`}>
            <WcTimelineChart
              key={`wctimeline-${periodCacheKey(period)}-${entity}`}
              year={anchor.year}
              month={anchor.month}
              entity={ent}
              onDrill={openDrill('wc-timeline')}
            />
          </ChartErrorBoundary>
        )}

        {tab === 'wc' && (
          <FinancialsDrillSlot
            anchor="wc-timeline"
            drill={drill}
            entity={ent}
            onClose={() => setDrill(null)}
          />
        )}

        {tab === 'pl' ? (
          <PlConsolidationSection
            key={`consl-pl-${periodCacheKey(period)}`}
            consol={conslData}
            monthly={monthlyData}
            loading={conslLoading}
            year={anchor.year}
            month={anchor.month}
            periodSelection={period}
            onDrill={openDrill('consolidation')}
          />
        ) : (
          <StatementConsolidationSection
            key={`consl-${tab}-${periodCacheKey(period)}`}
            statement={tab as 'bs' | 'cf' | 'wc'}
            consol={conslData}
            monthly={monthlyData}
            loading={conslLoading}
            year={anchor.year}
            month={anchor.month}
            periodSelection={period}
            onDrill={openDrill('consolidation')}
          />
        )}

        {(tab === 'pl' || tab === 'bs' || tab === 'wc') && (
          <FinancialsDrillSlot
            anchor="consolidation"
            drill={drill}
            entity={ent}
            onClose={() => setDrill(null)}
          />
        )}

        {(tab === 'pl' || tab === 'cf') && period.grain === 'week' ? (
          <WeeklyTable
            key={`weekly-${tab}-${periodCacheKey(period)}-${entity}`}
            data={weeklyData}
            loading={weeklyLoading}
            showColumnEditor={tab === 'pl'}
          />
        ) : (
          <MonthlyTable
            key={`monthly-${periodCacheKey(period)}-${entity}`}
            data={monthlyData}
            loading={monthlyLoading}
            entity={ent}
            enableCellDetail={getStatementConfig(tab).features.monthlyCellDetail}
            showColumnEditor={getStatementConfig(tab).features.columnEditor}
          />
        )}

          </>
        )}

        <div className="mt-16" />
      </div>
      </AnalyticsPageShell>
        <FloatingAssistants
          year={anchor.year}
          month={anchor.month}
          entity={entity}
          route={`/${tab === 'pl' ? 'income-statement' : tab === 'bs' ? 'balance-sheet' : tab === 'wc' ? 'working-capital' : 'cash-flow'}`}
          tab={tab}
          sessionTitle={`${title} · ${periodLabel} · ${entity === 'all' ? 'All entities' : entity}`}
        />
    </div>
  )
}
