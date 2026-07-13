/**
 * Annual entity-breakdown for snapshot statements (BS, WC): table, entity report, group report.
 */
import { useCallback, useEffect, useMemo, useRef, useState, type ReactNode } from 'react'
import { ChevronDown, ChevronUp, GripVertical, Pencil, Pin, X } from 'lucide-react'
import { PL_TOOLBAR_ICON_BTN, PL_TOOLBAR_BTN_STYLE } from '../statement-two-view/statementToolbarButton'
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
import type { ErSnapshotColLabels } from '../../../lib/api'
import {
  buildSnapshotCatalog,
  loadConsolExtraCols,
  makeConsolExtraColId,
  saveConsolExtraCols,
  type SnapshotConsolExtraCol,
  type SnapshotColId,
} from './snapshotColumnRegistry'
import type { SnapshotConsolTarget } from './snapshotConsolidationCellResolver'

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

// ─── Editor helpers ───────────────────────────────────────────────────────────

function EditorSection({ title, children, defaultOpen = true }: { title: string; children: ReactNode; defaultOpen?: boolean }) {
  const [open, setOpen] = useState(defaultOpen)
  return (
    <div className="border border-slate-200 rounded-lg overflow-hidden">
      <button
        type="button"
        onClick={() => setOpen(v => !v)}
        className="w-full flex items-center justify-between px-3 py-2 text-xs font-semibold text-slate-700 bg-slate-50 hover:bg-slate-100"
      >
        {title}
        <ChevronDown size={14} className={`transition-transform ${open ? 'rotate-180' : ''}`} />
      </button>
      {open && <div className="p-3 space-y-2">{children}</div>}
    </div>
  )
}

const CONSOL_TARGET_OPTIONS: Array<{ id: 'aggregated' | 'consolidation' | 'entity'; label: string }> = [
  { id: 'entity',        label: 'All entities' },
  { id: 'aggregated',    label: 'Aggregated' },
  { id: 'consolidation', label: 'Consolidation' },
]

const SNAPSHOT_PALETTE_GROUPS: Array<{ title: string; ids: SnapshotColId[] }> = [
  { title: 'Year-end balances', ids: ['dec_py2', 'fy_py', 'fy', 'cm_py'] },
  { title: 'Deltas & CAGR',     ids: ['delta_fy', 'delta_cm', 'cagr'] },
  { title: 'Current period',    ids: ['cm'] },
]

function targetDisplayLabel(target: SnapshotConsolTarget, entities: ConsolidationResponse['entities']): string {
  switch (target.kind) {
    case 'entity': {
      const ent = entities.find(e => e.code === target.code)
      return ent?.label ?? target.code
    }
    case 'aggregated':    return 'Aggregated'
    case 'consolidation': return 'Consolidation'
    case 'ic':            return 'IC Elim.'
  }
}

