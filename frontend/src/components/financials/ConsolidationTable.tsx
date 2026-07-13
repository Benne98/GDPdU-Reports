import { useEffect, useMemo, useState } from 'react'
import { ChevronRight, Pin } from 'lucide-react'
import { ConsolidationRow, ConsolidationResponse } from '../../lib/api'
import { fmtKpi, fmtPct, fmtDays } from '../../lib/fmt'
import { FIN_TABLE_CELL_CLASS, FIN_TABLE_VALUE_FONT } from './finReportLayout'
import PlExportMenu, { type PlExportKind } from './pl-two-view/PlExportMenu'
import { PL_TOOLBAR_ICON_BTN, PL_TOOLBAR_BTN_STYLE } from './statement-two-view/statementToolbarButton'
import { exportConsolidationPptx, exportConsolidationXlsx } from './statement-two-view/consolidation/consolidationExport'
import { computeAutoExpandedIds } from './statementRowExpansion'
import { useOptionalActionNotesContext } from '../action-notes/ActionNotesContext'
import { captureConsolidationSnapshot } from '../action-notes/captureConsolidationTable'

const STATEMENT_TITLES: Record<string, string> = {
  pl: 'Income statement (consolidated)',
  bs: 'Balance sheet (consolidated)',
  cf: 'Cash flow statement (consolidated)',
  wc: 'Working capital (consolidated)',
}

// ─── Number cell ──────────────────────────────────────────────────────────────

function NumCell({
  value,
  bold = false,
  highlighted = false,
  muted = false,
  isKpi = false,
  isDays = false,
}: {
  value: number
  bold?: boolean
  highlighted?: boolean
  muted?: boolean
  isKpi?: boolean
  isDays?: boolean
}) {
  return (
    <td
      className={`${FIN_TABLE_CELL_CLASS} text-right whitespace-nowrap tabular-nums`}
      style={{
        background: highlighted ? 'rgba(30,58,95,0.04)' : undefined,
        fontWeight: bold ? 600 : 400,
        fontSize: FIN_TABLE_VALUE_FONT,
        color: muted ? '#94A3B8' : isKpi ? '#475569' : '#111827',
        fontStyle: isKpi ? 'italic' : undefined,
      }}
    >
      {isKpi ? (isDays ? fmtDays(value) : fmtPct(value)) : fmtKpi(value)}
    </td>
  )
}

// ─── Component ───────────────────────────────────────────────────────────────

interface ConsolidationTableProps {
  data:    ConsolidationResponse | null
  loading: boolean
  error?:  string | null
  /** When true, render only the table (parent supplies card header / toolbar). */
  embedded?: boolean
  /** Parent export menu can read current expand/collapse state. */
  onRegisterCheckOpen?: (checkOpen: (id: string) => boolean) => void
}

