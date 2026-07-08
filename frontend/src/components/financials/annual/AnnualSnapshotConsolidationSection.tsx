/**
 * Annual entity-breakdown for snapshot statements (BS, WC): table, entity report, group report.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import type { ConsolidationResponse, ErSnapshotResponse, FinancialStatementRow } from '../../../lib/api'
import type { FinancialsDrillOpen } from '../FinancialStatementTable'
import PlExportMenu, { type PlExportKind } from '../pl-two-view/PlExportMenu'
import PlConsolidationTableView from '../pl-two-view/PlConsolidationTableView'
import AnnualSnapshotConsolidationTableView from './AnnualSnapshotConsolidationTableView'
import { exportConsolidationTableXlsx, exportConsolidationTablePptx } from '../pl-two-view/plConsolidationExport'
import { formatConsolidationPeriodLabel, labelActual } from '../../../lib/periodColumnLabels'
import { monthLabelShort } from '../../../lib/periodSelection'
import { useOptionalActionNotesContext } from '../../action-notes/ActionNotesContext'
import { captureConsolidationSnapshot } from '../../action-notes/captureConsolidationTable'
import PlEntityCarousel from '../pl-two-view/PlEntityCarousel'
import ErSnapshotReportView from './ErSnapshotReportView'
import { buildAnnualSnapshotNarrativeResponse } from './erAnnualNarrative'
import type { PeriodSelection } from '../../../lib/periodSelection'
import { usePlRowExpansion } from '../pl-two-view/usePlRowExpansion'
import AnnualConsolidationReportView from './AnnualConsolidationReportView'
import AnnualConsolidationViewToggle, {
  type AnnualConsolViewMode,
} from './AnnualConsolidationViewToggle'
import { fetchAnnualSnapshotForEntity } from '../statement-two-view/consolidation/statementConsolidationApi'

const VIEW_KEY_BS = 'finssentials.annual.bs.consol.viewMode.v1'
const VIEW_KEY_WC = 'finssentials.annual.wc.consol.viewMode.v1'
const ENTITY_IDX_KEY_BS = 'finssentials.annual.bs.consol.entityIndex.v1'
const ENTITY_IDX_KEY_WC = 'finssentials.annual.wc.consol.entityIndex.v1'

const TITLES: Record<'bs' | 'wc', string> = {
  bs: 'Balance sheet (consolidated) — entity breakdown',
  wc: 'Working capital (consolidated) — entity breakdown',
}

function viewKey(statement: 'bs' | 'wc') {
  return statement === 'bs' ? VIEW_KEY_BS : VIEW_KEY_WC
}

function entityIdxKey(statement: 'bs' | 'wc') {
  return statement === 'bs' ? ENTITY_IDX_KEY_BS : ENTITY_IDX_KEY_WC
}

function loadViewMode(statement: 'bs' | 'wc'): AnnualConsolViewMode {
  try {
    const v = localStorage.getItem(viewKey(statement))
    if (v === 'table' || v === 'entity' || v === 'group') return v
    return 'group'
  } catch {
    return 'group'
  }
}

function saveViewMode(statement: 'bs' | 'wc', m: AnnualConsolViewMode) {
  try {
    localStorage.setItem(viewKey(statement), m)
  } catch {
    // ignore
  }
}

function loadEntityIndex(statement: 'bs' | 'wc', max: number): number {
  try {
    const v = parseInt(localStorage.getItem(entityIdxKey(statement)) ?? '0', 10)
    if (Number.isNaN(v) || v < 0) return 0
    return max > 0 ? Math.min(v, max - 1) : 0
  } catch {
    return 0
  }
}

interface Props {
  statement: 'bs' | 'wc'
  consol: ConsolidationResponse | null
  loading: boolean
  year: number
  month: number
  periodSelection: PeriodSelection
  onDrill: (d: FinancialsDrillOpen) => void
}

export default function AnnualSnapshotConsolidationSection({
  statement,
  consol,
  loading,
  year,
  month,
  periodSelection,
  onDrill,
}: Props) {
  const [viewMode, setViewMode] = useState<AnnualConsolViewMode>(() => loadViewMode(statement))
  const [entityIndex, setEntityIndex] = useState(0)
  const [entityStmt, setEntityStmt] = useState<ErSnapshotResponse | null>(null)
  const [entityLoading, setEntityLoading] = useState(false)
  const checkOpenRef = useRef<((id: string) => boolean) | null>(null)
  const notesCtx = useOptionalActionNotesContext()

  const entities = consol?.entities ?? []
  const selected = entities[entityIndex]

  const entityExpansion = usePlRowExpansion(
    (entityStmt?.rows ?? []) as FinancialStatementRow[],
    statement,
  )

  const colLabel = consol?.col_label ?? ''
  const periodLabel = formatConsolidationPeriodLabel(
    colLabel || labelActual(monthLabelShort(year, month)),
  )

  useEffect(() => {
    if (!entities.length) return
    setEntityIndex(loadEntityIndex(statement, entities.length))
  }, [entities.length, year, month, statement])

  useEffect(() => {
    localStorage.setItem(viewKey(statement), viewMode)
  }, [viewMode, statement])

  useEffect(() => {
    localStorage.setItem(entityIdxKey(statement), String(entityIndex))
  }, [entityIndex, statement])

  useEffect(() => {
    if (viewMode !== 'entity' || !selected?.code) {
      setEntityStmt(null)
      return
    }
    const code = selected.code
    let cancelled = false
    setEntityLoading(true)
    void fetchAnnualSnapshotForEntity(statement, year, month, code)
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
  }, [viewMode, selected?.code, year, month, statement])

  const lbl = entityStmt?.col_labels

  const annualEntityClientNarrative = useMemo(() => {
    if (!entityStmt?.rows?.length || !lbl) return null
    return buildAnnualSnapshotNarrativeResponse(
      entityStmt.rows,
      lbl.fy ?? labelActual(`Dec${String(year - 1).slice(-2)}`),
      lbl.fy_py ?? labelActual(`Dec${String(year - 2).slice(-2)}`),
      lbl.cm ?? 'CM',
      selected?.code,
    )
  }, [entityStmt, lbl, year, selected?.code])

  const handleViewChange = (m: AnnualConsolViewMode) => {
    setViewMode(m)
    saveViewMode(statement, m)
  }

  const handleRegisterCheckOpen = useCallback((fn: (id: string) => boolean) => {
    checkOpenRef.current = fn
  }, [])

  const handleExport = useCallback(
    async (kind: PlExportKind) => {
      if (!consol) return
      const footerRight = `${periodLabel} · Values in EURk`
      const checkOpen = checkOpenRef.current ?? (() => false)
      if (kind === 'xlsx') {
        await exportConsolidationTableXlsx(consol, [], new Map(), null, {}, null, checkOpen)
      } else if (kind === 'pptx') {
        await exportConsolidationTablePptx(consol, [], new Map(), null, null, footerRight, checkOpen)
      }
    },
    [consol, periodLabel],
  )

  useEffect(() => {
    const pinId = `${statement}-annual-consolidation`
    if (!notesCtx || !consol?.rows?.length) {
      notesCtx?.unregisterTableCandidate(pinId)
      return
    }
    notesCtx.registerTableCandidate({
      id: pinId,
      label: `${TITLES[statement]} — annual entity breakdown`,
      description: 'Balance per entity — values in EURk',
      capture: () => captureConsolidationSnapshot(consol, 'AnnualSnapshotConsolidation'),
      viewState: { view_mode: viewMode, tab: statement },
    })
    return () => notesCtx.unregisterTableCandidate(pinId)
  }, [notesCtx, consol, viewMode, statement])

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
      className="rounded-xl mt-4"
      style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.05)' }}
    >
      <div
        className="px-4 pt-4 pb-3 flex flex-wrap items-center justify-between gap-x-4 gap-y-2"
        style={{ borderBottom: '1px solid #F1F5F9' }}
      >
        <div className="min-w-0 flex-1 basis-[min(200px,100%)]">
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-sm font-semibold" style={{ color: '#111827' }}>
              {TITLES[statement]}
            </span>
            {periodLabel && (
              <span
                className="text-xs font-medium px-2 py-0.5 rounded-md"
                style={{
                  background: 'rgba(30,58,95,0.08)',
                  color: '#1E3A5F',
                  border: '1px solid rgba(30,58,95,0.15)',
                }}
              >
                {periodLabel}
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
          <PlExportMenu formats={['pptx', 'xlsx']} onExport={handleExport} disabled={!consol} />
        </div>
      </div>

      {viewMode === 'table' ? (
        <div className="px-6 pb-4 pt-2 overflow-x-auto">
          {(statement === 'bs' || statement === 'wc') && consol.period_grain === 'year' ? (
            <AnnualSnapshotConsolidationTableView
              data={consol}
              year={year}
              month={month}
              onDrill={onDrill}
              onRegisterCheckOpen={handleRegisterCheckOpen}
            />
          ) : (
            <PlConsolidationTableView
              data={consol}
              year={year}
              month={month}
              periodColumnLabel={periodLabel}
              extraColumns={[]}
              statementByEntity={new Map()}
              groupStatement={null}
              monthly={null}
              onDrill={onDrill}
              onRegisterCheckOpen={handleRegisterCheckOpen}
            />
          )}
        </div>
      ) : viewMode === 'entity' ? (
        entityLoading && !entityStmt ? (
          <div className="p-8 text-center text-sm text-slate-500">Loading entity statement…</div>
        ) : entityStmt ? (
          <ErSnapshotReportView
            data={entityStmt}
            statement={statement}
            year={year}
            month={month}
            entity={selected?.code}
            periodSelection={periodSelection}
            entityDisplayName={selected?.label}
            clientNarrative={annualEntityClientNarrative}
            onDrill={d => onDrill({ ...d, entityOverride: selected?.code })}
            checkOpen={entityExpansion.checkOpen}
            toggle={entityExpansion.toggle}
            useClientNarrativeFallback={false}
          />
        ) : (
          <div className="p-8 text-center text-sm text-slate-500">No data for this entity.</div>
        )
      ) : (
        <AnnualConsolidationReportView
          consol={consol}
          year={year}
          month={month}
          ytdLabel={periodLabel}
          periodSelection={periodSelection}
          statement={statement}
          onDrill={onDrill}
        />
      )}
    </div>
  )
}
