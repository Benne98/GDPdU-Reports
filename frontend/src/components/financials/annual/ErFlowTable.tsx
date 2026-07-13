import { useEffect, useMemo, useState, type ReactNode } from 'react'
import { ChevronDown, ChevronRight, ChevronUp, GripVertical, Pencil, Pin, X } from 'lucide-react'
import { useOptionalActionNotesContext } from '../../action-notes/ActionNotesContext'
import { captureErFlowSnapshot } from '../../action-notes/captureExitReadiness'
import { ErFlowResponse, ErStatementRow, ErFlowColLabels } from '../../../lib/api'
import { FinancialsDrillOpen } from '../FinancialStatementTable'
import PlExportMenu, { type PlExportKind } from '../pl-two-view/PlExportMenu'
import { exportFlatTablePptx } from '../../../lib/finssentialsExport/exportFlatTablePptx'
import { exportToXlsx, flattenTree, todayStr } from '../../../lib/exportXlsx'
import { DeltaCell, TwoLineHeader, ValCell } from '../pl-two-view/plTableCore'
import { buildAnnualFlowNarrativeResponse } from './erAnnualNarrative'
import PlViewToggleButton, { type PlViewMode } from '../pl-two-view/PlViewToggleButton'
import { getStatementConfig } from '../statement-two-view/statementConfig'
import {
  applyViewModeFromSearchParams,
  loadStatementViewMode,
  saveStatementViewMode,
} from '../statement-two-view/statementViewMode'
import type { PeriodSelection } from '../../../lib/periodSelection'
import ErFlowReportView from './ErFlowReportView'
import { computeAutoExpandedIds } from '../statementRowExpansion'
import {
  labelActual,
  labelForecastFy,
  labelPlanFy,
  resolveAnnualForecastColumnLabel,
} from '../../../lib/periodColumnLabels'
import { buildAnnualFlowReportColumns } from './annualFlowReportColumns'
import { STATEMENT_TOOLBAR_ICON_BTN, STATEMENT_TOOLBAR_BTN_STYLE } from '../statement-two-view/statementToolbarButton'
import { shouldDisplayErStatementRow } from './annualRowVisibility'
import { IS_OVERVIEW_V2 } from '../../../lib/overviewV2Mode'

type FlowCol = 'fy1' | 'fy2' | 'fy3' | 'ytd' | 'ltm' | 'ytd_py' | 'ltm_py'
type ErViewMode = PlViewMode
type ErColKind = 'amount' | 'delta'

type ErColDef = {
  id: string
  kind: ErColKind
  labelLine1: string
  labelLine2?: string
  amountKey?: string
  deltaKey?: string
  flowCol?: FlowCol
  highlighted?: boolean
  isPct?: boolean
}

// Narrative type now provided by erAnnualNarrative

interface ErFlowTableProps {
  data: ErFlowResponse | null
  loading: boolean
  error: string | null
  year: number
  month: number
  entity?: string
  periodSelection?: PeriodSelection
  entityDisplayName?: string
  onDrill: (d: FinancialsDrillOpen) => void
  pinId?: string
  pinLabel?: string
}

function pad2(n: number): string {
  return String(n).padStart(2, '0')
}

function monthShort(m: number): string {
  return ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'][Math.max(1, Math.min(12, m)) - 1] ?? 'Mon'
}

function lastDay(y: number, m: number): string {
  return new Date(y, m, 0).toISOString().slice(0, 10)
}

