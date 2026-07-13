import { useCallback, useEffect, useMemo, useState } from 'react'
import { Pin } from 'lucide-react'
import { useOptionalActionNotesContext } from '../../../action-notes/ActionNotesContext'
import { buildPlTablePinSnapshot } from '../../../action-notes/plTableCaptureRegister'
import { usePlRowExpansion } from '../../pl-two-view/usePlRowExpansion'
import type { FinancialStatementResponse, MonthlyResponse, PlNarrativeResponse } from '../../../../lib/api'
import type { PeriodSelection } from '../../../../lib/periodSelection'
import type { FinancialsDrillOpen } from '../../FinancialStatementTable'
import PlExportMenu, { type PlExportKind } from '../../pl-two-view/PlExportMenu'
import PlTableView, { usePlTableColumns } from '../../pl-two-view/PlTableView'
import {
  buildColumnCatalog,
  buildDefaultColumns,
  reconcileColumns,
  PLAN_ONLY_COL_KINDS,
  type PlTableColumnDef,
} from '../../pl-two-view/plColumnRegistry'
import { buildPlanMapFromStatement } from '../../pl-two-view/plPlanMap'
import { exportPlTableView } from '../../pl-two-view/plExport'
import type { PlExportContext } from '../../pl-two-view/plExportFooter'
import { buildClientCfNarrative } from './cfNarrativeEngine'
import {
  exportStatementReportViewPdf,
  exportStatementReportViewPptx,
  exportStatementTableViewPptx,
  exportStatementTableViewPdf,
} from '../statementReportExport'
import { useStatementExportBullets } from '../useStatementExportBullets'
import { buildReportCommentMarkerMap } from '../reportCommentMarkers'
import { getStatementConfig } from '../statementConfig'
import { applyViewModeFromSearchParams, loadStatementViewMode, saveStatementViewMode } from '../statementViewMode'
import type { StatementViewMode } from '../statementTypes'
import {
  STATEMENT_TOOLBAR_BTN_STYLE,
  STATEMENT_TOOLBAR_ICON_BTN,
} from '../statementToolbarButton'
import StatementViewToggleButton from '../StatementViewToggleButton'
import CfDetailOverlay from './CfDetailOverlay'
import CfReportView from './CfReportView'
import type { CfNarrativeBullet } from './cfNarrativeEngine'
import { labelActual } from '../../../../lib/periodColumnLabels'

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

