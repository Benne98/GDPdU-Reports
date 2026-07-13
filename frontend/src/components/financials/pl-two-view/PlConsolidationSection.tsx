import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Pin } from 'lucide-react'
import { PL_TOOLBAR_ICON_BTN, PL_TOOLBAR_BTN_STYLE } from './plToolbarButton'
import type {
  ConsolidationResponse,
  FinancialStatementResponse,
  MonthlyResponse,
  PlNarrativeResponse,
} from '../../../lib/api'
import { api, type FinPeriodParams } from '../../../lib/api'
import type { PeriodSelection } from '../../../lib/periodSelection'
import { periodAnchorYearMonth, periodCacheKey, periodQueryParams } from '../../../lib/periodSelection'
import type { FinancialsDrillOpen } from '../FinancialStatementTable'
import PlConsolidationColumnEditor from './PlConsolidationColumnEditor'
import PlConsolidationTableView from './PlConsolidationTableView'
import PlDetailOverlay from './PlDetailOverlay'
import PlEntityCarousel from './PlEntityCarousel'
import PlExportMenu, { type PlExportKind } from './PlExportMenu'
import PlReportView from './PlReportView'
import PlSectionHeading from './PlSectionHeading'
import { buildConsolidatedTableHeading } from './plReportSectionHeadings'
import PlViewToggleButton, { type PlViewMode } from './PlViewToggleButton'
import {
  loadConsolidationColumns,
  type PlConsolidationColumnDef,
} from './plConsolidationColumnRegistry'
import { buildPlanMapFromStatement } from './plPlanMap'
import { buildDefaultColumns, PLAN_ONLY_COL_KINDS } from './plColumnRegistry'
import {
  buildClientNarrativeResponse,
  isTrustedApiNarrative,
  mapApiBulletsToUi,
  type PlNarrativeBullet,
} from './plNarrativeEngine'
import { buildExportCheckOpen } from '../../../lib/finssentialsExport/buildExportCheckOpen'
import { buildReportCommentMarkerMap } from '../statement-two-view/reportCommentMarkers'
import { exportPlReportViewPdf, type PlExportContext } from './plExportPdf'
import { exportPlTableView } from './plExport'
import { exportConsolidationTablePptx, exportConsolidationTableXlsx } from './plConsolidationExport'
import { exportPlReportViewPptx } from './plExportPptx'
import { buildExportFooterLine } from './plExportFooter'
import { useOptionalActionNotesContext } from '../../action-notes/ActionNotesContext'
import { captureConsolidationSnapshot } from '../../action-notes/captureConsolidationTable'
import { useChartLoadReporter } from '../../../hooks/useChartLoadReporter'

const VIEW_KEY = 'finssentials.pl.consolidation.viewMode.v1'
const ENTITY_IDX_KEY = 'finssentials.pl.consolidation.entityIndex.v1'

function loadViewMode(): PlViewMode {
  try {
    return localStorage.getItem(VIEW_KEY) === 'table' ? 'table' : 'report'
  } catch {
    return 'report'
  }
}

function loadEntityIndex(max: number): number {
  try {
    const v = parseInt(localStorage.getItem(ENTITY_IDX_KEY) ?? '0', 10)
    if (Number.isNaN(v) || v < 0) return 0
    return max > 0 ? Math.min(v, max - 1) : 0
  } catch {
    return 0
  }
}

type Props = {
  consol: ConsolidationResponse | null
  monthly: MonthlyResponse | null
  loading: boolean
  error?: string | null
  year: number
  month: number
  periodSelection?: PeriodSelection
  onDrill: (d: FinancialsDrillOpen) => void
}

