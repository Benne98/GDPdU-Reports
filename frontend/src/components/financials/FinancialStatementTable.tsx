import { useEffect, useMemo, useState } from 'react'
import { ChevronRight, Pin } from 'lucide-react'
import {
  FinancialStatementRow,
  FinancialStatementResponse,
} from '../../lib/api'
import { fmtKpi, fmtPct, fmtDays } from '../../lib/fmt'
import { FIN_TABLE_CELL_CLASS, FIN_TABLE_VALUE_FONT } from './finReportLayout'
import { computeAutoExpandedIds } from './statementRowExpansion'
import PlExportMenu, { type PlExportKind } from './pl-two-view/PlExportMenu'
import { PL_TOOLBAR_ICON_BTN, PL_TOOLBAR_BTN_STYLE } from './statement-two-view/statementToolbarButton'
import { exportFinssentialsXlsx } from '../../lib/finssentialsExport'
import { buildFlatTablePptxConfig } from '../../lib/finssentialsExport/buildPptxExportConfig'
import { exportFinssentialsPptx } from '../../lib/finssentialsExport/pptx/exportFinssentialsPptx'
import { buildExportCheckOpen } from '../../lib/finssentialsExport/buildExportCheckOpen'
import { flattenTreeToExportRows, todayStr } from '../../lib/exportXlsx'
import { exportFinStatementTableViewPdf } from './pl-two-view/plExportPdf'
import type { PlTableColumnDef } from './pl-two-view/plColumnRegistry'
import { useOptionalActionNotesContext } from '../action-notes/ActionNotesContext'
import { labelActual } from '../../lib/periodColumnLabels'
function lastDay(y: number, m: number): string {
  return new Date(y, m, 0).toISOString().slice(0, 10)
}
function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

type ValueCol = 'py_cm' | 'pm' | 'cm' | 'ytd' | 'ytd_py'

function periodRange(year: number, month: number, col: ValueCol): { from: string; to: string } {
  const pmYear = month === 1 ? year - 1 : year
  const pmMonth = month === 1 ? 12 : month - 1
  switch (col) {
    case 'py_cm':
      return { from: `${year - 1}-${pad2(month)}-01`, to: lastDay(year - 1, month) }
    case 'pm':
      return { from: `${pmYear}-${pad2(pmMonth)}-01`, to: lastDay(pmYear, pmMonth) }
    case 'cm':
      return { from: `${year}-${pad2(month)}-01`, to: lastDay(year, month) }
    case 'ytd':
      return { from: `${year}-01-01`, to: lastDay(year, month) }
    case 'ytd_py':
      return { from: `${year - 1}-01-01`, to: lastDay(year - 1, month) }
  }
}

/** Where on Financials the drill was opened — drives inline GL table placement. */
export type FinancialsDrillAnchor = 'statement' | 'l4' | 'wc-timeline' | 'consolidation'

export interface FinancialsDrillOpen {
  dateFrom:       string
  dateTo:         string
  title:          string
  level2?:        string
  level3?:        string
  level4?:        string
  glAccountId?:   string
  statementType?: string
  /** When set (e.g. entity breakdown table), drill uses this legal entity instead of the page filter. */
  entityOverride?: string
  anchor?:        FinancialsDrillAnchor
}

function DeltaBar({ value, maxAbs }: { value: number; maxAbs: number }) {
  if (maxAbs === 0) return <span className="inline-block" style={{ width: 28 }} />
  const pct = Math.min((Math.abs(value) / maxAbs) * 100, 100)
  const isPos = value >= 0
  return (
    <span
      className="inline-block align-middle"
      style={{ width: 28, height: 6, background: '#F1F5F9', borderRadius: 2, overflow: 'hidden', flexShrink: 0 }}
    >
      <span
        style={{
          display: 'block',
          height: '100%',
          width: `${pct}%`,
          background: isPos ? '#10B981' : '#DC2626',
          borderRadius: 2,
        }}
      />
    </span>
  )
}

function deltaColor(value: number, invert: boolean): string {
  if (value === 0) return '#94A3B8'
  const good = value > 0
  const looksGood = invert ? !good : good
  return looksGood ? '#10B981' : '#DC2626'
}

