/**
 * ErSnapshotTable — Exit Readiness table for snapshot statements (BS, WC).
 *
 * Columns: Dec{yr-2} | Dec{yr-1} | ∆FY | {month}{yr-1} | {month}{yr} | ∆CM
 */
import { useMemo, useState, useEffect } from 'react'
import { ChevronRight } from 'lucide-react'
import { ErSnapshotResponse, ErStatementRow, ErSnapshotColLabels } from '../../../lib/api'
import { fmtKpi, fmtPct, fmtDays } from '../../../lib/fmt'
import { FinancialsDrillOpen } from '../FinancialStatementTable'
import PlExportMenu, { type PlExportKind } from '../pl-two-view/PlExportMenu'
import PlViewToggleButton, { type PlViewMode } from '../pl-two-view/PlViewToggleButton'
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

function DeltaBar({ value, maxAbs }: { value: number; maxAbs: number }) {
  if (maxAbs === 0) return <span className="inline-block" style={{ width: 28 }} />
  const pct = Math.min((Math.abs(value) / maxAbs) * 100, 100)
  const isPos = value >= 0
  return (
    <span className="inline-block align-middle" style={{ width: 28, height: 6, background: '#F1F5F9', borderRadius: 2, overflow: 'hidden', flexShrink: 0 }}>
      <span style={{ display: 'block', height: '100%', width: `${pct}%`, background: isPos ? '#10B981' : '#DC2626', borderRadius: 2 }} />
    </span>
  )
}

function deltaColor(value: number, invert: boolean): string {
  if (value === 0) return '#94A3B8'
  const looksGood = invert ? value < 0 : value > 0
  return looksGood ? '#10B981' : '#DC2626'
}

function DeltaCell({ value, maxAbs, invert, isPct, isDays, onClick }: {
  value: number; maxAbs: number; invert: boolean; isPct?: boolean; isDays?: boolean; onClick?: () => void
}) {
  const color = (isPct || isDays)
    ? (value > 0 ? '#10B981' : value < 0 ? '#DC2626' : '#94A3B8')
    : deltaColor(value, invert)
  const text = isPct
    ? `${value >= 0 ? '+' : ''}${value.toLocaleString('en-US', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} PP`
    : isDays
      ? `${value >= 0 ? '+' : ''}${fmtDays(Math.abs(value))} d`
      : fmtKpi(value)
  return (
    <td className="px-2.5 py-2 text-right whitespace-nowrap tabular-nums" onClick={onClick} style={{ cursor: onClick ? 'pointer' : 'default' }}>
      <span className="flex items-center justify-end gap-1.5">
        <span style={{ color, fontWeight: 500, fontSize: '0.72rem', fontStyle: 'italic' }}>{text}</span>
        {!isPct && !isDays && <DeltaBar value={value} maxAbs={maxAbs} />}
      </span>
    </td>
  )
}

function ValCell({ value, highlighted = false, bold = false, isPct = false, isDays = false, italic = false, onClick }: {
  value: number; highlighted?: boolean; bold?: boolean; isPct?: boolean; isDays?: boolean; italic?: boolean; onClick?: () => void
}) {
  const [hovered, setHovered] = useState(false)
  const text = isDays ? fmtDays(value) : isPct ? fmtPct(value) : fmtKpi(value)
  return (
    <td
      className="px-2.5 py-2 text-right whitespace-nowrap tabular-nums"
      onClick={onClick}
      onMouseEnter={() => onClick && setHovered(true)}
      onMouseLeave={() => onClick && setHovered(false)}
      style={{
        background: highlighted ? 'rgba(30,58,95,0.04)' : undefined,
        cursor: onClick ? 'pointer' : 'default',
        fontWeight: bold ? 600 : 400,
        fontStyle: italic ? 'italic' : undefined,
        fontSize: '0.72rem',
        color: hovered ? '#1E3A5F' : '#111827',
      }}
    >
      {text}
    </td>
  )
}

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
}

// ─── Component ────────────────────────────────────────────────────────────────