export default function CfStatementSection({
  data,
  monthly,
  loading,
  error,
  year,
  month,
  entity,
  entityLabel = 'all',
  entityDisplayName,
  periodSelection,
  onDrill,
}: Props) {
  const cfg = getStatementConfig('cf')
  const groupLabel =
    entityDisplayName ?? (entityLabel === 'all' ? 'All entities (consolidated)' : entityLabel)
  const [viewMode, setViewMode] = useState<StatementViewMode>(() => loadStatementViewMode('cf'))
  const [detailBullet, setDetailBullet] = useState<CfNarrativeBullet | null>(null)
  const [narrative, setNarrative] = useState<PlNarrativeResponse | null>(null)
  const hasPlanData = data?.plan?.has_plan_data ?? false
  const defaultColumns = usePlTableColumns(data, monthly, hasPlanData)
  const [columns, setColumns] = useState<PlTableColumnDef[]>([])
  const planMap = useMemo(() => buildPlanMapFromStatement(data), [data])
  const notesCtx = useOptionalActionNotesContext()
  const { checkOpen } = usePlRowExpansion(data?.rows, data?.statement ?? 'cf')

  useEffect(() => {
    if (!defaultColumns.length) return
    setColumns(prev => {
      if (!prev.length) return defaultColumns
      if (!data) return prev
      const periodGrain = data.period_grain === 'week' ? 'week' : 'month'
      const periods = periodGrain === 'week' ? [] : (monthly?.periods ?? [])
      const catalog = buildColumnCatalog(data.col_labels, periods, periodGrain)
      const reconciled = reconcileColumns(prev, catalog, periods)
      return hasPlanData ? reconciled : reconciled.filter(c => !PLAN_ONLY_COL_KINDS.has(c.kind))
    })
  }, [defaultColumns, data, monthly, hasPlanData])

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
    const grain = data?.period_grain === 'week' ? 'week' : 'month'
    const kinds = grain === 'week'
      ? (hasPlanData ? ['pm', 'cm', 'mom', 'mtd', 'plan_cm', 'plan_vs_actual'] : ['pm', 'cm', 'mom', 'mtd'])
      : (hasPlanData ? ['pm', 'cm', 'mom', 'plan_cm', 'plan_vs_actual'] : ['pm', 'cm', 'mom'])
    return allDataColumns.filter(c => kinds.includes(c.kind))
  }, [allDataColumns, data?.period_grain, hasPlanData])

  const displayColumns = columns.length > 0 ? columns : defaultColumns

  useEffect(() => {
    applyViewModeFromSearchParams(setViewMode)
  }, [])

  useEffect(() => {
    saveStatementViewMode('cf', viewMode)
  }, [viewMode])

  useEffect(() => {
    if (!notesCtx || !data) {
      if (notesCtx) notesCtx.unregisterTableCandidate('cf-statement')
      return
    }
    const cols = viewMode === 'table' ? displayColumns : miniColumns
    const component = viewMode === 'table' ? 'CfTableView' : 'CfReportView'
    notesCtx.registerTableCandidate({
      id: 'cf-statement',
      label: cfg.pinTableLabel,
      capture: () => buildPlTablePinSnapshot(data, checkOpen, cols, component),
      viewState: { view_mode: viewMode, tab: 'cf' },
    })
    return () => notesCtx.unregisterTableCandidate('cf-statement')
  }, [notesCtx, data, viewMode, displayColumns, miniColumns, checkOpen, cfg.pinTableLabel])

  const clientNarrative = useMemo(
    () => (data ? buildClientCfNarrative(data, year, month, 5) : null),
    [data, year, month],
  )

  const exportBullets = useStatementExportBullets(data, narrative, clientNarrative)

  const commentMarkersByLineCode = useMemo(() => {
    if (!data?.rows.length) return {}
    return buildReportCommentMarkerMap(exportBullets, data.rows, checkOpen)
  }, [exportBullets, data?.rows, checkOpen])

  const exportCtx = useMemo(
    (): PlExportContext => ({ entityLabel, entityDisplayName: groupLabel }),
    [entityLabel, groupLabel],
  )

  const exportFormats = useMemo(
    (): PlExportKind[] => (viewMode === 'report' ? ['pdf', 'pptx', 'xlsx'] : ['pdf', 'pptx', 'xlsx']),
    [viewMode],
  )

  const handleExport = useCallback(
    async (kind: PlExportKind) => {
      if (!data) return
      if (kind === 'pdf') {
        if (viewMode === 'report') {
          await exportStatementReportViewPdf(
            'cf',
            data,
            year,
            month,
            miniColumns,
            exportBullets,
            exportCtx,
            narrative,
            checkOpen,
            commentMarkersByLineCode,
          )
        } else {
          await exportStatementTableViewPdf(
            'cf',
            data,
            year,
            month,
            displayColumns,
            monthly,
            exportCtx,
            checkOpen,
          )
        }
        return
      }
      if (kind === 'pptx') {
        if (viewMode === 'report') {
          await exportStatementReportViewPptx(
            'cf',
            data,
            year,
            month,
            miniColumns,
            exportBullets,
            exportCtx,
            narrative,
            commentMarkersByLineCode,
            checkOpen,
          )
        } else {
          await exportStatementTableViewPptx(
            'cf',
            data,
            year,
            month,
            displayColumns,
            monthly,
            exportCtx,
            checkOpen,
          )
        }
        return
      }
      const xlsxColumns = viewMode === 'table' ? displayColumns : allDataColumns
      await exportPlTableView(
        data,
        xlsxColumns,
        planMap,
        monthly,
        entityLabel,
        checkOpen,
        `${cfg.cardTitle} — Table View`,
      )
    },
    [
      data,
      viewMode,
      year,
      month,
      miniColumns,
      exportBullets,
      exportCtx,
      narrative,
      commentMarkersByLineCode,
      displayColumns,
      allDataColumns,
      planMap,
      monthly,
      entityLabel,
      checkOpen,
      cfg.cardTitle,
    ],
  )

  if (loading && !data) {
    return (
      <div className="rounded-xl p-8 text-center text-sm" style={{ background: '#FFF', border: '1px solid #E2E8F0', color: '#64748B' }}>
        Loading…
      </div>
    )
  }

  if (error && !data) {
    return (
      <div className="rounded-xl p-8 text-center text-sm" style={{ background: '#FFF', border: '1px solid #FECACA', color: '#B91C1C' }}>
        {error}
      </div>
    )
  }

  if (!data?.rows.length) {
    return (
      <div className="rounded-xl p-8 text-center text-sm" style={{ background: '#FFF', border: '1px solid #E2E8F0', color: '#64748B' }}>
        No rows for this period / entity.
      </div>
    )
  }

  const periodBadge = data.col_labels?.cm ? labelActual(data.col_labels.cm) : ''

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
                {cfg.cardTitle}
              </span>
              {periodBadge ? (
                <span
                  className="text-xs font-medium px-2 py-0.5 rounded-md"
                  style={{ background: 'rgba(30,58,95,0.08)', color: '#1E3A5F', border: '1px solid rgba(30,58,95,0.15)' }}
                >
                  {periodBadge}
                </span>
              ) : null}
            </div>
            <p className="text-xs" style={{ color: '#94A3B8' }}>
              {viewMode === 'report' ? cfg.reportSubtitle : cfg.tableSubtitle}
            </p>
          </div>
          <div className="flex items-center gap-2 shrink-0">
            <StatementViewToggleButton mode={viewMode} onChange={setViewMode} disabled={loading} />
            {notesCtx && (
              <button
                type="button"
                title="Pin current table to Action Notes"
                className={STATEMENT_TOOLBAR_ICON_BTN}
                style={STATEMENT_TOOLBAR_BTN_STYLE}
                onClick={() => {
                  const snap = notesCtx.pinTable(cfg.pinTableLabel)
                  if (snap) notesCtx.setToast('Open Action Notes to save — or use Pin table in panel')
                  else notesCtx.setToast('No table data to pin')
                }}
              >
                <Pin size={14} strokeWidth={1.75} />
              </button>
            )}
            <PlExportMenu formats={exportFormats} onExport={handleExport} disabled={!data || loading} />
          </div>
        </div>

        {viewMode === 'report' ? (
          <CfReportView
            data={data}
            year={year}
            month={month}
            entity={entity}
            periodSelection={periodSelection}
            entityDisplayName={entityLabel === 'all' ? undefined : groupLabel}
            hasPlanData={hasPlanData}
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
            columns={displayColumns}
            onDrill={onDrill}
          />
        )}
      </div>

      {detailBullet && (
        <CfDetailOverlay
          bullet={detailBullet}
          year={year}
          month={month}
          entity={entity}
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
