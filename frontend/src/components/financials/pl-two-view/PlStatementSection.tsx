import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Pin } from 'lucide-react'
import { useOptionalActionNotesContext } from '../../action-notes/ActionNotesContext'
import { buildPlTablePinSnapshot } from '../../action-notes/plTableCaptureRegister'
import type { FinancialStatementResponse, MonthlyResponse, PlNarrativeResponse } from '../../../lib/api'
import type { PeriodSelection } from '../../../lib/periodSelection'
import PlExportMenu, { type PlExportKind } from './PlExportMenu'
import type { FinancialsDrillOpen } from '../FinancialStatementTable'
import PlColumnEditor from './PlColumnEditor'
import PlDetailOverlay from './PlDetailOverlay'
import { buildPlanMapFromStatement } from './plPlanMap'
import PlReportView from './PlReportView'
import PlTableView, { usePlTableColumns } from './PlTableView'
import PlViewToggleButton, { type PlViewMode } from './PlViewToggleButton'
import { buildColumnCatalog, buildDefaultColumns, reconcileColumns, PLAN_ONLY_COL_KINDS } from './plColumnRegistry'
import { buildClientNarrativeResponse, mapApiBulletsToUi } from './plNarrativeEngine'
import { exportPlTableView } from './plExport'
import { exportPlReportViewPdf, exportPlTableViewPdf, type PlExportContext } from './plExportPdf'
import { exportPlReportViewPptx, exportPlTableViewPptx } from './plExportPptx'
import type { PlNarrativeBullet } from './plNarrativeEngine'
import { buildReportCommentMarkerMap } from '../statement-two-view/reportCommentMarkers'
import type { PlTableColumnDef } from './plColumnRegistry'
import { usePlRowExpansion } from './usePlRowExpansion'
import { getStatementConfig } from '../statement-two-view/statementConfig'
import {
  applyViewModeFromSearchParams,
  loadStatementViewMode,
  saveStatementViewMode,
} from '../statement-two-view/statementViewMode'
import { PL_TOOLBAR_BTN_STYLE, PL_TOOLBAR_ICON_BTN } from './plToolbarButton'

type Props = {
  data: FinancialStatementResponse | null
  monthly: MonthlyResponse | null
  loading: boolean
  error: string | null
  year: number
  month: number
  entity?: string
  entityLabel?: string
  entityDisplayName?: string
  periodSelection?: PeriodSelection
  onDrill: (d: FinancialsDrillOpen) => void
}

