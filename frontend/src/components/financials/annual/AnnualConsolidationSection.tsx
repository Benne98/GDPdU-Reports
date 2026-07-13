/**
 * Annual entity-breakdown: table view, per-entity report (ErFlow), group consolidation report.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { Pin } from 'lucide-react'
import { PL_TOOLBAR_ICON_BTN, PL_TOOLBAR_BTN_STYLE } from '../statement-two-view/statementToolbarButton'
import type { ConsolidationResponse, ErFlowResponse, FinancialStatementResponse, FinancialStatementRow } from '../../../lib/api'
import { api } from '../../../lib/api'
import type { FinancialsDrillOpen } from '../FinancialStatementTable'
import PlExportMenu, { type PlExportKind } from '../pl-two-view/PlExportMenu'
import PlConsolidationColumnEditor from '../pl-two-view/PlConsolidationColumnEditor'
import PlConsolidationTableView from '../pl-two-view/PlConsolidationTableView'
import { exportConsolidationTableXlsx, exportConsolidationTablePptx } from '../pl-two-view/plConsolidationExport'
import {
  loadConsolidationColumns,
  type PlConsolidationColumnDef,
} from '../pl-two-view/plConsolidationColumnRegistry'
import { formatConsolidationPeriodLabel, labelActual } from '../../../lib/periodColumnLabels'
import { monthLabelShort } from '../../../lib/periodSelection'
import { useOptionalActionNotesContext } from '../../action-notes/ActionNotesContext'
import { captureConsolidationSnapshot } from '../../action-notes/captureConsolidationTable'
import PlEntityCarousel from '../pl-two-view/PlEntityCarousel'
import ErFlowReportView from './ErFlowReportView'
import { buildAnnualFlowNarrativeResponse } from './erAnnualNarrative'
import { buildAnnualFlowReportColumns } from './annualFlowReportColumns'
import type { PeriodSelection } from '../../../lib/periodSelection'
import { usePlRowExpansion } from '../pl-two-view/usePlRowExpansion'
import AnnualConsolidationReportView from './AnnualConsolidationReportView'
import AnnualConsolidationViewToggle, {
  type AnnualConsolViewMode,
} from './AnnualConsolidationViewToggle'

const VIEW_KEY = 'finssentials.annual.consol.viewMode.v2'
const ENTITY_IDX_KEY = 'finssentials.annual.consol.entityIndex.v1'

function loadViewMode(): AnnualConsolViewMode {
  try {
    const v = localStorage.getItem(VIEW_KEY)
    if (v === 'table' || v === 'entity' || v === 'group') return v
    return localStorage.getItem(VIEW_KEY) === 'table' ? 'table' : 'group'
  } catch {
    return 'group'
  }
}

function saveViewMode(m: AnnualConsolViewMode) {
  try {
    localStorage.setItem(VIEW_KEY, m)
  } catch {
    // ignore
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

interface Props {
  consol: ConsolidationResponse | null
  loading: boolean
  year: number
  month: number
  periodSelection: PeriodSelection
  onDrill: (d: FinancialsDrillOpen) => void
}

export default function AnnualConsolidationSection({
  consol,
  loading,
  year,
  month,
  periodSelection,
  onDrill,
}: Props) {
  const [viewMode, setViewMode] = useState<AnnualConsolViewMode>(loadViewMode)
  const [entityIndex, setEntityIndex] = useState(0)
  const [entityStmt, setEntityStmt] = useState<ErFlowResponse | null>(null)
  const [entityLoading, setEntityLoading] = useState(false)
  const [extraColumns, setExtraColumns] = useState<PlConsolidationColumnDef[]>(
    () => loadConsolidationColumns('pl-annual'),
  )
  const [stmtCache, setStmtCache] = useState<Map<string, FinancialStatementResponse>>(
    () => new Map(),
  )
  const [groupStatement, setGroupStatement] = useState<FinancialStatementResponse | null>(null)
  const checkOpenRef = useRef<((id: string) => boolean) | null>(null)
  const notesCtx = useOptionalActionNotesContext()

  const entities = consol?.entities ?? []
  const selected = entities[entityIndex]

  const entityExpansion = usePlRowExpansion(
    (entityStmt?.rows ?? []) as FinancialStatementRow[],
    'pl',
  )

  const colLabel = consol?.col_label ?? ''
  const ytdLabel = formatConsolidationPeriodLabel(
    colLabel || labelActual(`YTD${monthLabelShort(year, month)}`),
  )

  useEffect(() => {
    if (!entities.length) return
    setEntityIndex(loadEntityIndex(entities.length))
  }, [entities.length, year, month])

  useEffect(() => {
    localStorage.setItem(VIEW_KEY, viewMode)
  }, [viewMode])

  useEffect(() => {
    localStorage.setItem(ENTITY_IDX_KEY, String(entityIndex))
  }, [entityIndex])

  useEffect(() => {
    if (viewMode !== 'entity' || !selected?.code) {
      setEntityStmt(null)
      return
    }
    const code = selected.code
    let cancelled = false
    setEntityLoading(true)
    void api.exitReadinessPlStatement(year, month, code)
      .then(res => {
        if (!cancelled) setEntityStmt(res)
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
  }, [viewMode, selected?.code, year, month])

  // Reset the annual-statement cache when the period changes.
  useEffect(() => {
    setStmtCache(new Map())
    setGroupStatement(null)
  }, [year, month])

  // Fetch the annual group statement for aggregated/consolidation extra columns.
  useEffect(() => {
    if (viewMode !== 'table') return
    let cancelled = false
    void api.financialsPlStatementPeriod({ period_grain: 'year', year, month })
      .then(res => { if (!cancelled) setGroupStatement(res) })
      .catch(() => { if (!cancelled) setGroupStatement(null) })
    return () => { cancelled = true }
  }, [viewMode, year, month])

  // Fetch annual FinancialStatementResponse for each entity referenced in extraColumns.
  useEffect(() => {
    if (!consol?.entities.length || !extraColumns.length) return
    const needed = new Set<string>()
    for (const c of extraColumns) {
      if (c.entityCode) needed.add(c.entityCode)
    }
    for (const code of needed) {
      if (!stmtCache.has(code)) {
        void api.financialsPlStatementPeriod({ period_grain: 'year', year, month, entity: code })
          .then(res => setStmtCache(prev => new Map(prev).set(code, res)))
          .catch(() => { /* silently ignore — cell resolver will show "—" */ })
      }
    }
  // stmtCache intentionally excluded from deps: stale closure is fine as a
  // has() guard; including it would cause the effect to re-trigger on every
  // resolved fetch (matches PlConsolidationSection's pattern).
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [consol, extraColumns, year, month])

  const annualCfReportColumns = useMemo(
    () => buildAnnualFlowReportColumns(entityStmt?.col_labels, year, month, entityStmt?.has_plan_data ?? false),
    [entityStmt?.col_labels, year, month, entityStmt?.has_plan_data],
  )

  const fy3BaseLabel = entityStmt?.col_labels?.fy3 ?? labelActual(`FY${String(year - 1).slice(-2)}`)
  const fy2BaseLabel = entityStmt?.col_labels?.fy2 ?? labelActual(`FY${String(year - 2).slice(-2)}`)

  const annualEntityClientNarrative = useMemo(() => {
    if (!entityStmt?.rows?.length) return null
    return buildAnnualFlowNarrativeResponse(
      entityStmt.rows,
      year,
      month,
      fy3BaseLabel,
      fy2BaseLabel,
      selected?.code,
    )
  }, [entityStmt, year, month, fy3BaseLabel, fy2BaseLabel, selected?.code])

  // col_labels for the annual grain — needed by PlConsolidationColumnEditor catalog builder.
  const colLabels =
    groupStatement?.col_labels ??
    (stmtCache.size > 0 ? [...stmtCache.values()][0]?.col_labels : undefined)

  const handleViewChange = (m: AnnualConsolViewMode) => {
    setViewMode(m)
    saveViewMode(m)
  }

  const handleRegisterCheckOpen = useCallback((fn: (id: string) => boolean) => {
    checkOpenRef.current = fn
  }, [])

  const handleExport = useCallback(
    async (kind: PlExportKind) => {
      if (!consol) return
      const footerRight = `${ytdLabel} · Values in EURk`
      const checkOpen = checkOpenRef.current ?? (() => false)
      if (kind === 'pdf') {
        // No dedicated PDF exporter for annual consolidation yet — fall back to browser print.
        window.print()
        return
      }
      if (kind === 'xlsx') {
        await exportConsolidationTableXlsx(consol, extraColumns, stmtCache, groupStatement, {}, null, checkOpen)
      } else if (kind === 'pptx') {
        await exportConsolidationTablePptx(consol, extraColumns, stmtCache, groupStatement, null, footerRight, checkOpen)
      }
    },
    [consol, ytdLabel, extraColumns, stmtCache, groupStatement],
  )

  useEffect(() => {
    const stmt = consol?.statement ?? 'pl'
    const pinId = `${stmt}-annual-consolidation`
    if (!notesCtx || !consol?.rows?.length) {
      notesCtx?.unregisterTableCandidate(pinId)
      return
    }
    notesCtx.registerTableCandidate({
      id: pinId,
      label: `Income statement (${stmt.toUpperCase()}) — annual entity breakdown`,
      description: 'YTD per entity — values in EURk',
      capture: () => captureConsolidationSnapshot(consol, 'AnnualConsolidation'),
      viewState: { view_mode: viewMode, tab: stmt },
    })
    return () => notesCtx.unregisterTableCandidate(pinId)
  }, [notesCtx, consol, viewMode])

  if (loading) {
    return (
      <div
        className="rounded-xl mt-4 p-8 text-center text-sm"
        style={{ background: '#FFF', border: '1px solid #E2E8F0', color: '#64748B' }}
      >
        Loading consolidation…
      </div>
    )
  }

  if (!consol || !consol.rows.length) {
    return (
      <div
        className="rounded-xl mt-4 p-8 text-center text-sm"
        style={{ background: '#FFF', border: '1px solid #E2E8F0', color: '#94A3B8' }}
      >
        No consolidation data available.
      </div>
    )
  }

  const subtitle =
    viewMode === 'table'
      ? 'All entities, IC eliminations, and consolidation — values in EURk'
      : viewMode === 'entity'
        ? 'Annual report for one legal entity — same layout as consolidated statement'
        : 'Entity columns with group consolidation and cross-entity key drivers'

  return (
    <div
      className="rounded-xl mt-4 overflow-hidden"
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
            {ytdLabel && (
              <span
                className="text-xs font-medium px-2 py-0.5 rounded-md"
                style={{
                  background: 'rgba(30,58,95,0.08)',
                  color: '#1E3A5F',
                  border: '1px solid rgba(30,58,95,0.15)',
                }}
              >
                {ytdLabel}
              </span>
            )}
          </div>
          <p className="text-xs" style={{ color: '#94A3B8' }}>{subtitle}</p>
        </div>
        {viewMode === 'entity' && entities.length > 0 && (
          <PlEntityCarousel
            compact
            entities={entities}
            index={entityIndex}
            onChange={setEntityIndex}
            disabled={entityLoading}
          />
        )}
        <div className="flex flex-wrap items-center gap-2 shrink-0">
          <AnnualConsolidationViewToggle mode={viewMode} onChange={handleViewChange} disabled={loading} />
          {viewMode === 'table' && (
            <PlConsolidationColumnEditor
              consol={consol}
              colLabels={colLabels}
              monthly={null}
              columns={extraColumns}
              onChange={setExtraColumns}
              carouselEntityCode={selected?.code}
              statement="pl-annual"
            />
          )}
          {notesCtx && consol && (
            <button
              type="button"
              title="Pin to Action Board"
              className={PL_TOOLBAR_ICON_BTN}
              style={PL_TOOLBAR_BTN_STYLE}
              onClick={() => {
                const pinId = `${consol.statement ?? 'pl'}-annual-consolidation`
                const snap = notesCtx.pinTableById(pinId)
                if (snap) notesCtx.setToast('Open Action Notes to save — or use Pin table in panel')
                else notesCtx.setToast('No table data to pin')
              }}
            >
              <Pin size={14} strokeWidth={1.75} />
            </button>
          )}
          <PlExportMenu formats={['pdf', 'pptx', 'xlsx']} onExport={handleExport} disabled={!consol} />
        </div>
      </div>

      {viewMode === 'table' ? (
        <div className="px-6 pb-4 pt-2 overflow-x-auto">
          <PlConsolidationTableView
            data={consol}
            year={year}
            month={month}
            periodColumnLabel={ytdLabel}
            extraColumns={extraColumns}
            statementByEntity={stmtCache}
            groupStatement={groupStatement}
            monthly={null}
            onDrill={onDrill}
            onRegisterCheckOpen={handleRegisterCheckOpen}
          />
        </div>
      ) : viewMode === 'entity' ? (
        entityLoading && !entityStmt ? (
          <div className="p-8 text-center text-sm text-slate-500">Loading entity statement…</div>
        ) : entityStmt ? (
          <ErFlowReportView
            data={entityStmt}
            statement="pl"
            year={year}
            month={month}
            entity={selected?.code}
            periodSelection={periodSelection}
            entityDisplayName={selected?.label}
            reportColumns={annualCfReportColumns}
            clientNarrative={annualEntityClientNarrative}
            onDrill={d => onDrill({ ...d, entityOverride: selected?.code })}
            checkOpen={entityExpansion.checkOpen}
            toggle={entityExpansion.toggle}
            fy2Label={annualCfReportColumns[0]?.labelLine1 ?? fy2BaseLabel}
            fy3Label={annualCfReportColumns[1]?.labelLine1 ?? fy3BaseLabel}
          />
        ) : (
          <div className="p-8 text-center text-sm text-slate-500">No data for this entity.</div>
        )
      ) : (
        <AnnualConsolidationReportView
          consol={consol}
          year={year}
          month={month}
          ytdLabel={ytdLabel}
          periodSelection={periodSelection}
          onDrill={onDrill}
        />
      )}
    </div>
  )
}
