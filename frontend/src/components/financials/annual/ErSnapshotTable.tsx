/**
 * ErSnapshotTable — Exit Readiness table for snapshot statements (BS, WC).
 *
 * Columns are registry-driven and user-configurable via the Table Builder pencil.
 * Default: Dec{yr-2} | Dec{yr-1} | Dec{yr} | CAGR | Δ FY | {month}{yr-1} | {month}{yr} | Δ CM
 */
import { useMemo, useState, useEffect, type ReactNode } from 'react'
import { ChevronDown, ChevronRight, ChevronUp, GripVertical, Pencil, Pin, X } from 'lucide-react'
import { ErSnapshotResponse, ErStatementRow, ErSnapshotColLabels } from '../../../lib/api'
import { FinancialsDrillOpen } from '../FinancialStatementTable'
import PlExportMenu, { type PlExportKind } from '../pl-two-view/PlExportMenu'
import PlViewToggleButton, { type PlViewMode } from '../pl-two-view/PlViewToggleButton'
import { useOptionalActionNotesContext } from '../../action-notes/ActionNotesContext'
import { captureErSnapshotSnapshot } from '../../action-notes/captureExitReadiness'
import { PL_TOOLBAR_ICON_BTN, PL_TOOLBAR_BTN_STYLE } from '../statement-two-view/statementToolbarButton'
import { exportFlatTablePptx } from '../../../lib/finssentialsExport/exportFlatTablePptx'
import { exportToXlsx, flattenTree, todayStr } from '../../../lib/exportXlsx'
import { getStatementConfig } from '../statement-two-view/statementConfig'
import {
  applyViewModeFromSearchParams,
  loadStatementViewMode,
  saveStatementViewMode,
} from '../statement-two-view/statementViewMode'
import type { PeriodSelection } from '../../../lib/periodSelection'
import { buildAnnualSnapshotNarrativeResponse } from './erAnnualNarrative'
import ErSnapshotReportView from './ErSnapshotReportView'
import { computeAutoExpandedIds } from '../statementRowExpansion'
import { DeltaCell, TwoLineHeader, ValCell } from '../pl-two-view/plTableCore'
import {
  buildSnapshotCatalog,
  DEFAULT_SNAPSHOT_COLUMN_IDS,
  loadSnapshotColumns,
  saveSnapshotColumns,
  type SnapshotColumnDef,
  type SnapshotColId,
} from './snapshotColumnRegistry'

// ─── Period ranges for drill-down ────────────────────────────────────────────

function lastDay(y: number, m: number): string {
  return new Date(y, m, 0).toISOString().slice(0, 10)
}

type SnapCol = 'dec_py2' | 'fy_py' | 'fy' | 'cm_py' | 'cm'

function periodRange(year: number, month: number, col: SnapCol): { from: string; to: string } {
  switch (col) {
    case 'dec_py2': return { from: `${year - 3}-01-01`, to: lastDay(year - 3, 12) }
    case 'fy_py': return { from: `${year - 2}-01-01`, to: lastDay(year - 2, 12) }
    case 'fy':    return { from: `${year - 1}-01-01`, to: lastDay(year - 1, 12) }
    case 'cm_py': return { from: `${year - 1}-01-01`, to: lastDay(year - 1, month) }
    case 'cm':    return { from: `${year}-01-01`,      to: lastDay(year, month) }
  }
}

// ─── Sub-components ───────────────────────────────────────────────────────────

function collectNumericRows(rows: ErStatementRow[]): ErStatementRow[] {
  const out: ErStatementRow[] = []
  function walk(r: ErStatementRow) {
    if (r.amounts && r.row_kind !== 'title') out.push(r)
    for (const c of r.children ?? []) walk(c)
    for (const a of r.accounts ?? []) walk(a)
  }
  for (const r of rows) walk(r)
  return out
}

