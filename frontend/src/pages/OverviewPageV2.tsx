/**
 * OverviewPageV2 — P3 decision-first IA (reporting-v2 / port 5177 only).
 * Layout: HeroStrip → AlertRail → FindingsFeed → 7-block grid → LegacyDetail.
 *
 * P3 changes (round-trip reduction ~6 → ~1 on first paint):
 *   - ONE batched fetch via useOverviewSummaryV2 drives HeroStrip + FindingsFeed
 *     + WorkingCapitalBlock (incl. Δfy) + DriverFindingsBlock (DuPont).
 *   - LegacyDetailSection is React.lazy-loaded (separate chunk) and its inner
 *     LegacyBody only mounts on user expand — briefing calls fire on expand only.
 */
import { lazy, Suspense, useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { api, Entity, type FinPeriodParams } from '../lib/api'
import ModulePeriodFilterBar from '../components/ui/ModulePeriodFilterBar'
import { SlidersHorizontal, ChevronDown } from 'lucide-react'
import {
  defaultAnnualPeriodFromLatest,
  periodAnchorYearMonth,
  periodCacheKey,
  periodTitleLabel,
  type LatestPeriodInfo,
  type PeriodGrain,
  type PeriodSelection,
} from '../lib/periodSelection'
import AnalyticsPageShell from '../components/ui/AnalyticsPageShell'
import { ChartLoadReporter } from '../hooks/useChartLoadReporter'
import { usePageChartKeyboardNav } from '../hooks/usePageChartKeyboardNav'
import FloatingAssistants from '../components/action-notes/FloatingAssistants'
import type { FinTab } from '../components/financials/financialsTabs'
import type { DrillDownRequest } from '../components/cockpit/EbitTable'
// ── v2-only components ────────────────────────────────────────────────────────
import OverviewHeroStrip from '../components/financials/overview-v2/OverviewHeroStrip'
import OverviewFindingsFeed from '../components/financials/overview-v2/OverviewFindingsFeed'
import PerformanceBlock from '../components/financials/overview-v2/blocks/PerformanceBlock'
import CashLiquidityBlock from '../components/financials/overview-v2/blocks/CashLiquidityBlock'
import WorkingCapitalBlock from '../components/financials/overview-v2/blocks/WorkingCapitalBlock'
import DriverFindingsBlock from '../components/financials/overview-v2/blocks/DriverFindingsBlock'
import CustomerBlock from '../components/financials/overview-v2/blocks/CustomerBlock'
import SupplierBlock from '../components/financials/overview-v2/blocks/SupplierBlock'
import { buildFindingsFromSummary } from '../components/financials/overview-v2/findingsBuilders'
import { useOverviewBundleV2 } from '../components/financials/overview-v2/hooks/useOverviewBundleV2'

// LegacyDetailSection — lazy-loaded so the heavy bundle (GroupTile / DuPontTree /
// MarketWatch) is a separate chunk. Inner LegacyBody only mounts on user expand.
const LegacyDetailSection = lazy(
  () => import('../components/financials/overview-v2/LegacyDetailSection'),
)

// ─────────────────────────────────────────────────────────────────────────────

const ROUTE_BY_TAB: Record<FinTab, string> = {
  overview: '/overview',
  pl: '/income-statement',
  bs: '/balance-sheet',
  cf: '/cash-flow',
  wc: '/working-capital',
}

/** FinPeriodParams for the overview/entity-breakdown endpoints (year grain anchors at the YTD month). */
function finParamsFromPeriod(p: PeriodSelection, entity?: string): FinPeriodParams {
  if (p.grain === 'week') {
    return { period_grain: 'week', iso_year: p.isoYear, iso_week: p.isoWeek, entity }
  }
  if (p.grain === 'year') {
    return { period_grain: 'year', year: p.year, month: p.month, entity }
  }
  return { period_grain: 'month', year: p.year, month: p.month, entity }
}

/** Cockpit analytics (EBIT) need a month/week selection; year grain anchors at its YTD month. */
function cockpitPeriodFromSelection(p: PeriodSelection): PeriodSelection {
  if (p.grain === 'year') {
    const anchor = periodAnchorYearMonth(p)
    return { grain: 'month', year: anchor.year, month: anchor.month }
  }
  return p
}

// ─────────────────────────────────────────────────────────────────────────────

export default function OverviewPageV2() {
  const navigate = useNavigate()
  const [entities, setEntities] = useState<Entity[]>([])
  const [entity, setEntity] = useState('all')
  const [grain, setGrain] = useState<PeriodGrain>('year')
  const [period, setPeriod] = useState<PeriodSelection>({ grain: 'year', year: 2025, month: 7 })
  const [latest, setLatest] = useState<LatestPeriodInfo | null>(null)
  const [periodReady, setPeriodReady] = useState(false)
  const [bootError, setBootError] = useState<string | null>(null)
  const [drill, setDrill] = useState<DrillDownRequest | null>(null)
  const [filtersOpen, setFiltersOpen] = useState(false)

  const pageContentRef = useRef<HTMLDivElement>(null)

  const ent = entity === 'all' ? undefined : entity
  const anchor = periodAnchorYearMonth(period)
  const periodLabel = periodTitleLabel(period)
  const resetKey = `overview-${periodCacheKey(period)}-${entity}`
  const cockpitPeriod = cockpitPeriodFromSelection(period)

  usePageChartKeyboardNav(pageContentRef, { enabled: periodReady })

  const onNavigateTab = useCallback(
    (tab: FinTab) => {
      const route = ROUTE_BY_TAB[tab] ?? '/overview'
      if (route !== '/overview') navigate(route)
    },
    [navigate],
  )

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
          setBootError(`${msg} — Check Postgres and backend/.env (DB_*). API health check: GET /api/v1/health`)
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

  useEffect(() => {
    setGrain(period.grain)
  }, [period.grain])

  // Reset the open drill when the period/entity changes.
  useEffect(() => {
    setDrill(null)
  }, [resetKey])

  const periodParams = finParamsFromPeriod(period, ent)

  // ── ONE batched bundle fetch (collapses summary + partners + liquidity into 1
  //    round-trip). Slices keep the granular-hook {data,loading,error} shape. ──
  const bundle = useOverviewBundleV2(anchor.year, anchor.month, ent, 50, !periodReady)
  const summary = bundle.summary
  const partners = bundle.partners
  const liquidity = bundle.liquidity

  // Client-side findings derived from the batched summary payload (no extra calls).
  const findings = useMemo(
    () => buildFindingsFromSummary(summary.data),
    [summary.data],
  )

  return (
    <div className="min-h-screen" style={{ background: '#F4F6F9' }}>
      <AnalyticsPageShell
        resetKey={resetKey}
        bootReady={periodReady}
        bootLoading={false}
        timeoutMs={180_000}
        message="Loading overview…"
        submessage="Briefing and analysis blocks loading."
      >
        <ChartLoadReporter chartId="overview-page" loading={!periodReady} error={bootError} />
        <div ref={pageContentRef} className="mx-auto w-full max-w-[1920px] px-6 py-8">

          {/* Page header */}
          <motion.div
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4 }}
            className="mb-6 flex items-start justify-between gap-4"
          >
            <div className="min-w-0">
              <div
                className="text-xs font-semibold uppercase tracking-widest mb-1.5"
                style={{ color: '#1E3A5F' }}
              >
                Reporting
              </div>
              <h1 className="text-2xl font-bold tracking-tight" style={{ color: '#111827' }}>
                Overview
              </h1>
              <p className="text-sm mt-1" style={{ color: '#94A3B8' }}>
                Decision-first briefing · {periodLabel}
              </p>
            </div>

            {/* Filters trigger — aligned to the page title, top-right (matches statements) */}
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

          {/* Boot error banner */}
          {bootError && (
            <div
              className="mb-4 rounded-xl px-5 py-4 text-sm font-medium"
              style={{
                background: 'rgba(239,68,68,0.1)',
                border: '2px solid rgba(220,38,38,0.45)',
                color: '#991B1B',
              }}
              role="alert"
            >
              {bootError}
            </div>
          )}

          {/* Period filter window — opens beneath the title row (matches statements) */}
          {filtersOpen && (
            <ModulePeriodFilterBar
              entities={entities}
              entity={entity}
              onEntityChange={setEntity}
              grain={grain}
              onGrainChange={setGrain}
              period={period}
              onPeriodChange={setPeriod}
              latest={latest}
              showYearGrain
            />
          )}

          <div className="space-y-6">

            {/* HERO STRIP — driven by summary (1 call, skeleton while loading) */}
            <OverviewHeroStrip
              summaryData={summary.data}
              summaryLoading={summary.loading}
              summaryError={summary.error}
            />

            {/* FINDINGS FEED — derived from summary (no extra call) */}
            <OverviewFindingsFeed findings={findings} loading={summary.loading} />

            {/* ANALYSIS GRID (2-col, 7 blocks — Fixed assets deferred to later phase) */}
            <section aria-label="Analysis blocks">
              <div
                className="text-xs font-semibold uppercase tracking-widest mb-4"
                style={{ color: '#1E3A5F' }}
              >
                Analysis
              </div>
              <div className="grid grid-cols-1 lg:grid-cols-2 gap-5">
                {/* Row 1 */}
                <PerformanceBlock
                  summaryData={summary.data}
                  summaryLoading={summary.loading}
                  summaryError={summary.error}
                />
                <CashLiquidityBlock
                  data={liquidity.data}
                  loading={liquidity.loading}
                  error={liquidity.error}
                />
                {/* Row 2 — WC and DuPont both skip own fetch (summary supplies data) */}
                <WorkingCapitalBlock
                  year={anchor.year}
                  month={anchor.month}
                  entity={ent}
                  summaryWc={summary.data?.working_capital ?? null}
                />
                <DriverFindingsBlock
                  year={anchor.year}
                  month={anchor.month}
                  entity={ent}
                  summaryDupont={summary.data?.dupont ?? null}
                />
                {/* Row 3 — both blocks fed from the single partners fetch */}
                <CustomerBlock
                  customers={partners.data?.customers ?? null}
                  loading={partners.loading}
                  error={partners.error}
                />
                <SupplierBlock
                  suppliers={partners.data?.suppliers ?? null}
                  loading={partners.loading}
                  error={partners.error}
                />
              </div>
            </section>

            {/* LEGACY / DETAIL — lazy chunk + inner body deferred until expand */}
            <Suspense
              fallback={
                <div
                  className="rounded-xl h-14 animate-pulse"
                  style={{ background: '#FFFFFF', border: '1px solid #E2E8F0' }}
                />
              }
            >
              <LegacyDetailSection
                periodParams={periodParams}
                cockpitPeriod={cockpitPeriod}
                entity={ent}
                resetKey={resetKey}
                onNavigateTab={onNavigateTab}
                onDrillDown={setDrill}
                activeDrillKey={drill?.title}
                drill={drill}
                onCloseDrill={() => setDrill(null)}
                year={anchor.year}
                month={anchor.month}
                entities={entities}
              />
            </Suspense>

          </div>

          <div className="mt-16" />
        </div>
      </AnalyticsPageShell>

      <FloatingAssistants
        year={anchor.year}
        month={anchor.month}
        entity={entity}
        route="/overview"
        sessionTitle={`Overview · ${periodLabel} · ${entity === 'all' ? 'All entities' : entity}`}
      />
    </div>
  )
}