function DeltaCell({
  value,
  maxAbs,
  invert,
  isPct,
  isDays,
  italic,
  onClick,
}: {
  value:   number
  maxAbs:  number
  invert:  boolean
  isPct?:  boolean
  isDays?: boolean
  italic?: boolean
  onClick?: () => void
}) {
  const color = (isPct || isDays)
    ? (value > 0 ? '#10B981' : value < 0 ? '#DC2626' : '#94A3B8')
    : deltaColor(value, invert)
  const text = isDays
    ? `${value >= 0 ? '+' : ''}${Math.abs(value).toLocaleString('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}`
    : isPct
    ? `${value >= 0 ? '+' : ''}${value.toLocaleString('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 })} PP`
    : fmtKpi(value)
  return (
    <td
      className={`${FIN_TABLE_CELL_CLASS} text-right whitespace-nowrap tabular-nums`}
      onClick={onClick}
      style={{ cursor: onClick ? 'pointer' : 'default' }}
    >
      <span className="flex items-center justify-end gap-1">
        <span style={{ color, fontWeight: 500, fontSize: FIN_TABLE_VALUE_FONT, fontStyle: italic ? 'italic' : undefined }}>{text}</span>
        {!isPct && !isDays && <DeltaBar value={value} maxAbs={maxAbs} />}
      </span>
    </td>
  )
}

function ValCell({
  value,
  highlighted = false,
  bold = false,
  isPct = false,
  isDays = false,
  italic = false,
  onClick,
}: {
  value:        number
  highlighted?: boolean
  bold?:        boolean
  isPct?:       boolean
  isDays?:      boolean
  italic?:      boolean
  onClick?:     () => void
}) {
  const [hovered, setHovered] = useState(false)
  const text = isDays ? fmtDays(value) : isPct ? fmtPct(value) : fmtKpi(value)
  return (
    <td
      className={`${FIN_TABLE_CELL_CLASS} text-right whitespace-nowrap tabular-nums`}
      onClick={onClick}
      onMouseEnter={() => onClick && setHovered(true)}
      onMouseLeave={() => onClick && setHovered(false)}
      style={{
        background: highlighted ? 'rgba(30,58,95,0.04)' : undefined,
        cursor: onClick ? 'pointer' : 'default',
        fontWeight: bold ? 600 : 400,
        fontStyle: italic ? 'italic' : undefined,
        fontSize: FIN_TABLE_VALUE_FONT,
        color: hovered ? '#1E3A5F' : '#111827',
      }}
    >
      {text}
    </td>
  )
}

function collectNumericRows(rows: FinancialStatementRow[]): FinancialStatementRow[] {
  const out: FinancialStatementRow[] = []
  function walk(r: FinancialStatementRow) {
    if (r.amounts && r.row_kind !== 'title') out.push(r)
    for (const c of r.children ?? []) walk(c)
    for (const a of r.accounts ?? []) walk(a)
  }
  for (const r of rows) walk(r)
  return out
}

interface FinancialStatementTableProps {
  data:    FinancialStatementResponse | null
  loading: boolean
  error:   string | null
  year:    number
  month:   number
  onDrill: (d: FinancialsDrillOpen) => void
  /** When true, render only the table (header/toolbar on StatementSectionShell). */
  embedded?: boolean
}

