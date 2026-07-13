import { useCallback, useEffect, useMemo, useRef, useState, type ComponentType } from 'react'
import { Pin } from 'lucide-react'
import { useOptionalActionNotesContext } from '../../../action-notes/ActionNotesContext'
import { captureConsolidationSnapshot } from '../../../action-notes/captureConsolidationTable'
import { STATEMENT_TOOLBAR_ICON_BTN, STATEMENT_TOOLBAR_BTN_STYLE } from '../statementToolbarButton'
import type {
  ConsolidationResponse,
  FinancialStatementResponse,
  MonthlyResponse,
  PlNarrativeResponse,
} from '../../../../lib/api'
import type { FinancialsDrillOpen } from '../../FinancialStatementTable'
import PlConsolidationColumnEditor from '../../pl-two-view/PlConsolidationColumnEditor'
import PlConsolidationTableView from '../../pl-two-view/PlConsolidationTableView'
import PlEntityCarousel from '../../pl-two-view/PlEntityCarousel'
import PlExportMenu, { type PlExportKind } from '../../pl-two-view/PlExportMenu'
import {
  loadConsolidationColumns,
  type PlConsolidationColumnDef,
} from '../../pl-two-view/plConsolidationColumnRegistry'
import { buildDefaultColumns } from '../../pl-two-view/plColumnRegistry'
import { exportPlTableView } from '../../pl-two-view/plExport'
import { buildPlanMapFromStatement } from '../../pl-two-view/plPlanMap'
import type { PlNarrativeBullet } from '../../pl-two-view/plNarrativeEngine'
import { useStatementExportBullets } from '../useStatementExportBullets'
import type { PlExportContext } from '../../pl-two-view/plExportFooter'
import BsDetailOverlay from '../bs/BsDetailOverlay'
import BsReportView from '../bs/BsReportView'
import CfDetailOverlay from '../cf/CfDetailOverlay'
import CfReportView from '../cf/CfReportView'
import { getStatementConfig } from '../statementConfig'
import StatementViewToggleButton from '../StatementViewToggleButton'
import type { FinStatementKind, StatementViewMode } from '../statementTypes'
import { useChartLoadReporter } from '../../../../hooks/useChartLoadReporter'
import WcDetailOverlay from '../wc/WcDetailOverlay'
import WcReportView from '../wc/WcReportView'
import { exportConsolidationPptx, exportConsolidationXlsx } from './consolidationExport'
import { exportStatementReportViewPptx } from '../statementReportExport'
import { buildExportCheckOpen } from '../../../../lib/finssentialsExport/buildExportCheckOpen'
import { buildReportCommentMarkerMap } from '../reportCommentMarkers'
import type { PeriodSelection } from '../../../../lib/periodSelection'
import { monthLabelShort } from '../../../../lib/periodSelection'
import { usePlRowExpansion } from '../../pl-two-view/usePlRowExpansion'
import ErSnapshotReportView from '../../annual/ErSnapshotReportView'
import ErFlowReportView from '../../annual/ErFlowReportView'
import AnnualConsolidationReportView from '../../annual/AnnualConsolidationReportView'
import AnnualConsolidationViewToggle, {
  type AnnualConsolViewMode,
} from '../../annual/AnnualConsolidationViewToggle'
import { formatConsolidationPeriodLabel } from '../../../../lib/periodColumnLabels'
import {
  buildAnnualCfNarrativeResponse,
  buildAnnualSnapshotNarrativeResponse,
} from '../../annual/erAnnualNarrative'
import { buildAnnualFlowReportColumns } from '../../annual/annualFlowReportColumns'
import type { ErFlowColLabels, ErFlowResponse, ErSnapshotResponse } from '../../../../lib/api'
import { labelActual } from '../../../../lib/periodColumnLabels'
import { fetchConsolidatedStatement, fetchStatementForEntity } from './statementConsolidationApi'