export default function ConsolidationTable({
  data,
  loading,
  error,
  embedded,
  onRegisterCheckOpen,
}: ConsolidationTableProps) {
  const [userToggles, setUserToggles] = useState<Set<string>>(() => new Set())
  const notesCtx = useOptionalActionNotesContext()

  const autoExpandedIds = useMemo(
    () => computeAutoExpandedIds(data?.rows, data?.statement),
    [data?.rows, data?.statement],
  )

  function checkOpen(id: string): boolean {
    return autoExpandedIds.has(id) !== userToggles.has(id)
  }

  useEffect(() => {
    onRegisterCheckOpen?.(checkOpen)
  }, [checkOpen, onRegisterCheckOpen, userToggles, autoExpandedIds])

  // ─── Pin registration (non-embedded only) ─────────────────────────────────
  useEffect(() => {
    if (embedded) return
    const stmt = data?.statement ?? 'pl'
    const pinId = `${stmt}-legacy-consol`
    if (!notesCtx || !data?.rows?.length) {
      notesCtx?.unregisterTableCandidate(pinId)
      return
    }
    notesCtx.registerTableCandidate({
      id: pinId,
      label: `${STATEMENT_TITLES[stmt] ?? 'Consolidation'} — entity breakdown`,
      description: 'Entity columns with aggregation and consolidation — values in EURk',
      capture: () => captureConsolidationSnapshot(data, 'ConsolidationTable'),
      viewState: { tab: stmt },
    })
    return () => notesCtx.unregisterTableCandidate(pinId)
  }, [notesCtx, data, embedded])

  function toggle(id: string) {
    setUserToggles(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  async function handleExport(kind: PlExportKind) {
    if (!data) return
    if (kind === 'pdf') {
      // No dedicated PDF exporter for ConsolidationResponse yet — fall back to browser print.
      window.print()
      return
    }
    const footer = `${data.col_label ?? ''}A · Entity breakdown`
    if (kind === 'pptx') {
      await exportConsolidationPptx(data, footer, checkOpen)
      return
    }
    await exportConsolidationXlsx(data, checkOpen)
  }

  if (loading) {
    const box = (
      <div className="p-8 text-center text-sm" style={{ color: '#64748B' }}>
        Loading consolidation…
      </div>
    )
    return embedded ? box : (
      <div className="rounded-xl mt-4" style={{ background: '#FFF', border: '1px solid #E2E8F0' }}>
        {box}
      </div>
    )
  }
  if (error) {
    const box = (
      <div className="p-8 text-center text-sm" style={{ color: '#B91C1C' }}>
        {error}
      </div>
    )
    return embedded ? box : (
      <div className="rounded-xl mt-4" style={{ background: '#FFF', border: '1px solid #FECACA' }}>
        {box}
      </div>
    )
  }
  if (!data?.rows.length) return null

  const entities = data.entities
  const colLabel = data.col_label
  const tableTitle = STATEMENT_TITLES[data.statement] ?? 'Financial statement'
  const totalCols = 1 + entities.length + 3  // label + entities + Aggregated + IC Elim + Consolidation

  // ─── Row renderer ───────────────────────────────────────────────────────────

  function renderRow(row: ConsolidationRow, depth: number): JSX.Element {
    const pad = 12 + depth * 14
    const isTitle = row.row_kind === 'title'
    const isKpiHeader = row.row_kind === 'kpi_header'
    const isKpi = row.row_kind === 'kpi'
    const isOpen = checkOpen(row.id)
    const showChevron = (row.children?.length ?? 0) > 0

    if (isTitle) {
      return (
        <tr key={row.id} style={{ background: '#F8FAFC', borderTop: '1px solid #E2E8F0' }}>
          <td
            colSpan={totalCols}
            className="px-3 py-2 text-xs font-bold uppercase tracking-wide"
            style={{ color: '#1E3A5F' }}
          >
            {row.label}
          </td>
        </tr>
      )
    }

    if (isKpiHeader) {
      return (
        <tr key={row.id} style={{ background: '#F8FAFC', borderTop: '2px solid #E2E8F0' }}>
          <td className="px-3 py-2 text-xs font-semibold" style={{ color: '#1E3A5F', fontStyle: 'italic' }}>
            {row.label}
          </td>
          {entities.map(e => <td key={e.code} style={{ background: '#F8FAFC' }} />)}
          <td style={{ background: 'rgba(30,58,95,0.04)' }} />
          <td style={{ background: '#F8FAFC' }} />
          <td style={{ background: 'rgba(30,58,95,0.04)' }} />
        </tr>
      )
    }

    const isSubtotal = row.row_kind === 'subtotal'

    return (
      <tr
        key={row.id}
        style={{
          borderBottom: isKpi ? 'none' : '1px solid #E2E8F0',
          borderTop: isSubtotal && depth === 0 ? '2px solid #E2E8F0' : undefined,
          background: isKpi ? '#F8FAFC' : isSubtotal && depth === 0 ? '#F8FAFC' : undefined,
          fontStyle: isKpi ? 'italic' : undefined,
        }}
      >
        {/* Label cell */}
        <td
          className="py-2 text-left whitespace-nowrap"
          style={{ minWidth: 200, paddingLeft: pad, paddingRight: 12 }}
        >
          <div className="flex items-center gap-0.5">
            {showChevron ? (
              <button
                type="button"
                onClick={() => toggle(row.id)}
                className="p-0.5 rounded shrink-0"
                style={{ color: '#1E3A5F' }}
                aria-expanded={isOpen}
              >
                <ChevronRight
                  size={14}
                  style={{ transform: isOpen ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s' }}
                />
              </button>
            ) : (
              <span style={{ width: 22 }} />
            )}
            <span
              className="text-xs"
              style={{
                fontWeight: row.is_bold || isSubtotal ? 600 : 500,
                color: isKpi ? '#64748B' : row.row_kind === 'account' ? '#475569' : '#111827',
                fontStyle: isKpi ? 'italic' : undefined,
              }}
            >
              {row.label}
            </span>
          </div>
        </td>

        {/* Per-entity amounts — entity KPI values for KPI rows */}
        {entities.map(e => isKpi
          ? <td key={e.code} className="px-2.5 py-2 text-right text-xs tabular-nums" style={{ fontStyle: 'italic', color: '#475569' }}>
              {row.entity_amounts[e.code] != null && row.entity_amounts[e.code] !== 0
                ? (data?.statement === 'wc' ? fmtDays(row.entity_amounts[e.code] ?? 0) : fmtPct(row.entity_amounts[e.code] ?? 0))
                : <span style={{ color: '#CBD5E1' }}>—</span>}
            </td>
          : <NumCell key={e.code} value={row.entity_amounts[e.code] ?? 0} bold={row.is_bold} />
        )}

        {/* Aggregated — empty with highlighted background for KPI rows */}
        {isKpi
          ? <td style={{ background: 'rgba(30,58,95,0.04)' }} />
          : <NumCell value={row.aggregated} bold={row.is_bold} highlighted />}

        {/* IC Eliminations — empty for KPI rows */}
        {isKpi
          ? <td style={{ background: '#F8FAFC' }} />
          : <NumCell value={row.ic_eliminations} muted />}

        {/* Consolidation — always show (KPIs show the consolidated value here) */}
        <NumCell value={row.consolidation} bold={!isKpi && (row.is_bold ?? false)} highlighted isKpi={isKpi} isDays={isKpi && data?.statement === 'wc'} />
      </tr>
    )
  }

  function walkRows(rows: ConsolidationRow[], depth: number): JSX.Element[] {
    const nodes: JSX.Element[] = []
    for (const row of rows) {
      nodes.push(renderRow(row, depth))
      if (!checkOpen(row.id)) continue
      if (row.children?.length) {
        for (const ch of row.children) nodes.push(...walkRows([ch], depth + 1))
      }
    }
    return nodes
  }

  const tableEl = (
    <table className="w-full border-collapse text-xs">
        <thead>
          <tr style={{ borderBottom: '2px solid #E2E8F0', background: '#F8FAFC' }}>
            <th className="px-3 py-2.5 text-left font-semibold" style={{ color: '#475569' }}>
              EURk
            </th>
            {entities.map(e => (
              <th
                key={e.code}
                className="px-2.5 py-2.5 text-right font-semibold whitespace-nowrap"
                style={{ color: '#475569' }}
                title={e.label}
              >
                {e.label}
              </th>
            ))}
            <th className="px-2.5 py-2.5 text-right font-semibold whitespace-nowrap"
              style={{ color: '#1E3A5F', background: 'rgba(30,58,95,0.04)' }}>
              Aggregated
            </th>
            <th className="px-2.5 py-2.5 text-right font-semibold whitespace-nowrap"
              style={{ color: '#94A3B8' }}>
              IC Elim.
            </th>
            <th className="px-2.5 py-2.5 text-right font-semibold whitespace-nowrap"
              style={{ color: '#1E3A5F', background: 'rgba(30,58,95,0.04)' }}>
              Consolidation
            </th>
          </tr>
        </thead>
        <tbody>{walkRows(data.rows, 0)}</tbody>
      </table>
  )

  if (embedded) {
    return <div className="overflow-x-auto px-4 pb-4">{tableEl}</div>
  }

  return (
    <div
      className="rounded-xl overflow-x-auto mt-4"
      style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.05)' }}
    >
      <div className="px-4 pt-4 pb-3 flex items-start justify-between gap-3" style={{ borderBottom: '1px solid #F1F5F9' }}>
        <div>
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-sm font-semibold" style={{ color: '#111827' }}>
              {tableTitle} — entity breakdown
            </span>
            {colLabel && (
              <span
                className="text-xs font-medium px-2 py-0.5 rounded-md"
                style={{ background: 'rgba(30,58,95,0.08)', color: '#1E3A5F', border: '1px solid rgba(30,58,95,0.15)' }}
              >
                {colLabel}A
              </span>
            )}
          </div>
          <p className="text-xs" style={{ color: '#94A3B8' }}>
            Values in EURk — IC eliminations pending
          </p>
        </div>
        {notesCtx && data && (
          <button
            type="button"
            title="Pin to Action Board"
            className={PL_TOOLBAR_ICON_BTN}
            style={PL_TOOLBAR_BTN_STYLE}
            onClick={() => {
              const pinId = `${data.statement}-legacy-consol`
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
      {tableEl}
    </div>
  )
}