export default function PlConsolidationSection({
  consol,
  monthly,
  loading,
  error,
  year,
  month,
  periodSelection,
  onDrill,
}: Props) {
  const periodParams: FinPeriodParams = useMemo(
    () =>
      periodSelection
        ? (periodQueryParams(periodSelection) as FinPeriodParams)
        : { year, month },
    [periodSelection, year, month],
  )
  const planAnchor = useMemo(
    () => (periodSelection ? periodAnchorYearMonth(periodSelection) : { year, month }),
    [periodSelection, year, month],
  )
  const periodKey = useMemo(
    () => (periodSelection ? periodCacheKey(periodSelection) : `m-${year}-${month}`),
    [periodSelection, year, month],
  )
  const [viewMode, setViewMode] = useState<PlViewMode>(loadViewMode)
  const [entityIndex, setEntityIndex] = useState(0)
  const [entityStmt, setEntityStmt] = useState<FinancialStatementResponse | null>(null)
  const [entityLoading, setEntityLoading] = useState(false)
  const [stmtCache, setStmtCache] = useState<Map<string, FinancialStatementResponse>>(() => new Map())
  const [groupStatement, setGroupStatement] = useState<FinancialStatementResponse | null>(null)
  const [extraColumns, setExtraColumns] = useState<PlConsolidationColumnDef[]>(() => loadConsolidationColumns('pl'))
  const [detailBullet, setDetailBullet] = useState<PlNarrativeBullet | null>(null)
  const [narrative, setNarrative] = useState<PlNarrativeResponse | null>(null)

  const entities = consol?.entities ?? []
  const selected = entities[entityIndex]

  useChartLoadReporter('fin-consl-pl', loading || entityLoading, error)

  useEffect(() => {
    setStmtCache(new Map())
    setEntityStmt(null)
    setGroupStatement(null)
  }, [periodKey])

  useEffect(() => {
    if (!entities.length) return
    setEntityIndex(Math.min(loadEntityIndex(entities.length), entities.length - 1))
  }, [entities.length, periodKey])

  useEffect(() => {
    localStorage.setItem(VIEW_KEY, viewMode)
  }, [viewMode])

  useEffect(() => {
    localStorage.setItem(ENTITY_IDX_KEY, String(entityIndex))
  }, [entityIndex])

  useEffect(() => {
    setNarrative(null)
  }, [selected?.code, periodKey])

  useEffect(() => {
    if (!selected?.code) {
      setEntityStmt(null)
      return
    }
    const code = selected.code
    setStmtCache(prev => {
      const hit = prev.get(code)
      if (hit) {
        setEntityStmt(hit)
        return prev
      }
      return prev
    })
    let cancelled = false
    setEntityLoading(true)
    void (async () => {
      try {
        const [pl, plan] = await Promise.all([
          api.financialsPlStatementPeriod({ ...periodParams, entity: code }),
          api.financialsPlPlan(planAnchor.year, planAnchor.month, code).catch(() => null),
        ])
        if (cancelled) return
        const res = plan ? { ...pl, plan } : pl
        setStmtCache(prev => new Map(prev).set(code, res))
        setEntityStmt(res)
      } catch {
        if (!cancelled) setEntityStmt(null)
      } finally {
        if (!cancelled) setEntityLoading(false)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [selected?.code, periodParams, planAnchor.year, planAnchor.month])

  useEffect(() => {
    if (viewMode !== 'table') return
    let cancelled = false
    void (async () => {
      try {
        const pl = await api.financialsPlStatementPeriod(periodParams)
        const plan = await api.financialsPlPlan(planAnchor.year, planAnchor.month).catch(() => null)
        if (cancelled) return
        setGroupStatement(plan ? { ...pl, plan } : pl)
      } catch {
        if (!cancelled) setGroupStatement(null)
      }
    })()
    return () => {
      cancelled = true
    }
  }, [viewMode, periodParams, planAnchor.year, planAnchor.month])

  useEffect(() => {
    if (!consol?.entities.length || !extraColumns.length) return
    const needed = new Set<string>()
    for (const c of extraColumns) {
      if (c.entityCode) needed.add(c.entityCode)
    }
    for (const code of needed) {
      if (!stmtCache.has(code)) {
        void api.financialsPlStatementPeriod({ ...periodParams, entity: code }).then(pl =>
          api.financialsPlPlan(planAnchor.year, planAnchor.month, code).catch(() => null).then(plan => {
            const res = plan ? { ...pl, plan } : pl
            setStmtCache(prev => new Map(prev).set(code, res))
          }),
        )
      }
    }
  }, [consol, extraColumns, periodParams, planAnchor.year, planAnchor.month, periodKey])

  const planMap = useMemo(
    () => (entityStmt ? buildPlanMapFromStatement(entityStmt) : {}),
    [entityStmt],
  )

  const colLabels =
    entityStmt?.col_labels ??
    groupStatement?.col_labels ??
    (stmtCache.size > 0 ? [...stmtCache.values()][0]?.col_labels : undefined)
  const entityHasPlanData = entityStmt?.plan?.has_plan_data ?? false
  const miniColumns = useMemo(() => {
    if (!colLabels) return []
    return buildDefaultColumns(colLabels).filter(c =>
      ['pm', 'cm', 'mom', 'plan_cm', 'plan_vs_actual'].includes(c.kind) &&
      (entityHasPlanData || !PLAN_ONLY_COL_KINDS.has(c.kind))
    )
  }, [colLabels, entityHasPlanData])

  const exportCtx = useMemo(
    (): PlExportContext => ({
      entityLabel: selected?.code ?? '',
      entityDisplayName: selected?.label ?? 'Entity',
    }),
    [selected],
  )

  const exportBullets = useMemo((): PlNarrativeBullet[] => {
    if (!entityStmt) return []
    const client = buildClientNarrativeResponse(
      entityStmt,
      planMap,
      planAnchor.year,
      planAnchor.month,
      5,
    )
    const src =
      narrative && isTrustedApiNarrative(narrative, selected?.code) ? narrative : client
    if (src?.bullets?.length) return mapApiBulletsToUi(src.bullets, entityStmt.rows)
    return []
  }, [entityStmt, narrative, planMap, planAnchor.year, planAnchor.month, selected?.code])

  const consolidationCheckOpenRef = useRef<((id: string) => boolean) | null>(null)
  const notesCtx = useOptionalActionNotesContext()

  // ─── Pin registration ──────────────────────────────────────────────────────
  useEffect(() => {
    const stmt = consol?.statement ?? 'pl'
    const pinId = `${stmt}-consolidation`
    if (!notesCtx || !consol?.rows?.length) {
      notesCtx?.unregisterTableCandidate(pinId)
      return
    }
    notesCtx.registerTableCandidate({
      id: pinId,
      label: `Income statement (${stmt.toUpperCase()}) — entity breakdown`,
      description: 'All entities, IC eliminations, and consolidation',
      capture: () => captureConsolidationSnapshot(consol, 'PlConsolidationTableView'),
      viewState: { view_mode: viewMode, tab: stmt },
    })
    return () => notesCtx.unregisterTableCandidate(pinId)
  }, [notesCtx, consol, viewMode])

  const handleExport = useCallback(
    async (kind: PlExportKind) => {
      if (!consol) return
      if (kind === 'pdf' && entityStmt && viewMode === 'report') {
        const entityCheckOpen = buildExportCheckOpen(entityStmt.rows, entityStmt.statement)
        const markers = buildReportCommentMarkerMap(exportBullets, entityStmt.rows, entityCheckOpen)
        await exportPlReportViewPdf(
          entityStmt,
          year,
          month,
          miniColumns,
          planMap,
          exportBullets,
          exportCtx,
          narrative,
          markers,
          entityCheckOpen,
        )
        return
      }
      if (kind === 'pptx') {
        const footer = entityStmt
          ? buildExportFooterLine(entityStmt, exportCtx)
          : groupStatement
            ? buildExportFooterLine(groupStatement, exportCtx)
            : `${exportCtx.entityDisplayName} · ${consol.col_label ?? ''}A`
        if (viewMode === 'table') {
          await exportConsolidationTablePptx(
            consol,
            extraColumns,
            stmtCache,
            groupStatement,
            monthly,
            footer,
            consolidationCheckOpenRef.current ?? undefined,
          )
        } else if (entityStmt) {
          const entityCheckOpen = buildExportCheckOpen(entityStmt.rows, entityStmt.statement)
          const markers = buildReportCommentMarkerMap(exportBullets, entityStmt.rows, entityCheckOpen)
          await exportPlReportViewPptx(
            entityStmt,
            year,
            month,
            miniColumns,
            planMap,
            exportBullets,
            exportCtx,
            narrative,
            markers,
            entityCheckOpen,
          )
        }
        return
      }
      if (kind === 'xlsx') {
        if (viewMode === 'table') {
          await exportConsolidationTableXlsx(
            consol,
            extraColumns,
            stmtCache,
            groupStatement,
            planMap,
            monthly,
            consolidationCheckOpenRef.current ?? undefined,
          )
        } else if (entityStmt) {
          const cols = buildDefaultColumns(entityStmt.col_labels)
          const entityCheckOpen = buildExportCheckOpen(entityStmt.rows, entityStmt.statement)
          await exportPlTableView(
            entityStmt,
            cols,
            planMap,
            monthly,
            selected?.code ?? '',
            entityCheckOpen,
          )
        }
      }
    },
    [
      consol,
      entityStmt,
      viewMode,
      year,
      month,
      miniColumns,
      planMap,
      exportBullets,
      exportCtx,
      narrative,
      extraColumns,
      stmtCache,
      groupStatement,
      monthly,
      selected?.code,
    ],
  )

  const tableViewHeading = useMemo(() => {
    if (!consol) return 'Consolidated Income Statement'
    const lbl =
      colLabels ??
      (stmtCache.size > 0 ? [...stmtCache.values()][0]?.col_labels : undefined)
    if (lbl) return buildConsolidatedTableHeading(lbl)
    return consol.col_label
      ? `Consolidated Income Statement — ${consol.col_label}A`
      : 'Consolidated Income Statement'
  }, [consol, colLabels, stmtCache])

  if (loading && !consol) {
    return (
      <div className="rounded-xl p-8 text-center text-sm mt-4" style={{ background: '#FFF', border: '1px solid #E2E8F0', color: '#64748B' }}>
        Loading entity breakdown…
      </div>
    )
  }

  if (error && !consol) {
    return (
      <div className="rounded-xl p-8 text-center text-sm mt-4" style={{ background: '#FFF', border: '1px solid #FECACA', color: '#B91C1C' }}>
        {error}
      </div>
    )
  }

  if (!consol?.rows.length) return null

  const periodBadge = consol.col_label ? `${consol.col_label}A` : ''

  return (
    <>
      <div
        className="rounded-xl overflow-hidden mt-4"
        style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.05)' }}
      >
        <div
          className="px-4 pt-4 pb-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2"
          style={{ borderBottom: '1px solid #F1F5F9' }}
        >
          <div className="min-w-0 flex-1 basis-[min(200px,100%)]">
            <div className="flex items-center gap-2 mb-0.5">
              <span className="text-sm font-semibold" style={{ color: '#111827' }}>
                Income statement (consolidated) — entity breakdown
              </span>
              {periodBadge && (
                <span
                  className="text-xs font-medium px-2 py-0.5 rounded-md"
                  style={{ background: 'rgba(30,58,95,0.08)', color: '#1E3A5F', border: '1px solid rgba(30,58,95,0.15)' }}
                >
                  {periodBadge}
                </span>
              )}
            </div>
            <p className="text-xs" style={{ color: '#94A3B8' }}>
              {viewMode === 'report'
                ? 'Per-entity narrative and summary table'
                : 'All entities, IC eliminations, and consolidation — add columns per entity'}
            </p>
          </div>
          {viewMode === 'report' && entities.length > 0 && (
            <PlEntityCarousel
              compact
              entities={entities}
              index={entityIndex}
              onChange={setEntityIndex}
              disabled={entityLoading}
            />
          )}
          <div className="flex flex-wrap items-center gap-2 shrink-0">
            <PlViewToggleButton mode={viewMode} onChange={setViewMode} disabled={loading} />
            {viewMode === 'table' && (
              <PlConsolidationColumnEditor
                consol={consol}
                colLabels={colLabels}
                monthly={monthly}
                columns={extraColumns}
                onChange={setExtraColumns}
                carouselEntityCode={selected?.code}
                statement="pl"
              />
            )}
            {notesCtx && consol && (
              <button
                type="button"
                title="Pin to Action Board"
                className={PL_TOOLBAR_ICON_BTN}
                style={PL_TOOLBAR_BTN_STYLE}
                onClick={() => {
                  const pinId = `${consol.statement ?? 'pl'}-consolidation`
                  const snap = notesCtx.pinTableById(pinId)
                  if (snap) notesCtx.setToast('Open Action Notes to save — or use Pin table in panel')
                  else notesCtx.setToast('No table data to pin')
                }}
              >
                <Pin size={14} strokeWidth={1.75} />
              </button>
            )}
            <PlExportMenu onExport={handleExport} disabled={!consol || loading} />
          </div>
        </div>

        {viewMode === 'report' ? (
          entityLoading && !entityStmt ? (
            <div className="p-8 text-center text-sm text-slate-500">Loading entity statement…</div>
          ) : entityStmt ? (
            <PlReportView
              data={entityStmt}
              year={year}
              month={month}
              entity={selected?.code}
              entityDisplayName={selected?.label}
              planMap={planMap}
              hasPlanData={entityHasPlanData}
              onDrill={d => onDrill({ ...d, entityOverride: selected?.code })}
              onBulletSelect={setDetailBullet}
              onNarrativeLoaded={setNarrative}
            />
          ) : (
            <div className="p-8 text-center text-sm text-slate-500">No data for this entity.</div>
          )
        ) : (
          <div className="px-4 pt-4 pb-2">
            <PlSectionHeading>{tableViewHeading}</PlSectionHeading>
            <PlConsolidationTableView
              data={consol}
              year={year}
              month={month}
              extraColumns={extraColumns}
              statementByEntity={stmtCache}
              groupStatement={groupStatement}
              monthly={monthly}
              onDrill={onDrill}
              onRegisterCheckOpen={fn => {
                consolidationCheckOpenRef.current = fn
              }}
            />
          </div>
        )}
      </div>

      {detailBullet && (
        <PlDetailOverlay
          bullet={detailBullet}
          year={year}
          month={month}
          entity={selected?.code}
          narrativeContext={
            narrative
              ? { headline: narrative.headline, intro: narrative.intro, intro_facts: narrative.intro_facts }
              : undefined
          }
          onClose={() => setDetailBullet(null)}
        />
      )}
    </>
  )
}