type ReportViewProps = {
  data: FinancialStatementResponse
  year: number
  month: number
  periodSelection?: PeriodSelection
  entity?: string
  entityDisplayName?: string
  onDrill: (d: FinancialsDrillOpen) => void
  onBulletSelect: (b: PlNarrativeBullet) => void
  onNarrativeLoaded?: (narrative: PlNarrativeResponse | null) => void
}

type DetailOverlayProps = {
  bullet: PlNarrativeBullet
  year: number
  month: number
  entity?: string
  narrativeContext?: {
    headline?: string
    intro?: string
    intro_facts?: PlNarrativeResponse['intro_facts']
  }
  onClose: () => void
}

const REPORT_VIEWS: Record<Exclude<FinStatementKind, 'pl'>, ComponentType<ReportViewProps>> = {
  bs: BsReportView,
  cf: CfReportView,
  wc: WcReportView,
}

const DETAIL_OVERLAYS: Record<Exclude<FinStatementKind, 'pl'>, ComponentType<DetailOverlayProps>> = {
  bs: BsDetailOverlay,
  cf: CfDetailOverlay,
  wc: WcDetailOverlay,
}

function viewModeKey(kind: FinStatementKind): string {
  return `finssentials.${kind}.consolidation.viewMode.v1`
}

function entityIndexKey(kind: FinStatementKind): string {
  return `finssentials.${kind}.consolidation.entityIndex.v1`
}

function bsWcCfViewModeKey(statement: 'bs' | 'wc' | 'cf'): string {
  return `finssentials.${statement}.consol.viewMode.v2`
}

function loadBsWcCfConsolViewMode(statement: 'bs' | 'wc' | 'cf'): AnnualConsolViewMode {
  try {
    const v = localStorage.getItem(bsWcCfViewModeKey(statement))
    if (v === 'table' || v === 'entity' || v === 'group') return v
    const legacy = localStorage.getItem(viewModeKey(statement))
    if (legacy === 'table') return 'table'
    if (legacy === 'report') return 'entity'
    return 'entity'
  } catch {
    return 'entity'
  }
}

function loadViewMode(kind: FinStatementKind): StatementViewMode {
  try {
    return localStorage.getItem(viewModeKey(kind)) === 'table' ? 'table' : 'report'
  } catch {
    return 'report'
  }
}

function loadEntityIndex(kind: FinStatementKind, max: number): number {
  try {
    const v = parseInt(localStorage.getItem(entityIndexKey(kind)) ?? '0', 10)
    if (Number.isNaN(v) || v < 0) return 0
    return max > 0 ? Math.min(v, max - 1) : 0
  } catch {
    return 0
  }
}

type ConsolSectionViewMode = AnnualConsolViewMode | StatementViewMode

type Props = {
  statement: Exclude<FinStatementKind, 'pl'>
  consol: ConsolidationResponse | null
  monthly: MonthlyResponse | null
  loading: boolean
  error?: string | null
  year: number
  month: number
  periodSelection: PeriodSelection
  onDrill: (d: FinancialsDrillOpen) => void
}