function periodRange(year: number, month: number, col: FlowCol): { from: string; to: string } {
  const fy3y = year - 1
  const ltmStart = month < 12 ? `${fy3y}-${pad2(month + 1)}-01` : `${year}-01-01`
  const ltmPyStart = month < 12 ? `${year - 2}-${pad2(month + 1)}-01` : `${fy3y}-01-01`
  switch (col) {
    case 'fy1': return { from: `${year - 3}-01-01`, to: `${year - 3}-12-31` }
    case 'fy2': return { from: `${year - 2}-01-01`, to: `${year - 2}-12-31` }
    case 'fy3': return { from: `${fy3y}-01-01`, to: `${fy3y}-12-31` }
    case 'ytd': return { from: `${year}-01-01`, to: lastDay(year, month) }
    case 'ytd_py': return { from: `${fy3y}-01-01`, to: lastDay(fy3y, month) }
    case 'ltm': return { from: ltmStart, to: lastDay(year, month) }
    case 'ltm_py': return { from: ltmPyStart, to: lastDay(fy3y, month) }
  }
}

function loadTableCols(statement: string, fallback: string[]): string[] {
  if (typeof window === 'undefined') return fallback
  const raw = window.localStorage.getItem(`er-${statement}-table-cols`)
  if (!raw) return fallback
  try {
    const parsed = JSON.parse(raw)
    if (Array.isArray(parsed) && parsed.every(v => typeof v === 'string')) return parsed
  } catch {
    // ignore malformed storage
  }
  return fallback
}