/** Gross margin %, EBITDA margin %, Net profit margin % — bold label + values in KPI rows. */
const BOLD_MARGIN_KPI_CODES = new Set(['GROSS_MARGIN_PCT', 'EBITDA_MARGIN_PCT', 'NET_PROFIT_MARGIN_PCT'])

// ─── Editor helper ────────────────────────────────────────────────────────────

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

const SNAPSHOT_PALETTE_GROUPS: Array<{ title: string; ids: SnapshotColId[] }> = [
  { title: 'Year-end balances', ids: ['dec_py2', 'fy_py', 'fy', 'cm_py'] },
  { title: 'Deltas & CAGR',     ids: ['delta_fy', 'delta_cm', 'cagr'] },
  { title: 'Current period',    ids: ['cm'] },
]

// ─── Props ────────────────────────────────────────────────────────────────────

interface ErSnapshotTableProps {
  data:    ErSnapshotResponse | null
  loading: boolean
  error:   string | null
  year:    number
  month:   number
  entity?: string
  periodSelection?: PeriodSelection
  entityDisplayName?: string
  onDrill: (d: FinancialsDrillOpen) => void
  pinId?: string
  pinLabel?: string
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function ErSnapshotTable({
  data, loading, error, year, month, entity, periodSelection, entityDisplayName, onDrill, pinId, pinLabel,
}: ErSnapshotTableProps) {
  const statementKey = (data?.statement === 'wc' ? 'wc' : 'bs') as 'bs' | 'wc'
  const stmtCfg = getStatementConfig(statementKey)
  const [viewMode, setViewMode] = useState<PlViewMode>(() => loadStatementViewMode(statementKey) as PlViewMode)
  const [userToggles, setUserToggles] = useState<Set<string>>(() => new Set())
  const [columnIds, setColumnIds] = useState<SnapshotColId[]>(DEFAULT_SNAPSHOT_COLUMN_IDS)
  const [columnEditorOpen, setColumnEditorOpen] = useState(false)
  const [dragIdx, setDragIdx] = useState<number | null>(null)

  const lbl = data?.col_labels as ErSnapshotColLabels | undefined
  const isWc = data?.statement === 'wc'

  const catalog = useMemo(() => buildSnapshotCatalog(lbl), [lbl])

  // Hydrate from localStorage once we know the statement key
  useEffect(() => {
    const saved = loadSnapshotColumns(statementKey)
    if (saved) setColumnIds(saved)
    else setColumnIds(DEFAULT_SNAPSHOT_COLUMN_IDS)
  }, [statementKey])

  const columns = useMemo(
    () => columnIds.map(id => catalog[id]).filter((c): c is SnapshotColumnDef => Boolean(c)),
    [columnIds, catalog],
  )

  useEffect(() => {
    applyViewModeFromSearchParams(setViewMode)
  }, [])

  useEffect(() => {
    saveStatementViewMode(statementKey, viewMode)
  }, [statementKey, viewMode])

  const notesCtx = useOptionalActionNotesContext()

  useEffect(() => {
    if (!notesCtx || !pinId) return
    if (!data) {
      notesCtx.unregisterTableCandidate(pinId)
      return
    }
    notesCtx.registerTableCandidate({
      id: pinId,
      label: pinLabel ?? 'Annual statement',
      description: viewMode === 'report' ? 'Report view' : 'Table view',
      capture: () => captureErSnapshotSnapshot(data, 'ErSnapshotTable'),
      viewState: { tab: data.statement, view_mode: viewMode },
    })
    return () => notesCtx.unregisterTableCandidate(pinId)
  }, [notesCtx, data, pinId, pinLabel, viewMode])

  const autoExpandedIds = useMemo(
    () => computeAutoExpandedIds(data?.rows, data?.statement),
    [data?.rows, data?.statement],
  )

  function checkOpen(id: string): boolean {
    return autoExpandedIds.has(id) !== userToggles.has(id)
  }

  function toggle(id: string) {
    setUserToggles(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  const { maxDeltaFy, maxDeltaCm } = useMemo(() => {
    if (!data?.rows) return { maxDeltaFy: 1, maxDeltaCm: 1 }
    const nonKpi = collectNumericRows(data.rows).filter(r => r.row_kind !== 'kpi')
    const maxOf = (key: string) => Math.max(1, ...nonKpi.map(r => Math.abs((r.deltas as Record<string, number>)?.[key] ?? 0)))
    return { maxDeltaFy: maxOf('delta_fy'), maxDeltaCm: maxOf('delta_cm') }
  }, [data])

  const clientNarrative = useMemo(() => {
    if (!data?.rows || !lbl) return null
    return buildAnnualSnapshotNarrativeResponse(
      data.rows,
      lbl.fy ?? 'FY-1',
      lbl.fy_py ?? 'FY-2',
      lbl.cm ?? 'CM',
      entity,
    )
  }, [data?.rows, lbl, entity])

  function openDrill(row: ErStatementRow, col: SnapCol, colLabel: string) {
    if (!row.drill) return
    const { from, to } = periodRange(year, month, col)
    onDrill({
      dateFrom: from, dateTo: to,
      title: `${row.label} — ${colLabel}`,
      level2: row.drill.level_2 ?? undefined,
      level3: row.drill.level_3 ?? undefined,
      level4: row.drill.level_4 ?? undefined,
      glAccountId: row.drill.gl_account_id ?? undefined,
      statementType: row.drill.statement_type ?? undefined,
    })
  }

  function persist(ids: SnapshotColId[]) {
    setColumnIds(ids)
    saveSnapshotColumns(statementKey, ids)
  }

  // ─── Row rendering ───────────────────────────────────────────────────────────

  function renderRow(row: ErStatementRow, depth: number): JSX.Element {
    const isTitle    = row.row_kind === 'title'
    const isKpi      = row.row_kind === 'kpi'
    const isKpiHdr   = row.row_kind === 'kpi_header'
    const isSubtotal = row.row_kind === 'subtotal'
    const isAccount  = row.row_kind === 'account'
    const isMarginKpiBold = isKpi && !!row.line_code && BOLD_MARGIN_KPI_CODES.has(row.line_code)
    const pad = 12 + depth * 14
    const isOpen = checkOpen(row.id)
    const showChevron = (row.children?.length ?? 0) > 0 || (row.accounts?.length ?? 0) > 0

    if (isTitle || isKpiHdr) {
      return (
        <tr key={row.id} style={{ background: '#F8FAFC', borderTop: isKpiHdr ? '2px solid #E2E8F0' : '1px solid #E2E8F0' }}>
          <td
            colSpan={isKpiHdr ? undefined : columns.length + 1}
            className="px-3 py-1 text-[12px] font-bold uppercase tracking-wide"
            style={{
              color: '#1E3A5F',
              fontStyle: isKpiHdr ? 'italic' : undefined,
              textTransform: isKpiHdr ? 'none' : undefined,
              fontWeight: isKpiHdr ? 600 : undefined,
            }}
          >
            {row.label}
          </td>
          {isKpiHdr && columns.map(c => (
            <td key={c.id} style={{ background: (c.highlighted || c.kind === 'cagr') ? 'rgba(30,58,95,0.04)' : '#F8FAFC' }} />
          ))}
        </tr>
      )
    }

    const am  = row.amounts  ?? {}
    const d   = row.deltas   ?? {}
    const inv = row.invert_delta
    const get  = (k: string) => (am as Record<string, number>)[k] ?? 0
    const dget = (k: string) => (d  as Record<string, number>)[k] ?? 0
    const decPy2v = Number(am.dec_py2 ?? 0)
    const fyv     = Number(am.fy ?? 0)

    function cellFor(col: SnapshotColumnDef): JSX.Element {
      const key = `${row.id}-${col.id}`

      if (col.kind === 'cagr') {
        if (isKpi) return <td key={key} style={{ background: 'rgba(30,58,95,0.04)' }} />
        const cagrVal = Math.abs(decPy2v) > 1e-3
          ? (Math.pow(fyv / decPy2v, 1 / 2) - 1) * 100
          : null
        return (
          <td
            key={key}
            className="px-1.5 py-1 text-right whitespace-nowrap tabular-nums"
            style={{
              background: 'rgba(30,58,95,0.04)',
              fontSize: '0.8125rem',
              fontWeight: row.is_bold ? 600 : 400,
              color: cagrVal == null || cagrVal === 0 ? '#94A3B8' : cagrVal > 0 ? '#10B981' : '#DC2626',
              fontStyle: 'italic',
            }}
          >
            {cagrVal == null
              ? '—'
              : `${cagrVal >= 0 ? '+' : ''}${cagrVal.toLocaleString('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`}
          </td>
        )
      }

      if (col.kind === 'delta') {
        const dKey = col.id  // 'delta_fy' | 'delta_cm'
        const maxAbs = isKpi ? 1 : dKey === 'delta_fy' ? maxDeltaFy : maxDeltaCm
        const drillPeriod: SnapCol = dKey === 'delta_fy' ? 'fy' : 'cm'
        const drillLabel  = dKey === 'delta_fy'
          ? `${lbl?.fy} vs ${lbl?.fy_py}`
          : `${lbl?.cm} vs ${lbl?.cm_py}`
        return (
          <DeltaCell
            key={key}
            value={dget(dKey)}
            maxAbs={maxAbs}
            invert={isKpi ? false : inv}
            isDays={isKpi && isWc}
            isPct={isKpi && !isWc}
            compact
            onClick={!isKpi && row.drill ? () => openDrill(row, drillPeriod, drillLabel) : undefined}
          />
        )
      }

      // kind === 'amount': dec_py2 | fy_py | fy | cm_py | cm
      const snapDrillCol = col.id as SnapCol
      return (
        <ValCell
          key={key}
          value={get(col.id)}
          bold={!isKpi && !!row.is_bold}
          highlighted={col.highlighted}
          isPct={isKpi && !isWc}
          isDays={isKpi && isWc}
          italic={isKpi}
          compact
          onClick={!isKpi && row.drill ? () => openDrill(row, snapDrillCol, lbl?.[snapDrillCol] ?? '') : undefined}
        />
      )
    }

    return (
      <tr
        key={row.id}
        style={{
          borderBottom: isKpi ? 'none' : '1px solid #E2E8F0',
          borderTop: isSubtotal && depth === 0 ? '2px solid #E2E8F0' : undefined,
          background: isKpi ? '#F8FAFC' : isSubtotal && depth === 0 ? '#F8FAFC' : undefined,
        }}
      >
        <td className="py-1 text-left whitespace-nowrap" style={{ minWidth: 220, paddingLeft: pad, paddingRight: 12 }}>
          <div className="flex items-center gap-0.5">
            {showChevron ? (
              <button type="button" onClick={() => toggle(row.id)} className="p-0.5 rounded shrink-0" style={{ color: '#1E3A5F' }} aria-expanded={isOpen}>
                <ChevronRight size={14} style={{ transform: isOpen ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s' }} />
              </button>
            ) : <span style={{ width: 22 }} />}
            <span className="text-[13px]" style={{
              fontWeight: row.is_bold || isSubtotal || isMarginKpiBold ? 600 : 500,
              fontStyle: isKpi ? 'italic' : undefined,
              color: isKpi ? '#64748B' : isAccount ? '#475569' : '#111827',
            }}>
              {row.label}
            </span>
          </div>
        </td>
        {columns.map(col => cellFor(col))}
      </tr>
    )
  }

  function walkRows(rows: ErStatementRow[], depth: number): JSX.Element[] {
    const nodes: JSX.Element[] = []
    let kpiHeaderInserted = false
    for (const row of rows) {
      if (row.row_kind === 'kpi_header') {
        nodes.push(renderRow(row, depth))
        continue
      }
      if (row.row_kind === 'kpi' && !kpiHeaderInserted && (data?.statement === 'bs' || data?.statement === 'wc')) {
        kpiHeaderInserted = true
        const kpiLabel = data?.statement === 'wc' ? 'KPIs — working capital days' : 'KPIs'
        nodes.push(
          <tr key="er-kpi-header" style={{ background: '#F8FAFC', borderTop: '2px solid #E2E8F0' }}>
            <td className="px-3 py-1 text-[12px] font-semibold" style={{ color: '#1E3A5F', fontStyle: 'italic' }}>{kpiLabel}</td>
            {columns.map(c => (
              <td key={c.id} style={{ background: (c.highlighted || c.kind === 'cagr') ? 'rgba(30,58,95,0.04)' : '#F8FAFC' }} />
            ))}
          </tr>,
        )
      }
      nodes.push(renderRow(row, depth))
      if (row.row_kind === 'title') continue
      if (!checkOpen(row.id)) continue
      for (const ch of row.children ?? []) nodes.push(...walkRows([ch], depth + 1))
      for (const acc of row.accounts ?? []) nodes.push(...walkRows([acc], depth + 1))
    }
    return nodes
  }

  // ─── Export ──────────────────────────────────────────────────────────────────

  const STATEMENT_TITLES: Record<string, string> = { bs: 'Balance sheet', wc: 'Working capital' }

  async function handleExport(kind: PlExportKind) {
    if (!data) return
    if (kind === 'pdf') { window.print(); return }
    const stmtName = STATEMENT_TITLES[data.statement] ?? 'Statement'
    // CAGR is client-side; exclude from flattenTree keys but include as a header placeholder
    const amountCols = columns.filter(c => c.kind === 'amount')
    const deltaCols  = columns.filter(c => c.kind === 'delta')
    const rows = flattenTree(
      data.rows,
      amountCols.map(c => c.id),
      deltaCols.map(c => c.id),
      { isRowOpen: checkOpen },
    )
    const headers = ['EURk', ...columns.map(c => c.labelLine2 ? `${c.labelLine1} ${c.labelLine2}` : c.labelLine1)]
    const columnKinds = ['', ...columns.map(c =>
      c.kind === 'delta' ? 'delta' : c.highlighted ? 'cm' : c.kind === 'cagr' ? 'cagr' : '',
    )]
    const base = `ER_${stmtName.replace(/ /g, '_')}_${todayStr()}`
    if (kind === 'pptx') {
      await exportFlatTablePptx({
        fileName: `${base}.pptx`,
        pageTitle: stmtName,
        tableHeading: stmtName,
        breadcrumbCurrent: 'Annual view',
        footerRight: 'Values in EURk',
        headers,
        columnKinds,
        rows,
      })
      return
    }
    await exportToXlsx({
      title: `Annual — ${stmtName}`,
      subtitle: 'Values in EURk',
      headers,
      rows,
      filename: `${base}.xlsx`,
    })
  }

  // ─── Render guards ───────────────────────────────────────────────────────────

  if (error) return (
    <div className="rounded-xl p-8 text-center text-sm" style={{ background: '#FFF', border: '1px solid #FECACA', color: '#B91C1C' }}>{error}</div>
  )
  if (loading) return (
    <div className="rounded-xl p-8 text-center text-sm" style={{ background: '#FFF', border: '1px solid #E2E8F0', color: '#64748B' }}>Loading…</div>
  )
  if (!data?.rows.length) return (
    <div className="rounded-xl p-8 text-center text-sm" style={{ background: '#FFF', border: '1px solid #E2E8F0', color: '#64748B' }}>No rows</div>
  )

  const tableTitle = stmtCfg.cardTitle || (STATEMENT_TITLES[data.statement] ?? 'Statement')
  const periodBadge = lbl?.cm ?? ''

  // ─── JSX ─────────────────────────────────────────────────────────────────────

  return (
    <div className="rounded-xl" style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.05)' }}>
      {/* Toolbar */}
      <div className="px-4 pt-4 pb-3 flex items-start justify-between gap-3" style={{ borderBottom: '1px solid #F1F5F9' }}>
        <div>
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-sm font-semibold" style={{ color: '#111827' }}>{tableTitle}</span>
            {periodBadge && (
              <span className="text-xs font-medium px-2 py-0.5 rounded-md" style={{ background: 'rgba(30,58,95,0.08)', color: '#1E3A5F', border: '1px solid rgba(30,58,95,0.15)' }}>
                {periodBadge}
              </span>
            )}
          </div>
          <p className="text-xs" style={{ color: '#94A3B8' }}>
            {viewMode === 'report' ? stmtCfg.reportSubtitle : stmtCfg.tableSubtitle}
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <PlViewToggleButton mode={viewMode} onChange={setViewMode} disabled={loading} />
          {viewMode === 'table' && (
            <button
              type="button"
              title="Build table columns"
              onClick={() => setColumnEditorOpen(true)}
              className={PL_TOOLBAR_ICON_BTN}
              style={PL_TOOLBAR_BTN_STYLE}
            >
              <Pencil size={14} strokeWidth={1.75} />
            </button>
          )}
          {notesCtx && pinId && (
            <button
              type="button"
              title="Pin current table to Action Notes"
              className={PL_TOOLBAR_ICON_BTN}
              style={PL_TOOLBAR_BTN_STYLE}
              onClick={() => {
                const snap = notesCtx.pinTableById(pinId)
                if (snap) notesCtx.setToast('Open Action Notes to save — or use Pin table in panel')
                else notesCtx.setToast('No table data to pin')
              }}
            >
              <Pin size={14} strokeWidth={1.75} />
            </button>
          )}
          <PlExportMenu formats={['pdf', 'pptx', 'xlsx']} onExport={handleExport} disabled={!data} />
        </div>
      </div>

      {/* Content */}
      {viewMode === 'report' ? (
        <ErSnapshotReportView
          data={data}
          statement={statementKey}
          year={year}
          month={month}
          entity={entity}
          periodSelection={periodSelection}
          entityDisplayName={entityDisplayName}
          clientNarrative={clientNarrative}
          onDrill={onDrill}
          checkOpen={checkOpen}
          toggle={toggle}
          useClientNarrativeFallback={false}
        />
      ) : (
        <div className="overflow-x-auto px-6 py-4">
          <table className="w-full border-collapse text-[12px]">
            <thead>
              <tr style={{ borderBottom: '2px solid #E2E8F0', background: '#F8FAFC', verticalAlign: 'bottom' }}>
                <th className="px-3 py-2 text-left font-semibold text-[13px]" style={{ color: '#475569' }}>EURk</th>
                {columns.map(c => (
                  <TwoLineHeader
                    key={c.id}
                    line1={c.labelLine1}
                    line2={c.labelLine2}
                    highlighted={c.highlighted}
                  />
                ))}
              </tr>
            </thead>
            <tbody>{walkRows(data.rows, 0)}</tbody>
          </table>
        </div>
      )}

      {/* Column editor drawer */}
      {columnEditorOpen && viewMode === 'table' && (
        <>
          <div className="fixed inset-0 z-40 bg-slate-900/20" onClick={() => setColumnEditorOpen(false)} aria-hidden />
          <div
            className="fixed inset-y-0 right-0 z-50 w-full max-w-md shadow-2xl flex flex-col"
            style={{ background: '#fff', borderLeft: '1px solid #E2E8F0' }}
          >
            <div className="flex items-center justify-between px-4 py-3 border-b border-slate-100 shrink-0">
              <div>
                <p className="text-sm font-semibold text-slate-900">Table builder</p>
                <p className="text-[0.65rem] text-slate-500 mt-0.5">Add and reorder columns for Table View</p>
              </div>
              <button type="button" onClick={() => setColumnEditorOpen(false)} className="p-1 rounded hover:bg-slate-100" aria-label="Close">
                <X size={18} />
              </button>
            </div>

            <div className="flex-1 overflow-y-auto p-4 space-y-4">
              {/* Column order list */}
              <section>
                <p className="text-xs font-semibold text-slate-700 mb-2">Column order ({columns.length})</p>
                {columns.length === 0 ? (
                  <p className="text-xs text-slate-500">No columns selected.</p>
                ) : (
                  <ul className="space-y-1">
                    {columns.map((c, idx) => (
                      <li
                        key={c.id}
                        draggable
                        onDragStart={() => setDragIdx(idx)}
                        onDragOver={e => e.preventDefault()}
                        onDrop={() => {
                          if (dragIdx == null || dragIdx === idx) return
                          const next = [...columnIds]
                          const [item] = next.splice(dragIdx, 1)
                          next.splice(idx, 0, item)
                          setDragIdx(null)
                          persist(next)
                        }}
                        onDragEnd={() => setDragIdx(null)}
                        className={`flex items-center gap-1 rounded-lg border px-2 py-1.5 bg-white ${dragIdx === idx ? 'border-[#1E3A5F] ring-1 ring-[#1E3A5F]/20' : 'border-slate-200'}`}
                      >
                        <GripVertical size={14} className="shrink-0 text-slate-400 cursor-grab" />
                        <div className="flex-1 min-w-0">
                          <p className="text-xs font-medium text-slate-800 truncate">{c.labelLine1}</p>
                          {c.labelLine2 && <p className="text-[0.65rem] text-slate-500 truncate">{c.labelLine2}</p>}
                        </div>
                        <div className="flex shrink-0">
                          <button
                            type="button"
                            onClick={() => {
                              if (idx === 0) return
                              const next = [...columnIds]
                              const [item] = next.splice(idx, 1)
                              next.splice(idx - 1, 0, item)
                              persist(next)
                            }}
                            disabled={idx === 0}
                            className="p-1 text-slate-500 disabled:opacity-30"
                            aria-label="Move up"
                          >
                            <ChevronUp size={14} />
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              if (idx === columns.length - 1) return
                              const next = [...columnIds]
                              const [item] = next.splice(idx, 1)
                              next.splice(idx + 1, 0, item)
                              persist(next)
                            }}
                            disabled={idx === columns.length - 1}
                            className="p-1 text-slate-500 disabled:opacity-30"
                            aria-label="Move down"
                          >
                            <ChevronDown size={14} />
                          </button>
                          <button
                            type="button"
                            onClick={() => persist(columnIds.filter(id => id !== c.id))}
                            className="p-1 text-slate-500 hover:text-rose-600"
                            aria-label="Remove"
                          >
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
                      {groupCols.map(c => (
                        <button
                          key={c.id}
                          type="button"
                          disabled={columnIds.includes(c.id)}
                          onClick={() => persist([...columnIds, c.id])}
                          className="px-2 py-1.5 rounded-md text-[0.7rem] text-left disabled:opacity-40 hover:bg-slate-50"
                          style={{ border: '1px solid #E2E8F0', color: '#475569' }}
                        >
                          {c.labelLine1}
                        </button>
                      ))}
                    </div>
                  </EditorSection>
                )
              })}

              {/* Reset */}
              <button
                type="button"
                onClick={() => persist(DEFAULT_SNAPSHOT_COLUMN_IDS)}
                className="text-xs font-medium w-full py-2 rounded-lg border border-slate-200 text-slate-600 hover:bg-slate-50"
              >
                Reset to default layout
              </button>
            </div>
          </div>
        </>
      )}
    </div>
  )
}
