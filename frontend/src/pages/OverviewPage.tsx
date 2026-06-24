/**
 * Overview page (GDPdU edition) — PLAN.md §6.1.
 * Annual slices by default (toggle to Month/Week), mirroring the Income Statement page.
 * Sections: Group overview · DuPont · Top customers · Top suppliers.
 */
import { useCallback, useEffect, useRef, useState } from 'react'
import { useNavigate } from 'react-router-dom'
import { motion } from 'framer-motion'
import { api, Entity, type FinPeriodParams } from '../lib/api'
import CollapsibleModuleFiltersCard from '../components/ui/CollapsibleModuleFiltersCard'
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
import OverviewGroupTile from '../components/financials/overview/OverviewGroupTile'
import DuPontTree from '../components/cockpit/DuPontTree'
import type { DrillDownRequest } from '../components/cockpit/EbitTable'
import TopCustomerTable from '../components/cockpit/TopCustomerTable'
import TopSupplierTable from '../components/cockpit/TopSupplierTable'
import DrillDownTable from '../components/cockpit/DrillDownTable'
import AnomaliesPanel from '../components/financials/AnomaliesPanel'
import type { AnomalyPeriodParams } from '../lib/api'

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

export default function OverviewPage() {
  const navigate = useNavigate()
  const [entities, setEntities] = useState<Entity[]>([])
  const [entity, setEntity] = useState('all')
  const [grain, setGrain] = useState<PeriodGrain>('year')
  const [period, setPeriod] = useState<PeriodSelection>({ grain: 'year', year: 2025, month: 7 })
  const [latest, setLatest] = useState<LatestPeriodInfo | null>(null)
  const [periodReady, setPeriodReady] = useState(false)
  const [bootError, setBootError] = useState<string | null>(null)
  const [drill, setDrill] = useState<DrillDownRequest | null>(null)

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
          setBootError(`${msg} — Prüfe Postgres und backend/.env (DB_*). API-Schnelltest: GET /api/v1/health`)
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

  // Reset the open booking drill when the period/entity changes.
  useEffect(() => {
    setDrill(null)
  }, [resetKey])

  const periodParams = finParamsFromPeriod(period, ent)

  const anomalyParams: AnomalyPeriodParams =
    period.grain === 'week'
      ? { period_grain: 'week', iso_year: period.isoYear, iso_week: period.isoWeek }
      : period.grain === 'year'
        ? { period_grain: 'year', year: period.year }
        : { period_grain: 'month', year: period.year, month: period.month }

  return (
    <div className="min-h-screen" style={{ background: '#F4F6F9' }}>
      <AnalyticsPageShell
        resetKey={resetKey}
        bootReady={periodReady}
        bootLoading={false}
        timeoutMs={180_000}
        message="Loading overview…"
        submessage="DuPont and top partners are loading."
      >
        <ChartLoadReporter chartId="overview-page" loading={!periodReady} error={bootError} />
        <div ref={pageContentRef} className="mx-auto w-full max-w-[1680px] px-6 py-8">
          <motion.div
            initial={{ opacity: 0, y: 16 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.4 }}
            className="mb-6"
          >
            <div className="text-xs font-semibold uppercase tracking-widest mb-1.5" style={{ color: '#1E3A5F' }}>
              Reporting
            </div>
            <h1 className="text-2xl font-bold tracking-tight" style={{ color: '#111827' }}>
              Overview
            </h1>
            <p className="text-sm mt-1" style={{ color: '#94A3B8' }}>
              Performance overview, executive summary and top customers &amp; suppliers · {periodLabel}
            </p>
          </motion.div>

          {bootError && (
            <div
              className="mb-4 rounded-xl px-5 py-4 text-sm font-medium"
              style={{ background: 'rgba(239,68,68,0.1)', border: '2px solid rgba(220,38,38,0.45)', color: '#991B1B' }}
              role="alert"
            >
              {bootError}
            </div>
          )}

          <CollapsibleModuleFiltersCard
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

          <div className="space-y-5">
            {/* 1 — Performance overview (DuPont) */}
            <DuPontTree
              key={`dupont-${resetKey}`}
              title="Performance Overview"
              year={anchor.year}
              month={anchor.month}
              entities={entities}
            />

            {/* 2 — Group overview (Report ↔ Table) */}
            {periodReady && (
              <OverviewGroupTile
                periodParams={periodParams}
                cockpitPeriod={cockpitPeriod}
                entity={ent}
                resetKey={resetKey}
                onNavigateTab={onNavigateTab}
                onDrillDown={setDrill}
                activeDrillKey={drill?.title}
              />
            )}

            {drill && (
              <DrillDownTable
                entity={drill.entityCode}
                dateFrom={drill.dateFrom}
                dateTo={drill.dateTo}
                level3={drill.level3}
                statementType={drill.statementType}
                customerName={drill.customerName}
                title={drill.title}
                onClose={() => setDrill(null)}
              />
            )}

            {/* 5 — Top customers */}
            <TopCustomerTable period={cockpitPeriod} entity={ent} />

            {/* 6 — Top suppliers */}
            <TopSupplierTable period={cockpitPeriod} entity={ent} />

            {/* 7 — Anomalies (compact panel, basis for narratives) */}
            {periodReady && (
              <AnomaliesPanel
                key={`anomaly-overview-${resetKey}`}
                periodParams={anomalyParams}
                entity={ent}
                mode="compact"
              />
            )}
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