export default function FinancialStatementTable({
  data,
  loading,
  error,
  year,
  month,
  onDrill,
  embedded = false,
}: FinancialStatementTableProps) {
  // userToggles tracks rows the user has explicitly toggled (XOR with autoExpanded)
  const [userToggles, setUserToggles] = useState<Set<string>>(() => new Set())
  const notesCtx = useOptionalActionNotesContext()

  const autoExpandedIds = useMemo(
    () => computeAutoExpandedIds(data?.rows, data?.statement),
    [data],
  )

  function checkOpen(id: string): boolean {
    // XOR: auto-expanded rows are open unless user closed them, and vice-versa
    return autoExpandedIds.has(id) !== userToggles.has(id)
  }

  // ─── Pin registration (non-embedded only) ─────────────────────────────────
  useEffect(() => {
    if (embedded) return
    const stmt = data?.statement ?? 'pl'
    const pinId = `${stmt}-legacy-statement`
    if (!notesCtx || !data?.rows?.length) {
      notesCtx?.unregisterTableCandidate(pinId)
      return
    }
    notesCtx.registerTableCandidate({
      id: pinId,
      label: `${stmt.toUpperCase()} financial statement — table view`,
      description: 'Actuals vs prior month and YTD — values in EURk',
      capture: () => {
        if (!data.rows.length) return null
        const row_preview = data.rows
          .filter(r => r.row_kind !== 'title' && r.row_kind !== 'kpi_header')
          .slice(0, 25)
          .map(r => ({ id: r.id, label: r.label, values: { cm: r.amounts?.cm ?? '—', ytd: r.amounts?.ytd ?? '—' } }))
        return { component: 'FinancialStatementTable', expanded_row_ids: [], visible_column_ids: ['py_cm', 'pm', 'cm', 'ytd', 'ytd_py'], row_preview }
      },
      viewState: { tab: stmt },
    })
    return () => notesCtx.unregisterTableCandidate(pinId)
  }, [notesCtx, data, embedded])

  const { maxMom, maxYoy, maxYtd, maxKpiMom, maxKpiYoy, maxKpiYtd } = useMemo(() => {
    if (!data?.rows) {
      return { maxMom: 1, maxYoy: 1, maxYtd: 1, maxKpiMom: 1, maxKpiYoy: 1, maxKpiYtd: 1 }
    }
    const all = collectNumericRows(data.rows)
    const cur = all.filter(r => r.row_kind !== 'kpi')
    const kpi = all.filter(r => r.row_kind === 'kpi')
    const maxOf = (rows: FinancialStatementRow[], key: 'mom' | 'yoy' | 'ytd') =>
      Math.max(
        1,
        ...rows.map(r => Math.abs(r.deltas?.[key] ?? 0)),
      )
    return {
      maxMom: maxOf(cur, 'mom'),
      maxYoy: maxOf(cur, 'yoy'),
      maxYtd: maxOf(cur, 'ytd'),
      maxKpiMom: maxOf(kpi, 'mom'),
      maxKpiYoy: maxOf(kpi, 'yoy'),
      maxKpiYtd: maxOf(kpi, 'ytd'),
    }
  }, [data])

  const lbl = data?.col_labels
  const hdrMom = lbl ? `Δ ${lbl.cm} − ${lbl.pm}` : 'Δ MoM'
  const hdrYoy = lbl ? `Δ ${lbl.cm} − ${lbl.py_cm}` : 'Δ YoY'
  const hdrYtd = lbl ? `Δ ${lbl.ytd} − ${lbl.ytd_py}` : 'Δ YTD'

  function toggle(id: string) {
    setUserToggles(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })
  }

  function openDrill(
    row: FinancialStatementRow,
    col: ValueCol,
    colLabel: string,
  ) {
    if (!row.drill) return
    const { from, to } = periodRange(year, month, col)
    onDrill({
      dateFrom: from,
      dateTo: to,
      title: `${row.label} — ${colLabel}`,
      level2: row.drill.level_2 ?? undefined,
      level3: row.drill.level_3 ?? undefined,
      level4: row.drill.level_4 ?? undefined,
      glAccountId: row.drill.gl_account_id ?? undefined,
      statementType: row.drill.statement_type ?? undefined,
    })
  }

  function renderDataRow(row: FinancialStatementRow, depth: number) {
    const isTitle = row.row_kind === 'title'
    const isKpi = row.row_kind === 'kpi'
    const pad = 12 + depth * 14
    const isOpen = checkOpen(row.id)
    const showChevron =
      (row.children?.length ?? 0) > 0 || (row.accounts?.length ?? 0) > 0

    if (isTitle) {
      return (
        <tr key={row.id} style={{ background: '#F8FAFC', borderTop: '1px solid #E2E8F0' }}>
          <td
            colSpan={9}
            className="px-3 py-2 text-xs font-bold uppercase tracking-wide"
            style={{ color: '#1E3A5F' }}
          >
            {row.label}
          </td>
        </tr>
      )
    }

    const am = row.amounts!
    const d = row.deltas!
    const inv = row.invert_delta
    const isSubtotal = row.row_kind === 'subtotal'
    const isAccount = row.row_kind === 'account'
    const kpiMax = { mom: maxKpiMom, yoy: maxKpiYoy, ytd: maxKpiYtd }

    return (
      <tr
        key={row.id}
        style={{
          borderBottom: isKpi ? 'none' : '1px solid #E2E8F0',
          borderTop: isSubtotal && depth === 0 ? '2px solid #E2E8F0' : undefined,
          background: isKpi ? '#F8FAFC' : isSubtotal && depth === 0 ? '#F8FAFC' : undefined,
        }}
      >
        <td
          className={`${FIN_TABLE_CELL_CLASS} text-left`}
          style={{ minWidth: 168, paddingLeft: pad, paddingRight: 8, fontSize: FIN_TABLE_VALUE_FONT }}
        >
          <div className="flex items-center gap-0.5">
            {showChevron && (
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
            )}
            {!showChevron && <span style={{ width: 22 }} />}
            <span
              className="text-xs"
              style={{
                fontWeight: row.is_bold || isSubtotal ? 600 : 500,
                fontStyle: isKpi ? 'italic' : undefined,
                color: isKpi ? '#64748B' : isAccount ? '#475569' : '#111827',
              }}
            >
              {row.label}
            </span>
          </div>
        </td>
        {isKpi ? (
          <>
            {/* WC KPIs are in days, all others are percentages */}
            {data?.statement === 'wc' ? (
              <>
                <ValCell value={am.py_cm} isDays italic />
                <ValCell value={am.pm}    isDays italic />
                <ValCell value={am.cm}    isDays highlighted italic />
                <DeltaCell value={d.mom} maxAbs={kpiMax.mom} invert={false} isDays italic />
                <DeltaCell value={d.yoy} maxAbs={kpiMax.yoy} invert={false} isDays italic />
                <ValCell value={am.ytd}    isDays highlighted italic />
                <ValCell value={am.ytd_py} isDays italic />
                <DeltaCell value={d.ytd} maxAbs={kpiMax.ytd} invert={false} isDays italic />
              </>
            ) : (
              <>
                <ValCell value={am.py_cm} isPct italic />
                <ValCell value={am.pm} isPct italic />
                <ValCell value={am.cm} isPct highlighted italic />
                <DeltaCell value={d.mom} maxAbs={kpiMax.mom} invert={false} isPct italic />
                <DeltaCell value={d.yoy} maxAbs={kpiMax.yoy} invert={false} isPct italic />
                <ValCell value={am.ytd} isPct highlighted italic />
                <ValCell value={am.ytd_py} isPct italic />
                <DeltaCell value={d.ytd} maxAbs={kpiMax.ytd} invert={false} isPct italic />
              </>
            )}
          </>
        ) : (
          <>
            <ValCell
              value={am.py_cm}
              bold={row.is_bold}
              onClick={row.drill ? () => openDrill(row, 'py_cm', lbl?.py_cm ?? '') : undefined}
            />
            <ValCell
              value={am.pm}
              bold={row.is_bold}
              onClick={row.drill ? () => openDrill(row, 'pm', lbl?.pm ?? '') : undefined}
            />
            <ValCell
              value={am.cm}
              bold={row.is_bold}
              highlighted
              onClick={row.drill ? () => openDrill(row, 'cm', lbl?.cm ?? '') : undefined}
            />
            <DeltaCell
              value={d.mom}
              maxAbs={maxMom}
              invert={inv}
              onClick={row.drill ? () => openDrill(row, 'cm', `${lbl?.cm} vs ${lbl?.pm}`) : undefined}
            />
            <DeltaCell
              value={d.yoy}
              maxAbs={maxYoy}
              invert={inv}
              onClick={row.drill ? () => openDrill(row, 'cm', `${lbl?.cm} vs ${lbl?.py_cm}`) : undefined}
            />
            <ValCell
              value={am.ytd}
              bold={row.is_bold}
              highlighted
              onClick={row.drill ? () => openDrill(row, 'ytd', lbl?.ytd ?? '') : undefined}
            />
            <ValCell
              value={am.ytd_py}
              bold={row.is_bold}
              onClick={row.drill ? () => openDrill(row, 'ytd_py', lbl?.ytd_py ?? '') : undefined}
            />
            <DeltaCell
              value={d.ytd}
              maxAbs={maxYtd}
              invert={inv}
              onClick={row.drill ? () => openDrill(row, 'ytd', `${lbl?.ytd} vs ${lbl?.ytd_py}`) : undefined}
            />
          </>
        )}
      </tr>
    )
  }

  function walkRows(rows: FinancialStatementRow[], depth: number): JSX.Element[] {
    const nodes: JSX.Element[] = []
    let kpiHeaderInserted = false
    for (const row of rows) {
      if (row.row_kind === 'kpi' && !kpiHeaderInserted) {
        kpiHeaderInserted = true
        const kpiHeaderLabel = data?.statement === 'wc' ? 'KPIs — working capital days'
          : data?.statement === 'bs' ? 'KPIs'
          : 'KPIs — as % of total output'
        nodes.push(
          <tr key="kpi-section-header" style={{ background: '#F8FAFC', borderTop: '2px solid #E2E8F0' }}>
            <td className="px-3 py-2 text-xs font-semibold" style={{ color: '#1E3A5F', fontStyle: 'italic' }}>
              {kpiHeaderLabel}
            </td>
            {/* py_cm */}
            <td style={{ background: '#F8FAFC' }} />
            {/* pm */}
            <td style={{ background: '#F8FAFC' }} />
            {/* cm highlighted */}
            <td style={{ background: 'rgba(30,58,95,0.04)' }} />
            {/* delta mom */}
            <td style={{ background: '#F8FAFC' }} />
            {/* delta yoy */}
            <td style={{ background: '#F8FAFC' }} />
            {/* ytd highlighted */}
            <td style={{ background: 'rgba(30,58,95,0.04)' }} />
            {/* ytd_py */}
            <td style={{ background: '#F8FAFC' }} />
            {/* delta ytd */}
            <td style={{ background: '#F8FAFC' }} />
          </tr>
        )
      }
      if (row.row_kind === 'title') {
        nodes.push(renderDataRow(row, depth))
        continue
      }
      nodes.push(renderDataRow(row, depth))
      if (!checkOpen(row.id)) continue
      if (row.children?.length) {
        for (const ch of row.children) nodes.push(...walkRows([ch], depth + 1))
      }
      if (row.accounts?.length) {
        for (const acc of row.accounts) nodes.push(...walkRows([acc], depth + 1))
      }
    }
    return nodes
  }

  async function handleExport(kind: PlExportKind) {
    if (!data) return
    if (kind === 'pdf') {
      const isRowOpen = buildExportCheckOpen(data.rows, data.statement, userToggles)
      const fixedCols: PlTableColumnDef[] = [
        { id: 'py_cm', kind: 'py_cm', labelLine1: lbl?.py_cm ?? 'PY CM' },
        { id: 'pm',    kind: 'pm',    labelLine1: lbl?.pm    ?? 'PM' },
        { id: 'cm',    kind: 'cm',    labelLine1: lbl?.cm    ?? 'CM' },
        { id: 'ytd',   kind: 'ytd',   labelLine1: lbl?.ytd   ?? 'YTD' },
        { id: 'ytd_py', kind: 'ytd_py', labelLine1: lbl?.ytd_py ?? 'YTD PY' },
      ]
      const stmtPrefix = ({ pl: 'PL', bs: 'BS', cf: 'CF', wc: 'WC' } as Record<string, string>)[data.statement] ?? 'PL'
      await exportFinStatementTableViewPdf(data, year, month, fixedCols, {}, null, { entityLabel: '', entityDisplayName: 'Group' }, 'Financial Statement', stmtPrefix, isRowOpen)
      return
    }
    const stmtName = ({
      pl: 'Income Statement', bs: 'Balance Sheet',
      cf: 'Cash Flow Statement', wc: 'Working Capital',
    } as Record<string, string>)[data.statement] ?? 'Financial Statement'
    const isRowOpen = buildExportCheckOpen(data.rows, data.statement, userToggles)
    const rows = flattenTreeToExportRows(
      data.rows as Parameters<typeof flattenTreeToExportRows>[0],
      ['py_cm', 'pm', 'cm', 'ytd', 'ytd_py'],
      ['mom', 'yoy', 'ytd'],
      isRowOpen,
    )
    const headers = ['EURk', lbl?.py_cm ?? 'PY CM', lbl?.pm ?? 'PM', lbl?.cm ?? 'CM',
      hdrMom, hdrYoy, lbl?.ytd ?? 'YTD', lbl?.ytd_py ?? 'YTD PY', hdrYtd]
    const columnKinds = ['', 'py_cm', 'pm', 'cm', 'mom', 'yoy', 'ytd', 'ytd_py', 'ytd_delta']
    if (kind === 'pptx') {
      await exportFinssentialsPptx(
        buildFlatTablePptxConfig({
          fileName: `${stmtName.replace(/ /g, '_')}_${todayStr()}.pptx`,
          pageTitle: tableTitle,
          tableHeading: tableTitle,
          breadcrumbCurrent: stmtName,
          footerRight: `EURk · ${lbl?.cm ?? ''}A`,
          headers,
          columnKinds,
          rows,
        }),
      )
      return
    }
    await exportFinssentialsXlsx({
      tableTitle: stmtName,
      subtitle: `Values in EURk · ${lbl?.cm ?? ''}A`,
      headers,
      columnKinds,
      rows,
      collapseNonReportColumns: data.statement,
      filename: `${stmtName.replace(/ /g, '_')}_${todayStr()}.xlsx`,
    })
  }

  if (error) {
    return (
      <div className="rounded-xl p-8 text-center text-sm" style={{ background: '#FFF', border: '1px solid #FECACA', color: '#B91C1C' }}>
        {error}
      </div>
    )
  }
  if (loading) {
    return (
      <div className="rounded-xl p-8 text-center text-sm" style={{ background: '#FFF', border: '1px solid #E2E8F0', color: '#64748B' }}>
        Loading…
      </div>
    )
  }
  if (!data?.rows.length) {
    return (
      <div className="rounded-xl p-8 text-center text-sm" style={{ background: '#FFF', border: '1px solid #E2E8F0', color: '#64748B' }}>
        No rows for this period or entity. Check that demo data is loaded (setup_db) and that the period exists in the database.
      </div>
    )
  }

  const STATEMENT_TITLES: Record<string, string> = {
    pl: 'Income statement (consolidated)',
    bs: 'Balance sheet (consolidated)',
    cf: 'Cash flow statement (consolidated)',
    wc: 'Working capital (consolidated)',
  }
  const tableTitle = data.statement ? (STATEMENT_TITLES[data.statement] ?? 'Financial statement') : 'Financial statement'
  const periodBadge = lbl?.cm ? labelActual(lbl.cm) : ''

  const tableEl = (
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr style={{ borderBottom: '2px solid #E2E8F0', background: '#F8FAFC' }}>
            <th className="px-3 py-2.5 text-left font-semibold" style={{ color: '#475569' }}>
              EURk
            </th>
            <th className="px-2.5 py-2.5 text-right font-semibold whitespace-nowrap" style={{ color: '#475569' }}>
              {lbl?.py_cm}
            </th>
            <th className="px-2.5 py-2.5 text-right font-semibold whitespace-nowrap" style={{ color: '#475569' }}>
              {lbl?.pm}
            </th>
            <th className="px-2.5 py-2.5 text-right font-semibold whitespace-nowrap" style={{ color: '#1E3A5F' }}>
              {lbl?.cm}
            </th>
            <th className="px-2.5 py-2.5 text-right font-semibold whitespace-nowrap" style={{ color: '#475569' }}>
              {hdrMom}
            </th>
            <th className="px-2.5 py-2.5 text-right font-semibold whitespace-nowrap" style={{ color: '#475569' }}>
              {hdrYoy}
            </th>
            <th className="px-2.5 py-2.5 text-right font-semibold whitespace-nowrap" style={{ color: '#1E3A5F' }}>
              {lbl?.ytd}
            </th>
            <th className="px-2.5 py-2.5 text-right font-semibold whitespace-nowrap" style={{ color: '#475569' }}>
              {lbl?.ytd_py}
            </th>
            <th className="px-2.5 py-2.5 text-right font-semibold whitespace-nowrap" style={{ color: '#475569' }}>
              {hdrYtd}
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
      className="rounded-xl overflow-x-auto"
      style={{ background: '#FFFFFF', border: '1px solid #E2E8F0', boxShadow: '0 1px 3px rgba(0,0,0,0.05)' }}
    >
      <div className="px-4 pt-4 pb-3 flex items-start justify-between gap-3" style={{ borderBottom: '1px solid #F1F5F9' }}>
        <div>
          <div className="flex items-center gap-2 mb-0.5">
            <span className="text-sm font-semibold" style={{ color: '#111827' }}>{tableTitle}</span>
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
            Values in EURk — click any value to drill into bookings
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {notesCtx && data && (
            <button
              type="button"
              title="Pin to Action Board"
              className={PL_TOOLBAR_ICON_BTN}
              style={PL_TOOLBAR_BTN_STYLE}
              onClick={() => {
                const pinId = `${data.statement}-legacy-statement`
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
      {tableEl}
    </div>
  )
}