export default function PlStatementSection({
  data,
  monthly,
  loading,
  error,
  year,
  month,
  entity: _entity,
  entityLabel = 'all',
  entityDisplayName,
  periodSelection,
  onDrill,
}: Props) {
  const groupLabel =
    entityDisplayName ?? (entityLabel === 'all' ? 'All entities (consolidated)' : entityLabel)
  const plCfg = getStatementConfig('pl')
  const [viewMode, setViewMode] = useState<PlViewMode>(() =>
    loadStatementViewMode('pl') as PlViewMode,
  )
  const [detailBullet, setDetailBullet] = useState<PlNarrativeBullet | null>(null)
  const [narrative, setNarrative] = useState<PlNarrativeResponse | null>(null)
  const hasPlanData = data?.plan?.has_plan_data ?? false
  const defaultColumns = usePlTableColumns(data, monthly, hasPlanData)
  const [columns, setColumns] = useState<PlTableColumnDef[]>([])
  // Tracks the period grain the current `columns` were built for. When the grain
  // flips (month <-> week) we must reset to that grain's defaults instead of
  // reconciling the previous grain's columns (otherwise week-only columns like
  // MTD/MTG never appear after switching from month, and vice versa).
  const lastGrainRef = useRef<string | null>(null)
  const notesCtx = useOptionalActionNotesContext()
  const { checkOpen } = usePlRowExpansion(data?.rows)

  const planMap = useMemo(() => buildPlanMapFromStatement(data), [data])

  useEffect(() => {
    if (!defaultColumns.length || !data) return
    const periodGrain = data.period_grain === 'week' ? 'week' : 'month'
    // Detect grain flips OUTSIDE the state updater. Mutating the ref inside the
    // updater is impure and double-fires under React StrictMode, which would
    // wrongly take the reconcile path and keep the previous grain's columns.
    const grainChanged = lastGrainRef.current !== periodGrain
    lastGrainRef.current = periodGrain
    setColumns(prev => {
      if (!prev.length || grainChanged) return defaultColumns
      const periods = periodGrain === 'week' ? [] : (monthly?.periods ?? [])
      const catalog = buildColumnCatalog(data.col_labels, periods, periodGrain)
      const reconciled = reconcileColumns(prev, catalog, periods)
      return hasPlanData ? reconciled : reconciled.filter(c => !PLAN_ONLY_COL_KINDS.has(c.kind))
    })
  }, [defaultColumns, data, monthly, hasPlanData])

  useEffect(() => {
    applyViewModeFromSearchParams(setViewMode)
  }, [])

  useEffect(() => {
    saveStatementViewMode('pl', viewMode)
  }, [viewMode])

  const allDataColumns = useMemo(
    () =>
      data
        ? buildDefaultColumns(
            data.col_labels,
            data.period_grain === 'week' ? 'week' : 'month',
            data.statement,
            hasPlanData,
          )
        : [],
    [data, hasPlanData],
  )

  const miniColumns = useMemo(() => {
    const kinds = hasPlanData
      ? ['pm', 'cm', 'mom', 'plan_cm', 'plan_vs_actual']
      : ['pm', 'cm', 'mom']
    return allDataColumns.filter(c => kinds.includes(c.kind as string))
  }, [allDataColumns, hasPlanData])

  useEffect(() => {
    if (!notesCtx || !data) {
      if (notesCtx) notesCtx.unregisterTableCandidate('pl-statement')
      return
    }
    const cols = viewMode === 'table' ? columns : miniColumns
    const component = viewMode === 'table' ? 'PlTableView' : 'PlReportView'
    notesCtx.registerTableCandidate({
      id: 'pl-statement',
      label: plCfg.pinTableLabel,
      description: viewMode === 'table' ? 'Full table columns' : 'Report view mini table',
      capture: () => buildPlTablePinSnapshot(data, checkOpen, cols, component),
      viewState: { view_mode: viewMode, tab: 'pl' },
    })
    return () => notesCtx.unregisterTableCandidate('pl-statement')
  }, [notesCtx, data, viewMode, columns, miniColumns, checkOpen, plCfg.pinTableLabel])

  const clientNarrative = useMemo(
    () => (data ? buildClientNarrativeResponse(data, planMap, year, month, 5) : null),
    [data, planMap, year, month],
  )

  const exportBullets = useMemo((): PlNarrativeBullet[] => {
    if (!data) return []
    const src = narrative ?? clientNarrative
    if (src?.bullets?.length) {
      return mapApiBulletsToUi(src.bullets, data.rows)
    }
    return []
  }, [data, narrative, clientNarrative])

  const commentMarkersByLineCode = useMemo(() => {
    if (!data?.rows.length) return {}
    return buildReportCommentMarkerMap(exportBullets, data.rows, checkOpen)
  }, [exportBullets, data?.rows, checkOpen])

  const exportCtx = useMemo(
    (): PlExportContext => ({ entityLabel, entityDisplayName: groupLabel }),
    [entityLabel, groupLabel],
  )

  const handleExport = useCallback(async (kind: PlExportKind) => {
    if (!data) return
    if (kind === 'pdf') {
      if (viewMode === 'table') {
        await exportPlTableViewPdf(data, year, month, columns, planMap, monthly, exportCtx, checkOpen)
      } else {
        await exportPlReportViewPdf(
          data,
          year,
          month,
          miniColumns,
          planMap,
          exportBullets,
          exportCtx,
          narrative,
          commentMarkersByLineCode,
          checkOpen,
        )
      }
      return
    }
    if (kind === 'pptx') {
      if (viewMode === 'table') {
        await exportPlTableViewPptx(data, year, month, columns, planMap, monthly, exportCtx, checkOpen)
      } else {
        await exportPlReportViewPptx(
          data,
          year,
          month,
          miniColumns,
          planMap,
          exportBullets,
          exportCtx,
          narrative,
          commentMarkersByLineCode,
          checkOpen,
        )
      }
      return
    }
    const xlsxColumns = viewMode === 'table' ? columns : allDataColumns
    await exportPlTableView(data, xlsxColumns, planMap, monthly, entityLabel, checkOpen, undefined, _entity)
  }, [
    data,
    viewMode,
    columns,
    allDataColumns,
    planMap,
    monthly,
    miniColumns,
    exportBullets,
    exportCtx,
    narrative,
    commentMarkersByLineCode,
    entityLabel,
    year,
    month,
    checkOpen,
  ])

  if (loading && !data) {
    return (
      <div className="rounded-xl p-8 text-center text-sm" style={{ background: '#FFF', border: '1px solid #E2E8F0', color: '#64748B' }}>
        Loading…
      </div>
    )
  }

  if (error && !data) {
    return (
      <div className="rounded-xl p-8 text-center text-sm" style={{ background: '#FFF', border: '1px solid #E2E8F0', color: '#B91C1C' }}>
        {error}
      </div>
    )
  }

  if (!data?.rows.length) {
    return (
      <div className="rounded-xl p-8 text-center text-sm" style={{ background: '#FFF', border: '1px solid #E2E8F0', color: '#64748B' }}>
        No rows for this period / entity. Check demo data (setup_db) and that the period exists in the database.
      </div>
    )
  }

  const periodBadge = data.col_labels?.cm ? `${data.col_labels.cm}A` : ''
  return (
    <>
      <div
        className="rounded-xl overflow-hidden"
        style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.05)' }}
      >
        <div className="px-4 pt-4 pb-3 flex items-start justify-between gap-3" style={{ borderBottom: '1px solid #F1F5F9' }}>
          <div>
            <div className="flex items-center gap-2 mb-0.5">
              <span className="text-sm font-semibold" style={{ color: '#111827' }}>
                {plCfg.cardTitle}
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
              {viewMode === 'report' ? plCfg.reportSubtitle : plCfg.tableSubtitle}
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <PlViewToggleButton mode={viewMode} onChange={setViewMode} disabled={loading} />
            {viewMode === 'table' && (
              <PlColumnEditor
                colLabels={data.col_labels}
                monthly={monthly}
                columns={columns}
                onChange={setColumns}
                statement="pl"
                hasPlanData={hasPlanData}
              />
            )}
            {notesCtx && (
              <button
                type="button"
                title="Pin current table to Action Notes"
                className={PL_TOOLBAR_ICON_BTN}
                style={PL_TOOLBAR_BTN_STYLE}
                onClick={() => {
                  const snap = notesCtx.pinTable(plCfg.pinTableLabel)
                  if (snap) notesCtx.setToast('Open Action Notes to save — or use Pin table in panel')
                  else notesCtx.setToast('No table data to pin')
                }}
              >
                <Pin size={14} strokeWidth={1.75} />
              </button>
            )}
            <PlExportMenu onExport={handleExport} disabled={!data || loading} />
          </div>
        </div>

        {viewMode === 'report' ? (
          <PlReportView
            data={data}
            year={year}
            month={month}
            entity={_entity}
            periodSelection={periodSelection}
            entityDisplayName={entityLabel === 'all' ? undefined : groupLabel}
            planMap={planMap}
            onDrill={onDrill}
            onBulletSelect={setDetailBullet}
            onNarrativeLoaded={setNarrative}
          />
        ) : (
          <PlTableView
            data={data}
            monthly={monthly}
            year={year}
            month={month}
            planMap={planMap}
            columns={columns}
            onDrill={onDrill}
          />
        )}
      </div>

      {detailBullet && (
        <PlDetailOverlay
          bullet={detailBullet}
          year={year}
          month={month}
          entity={_entity}
          narrativeContext={
            narrative
              ? {
                  headline: narrative.headline,
                  intro: narrative.intro,
                  intro_facts: narrative.intro_facts,
                }
              : undefined
          }
          onClose={() => setDetailBullet(null)}
        />
      )}
    </>
  )
}
