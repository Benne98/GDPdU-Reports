/**
 * ErSnapshotTable — Exit Readiness table for snapshot statements (BS, WC).
 *
 * Columns: Dec{yr-2} | Dec{yr-1} | ∆FY | {month}{yr-1} | {month}{yr} | ∆CM
 */
import { useMemo, useState, useEffect } from 'react'
import { ChevronRight, Pin } from 'lucide-react'
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
// ValCell, DeltaCell, DeltaBar, TwoLineHeader imported from plTableCore (compact mode)

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
  const statementKey = data?.statement === 'wc' ? 'wc' : 'bs'
  const stmtCfg = getStatementConfig(statementKey)
  const [viewMode, setViewMode] = useState<PlViewMode>(() => loadStatementViewMode(statementKey) as PlViewMode)
  const [userToggles, setUserToggles] = useState<Set<string>>(() => new Set())

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
    const maxOf = (key: string) => Math.max(1, ...nonKpi.map(r => Math.abs((r.deltas as any)?.[key] ?? 0)))
    return { maxDeltaFy: maxOf('delta_fy'), maxDeltaCm: maxOf('delta_cm') }
  }, [data])

  const lbl = data?.col_labels as ErSnapshotColLabels | undefined
  const isWc = data?.statement === 'wc'

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

  // 8 data columns (dec_py2 | fy_py | fy | CAGR | ∆FY | cm_py | cm | ∆CM)
  // CAGR at index 3 (0-based), cm at index 6
  const COLS = 8
  const CM_IDX = 6
  const CAGR_IDX = 3

  function renderRow(row: ErStatementRow, depth: number): JSX.Element {
    const isTitle       = row.row_kind === 'title'
    const isKpi         = row.row_kind === 'kpi'
    const isKpiHdr      = row.row_kind === 'kpi_header'
    const isSubtotal    = row.row_kind === 'subtotal'
    const isAccount     = row.row_kind === 'account'
    const isMarginKpiBold = isKpi && !!row.line_code && BOLD_MARGIN_KPI_CODES.has(row.line_code)
    const pad = 12 + depth * 14
    const isOpen = checkOpen(row.id)
    const showChevron = (row.children?.length ?? 0) > 0 || (row.accounts?.length ?? 0) > 0

    if (isTitle || isKpiHdr) {
      return (
        <tr key={row.id} style={{ background: '#F8FAFC', borderTop: isKpiHdr ? '2px solid #E2E8F0' : '1px solid #E2E8F0' }}>
          <td colSpan={isKpiHdr ? undefined : COLS + 1}
              className="px-3 py-1 text-[12px] font-bold uppercase tracking-wide"
              style={{ color: '#1E3A5F', fontStyle: isKpiHdr ? 'italic' : undefined, textTransform: isKpiHdr ? 'none' : undefined, fontWeight: isKpiHdr ? 600 : undefined }}>
            {row.label}
          </td>
          {isKpiHdr && Array.from({ length: COLS }).map((_, i) => (
            <td key={i} style={{ background: (i === CM_IDX || i === CAGR_IDX) ? 'rgba(30,58,95,0.04)' : '#F8FAFC' }} />
          ))}
        </tr>
      )
    }

    const am  = row.amounts  ?? {}
    const d   = row.deltas   ?? {}
    const inv = row.invert_delta

    const get  = (k: string) => (am as Record<string, number>)[k] ?? 0
    const dget = (k: string) => (d  as Record<string, number>)[k] ?? 0

    // CAGR: Dec-3 → Dec-1, 2-period compound growth
    const decPy2v = Number(am.dec_py2 ?? 0)
    const fyv     = Number(am.fy ?? 0)
    const cagrVal = !isKpi && Math.abs(decPy2v) > 1e-3
      ? (Math.pow(fyv / decPy2v, 1 / 2) - 1) * 100
      : null

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

        {isKpi ? (
          isWc ? (
            /* WC KPIs — days */
            <>
              <ValCell value={get('dec_py2')} isDays italic compact />
              <ValCell value={get('fy_py')} isDays italic compact />
              <ValCell value={get('fy')}    isDays italic compact />
              <td style={{ background: 'rgba(30,58,95,0.04)' }} />
              <DeltaCell value={dget('delta_fy')} maxAbs={1} invert={false} isDays compact />
              <ValCell value={get('cm_py')} isDays italic compact />
              <ValCell value={get('cm')}    isDays highlighted italic compact />
              <DeltaCell value={dget('delta_cm')} maxAbs={1} invert={false} isDays compact />
            </>
          ) : (
            /* BS KPIs — percentages */
            <>
              <ValCell value={get('dec_py2')} isPct italic compact />
              <ValCell value={get('fy_py')} isPct italic compact />
              <ValCell value={get('fy')}    isPct italic compact />
              <td style={{ background: 'rgba(30,58,95,0.04)' }} />
              <DeltaCell value={dget('delta_fy')} maxAbs={1} invert={false} isPct compact />
              <ValCell value={get('cm_py')} isPct italic compact />
              <ValCell value={get('cm')}    isPct highlighted italic compact />
              <DeltaCell value={dget('delta_cm')} maxAbs={1} invert={false} isPct compact />
            </>
          )
        ) : (
          <>
            <ValCell value={get('dec_py2')} bold={row.is_bold} compact onClick={row.drill ? () => openDrill(row, 'dec_py2', lbl?.dec_py2 ?? '') : undefined} />
            <ValCell value={get('fy_py')} bold={row.is_bold} compact onClick={row.drill ? () => openDrill(row, 'fy_py', lbl?.fy_py ?? '') : undefined} />
            <ValCell value={get('fy')}    bold={row.is_bold} compact onClick={row.drill ? () => openDrill(row, 'fy',    lbl?.fy    ?? '') : undefined} />
            {/* CAGR: (Dec-1 / Dec-3)^(1/2) − 1, client-side, blank for KPI rows */}
            <td
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
            <DeltaCell value={dget('delta_fy')} maxAbs={maxDeltaFy} invert={inv} compact
              onClick={row.drill ? () => openDrill(row, 'fy', `${lbl?.fy} vs ${lbl?.fy_py}`) : undefined} />
            <ValCell value={get('cm_py')} bold={row.is_bold} compact onClick={row.drill ? () => openDrill(row, 'cm_py', lbl?.cm_py ?? '') : undefined} />
            <ValCell value={get('cm')}    bold={row.is_bold} compact highlighted onClick={row.drill ? () => openDrill(row, 'cm', lbl?.cm ?? '') : undefined} />
            <DeltaCell value={dget('delta_cm')} maxAbs={maxDeltaCm} invert={inv} compact
              onClick={row.drill ? () => openDrill(row, 'cm', `${lbl?.cm} vs ${lbl?.cm_py}`) : undefined} />
          </>
        )}
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
            {Array.from({ length: COLS }).map((_, i) => (
              <td key={i} style={{ background: (i === CM_IDX || i === CAGR_IDX) ? 'rgba(30,58,95,0.04)' : '#F8FAFC' }} />
            ))}
          </tr>
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

  const STATEMENT_TITLES: Record<string, string> = {
    bs: 'Balance sheet',
    wc: 'Working capital',
  }

  async function handleExport(kind: PlExportKind) {
    if (!data) return
    if (kind === 'pdf') {
      // No dedicated PDF exporter for ErSnapshotResponse yet — fall back to browser print.
      window.print()
      return
    }
    const stmtName = STATEMENT_TITLES[data.statement] ?? 'Statement'
    const rows = flattenTree(
      data.rows,
      ['dec_py2', 'fy_py', 'fy', 'cm_py', 'cm'],
      ['delta_fy', 'delta_cm'],
      { isRowOpen: checkOpen },
    )
    const headers = [
      'EURk',
      lbl?.dec_py2 ?? 'Dec-3',
      lbl?.fy_py ?? 'Dec-2',
      lbl?.fy ?? 'Dec-1',
      'CAGR',
      lbl ? `Δ ${lbl.fy_py} − ${lbl.fy}` : 'Δ FY',
      lbl?.cm_py ?? 'CM PY',
      lbl?.cm ?? 'CM',
      lbl ? `Δ ${lbl.cm_py} − ${lbl.cm}` : 'Δ CM',
    ]
    const columnKinds = ['', '', '', '', 'cagr', 'delta', '', 'cm', 'delta']
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
  const hdrDeltaFy = lbl ? `Δ ${lbl.fy_py} − ${lbl.fy}` : 'Δ'
  const hdrDeltaCm = lbl ? `Δ ${lbl.cm_py} − ${lbl.cm}` : 'Δ'
  const periodBadge = lbl?.cm ?? ''

  return (
    <div className="rounded-xl" style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.05)' }}>
      <div className="px-4 pt-4 pb-3 flex items-start justify-between gap-3" style={{ borderBottom: '1px solid #F1F5F9' }}>
        <div>
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-sm font-semibold" style={{ color: '#111827' }}>{tableTitle}</span>
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
            {viewMode === 'report' ? stmtCfg.reportSubtitle : stmtCfg.tableSubtitle}
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <PlViewToggleButton mode={viewMode} onChange={setViewMode} disabled={loading} />
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
                <TwoLineHeader line1={lbl?.dec_py2 ?? ''} line2="FY end" />
                <TwoLineHeader line1={lbl?.fy_py ?? ''} line2="FY end" />
                <TwoLineHeader line1={lbl?.fy ?? ''} line2="FY end" highlighted />
                <TwoLineHeader line1="CAGR" line2={`${lbl?.dec_py2 ?? ''} – ${lbl?.fy ?? ''}`} />
                <TwoLineHeader line1={hdrDeltaFy} line2="vs prior FY" />
                <TwoLineHeader line1={lbl?.cm_py ?? ''} line2="Prior year CM" />
                <TwoLineHeader line1={lbl?.cm ?? ''} line2="Current period" highlighted />
                <TwoLineHeader line1={hdrDeltaCm} line2="vs prior CM" />
              </tr>
            </thead>
            <tbody>{walkRows(data.rows, 0)}</tbody>
          </table>
        </div>
      )}
    </div>
  )
}