// ─── Props ────────────────────────────────────────────────────────────────────

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

  // Column editor state
  const [extraCols, setExtraCols] = useState<SnapshotConsolExtraCol[]>(() => loadConsolExtraCols(statement))
  const [editorOpen, setEditorOpen] = useState(false)
  const [consolDragIdx, setConsolDragIdx] = useState<number | null>(null)
  const [addTarget, setAddTarget] = useState<'aggregated' | 'consolidation' | 'entity'>('aggregated')

  const entities = consol?.entities ?? []
  const selected = entities[entityIndex]

  // Rebuild catalog labels when consol col_labels updates
  const catalog = useMemo(
    () => buildSnapshotCatalog(consol?.col_labels as ErSnapshotColLabels | undefined),
    [consol?.col_labels],
  )

  function persistExtra(cols: SnapshotConsolExtraCol[]) {
    setExtraCols(cols)
    saveConsolExtraCols(statement, cols)
  }

  function addExtraCol(snapshotColId: SnapshotColId) {
    const colDef = catalog[snapshotColId]
    if (!colDef) return

    if (addTarget === 'entity') {
      // Add one column per entity (skip any already present)
      const additions: SnapshotConsolExtraCol[] = []
      for (const e of entities) {
        const target: SnapshotConsolTarget = { kind: 'entity', code: e.code }
        const id = makeConsolExtraColId(target, snapshotColId)
        if (extraCols.some(c => c.id === id)) continue
        additions.push({ id, snapshotColId, target, labelLine1: colDef.labelLine1, labelLine2: e.label })
      }
      if (additions.length) persistExtra([...extraCols, ...additions])
      return
    }

    const target: SnapshotConsolTarget = addTarget === 'aggregated'
      ? { kind: 'aggregated' }
      : { kind: 'consolidation' }
    const id = makeConsolExtraColId(target, snapshotColId)
    if (extraCols.some(c => c.id === id)) return
    persistExtra([...extraCols, {
      id,
      snapshotColId,
      target,
      labelLine1: colDef.labelLine1,
      labelLine2: targetDisplayLabel(target, entities),
    }])
  }

  function removeExtraAt(idx: number) {
    persistExtra(extraCols.filter((_, i) => i !== idx))
  }

  function moveExtra(idx: number, dir: -1 | 1) {
    const j = idx + dir
    if (j < 0 || j >= extraCols.length) return
    const next = [...extraCols]
    const [item] = next.splice(idx, 1)
    next.splice(j, 0, item)
    persistExtra(next)
  }

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
      if (kind === 'pdf') {
        window.print()
        return
      }
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
          {/* Pencil only shown in table mode (snapshot period_grain check handled by parent) */}
          {viewMode === 'table' && (
            <button
              type="button"
              title="Add extra columns"
              onClick={() => setEditorOpen(true)}
              className={PL_TOOLBAR_ICON_BTN}
              style={PL_TOOLBAR_BTN_STYLE}
            >
              <Pencil size={14} strokeWidth={1.75} />
            </button>
          )}
          {notesCtx && consol && (
            <button
              type="button"
              title="Pin to Action Board"
              className={PL_TOOLBAR_ICON_BTN}
              style={PL_TOOLBAR_BTN_STYLE}
              onClick={() => {
                const pinId = `${statement}-annual-consolidation`
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
          {(statement === 'bs' || statement === 'wc') && consol.period_grain === 'year' ? (
            <AnnualSnapshotConsolidationTableView
              data={consol}
              year={year}
              month={month}
              extraColumns={extraCols}
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

      {/* Extra columns editor drawer */}
      {editorOpen && (
        <>
          <div className="fixed inset-0 z-40 bg-slate-900/20" onClick={() => setEditorOpen(false)} aria-hidden />
          <div
            className="fixed inset-y-0 right-0 z-50 w-full max-w-md shadow-2xl flex flex-col"
            style={{ background: '#fff', borderLeft: '1px solid #E2E8F0' }}
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100 shrink-0">
              <div>
                <p className="text-sm font-semibold text-slate-900">Table builder</p>
                <p className="text-[0.65rem] text-slate-500 mt-0.5">Add extra snapshot columns alongside the entity breakdown</p>
              </div>
              <button type="button" onClick={() => setEditorOpen(false)} className="p-1 rounded hover:bg-slate-100" aria-label="Close">
                <X size={18} />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {/* Target selector */}
              <section>
                <p className="text-xs font-semibold text-slate-700 mb-2">Apply column to</p>
                <div className="grid grid-cols-3 gap-1 p-0.5 rounded-lg bg-slate-100">
                  {CONSOL_TARGET_OPTIONS.map(opt => (
                    <button
                      key={opt.id}
                      type="button"
                      className={`text-[0.65rem] font-medium py-1.5 rounded-md ${
                        addTarget === opt.id ? 'bg-white shadow text-[#1E3A5F]' : 'text-slate-600'
                      }`}
                      onClick={() => setAddTarget(opt.id)}
                    >
                      {opt.label}
                    </button>
                  ))}
                </div>
              </section>

              {/* Current extra columns */}
              <section>
                <p className="text-xs font-semibold text-slate-700 mb-2">
                  Extra columns ({extraCols.length})
                </p>
                {extraCols.length === 0 ? (
                  <p className="text-xs text-slate-500">No extra columns — showing default layout only.</p>
                ) : (
                  <ul className="space-y-1">
                    {extraCols.map((c, idx) => (
                      <li
                        key={c.id}
                        draggable
                        onDragStart={() => setConsolDragIdx(idx)}
                        onDragOver={e => e.preventDefault()}
                        onDrop={() => {
                          if (consolDragIdx == null || consolDragIdx === idx) return
                          const next = [...extraCols]
                          const [item] = next.splice(consolDragIdx, 1)
                          next.splice(idx, 0, item)
                          setConsolDragIdx(null)
                          persistExtra(next)
                        }}
                        onDragEnd={() => setConsolDragIdx(null)}
                        className={`flex items-center gap-1 rounded-lg border px-2 py-1.5 bg-white ${
                          consolDragIdx === idx ? 'border-[#1E3A5F] ring-1 ring-[#1E3A5F]/20' : 'border-slate-200'
                        }`}
                      >
                        <GripVertical size={14} className="shrink-0 text-slate-400 cursor-grab" />
                        <div className="flex-1 min-w-0">
                          <p className="text-[0.65rem] text-slate-500 truncate">{c.labelLine2 ?? targetDisplayLabel(c.target, entities)}</p>
                          <p className="text-xs font-medium text-slate-800 truncate">{c.labelLine1}</p>
                        </div>
                        <div className="flex shrink-0">
                          <button type="button" onClick={() => moveExtra(idx, -1)} disabled={idx === 0} className="p-1 text-slate-500 disabled:opacity-30" aria-label="Move up">
                            <ChevronUp size={14} />
                          </button>
                          <button type="button" onClick={() => moveExtra(idx, 1)} disabled={idx === extraCols.length - 1} className="p-1 text-slate-500 disabled:opacity-30" aria-label="Move down">
                            <ChevronDown size={14} />
                          </button>
                          <button type="button" onClick={() => removeExtraAt(idx)} className="p-1 text-slate-500 hover:text-rose-600" aria-label="Remove">
                            <X size={14} />
                          </button>
                        </div>
                      </li>
                    ))}
                  </ul>
                )}
              </section>

              {/* Palette groups */}
              {SNAPSHOT_PALETTE_GROUPS.map(group => {
                const groupCols = group.ids.map(id => catalog[id]).filter(Boolean)
                if (!groupCols.length) return null
                return (
                  <EditorSection key={group.title} title={group.title}>
                    <div className="flex flex-wrap gap-1.5">
                      {groupCols.map(c => {
                        const wouldAdd: SnapshotConsolTarget[] =
                          addTarget === 'entity'
                            ? entities.map(e => ({ kind: 'entity' as const, code: e.code }))
                            : [addTarget === 'aggregated' ? { kind: 'aggregated' as const } : { kind: 'consolidation' as const }]
                        const allPresent = wouldAdd.length > 0 && wouldAdd.every(t =>
                          extraCols.some(ec => ec.id === makeConsolExtraColId(t, c.id)),
                        )
                        return (
                          <button
                            key={c.id}
                            type="button"
                            disabled={allPresent}
                            onClick={() => addExtraCol(c.id)}
                            className="px-2 py-1.5 rounded-md text-[0.7rem] text-left disabled:opacity-40 hover:bg-slate-50"
                            style={{ border: '1px solid #E2E8F0', color: '#475569' }}
                          >
                            {c.labelLine1}
                          </button>
                        )
                      })}
                    </div>
                  </EditorSection>
                )
              })}

              {/* Reset */}
              <button
                type="button"
                onClick={() => persistExtra([])}
                className="text-xs font-medium w-full py-2 rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50"
              >
                Reset — default columns only
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