function saveTableCols(statement: string, ids: string[]) {
  if (typeof window === 'undefined') return
  window.localStorage.setItem(`er-${statement}-table-cols`, JSON.stringify(ids))
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

function resolveForecastAmount(amounts: Record<string, number> | null | undefined): number {
  void amounts
  // Forecast column intentionally left blank (forecast methodology parked).
  return NaN
}

function resolveCoveragePct(amounts: Record<string, number> | null | undefined): number {
  const am = amounts ?? {}
  const forecast = resolveForecastAmount(am)
  if (Math.abs(forecast) <= 1e-6) return 0
  const ytd = Number(am.ytd ?? 0) || 0
  return (ytd / forecast) * 100
}

// buildFddNarrative replaced by buildAnnualNarrative (imported from erAnnualNarrative)

// DeltaBar, DeltaCell, ValCell imported from plTableCore above

// ─── Annual editor helper ─────────────────────────────────────────────────────

function AnnualEditorSection({ title, children, defaultOpen = true }: { title: string; children: ReactNode; defaultOpen?: boolean }) {
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

const ANNUAL_COLUMN_GROUPS: Array<{ title: string; ids: string[] }> = [
  { title: 'Historical FY & CAGR', ids: ['fy1', 'fy2', 'fy3', 'cagr', 'delta_fy'] },
  { title: 'Year to date', ids: ['ytd_py', 'ytd', 'delta_ytd'] },
  { title: 'Last twelve months', ids: ['ltm_py', 'ltm', 'delta_ltm'] },
  { title: 'Forecast & plan', ids: ['fy_f', 'plan_cm', 'coverage_pct'] },
]

/** Gross margin %, EBITDA margin %, Net profit margin % — bold label + values in KPI rows. */
const BOLD_MARGIN_KPI_CODES = new Set(['GROSS_MARGIN_PCT', 'EBITDA_MARGIN_PCT', 'NET_PROFIT_MARGIN_PCT'])

// ─── Main component ───────────────────────────────────────────────────────────

export default function ErFlowTable({
  data, loading, error, year, month, entity, periodSelection, entityDisplayName, onDrill, pinId, pinLabel,
}: ErFlowTableProps) {
  const statementKey = data?.statement === 'cf' ? 'cf' : 'pl'
  const stmtCfg = getStatementConfig(statementKey)
  // DISPLAY GATE: Forecast (fy_f/forecast/fy25_proxy) + Coverage columns render ONLY
  // when the backend reports real plan values (active + include_in_reporting version).
  // Absent flag → false → both columns hidden (rule: hidden unless there ARE plan values).
  const hasPlanData = data?.has_plan_data ?? false
  const PLAN_ONLY_COL_IDS = ['forecast', 'fy_f', 'fy25_proxy', 'coverage_pct']
  const [userToggles, setUserToggles] = useState<Set<string>>(() => new Set())
  const [viewMode, setViewMode] = useState<ErViewMode>(() => loadStatementViewMode(statementKey) as ErViewMode)
  const [tableColIds, setTableColIds] = useState<string[]>([])
  const [columnEditorOpen, setColumnEditorOpen] = useState(false)
  const [dragIdx, setDragIdx] = useState<number | null>(null)
  const notesCtx = useOptionalActionNotesContext()
  const defaultTableColIds = useMemo(
    () => {
      const ids = ['fy1', 'fy2', 'fy3', 'cagr', 'delta_fy', 'ytd_py', 'ytd', 'delta_ytd', 'ltm_py', 'ltm', 'fy_f', 'delta_ltm']
      return hasPlanData ? ids : ids.filter(id => !PLAN_ONLY_COL_IDS.includes(id))
    },
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [hasPlanData],
  )

  useEffect(() => {
    setTableColIds(loadTableCols(statementKey, defaultTableColIds))
  }, [statementKey, defaultTableColIds])

  useEffect(() => {
    applyViewModeFromSearchParams(setViewMode)
  }, [])

  useEffect(() => {
    saveStatementViewMode(statementKey, viewMode)
  }, [statementKey, viewMode])

  useEffect(() => {
    if (!notesCtx || !pinId) return
    if (!data) {
      notesCtx.unregisterTableCandidate(pinId)
      return
    }
    notesCtx.registerTableCandidate({
      id: pinId,
      label: pinLabel ?? 'Exit readiness statement',
      description: viewMode === 'report' ? 'Report view mini table' : 'Full table columns',
      capture: () => captureErFlowSnapshot(data, 'ErFlowTable'),
      viewState: { tab: data.statement, view_mode: viewMode },
    })
    return () => notesCtx.unregisterTableCandidate(pinId)
  }, [notesCtx, data, pinId, pinLabel, viewMode])

  const autoExpandedIds = useMemo(
    () => computeAutoExpandedIds(data?.rows, statementKey),
    [data?.rows, statementKey],
  )
  const checkOpen = (id: string) => autoExpandedIds.has(id) !== userToggles.has(id)
  const toggle = (id: string) =>
    setUserToggles(prev => {
      const next = new Set(prev)
      next.has(id) ? next.delete(id) : next.add(id)
      return next
    })

  const lbl = data?.col_labels as ErFlowColLabels | undefined
  const currentYearTag = String(year).slice(-2)

  const fy3BaseLabel = lbl?.fy3 ?? labelActual(`FY${String(year - 1).slice(-2)}`)
  const fy2BaseLabel = lbl?.fy2 ?? labelActual(`FY${String(year - 2).slice(-2)}`)
  const fy1BaseLabel = lbl?.fy1 ?? labelActual(`FY${String(year - 3).slice(-2)}`)
  const ytdBaseLabel = lbl?.ytd ?? labelActual(`YTD${monthShort(month)}${currentYearTag}`)
  const ytdPyBaseLabel = lbl?.ytd_py ?? labelActual(`YTD${monthShort(month)}${String(year - 1).slice(-2)}`)

  const catalog = useMemo<Record<string, ErColDef>>(() => {
    const forecastLabel = resolveAnnualForecastColumnLabel(year, lbl?.fy_f ?? lbl?.ltm)
    const ltmLabel = lbl?.ltm ?? labelActual(`LTM${monthShort(month)}${currentYearTag}`)
    const ltmPyLabel = lbl?.ltm_py ?? labelActual(`LTM${monthShort(month)}${String(year - 1).slice(-2)}`)
    return {
    fy1: { id: 'fy1', kind: 'amount', amountKey: 'fy1', flowCol: 'fy1', labelLine1: lbl?.fy1 ?? labelActual(`FY${String(year - 3).slice(-2)}`), labelLine2: 'Full fiscal year' },
    fy2: { id: 'fy2', kind: 'amount', amountKey: 'fy2', flowCol: 'fy2', labelLine1: fy2BaseLabel, labelLine2: 'Full fiscal year' },
    fy3: { id: 'fy3', kind: 'amount', amountKey: 'fy3', flowCol: 'fy3', labelLine1: fy3BaseLabel, labelLine2: 'Full fiscal year' },
    cagr: { id: 'cagr', kind: 'amount', amountKey: 'cagr', flowCol: undefined, labelLine1: 'CAGR', labelLine2: `${fy1BaseLabel} – ${fy3BaseLabel}`, isPct: true },
    delta_fy: { id: 'delta_fy', kind: 'delta', deltaKey: 'delta_fy', flowCol: 'fy3', labelLine1: `Δ ${fy3BaseLabel} − ${fy2BaseLabel}`, labelLine2: 'vs prior FY' },
    ytd_py: { id: 'ytd_py', kind: 'amount', amountKey: 'ytd_py', flowCol: 'ytd_py', labelLine1: ytdPyBaseLabel, labelLine2: 'Prior-year YTD' },
    ytd: { id: 'ytd', kind: 'amount', amountKey: 'ytd', flowCol: 'ytd', labelLine1: ytdBaseLabel, labelLine2: 'Year to date', highlighted: true },
    delta_ytd: { id: 'delta_ytd', kind: 'delta', deltaKey: 'delta_ytd', flowCol: 'ytd', labelLine1: `Δ ${ytdBaseLabel} − ${ytdPyBaseLabel}`, labelLine2: 'Actual vs prior YTD' },
    ltm_py: { id: 'ltm_py', kind: 'amount', amountKey: 'ltm_py', flowCol: 'ltm_py', labelLine1: ltmPyLabel, labelLine2: 'Prior-year LTM' },
    ltm: { id: 'ltm', kind: 'amount', amountKey: 'ltm', flowCol: 'ltm', labelLine1: ltmLabel, labelLine2: 'Rolling 12 months' },
    fy_f: { id: 'fy_f', kind: 'amount', amountKey: 'fy_f', flowCol: undefined, labelLine1: forecastLabel, labelLine2: 'Forecast FY' },
    delta_ltm: { id: 'delta_ltm', kind: 'delta', deltaKey: 'delta_ltm', flowCol: 'ltm', labelLine1: `Δ ${forecastLabel} − ${ltmPyLabel}`, labelLine2: 'Forecast vs prior LTM' },
    plan_cm: { id: 'plan_cm', kind: 'amount', amountKey: 'plan_cm', labelLine1: lbl?.plan_cm ?? labelPlanFy(year), labelLine2: 'Budget FY' },
    forecast: { id: 'forecast', kind: 'amount', amountKey: 'fy_f', flowCol: undefined, labelLine1: forecastLabel, labelLine2: 'Forecast FY' },
    coverage_pct: { id: 'coverage_pct', kind: 'amount', amountKey: 'coverage_pct', labelLine1: 'Coverage', labelLine2: 'YTD vs forecast %', isPct: true },
    fy25_proxy: { id: 'fy25_proxy', kind: 'amount', amountKey: 'fy_f', flowCol: undefined, labelLine1: labelForecastFy(year), labelLine2: 'Forecast FY' },
  }}, [lbl?.fy1, lbl?.fy2, lbl?.fy3, lbl?.ltm, lbl?.ltm_py, lbl?.fy_f, lbl?.ytd, lbl?.ytd_py, lbl?.plan_cm, year, fy1BaseLabel, fy2BaseLabel, fy3BaseLabel, ytdBaseLabel, ytdPyBaseLabel, month, currentYearTag])

  const availableTableColumns = useMemo(() => {
    const ids = ['fy1', 'fy2', 'fy3', 'cagr', 'delta_fy', 'ytd_py', 'ytd', 'delta_ytd', 'ltm_py', 'ltm', 'fy_f', 'delta_ltm', 'plan_cm', 'coverage_pct']
    const gated = hasPlanData ? ids : ids.filter(id => !PLAN_ONLY_COL_IDS.includes(id))
    return gated.map(id => catalog[id]).filter(Boolean)
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [catalog, hasPlanData])
  const tableColumns = useMemo(() => {
    const allowed = new Set(availableTableColumns.map(c => c.id))
    return tableColIds.filter(id => allowed.has(id)).map(id => catalog[id]).filter(Boolean)
  }, [tableColIds, catalog, availableTableColumns])
  const reportColumns = useMemo(
    () => buildAnnualFlowReportColumns(lbl, year, month, hasPlanData),
    [lbl, year, month, hasPlanData],
  )
  const effectiveViewMode: ErViewMode = viewMode
  const activeColumns = effectiveViewMode === 'report' ? reportColumns : tableColumns

  const clientNarrative = useMemo(
    () => (data?.rows ? buildAnnualFlowNarrativeResponse(data.rows, year, month, fy3BaseLabel, fy2BaseLabel, entity) : null),
    [data?.rows, year, month, fy3BaseLabel, fy2BaseLabel, entity],
  )

  const maxAbsByDeltaKey = useMemo(() => {
    if (!data?.rows?.length) return {} as Record<string, number>
    const nonKpi = collectNumericRows(data.rows).filter(r => r.row_kind !== 'kpi')
    const out: Record<string, number> = {}
    for (const c of activeColumns) {
      if (c.kind !== 'delta' || !c.deltaKey) continue
      out[c.deltaKey] = Math.max(1, ...nonKpi.map(r => Math.abs(Number((r.deltas ?? {})[c.deltaKey!] ?? 0))))
    }
    return out
  }, [data?.rows, activeColumns])

  const openDrill = (row: ErStatementRow, col: FlowCol, colLabel: string) => {
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

  const renderRow = (row: ErStatementRow, depth: number): JSX.Element => {
    const isTitle = row.row_kind === 'title'
    const isKpi = row.row_kind === 'kpi'
    const isSubtotal = row.row_kind === 'subtotal'
    const isAccount = row.row_kind === 'account'
    const isMarginKpiBold = isKpi && !!row.line_code && BOLD_MARGIN_KPI_CODES.has(row.line_code)
    const isOpen = checkOpen(row.id)
    const showChevron = (row.children?.length ?? 0) > 0 || (row.accounts?.length ?? 0) > 0
    const am = row.amounts ?? {}
    const deltas = row.deltas ?? {}

    if (isTitle) {
      return (
        <tr key={row.id} style={{ background: '#F8FAFC', borderTop: '1px solid #E2E8F0' }}>
          <td colSpan={activeColumns.length + 1} className="px-3 py-1.5 text-[12px] font-bold uppercase tracking-wide" style={{ color: '#1E3A5F' }}>
            {row.label}
          </td>
        </tr>
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
        <td className="py-1 text-left whitespace-nowrap" style={{ minWidth: 220, paddingLeft: 12 + depth * 14, paddingRight: 12 }}>
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

        {activeColumns.map(col => {
          if (col.kind === 'amount') {
            // CAGR: client-side computation from fy2/fy3
            if (col.id === 'cagr') {
              if (isKpi) return <td key={`${row.id}-cagr`} style={{ background: 'rgba(30,58,95,0.04)' }} />
              const fy1v = Number(am.fy1 ?? 0)
              const fy3v = Number(am.fy3 ?? 0)
              const cagr = Math.abs(fy1v) > 1e-3 ? (Math.pow(fy3v / fy1v, 1 / 2) - 1) * 100 : null
              return (
                <td
                  key={`${row.id}-cagr`}
                  className="px-1.5 py-1 text-right whitespace-nowrap tabular-nums"
                  style={{
                    background: 'rgba(30,58,95,0.04)',
                    fontSize: '0.8125rem',
                    fontWeight: (row.is_bold && !isKpi) || isMarginKpiBold ? 600 : 400,
                    color: cagr == null || cagr === 0 ? '#94A3B8' : cagr > 0 ? '#10B981' : '#DC2626',
                    fontStyle: 'italic',
                  }}
                >
                  {cagr == null ? '—' : `${cagr >= 0 ? '+' : ''}${cagr.toLocaleString('de-DE', { minimumFractionDigits: 1, maximumFractionDigits: 1 })}%`}
                </td>
              )
            }
            if (col.id === 'forecast' || col.id === 'fy_f') {
              const forecast = resolveForecastAmount(am)
              return (
                <ValCell
                  key={`${row.id}-forecast`}
                  value={forecast}
                  bold={(Boolean(row.is_bold) && !isKpi) || isMarginKpiBold}
                  italic={isKpi}
                  isPct={isKpi}
                  compact
                  onClick={row.drill && col.flowCol ? () => openDrill(row, col.flowCol!, col.labelLine1) : undefined}
                />
              )
            }
            if (IS_OVERVIEW_V2 && isKpi && col.id === 'coverage_pct') {
              return <td key={`${row.id}-${col.id}`} className="px-2.5 py-2" style={{ background: '#F8FAFC' }} />
            }
            const value = col.id === 'coverage_pct'
              ? resolveCoveragePct(am)
              : Number(am[col.amountKey ?? ''] ?? 0)
            return (
              <ValCell
                key={`${row.id}-${col.id}`}
                value={value}
                highlighted={Boolean(col.highlighted)}
                bold={(Boolean(row.is_bold) && !isKpi) || isMarginKpiBold}
                isPct={isKpi || Boolean(col.isPct)}
                italic={isKpi}
                compact
                onClick={row.drill && col.flowCol ? () => openDrill(row, col.flowCol!, col.labelLine1) : undefined}
              />
            )
          }
          const dKey = col.deltaKey ?? ''
          return (
            <DeltaCell
              key={`${row.id}-${col.id}`}
              value={Number(deltas[dKey] ?? 0)}
              maxAbs={isKpi ? 1 : (maxAbsByDeltaKey[dKey] ?? 1)}
              invert={isKpi ? false : row.invert_delta}
              isPct={isKpi || Boolean(col.isPct)}
              italic={isKpi}
              compact
              onClick={row.drill && col.flowCol ? () => openDrill(row, col.flowCol!, `${col.labelLine1}${col.labelLine2 ? ` vs ${col.labelLine2}` : ''}`) : undefined}
            />
          )
        })}
      </tr>
    )
  }

  const walkRows = (rows: ErStatementRow[], depth: number): JSX.Element[] => {
    const nodes: JSX.Element[] = []
    let kpiHeaderInserted = false
    for (const row of rows) {
      if (row.row_kind === 'kpi_header') {
        const anyKpi = rows.some(r => r.row_kind === 'kpi' && shouldDisplayErStatementRow(r))
        if (!anyKpi) continue
        kpiHeaderInserted = true
        nodes.push(
          <tr key={row.id} style={{ background: '#F8FAFC', borderTop: '2px solid #E2E8F0' }}>
            <td className="px-3 py-1 text-[12px] font-semibold" style={{ color: '#1E3A5F', fontStyle: 'italic' }}>
              KPIs — as % of total output
            </td>
            {activeColumns.map(col => (
              <td key={col.id} style={{ background: col.highlighted || col.id === 'cagr' ? 'rgba(30,58,95,0.04)' : '#F8FAFC' }} />
            ))}
          </tr>,
        )
        continue
      }
      if (row.row_kind === 'kpi' && !kpiHeaderInserted && shouldDisplayErStatementRow(row)) {
        kpiHeaderInserted = true
        nodes.push(
          <tr key="er-kpi-header" style={{ background: '#F8FAFC', borderTop: '2px solid #E2E8F0' }}>
            <td className="px-3 py-1 text-[12px] font-semibold" style={{ color: '#1E3A5F', fontStyle: 'italic' }}>
              KPIs — as % of total output
            </td>
            {activeColumns.map(col => (
              <td key={col.id} style={{ background: col.highlighted || col.id === 'cagr' ? 'rgba(30,58,95,0.04)' : '#F8FAFC' }} />
            ))}
          </tr>,
        )
      }
      if (!shouldDisplayErStatementRow(row)) continue
      nodes.push(renderRow(row, depth))
      if (row.row_kind === 'title') continue
      if (!checkOpen(row.id)) continue
      for (const ch of row.children ?? []) nodes.push(...walkRows([ch], depth + 1))
      for (const acc of row.accounts ?? []) nodes.push(...walkRows([acc], depth + 1))
    }
    return nodes
  }

  const titles: Record<string, string> = { pl: 'Income statement', cf: 'Cash flow statement' }
  const tableTitle = stmtCfg.cardTitle || (titles[statementKey] ?? 'Statement')
  const periodBadge = lbl?.ytd ?? ''

  async function handleExport(kind: PlExportKind) {
    if (!data) return
    if (kind === 'pdf') {
      // No dedicated PDF exporter for ErFlowResponse yet — fall back to browser print.
      window.print()
      return
    }
    const exportColumns = effectiveViewMode === 'report' ? reportColumns : activeColumns
    const amountKeys = exportColumns.filter(c => c.kind === 'amount').map(c => c.amountKey!).filter(Boolean)
    const deltaKeys = exportColumns.filter(c => c.kind === 'delta').map(c => c.deltaKey!).filter(Boolean)
    const rows = flattenTree(data.rows, amountKeys, deltaKeys, { isRowOpen: checkOpen })
    const headers = ['EURk', ...exportColumns.map(c => c.kind === 'delta' && c.labelLine2 ? `${c.labelLine1} ${c.labelLine2}` : c.labelLine1)]
    const columnKinds = [''].concat(exportColumns.map(c => (c.kind === 'delta' ? 'delta' : c.id.includes('ytd') ? 'ytd' : '')))
    const base = `ER_${tableTitle.replace(/ /g, '_')}_${todayStr()}`
    if (kind === 'pptx') {
      await exportFlatTablePptx({
        fileName: `${base}.pptx`,
        pageTitle: tableTitle,
        tableHeading: tableTitle,
        breadcrumbCurrent: 'Exit Readiness',
        footerRight: 'Values in EURk',
        headers,
        columnKinds,
        rows,
      })
      return
    }
    await exportToXlsx({
      title: `Exit Readiness — ${tableTitle}`,
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
            {effectiveViewMode === 'report' ? stmtCfg.reportSubtitle : stmtCfg.tableSubtitle}
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          <PlViewToggleButton mode={viewMode} onChange={setViewMode} disabled={loading} />
          {effectiveViewMode === 'table' && (
            <button
              type="button"
              title="Build table columns"
              onClick={() => setColumnEditorOpen(true)}
              className={STATEMENT_TOOLBAR_ICON_BTN}
              style={STATEMENT_TOOLBAR_BTN_STYLE}
            >
              <Pencil size={14} strokeWidth={1.75} />
            </button>
          )}
          {notesCtx && pinId && (
            <button
              type="button"
              title="Pin to Action Board"
              className={STATEMENT_TOOLBAR_ICON_BTN}
              style={STATEMENT_TOOLBAR_BTN_STYLE}
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

      {effectiveViewMode === 'report' && data ? (
        <ErFlowReportView
          data={data}
          statement={statementKey}
          year={year}
          month={month}
          entity={entity}
          periodSelection={periodSelection}
          entityDisplayName={entityDisplayName}
          reportColumns={reportColumns}
          clientNarrative={clientNarrative}
          onDrill={onDrill}
          checkOpen={checkOpen}
          toggle={toggle}
          fy2Label={fy2BaseLabel}
          fy3Label={fy3BaseLabel}
        />
      ) : (
        <div className="overflow-x-auto px-6 py-4">
          <table className="w-full border-collapse text-[12px]">
            <thead>
              <tr style={{ borderBottom: '2px solid #E2E8F0', background: '#F8FAFC', verticalAlign: 'bottom' }}>
                <th className="px-3 py-2 text-left font-semibold text-[13px]" style={{ color: '#475569' }}>EURk</th>
                {tableColumns.map(c => (
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

      {columnEditorOpen && effectiveViewMode === 'table' && (
        <>
          <div className="fixed inset-0 z-40 bg-slate-900/20" onClick={() => setColumnEditorOpen(false)} aria-hidden />
          <div className="fixed inset-y-0 right-0 z-50 w-full max-w-md shadow-2xl flex flex-col" style={{ background: '#fff', borderLeft: '1px solid #E2E8F0' }}>
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
              <section>
                <p className="text-xs font-semibold text-slate-700 mb-2">Column order ({tableColumns.length})</p>
                {tableColumns.length === 0 ? (
                  <p className="text-xs text-slate-500">No columns selected.</p>
                ) : (
                  <ul className="space-y-1">
                    {tableColumns.map((c, idx) => (
                      <li
                        key={c.id}
                        draggable
                        onDragStart={() => setDragIdx(idx)}
                        onDragOver={e => e.preventDefault()}
                        onDrop={() => {
                          if (dragIdx == null || dragIdx === idx) return
                          const next = [...tableColIds]
                          const [item] = next.splice(dragIdx, 1)
                          next.splice(idx, 0, item)
                          setDragIdx(null)
                          setTableColIds(next)
                          saveTableCols(statementKey, next)
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
                              const next = [...tableColIds]
                              const [item] = next.splice(idx, 1)
                              next.splice(idx - 1, 0, item)
                              setTableColIds(next)
                              saveTableCols(statementKey, next)
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
                              if (idx === tableColumns.length - 1) return
                              const next = [...tableColIds]
                              const [item] = next.splice(idx, 1)
                              next.splice(idx + 1, 0, item)
                              setTableColIds(next)
                              saveTableCols(statementKey, next)
                            }}
                            disabled={idx === tableColumns.length - 1}
                            className="p-1 text-slate-500 disabled:opacity-30"
                            aria-label="Move down"
                          >
                            <ChevronDown size={14} />
                          </button>
                          <button
                            type="button"
                            onClick={() => {
                              const next = tableColIds.filter(id => id !== c.id)
                              setTableColIds(next)
                              saveTableCols(statementKey, next)
                            }}
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

              {ANNUAL_COLUMN_GROUPS.map(group => {
                const groupCols = group.ids
                  .map(id => availableTableColumns.find(c => c.id === id))
                  .filter((c): c is ErColDef => Boolean(c))
                if (groupCols.length === 0) return null
                return (
                  <AnnualEditorSection key={group.title} title={group.title}>
                    <div className="flex flex-wrap gap-1.5">
                      {groupCols.map(c => (
                        <button
                          key={c.id}
                          type="button"
                          disabled={tableColIds.includes(c.id)}
                          onClick={() => {
                            const next = [...tableColIds, c.id]
                            setTableColIds(next)
                            saveTableCols(statementKey, next)
                          }}
                          className="px-2 py-1.5 rounded-md text-[0.7rem] text-left disabled:opacity-40 hover:bg-slate-50"
                          style={{ border: '1px solid #E2E8F0', color: '#475569' }}
                        >
                          {c.labelLine1}
                        </button>
                      ))}
                    </div>
                  </AnnualEditorSection>
                )
              })}

              <button
                type="button"
                onClick={() => {
                  setTableColIds(defaultTableColIds)
                  saveTableCols(statementKey, defaultTableColIds)
                }}
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