export default function StatementConsolidationSection({
  statement,
  consol,
  monthly,
  loading,
  error,
  year,
  month,
  periodSelection,
  onDrill,
}: Props) {
  const cfg = getStatementConfig(statement)
  const ReportView = REPORT_VIEWS[statement]
  const DetailOverlay = DETAIL_OVERLAYS[statement]
  const threeMode = statement === 'bs' || statement === 'wc' || statement === 'cf'

  const [viewMode, setViewMode] = useState<ConsolSectionViewMode>(() =>
    threeMode ? loadBsWcCfConsolViewMode(statement) : loadViewMode(statement),
  )
  const [entityIndex, setEntityIndex] = useState(0)
  const [entityStmt, setEntityStmt] = useState<FinancialStatementResponse | null>(null)
  const [entityLoading, setEntityLoading] = useState(false)
  const [stmtCache, setStmtCache] = useState<Map<string, FinancialStatementResponse>>(() => new Map())
  const [groupStatement, setGroupStatement] = useState<FinancialStatementResponse | null>(null)
  const [extraColumns, setExtraColumns] = useState<PlConsolidationColumnDef[]>(() =>
    loadConsolidationColumns(statement),
  )
  const [detailBullet, setDetailBullet] = useState<PlNarrativeBullet | null>(null)
  const [narrative, setNarrative] = useState<PlNarrativeResponse | null>(null)

  const isAnnualGrain = periodSelection.grain === 'year'
  const isEntityReport = threeMode ? viewMode === 'entity' : viewMode === 'report'
  const isGroupReport = threeMode && viewMode === 'group'
  const isTableView = viewMode === 'table'

  const groupPeriodLabel = useMemo(
    () =>
      formatConsolidationPeriodLabel(
        consol?.col_label ?? monthLabelShort(year, month),
      ),
    [consol?.col_label, year, month],
  )

  const entities = consol?.entities ?? []
  const selected = entities[entityIndex]

  const entityExpansion = usePlRowExpansion(entityStmt?.rows ?? [], statement)

  const monthlyForStatement =
    monthly?.statement === statement ? monthly : null

  const annualSnapshotClientNarrative = useMemo(() => {
    if (!isAnnualGrain || !entityStmt?.rows?.length || statement === 'cf') return null
    const lbl = entityStmt.col_labels as { fy?: string; fy_py?: string; cm?: string; cm_py?: string }
    return buildAnnualSnapshotNarrativeResponse(
      entityStmt.rows as import('../../../../lib/api').ErStatementRow[],
      lbl.fy ?? labelActual(`FY${String(year - 1).slice(-2)}`),
      lbl.fy_py ?? labelActual(`FY${String(year - 2).slice(-2)}`),
      lbl.cm ?? 'CM',
      selected?.code,
    )
  }, [isAnnualGrain, entityStmt, statement, year, selected?.code])

  const annualFlowClientNarrative = useMemo(() => {
    if (!isAnnualGrain || !entityStmt?.rows?.length || statement !== 'cf') return null
    const lbl = entityStmt.col_labels as unknown as ErFlowColLabels
    const fy3 = lbl.fy3 ?? labelActual(`FY${String(year - 1).slice(-2)}`)
    const fy2 = lbl.fy2 ?? labelActual(`FY${String(year - 2).slice(-2)}`)
    return buildAnnualCfNarrativeResponse(entityStmt.rows as import('../../../../lib/api').ErStatementRow[], year, month, fy3, fy2, selected?.code)
  }, [isAnnualGrain, entityStmt, statement, year, month, selected?.code])

  const annualCfHasPlanData = (entityStmt as unknown as ErFlowResponse | null)?.has_plan_data ?? false
  const annualCfReportColumns = useMemo(
    () => buildAnnualFlowReportColumns(
      entityStmt?.col_labels as unknown as ErFlowColLabels | undefined,
      year,
      month,
      annualCfHasPlanData,
    ),
    [entityStmt?.col_labels, year, month, annualCfHasPlanData],
  )

  useChartLoadReporter(`fin-consl-${statement}`, loading || entityLoading, error)

  useEffect(() => {
    setStmtCache(new Map())
    setEntityStmt(null)
    setGroupStatement(null)
  }, [periodSelection])

  useEffect(() => {
    if (!entities.length) return
    setEntityIndex(loadEntityIndex(statement, entities.length))
  }, [entities.length, periodSelection, statement])

  useEffect(() => {
    if (threeMode) {
      localStorage.setItem(bsWcCfViewModeKey(statement), viewMode as AnnualConsolViewMode)
    } else {
      localStorage.setItem(viewModeKey(statement), viewMode as StatementViewMode)
    }
  }, [viewMode, statement, threeMode])

  useEffect(() => {
    localStorage.setItem(entityIndexKey(statement), String(entityIndex))
  }, [entityIndex, statement])

  useEffect(() => {
    setExtraColumns(loadConsolidationColumns(statement))
  }, [statement])

  useEffect(() => {
    setNarrative(null)
  }, [selected?.code, periodSelection])

  useEffect(() => {
    if (!selected?.code) {
      setEntityStmt(null)
      return
    }
    const code = selected.code
    let cancelled = false
    let skipFetch = false
    setStmtCache(prev => {
      const hit = prev.get(code)
      if (hit) {
        setEntityStmt(hit)
        skipFetch = true
      }
      return prev
    })
    if (skipFetch) {
      setEntityLoading(false)
      return
    }
    setEntityLoading(true)
    void fetchStatementForEntity(statement, periodSelection, code)
      .then(res => {
        if (!cancelled) {
          setStmtCache(prev => new Map(prev).set(code, res))
          setEntityStmt(res)
        }
      })
      .catch(() => {
        if (!cancelled) setEntityStmt(null)
      })
      .finally(() => {
        if (!cancelled) setEntityLoading(false)
      })
    return () => {
      cancelled = true
    }
  }, [selected?.code, periodSelection, statement])

  useEffect(() => {
    if (viewMode !== 'table') return
    let cancelled = false
    void fetchConsolidatedStatement(statement, periodSelection)
      .then(res => {
        if (!cancelled) setGroupStatement(res)
      })
      .catch(() => {
        if (!cancelled) setGroupStatement(null)
      })
    return () => {
      cancelled = true
    }
  }, [viewMode, periodSelection, statement])

  useEffect(() => {
    if (!consol?.entities.length || !extraColumns.length) return
    const needed = new Set<string>()
    for (const c of extraColumns) {
      if (c.entityCode) needed.add(c.entityCode)
    }
    for (const code of needed) {
      if (!stmtCache.has(code)) {
        void fetchStatementForEntity(statement, periodSelection, code).then(res =>
          setStmtCache(prev => new Map(prev).set(code, res)),
        )
      }
    }
  }, [consol, extraColumns, periodSelection, statement])

  const colLabels =
    entityStmt?.col_labels ??
    groupStatement?.col_labels ??
    (stmtCache.size > 0 ? [...stmtCache.values()][0]?.col_labels : undefined)

  const sectionTitle = `${cfg.cardTitle} — entity breakdown`
  const consolidationCheckOpenRef = useRef<((id: string) => boolean) | null>(null)
  const notesCtx = useOptionalActionNotesContext()

  // ─── Pin registration ──────────────────────────────────────────────────────
  useEffect(() => {
    const pinId = `${statement}-consolidation`
    if (!notesCtx || !consol?.rows?.length) {
      notesCtx?.unregisterTableCandidate(pinId)
      return
    }
    notesCtx.registerTableCandidate({
      id: pinId,
      label: `${cfg.cardTitle} — entity breakdown`,
      description: 'All entities, IC eliminations, and consolidation',
      capture: () => captureConsolidationSnapshot(consol, 'StatementConsolidationSection'),
      viewState: { view_mode: viewMode, tab: statement },
    })
    return () => notesCtx.unregisterTableCandidate(pinId)
  }, [notesCtx, consol, viewMode, statement, cfg.cardTitle])

  const entityPlanMap = useMemo(() => buildPlanMapFromStatement(entityStmt), [entityStmt])

  const entityTableColumns = useMemo(() => {
    if (!entityStmt) return []
    const grain = entityStmt.period_grain === 'week' ? 'week' : 'month'
    return buildDefaultColumns(entityStmt.col_labels, grain, entityStmt.statement)
  }, [entityStmt])

  const miniColumns = useMemo(() => {
    const grain = entityStmt?.period_grain === 'week' ? 'week' : 'month'
    const kinds =
      grain === 'week'
        ? ['pm', 'cm', 'mom', 'mtd', 'plan_cm', 'plan_vs_actual']
        : ['pm', 'cm', 'mom', 'plan_cm', 'plan_vs_actual']
    return entityTableColumns.filter(c => kinds.includes(c.kind))
  }, [entityTableColumns, entityStmt?.period_grain])

  const exportBullets = useStatementExportBullets(entityStmt, narrative, null)

  const commentMarkersByLineCode = useMemo(() => {
    if (!entityStmt?.rows.length) return {}
    const checkOpen = buildExportCheckOpen(entityStmt.rows, entityStmt.statement)
    return buildReportCommentMarkerMap(exportBullets, entityStmt.rows, checkOpen)
  }, [exportBullets, entityStmt])

  const exportCtx = useMemo(
    (): PlExportContext => ({
      entityLabel: selected?.code ?? 'all',
      entityDisplayName: selected?.label ?? 'Consolidated',
    }),
    [selected?.code, selected?.label],
  )

  const handleExport = useCallback(
    async (kind: PlExportKind) => {
      if (!consol) return
      const footer = `${selected?.label ?? 'Consolidated'} · ${consol.col_label ?? ''}A`
      if (kind === 'pptx') {
        if (viewMode === 'table') {
          await exportConsolidationPptx(consol, footer, consolidationCheckOpenRef.current ?? undefined)
        } else if (entityStmt && isEntityReport) {
          await exportStatementReportViewPptx(
            statement,
            entityStmt,
            year,
            month,
            miniColumns,
            exportBullets,
            exportCtx,
            narrative,
            commentMarkersByLineCode,
          )
        }
        return
      }
      if (kind === 'xlsx') {
        if (viewMode === 'table') {
          await exportConsolidationXlsx(consol, consolidationCheckOpenRef.current ?? undefined)
        } else if (entityStmt && isEntityReport) {
          const grain = entityStmt.period_grain === 'week' ? 'week' : 'month'
          const cols = buildDefaultColumns(entityStmt.col_labels, grain, entityStmt.statement)
          await exportPlTableView(
            entityStmt,
            cols,
            entityPlanMap,
            monthly,
            selected?.code ?? '',
          )
        }
      }
    },
    [
      consol,
      viewMode,
      entityStmt,
      entityPlanMap,
      monthly,
      selected?.code,
      selected?.label,
      statement,
      year,
      month,
      exportBullets,
      exportCtx,
      narrative,
      commentMarkersByLineCode,
      isEntityReport,
    ],
  )

  const periodBadge = consol?.col_label ? `${consol.col_label}A` : ''

  if (loading && !consol) {
    return (
      <div
        className="rounded-xl p-8 text-center text-sm mt-4"
        style={{ background: '#FFF', border: '1px solid #E2E8F0', color: '#64748B' }}
      >
        Loading entity breakdown…
      </div>
    )
  }

  if (error && !consol) {
    return (
      <div
        className="rounded-xl p-8 text-center text-sm mt-4"
        style={{ background: '#FFF', border: '1px solid #FECACA', color: '#B91C1C' }}
      >
        {error}
      </div>
    )
  }

  if (!consol?.rows.length) return null

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
                {sectionTitle}
              </span>
              {periodBadge ? (
                <span
                  className="text-xs font-medium px-2 py-0.5 rounded-md"
                  style={{
                    background: 'rgba(30,58,95,0.08)',
                    color: '#1E3A5F',
                    border: '1px solid rgba(30,58,95,0.15)',
                  }}
                >
                  {periodBadge}
                </span>
              ) : null}
            </div>
            <p className="text-xs" style={{ color: '#94A3B8' }}>
              {isGroupReport
                ? 'Group-level narrative with entity attribution across the portfolio'
                : isEntityReport
                  ? 'Per-entity narrative and summary table'
                  : 'All entities, IC eliminations, and consolidation — add columns per entity'}
            </p>
          </div>
          {isEntityReport && entities.length > 0 ? (
            <PlEntityCarousel
              compact
              entities={entities}
              index={entityIndex}
              onChange={setEntityIndex}
              disabled={entityLoading}
            />
          ) : null}
          <div className="flex flex-wrap items-center gap-2 shrink-0">
            {threeMode ? (
              <AnnualConsolidationViewToggle
                mode={viewMode as AnnualConsolViewMode}
                onChange={m => setViewMode(m)}
                disabled={loading}
              />
            ) : (
              <StatementViewToggleButton
                mode={viewMode as StatementViewMode}
                onChange={m => setViewMode(m)}
                disabled={loading}
              />
            )}
            {isTableView && cfg.features.columnEditor && consol && colLabels ? (
              <PlConsolidationColumnEditor
                consol={consol}
                colLabels={colLabels}
                monthly={monthlyForStatement}
                columns={extraColumns}
                onChange={setExtraColumns}
                carouselEntityCode={selected?.code}
                statement={statement}
              />
            ) : null}
            {notesCtx && (
              <button
                type="button"
                title="Pin current table to Action Notes"
                className={STATEMENT_TOOLBAR_ICON_BTN}
                style={STATEMENT_TOOLBAR_BTN_STYLE}
                onClick={() => {
                  const pinId = `${statement}-consolidation`
                  const snap = notesCtx.pinTableById(pinId)
                  if (snap) notesCtx.setToast('Open Action Notes to save — or use Pin table in panel')
                  else notesCtx.setToast('No table data to pin')
                }}
              >
                <Pin size={14} strokeWidth={1.75} />
              </button>
            )}
            <PlExportMenu formats={['pptx', 'xlsx']} onExport={handleExport} disabled={!consol || loading} />
          </div>
        </div>

        {isGroupReport && consol ? (
          <AnnualConsolidationReportView
            consol={consol}
            year={year}
            month={month}
            ytdLabel={groupPeriodLabel}
            periodSelection={periodSelection}
            statement={statement}
            onDrill={onDrill}
          />
        ) : isEntityReport ? (
          entityLoading && !entityStmt ? (
            <div className="p-8 text-center text-sm text-slate-500">Loading entity statement…</div>
          ) : entityStmt && isAnnualGrain && statement === 'cf' ? (
            <ErFlowReportView
              data={entityStmt as unknown as ErFlowResponse}
              statement="cf"
              year={year}
              month={month}
              entity={selected?.code}
              periodSelection={periodSelection}
              entityDisplayName={selected?.label}
              reportColumns={annualCfReportColumns}
              clientNarrative={annualFlowClientNarrative}
              onDrill={d => onDrill({ ...d, entityOverride: selected?.code })}
              checkOpen={entityExpansion.checkOpen}
              toggle={entityExpansion.toggle}
              fy2Label={annualCfReportColumns[0]?.labelLine1 ?? ''}
              fy3Label={annualCfReportColumns[1]?.labelLine1 ?? ''}
            />
          ) : entityStmt && isAnnualGrain && (statement === 'bs' || statement === 'wc') ? (
            <ErSnapshotReportView
              data={entityStmt as unknown as ErSnapshotResponse}
              statement={statement}
              year={year}
              month={month}
              entity={selected?.code}
              periodSelection={periodSelection}
              entityDisplayName={selected?.label}
              clientNarrative={annualSnapshotClientNarrative}
              onDrill={d => onDrill({ ...d, entityOverride: selected?.code })}
              checkOpen={entityExpansion.checkOpen}
              toggle={entityExpansion.toggle}
            />
          ) : entityStmt ? (
            <ReportView
              data={entityStmt}
              year={year}
              month={month}
              periodSelection={periodSelection}
              entity={selected?.code}
              entityDisplayName={selected?.label}
              onDrill={d => onDrill({ ...d, entityOverride: selected?.code })}
              onBulletSelect={setDetailBullet}
              onNarrativeLoaded={setNarrative}
            />
          ) : (
            <div className="p-8 text-center text-sm text-slate-500">No data for this entity.</div>
          )
        ) : (
          <PlConsolidationTableView
            data={consol}
            year={year}
            month={month}
            extraColumns={extraColumns}
            statementByEntity={stmtCache}
            groupStatement={groupStatement}
            monthly={monthlyForStatement}
            onDrill={onDrill}
            onRegisterCheckOpen={fn => {
              consolidationCheckOpenRef.current = fn
            }}
          />
        )}
      </div>

      {detailBullet && (
        <DetailOverlay
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