export default function ErSnapshotTable({
  data, loading, error, year, month, entity, periodSelection, entityDisplayName, onDrill,
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

  const autoExpandedIds = useMemo(() => {
    if (!data?.rows) return new Set<string>()
    if (data.statement === 'bs') {
      const BS_KEEP_CLOSED = new Set(['Deferred tax assets', 'Prepaid expenses'])
      function collectIds(rows: ErStatementRow[], depth: number): string[] {
        if (depth >= 2) return []
        const ids: string[] = []
        for (const row of rows) {
          if ((row.children?.length ?? 0) > 0) {
            if (BS_KEEP_CLOSED.has(row.label)) continue
            ids.push(row.id)
            ids.push(...collectIds(row.children ?? [], depth + 1))
          }
        }
        return ids
      }
      return new Set(collectIds(data.rows, 0))
    }
    if (data.statement === 'wc') {
      const ids: string[] = []
      for (const r of data.rows) {
        if ((r.children?.length ?? 0) > 0) ids.push(r.id)
      }
      return new Set(ids)
    }
    return new Set<string>()
  }, [data])

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

  // 7 data columns (dec_py2 | fy_py | fy | ∆FY | cm_py | cm | ∆CM); cm is index 5 (0-based)
  const COLS = 7
  const CM_IDX = 5

  function renderRow(row: ErStatementRow, depth: number): JSX.Element {
    const isTitle    = row.row_kind === 'title'
    const isKpi      = row.row_kind === 'kpi'
    const isKpiHdr   = row.row_kind === 'kpi_header'
    const pad = 12 + depth * 14
    const isOpen = checkOpen(row.id)
    const showChevron = (row.children?.length ?? 0) > 0 || (row.accounts?.length ?? 0) > 0

    if (isTitle || isKpiHdr) {
      return (
        <tr key={row.id} style={{ background: '#F8FAFC', borderTop: isKpiHdr ? '2px solid #E2E8F0' : '1px solid #E2E8F0' }}>
          <td colSpan={isKpiHdr ? undefined : COLS + 1}
              className="px-3 py-2 text-xs font-bold uppercase tracking-wide"
              style={{ color: '#1E3A5F', fontStyle: isKpiHdr ? 'italic' : undefined, textTransform: isKpiHdr ? 'none' : undefined, fontWeight: isKpiHdr ? 600 : undefined }}>
            {row.label}
          </td>
          {isKpiHdr && Array.from({ length: COLS }).map((_, i) => (
            <td key={i} style={{ background: i === CM_IDX ? 'rgba(30,58,95,0.04)' : '#F8FAFC' }} />
          ))}
        </tr>
      )
    }

    const am  = row.amounts  ?? {}
    const d   = row.deltas   ?? {}
    const inv = row.invert_delta
    const isSubtotal = row.row_kind === 'subtotal'
    const isAccount  = row.row_kind === 'account'

    const get  = (k: string) => (am as Record<string, number>)[k] ?? 0
    const dget = (k: string) => (d  as Record<string, number>)[k] ?? 0

    return (
      <tr
        key={row.id}
        style={{
          borderBottom: isKpi ? 'none' : '1px solid #E2E8F0',
          borderTop: isSubtotal && depth === 0 ? '2px solid #E2E8F0' : undefined,
          background: isKpi ? '#F8FAFC' : isSubtotal && depth === 0 ? '#F8FAFC' : undefined,
        }}
      >
        <td className="py-2 text-left whitespace-nowrap" style={{ minWidth: 220, paddingLeft: pad, paddingRight: 12 }}>
          <div className="flex items-center gap-0.5">
            {showChevron ? (
              <button type="button" onClick={() => toggle(row.id)} className="p-0.5 rounded shrink-0" style={{ color: '#1E3A5F' }} aria-expanded={isOpen}>
                <ChevronRight size={14} style={{ transform: isOpen ? 'rotate(90deg)' : 'none', transition: 'transform 0.15s' }} />
              </button>
            ) : <span style={{ width: 22 }} />}
            <span className="text-xs" style={{
              fontWeight: row.is_bold || isSubtotal ? 600 : 500,
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
              <ValCell value={get('fy_py')} isDays italic />
              <ValCell value={get('fy')}    isDays italic />
              <DeltaCell value={dget('delta_fy')} maxAbs={1} invert={false} isDays />
              <ValCell value={get('cm_py')} isDays italic />
              <ValCell value={get('cm')}    isDays highlighted italic />
              <DeltaCell value={dget('delta_cm')} maxAbs={1} invert={false} isDays />
            </>
          ) : (
            /* BS KPIs — percentages */
            <>
              <ValCell value={get('dec_py2')} isPct italic />
            <ValCell value={get('fy_py')} isPct italic />
              <ValCell value={get('fy')}    isPct italic />
              <DeltaCell value={dget('delta_fy')} maxAbs={1} invert={false} isPct />
              <ValCell value={get('cm_py')} isPct italic />
              <ValCell value={get('cm')}    isPct highlighted italic />
              <DeltaCell value={dget('delta_cm')} maxAbs={1} invert={false} isPct />
            </>
          )
        ) : (
          <>
            <ValCell value={get('dec_py2')} bold={row.is_bold} onClick={row.drill ? () => openDrill(row, 'dec_py2', lbl?.dec_py2 ?? '') : undefined} />
            <ValCell value={get('fy_py')} bold={row.is_bold} onClick={row.drill ? () => openDrill(row, 'fy_py', lbl?.fy_py ?? '') : undefined} />
            <ValCell value={get('fy')}    bold={row.is_bold} onClick={row.drill ? () => openDrill(row, 'fy',    lbl?.fy    ?? '') : undefined} />
            <DeltaCell value={dget('delta_fy')} maxAbs={maxDeltaFy} invert={inv}
              onClick={row.drill ? () => openDrill(row, 'fy', `${lbl?.fy} vs ${lbl?.fy_py}`) : undefined} />
            <ValCell value={get('cm_py')} bold={row.is_bold} onClick={row.drill ? () => openDrill(row, 'cm_py', lbl?.cm_py ?? '') : undefined} />
            <ValCell value={get('cm')}    bold={row.is_bold} highlighted onClick={row.drill ? () => openDrill(row, 'cm', lbl?.cm ?? '') : undefined} />
            <DeltaCell value={dget('delta_cm')} maxAbs={maxDeltaCm} invert={inv}
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
      if (row.row_kind === 'kpi' && !kpiHeaderInserted && data?.statement !== 'wc') {
        kpiHeaderInserted = true
        nodes.push(
          <tr key="er-kpi-header" style={{ background: '#F8FAFC', borderTop: '2px solid #E2E8F0' }}>
            <td className="px-3 py-2 text-xs font-semibold" style={{ color: '#1E3A5F', fontStyle: 'italic' }}>KPIs</td>
            {Array.from({ length: COLS }).map((_, i) => (
              <td key={i} style={{ background: i === CM_IDX ? 'rgba(30,58,95,0.04)' : '#F8FAFC' }} />
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
      lbl ? `∆ ${lbl.fy_py}–${lbl.fy}` : '∆ FY',
      lbl?.cm_py ?? 'CM PY',
      lbl?.cm ?? 'CM',
      lbl ? `∆ ${lbl.cm_py}–${lbl.cm}` : '∆ CM',
    ]
    const columnKinds = ['', '', '', '', 'delta', '', 'cm', 'delta']
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
  const hdrDeltaFy = lbl ? `∆ ${lbl.fy_py}–${lbl.fy}` : '∆'
  const hdrDeltaCm = lbl ? `∆ ${lbl.cm_py}–${lbl.cm}` : '∆'
  const periodBadge = lbl?.cm ?? ''

  return (
    <div className="rounded-xl overflow-x-auto" style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.05)' }}>
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
          <PlExportMenu formats={['pptx', 'xlsx']} onExport={handleExport} disabled={!data} />
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
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr style={{ borderBottom: '2px solid #E2E8F0', background: '#F8FAFC' }}>
            <th className="px-3 py-2.5 text-left font-semibold" style={{ color: '#475569' }}>EURk</th>
            <th className="px-2.5 py-2.5 text-right font-semibold whitespace-nowrap" style={{ color: '#475569' }}>{lbl?.dec_py2}</th>
            <th className="px-2.5 py-2.5 text-right font-semibold whitespace-nowrap" style={{ color: '#475569' }}>{lbl?.fy_py}</th>
            <th className="px-2.5 py-2.5 text-right font-semibold whitespace-nowrap" style={{ color: '#475569' }}>{lbl?.fy}</th>
            <th className="px-2.5 py-2.5 text-right font-semibold whitespace-nowrap" style={{ color: '#475569' }}>{hdrDeltaFy}</th>
            <th className="px-2.5 py-2.5 text-right font-semibold whitespace-nowrap" style={{ color: '#475569' }}>{lbl?.cm_py}</th>
            <th className="px-2.5 py-2.5 text-right font-semibold whitespace-nowrap" style={{ color: '#1E3A5F' }}>{lbl?.cm}</th>
            <th className="px-2.5 py-2.5 text-right font-semibold whitespace-nowrap" style={{ color: '#475569' }}>{hdrDeltaCm}</th>
          </tr>
        </thead>
        <tbody>{walkRows(data.rows, 0)}</tbody>
      </table>
      )}
    </div>
  )
}
