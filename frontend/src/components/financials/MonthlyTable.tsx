import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { ChevronRight } from 'lucide-react'
import { MonthlyRow, MonthlyResponse, type MonthlyTotal, type FinancialStatementRow } from '../../lib/api'
import { useOptionalActionNotesContext } from '../action-notes/ActionNotesContext'
import { captureMonthlySnapshot } from '../action-notes/captureMonthlyTable'
import { computeAutoExpandedIds } from './statementRowExpansion'
import { fmtKpi, fmtPct, fmtDays } from '../../lib/fmt'
import PlMonthlyDetailPanel, { type MonthlyCellSelection } from './pl-two-view/PlMonthlyDetailPanel'
import MonthlyColumnEditor from './pl-two-view/MonthlyColumnEditor'
import PlExportMenu, { type PlExportKind } from './pl-two-view/PlExportMenu'
import {
  detailPeriodFromColumn,
  loadMonthlyColumns,
  reconcileMonthlyColumns,
  resolveMonthlyColumnValue,
  type MonthlyViewColumnDef,
} from './pl-two-view/monthlyColumnRegistry'
import { exportMonthlyTablePptx, exportMonthlyTableXlsx } from './pl-two-view/monthlyExport'
import {
  isMonthlyRowClickable as isPlMonthlyRowClickable,
  monthlyPeriodKey,
  monthlyRowToLineCode as plMonthlyRowToLineCode,
} from './pl-two-view/plMonthlyLineCode'
import {
  isMonthlyRowClickable as isBsMonthlyRowClickable,
  monthlyRowToLineCode as bsMonthlyRowToLineCode,
} from './statement-two-view/bs/bsMonthlyLineCode'
import {
  isMonthlyRowClickable as isCfMonthlyRowClickable,
  monthlyRowToLineCode as cfMonthlyRowToLineCode,
} from './statement-two-view/cf/cfMonthlyLineCode'
import {
  isMonthlyRowClickable as isWcMonthlyRowClickable,
  isWcMonthlyBoldRow,
  monthlyRowToLineCode as wcMonthlyRowToLineCode,
} from './statement-two-view/wc/wcMonthlyLineCode'
import type { FinStatementKind } from './statement-two-view/statementTypes'
import { periodLabel, priorPeriod } from './pl-two-view/plPeriodLabels'
import { useMonthlyPlanMaps } from './pl-two-view/useMonthlyPlanMaps'
import {
  FIN_REPORT_SPLIT_GRID,
  FIN_TABLE_CELL_CLASS,
  FIN_TABLE_VALUE_FONT,
} from './finReportLayout'

const STATEMENT_TITLES: Record<string, string> = {
  pl: 'Income statement — monthly view',
  bs: 'Balance sheet — monthly view',
  cf: 'Cash flow statement — monthly view',
  wc: 'Working capital — monthly view',
}

function NumCell({
  value,
  bold = false,
  isCurrent = false,
  isKpi = false,
  isDays = false,
  isExtra = false,
  clickable = false,
  selected = false,
  onClick,
}: {
  value: number
  bold?: boolean
  isCurrent?: boolean
  isKpi?: boolean
  isDays?: boolean
  isExtra?: boolean
  clickable?: boolean
  selected?: boolean
  onClick?: () => void
}) {
  return (
    <td
      className={`${FIN_TABLE_CELL_CLASS} text-right whitespace-nowrap tabular-nums ${
        clickable ? 'cursor-pointer hover:bg-slate-100/80' : ''
      }`}
      style={{
        background: selected
          ? 'rgba(30,58,95,0.12)'
          : isCurrent
            ? 'rgba(30,58,95,0.06)'
            : isExtra
              ? 'rgba(30,58,95,0.03)'
              : undefined,
        fontWeight: bold ? 600 : 400,
        fontSize: FIN_TABLE_VALUE_FONT,
        color: isKpi ? '#475569' : '#111827',
        fontStyle: isKpi ? 'italic' : undefined,
        borderLeft: isExtra || isCurrent || selected ? '1px solid rgba(30,58,95,0.12)' : undefined,
        borderRight: isCurrent || selected ? '1px solid rgba(30,58,95,0.12)' : undefined,
        outline: selected ? '2px solid rgba(30,58,95,0.35)' : undefined,
        outlineOffset: selected ? -2 : undefined,
      }}
      onClick={clickable ? onClick : undefined}
      role={clickable ? 'button' : undefined}
      tabIndex={clickable ? 0 : undefined}
      onKeyDown={
        clickable
          ? e => {
              if (e.key === 'Enter' || e.key === ' ') {
                e.preventDefault()
                onClick?.()
              }
            }
          : undefined
      }
    >
      {isKpi ? (isDays ? fmtDays(value) : fmtPct(value)) : fmtKpi(value)}
    </td>
  )
}

interface MonthlyTableProps {
  data: MonthlyResponse | null
  loading: boolean
  error?: string | null
  entity?: string
  enableCellDetail?: boolean
  showColumnEditor?: boolean
  /** Annual grain: highlight anchor month per FY on non-PL statements; FY/YTD total columns always styled */
  annualGrain?: boolean
}

export default function MonthlyTable({
  data,
  loading,
  error,
  entity,
  enableCellDetail = false,
  showColumnEditor = false,
  annualGrain = false,
}: MonthlyTableProps) {
  const [userToggles, setUserToggles] = useState<Set<string>>(() => new Set())
  const [selection, setSelection] = useState<MonthlyCellSelection | null>(null)
  const statement = (data?.statement ?? 'pl') as FinStatementKind
  const [extraColumns, setExtraColumns] = useState<MonthlyViewColumnDef[]>(() =>
    loadMonthlyColumns(statement),
  )

  useEffect(() => {
    if (!data?.statement) return
    setExtraColumns(loadMonthlyColumns(data.statement))
  }, [data?.statement])
  const monthlyRowToLineCodeFn =
    statement === 'bs'
      ? bsMonthlyRowToLineCode
      : statement === 'wc'
        ? wcMonthlyRowToLineCode
        : statement === 'cf'
          ? cfMonthlyRowToLineCode
          : plMonthlyRowToLineCode

  const monthlyRowToLineCode = monthlyRowToLineCodeFn
  const isMonthlyRowClickable =
    statement === 'bs'
      ? isBsMonthlyRowClickable
      : statement === 'wc'
        ? isWcMonthlyRowClickable
        : statement === 'cf'
          ? isCfMonthlyRowClickable
          : isPlMonthlyRowClickable

  const periods = data?.periods ?? []
  const totals = data?.totals ?? []

  // ─── Interleaved display columns (periods + optional FY/YTD totals) ──────
  type DisplayCol =
    | { type: 'period'; period: (typeof periods)[0] }
    | { type: 'total'; total: MonthlyTotal }

  const displayCols = useMemo((): DisplayCol[] => {
    if (!totals.length) return periods.map(p => ({ type: 'period' as const, period: p }))
    const skipFyTotals = annualGrain && (statement === 'bs' || statement === 'wc')
    const cols: DisplayCol[] = []
    for (let i = 0; i < periods.length; i++) {
      const p = periods[i]
      cols.push({ type: 'period', period: p })
      const next = periods[i + 1]
      const isLastInYear = !next || next.year !== p.year
      if (isLastInYear && !skipFyTotals) {
        const matchingTotal = totals.find(t => t.year === p.year)
        if (matchingTotal) cols.push({ type: 'total', total: matchingTotal })
      }
    }
    return cols
  // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [data, annualGrain, statement])

  const reconciledExtras = useMemo(
    () => (data ? reconcileMonthlyColumns(extraColumns, periods) : extraColumns),
    [extraColumns, data, periods],
  )

  const notesCtx = useOptionalActionNotesContext()

  // ─── Pin registration ──────────────────────────────────────────────────────
  useEffect(() => {
    const stmt = data?.statement ?? 'pl'
    const pinId = `${stmt}-monthly`
    if (!notesCtx || !data?.rows?.length) {
      notesCtx?.unregisterTableCandidate(pinId)
      return
    }
    const visibleColumnKeys = displayCols.map(dc =>
      dc.type === 'period'
        ? `${String(dc.period.year)}-${String(dc.period.month).padStart(2, '0')}`
        : dc.total.key,
    )
    const label = STATEMENT_TITLES[stmt] ?? 'Monthly view'
    notesCtx.registerTableCandidate({
      id: pinId,
      label,
      description: 'Monthly column view — amounts in EURk',
      capture: () => captureMonthlySnapshot(data, 'MonthlyTable', visibleColumnKeys),
    })
    return () => notesCtx.unregisterTableCandidate(pinId)
  }, [notesCtx, data, displayCols])

  const planPeriodKeys = useMemo(() => {
    const keys = new Set<string>()
    for (const c of reconciledExtras) {
      if (c.kind === 'agg_vs_plan') {
        for (const k of c.periodKeys) keys.add(k)
      }
    }
    return [...keys]
  }, [reconciledExtras])

  const planByPeriod = useMonthlyPlanMaps(planPeriodKeys, entity, enableCellDetail)

  useEffect(() => {
    setSelection(null)
  }, [data?.year, data?.month, data?.statement, entity])

  useEffect(() => {
    if (!data?.periods.length) return
    setExtraColumns(prev => reconcileMonthlyColumns(prev, data.periods))
  }, [data?.periods])

  const autoExpandedIds = useMemo(
    () => computeAutoExpandedIds(data?.rows as FinancialStatementRow[] | undefined, data?.statement),
    [data],
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

  const openMonthSelection = useCallback(
    (row: MonthlyRow, year: number, month: number, periodLabel: string) => {
      const lineCode = monthlyRowToLineCode(row)
      if (!lineCode) return

      const pk = monthlyPeriodKey(year, month)
      const amountKeur = row.amounts?.[pk]
      const pm = priorPeriod(year, month)
      const pmPk = monthlyPeriodKey(pm.year, pm.month)
      const pmAmount = row.amounts?.[pmPk]
      const lineMomKeur =
        amountKeur != null && pmAmount != null ? amountKeur - pmAmount : undefined

      const next: MonthlyCellSelection = {
        rowId: row.id,
        lineCode,
        label: row.label,
        year,
        month,
        periodLabel,
        amountKeur,
        lineMomKeur,
        anchorYear: data?.year,
        anchorMonth: data?.month,
      }

      if (
        selection?.rowId === next.rowId &&
        selection.year === next.year &&
        selection.month === next.month &&
        !selection.columnId
      ) {
        setSelection(null)
        return
      }
      setSelection(next)
    },
    [selection, data?.year, data?.month],
  )

  const openExtraSelection = useCallback(
    (row: MonthlyRow, col: MonthlyViewColumnDef) => {
      const lineCode = monthlyRowToLineCode(row)
      const period = detailPeriodFromColumn(col)
      if (!lineCode || !period) return

      const amountKeur =
        resolveMonthlyColumnValue(row, col, planByPeriod, monthlyRowToLineCodeFn) ?? undefined

      const next: MonthlyCellSelection = {
        rowId: row.id,
        lineCode,
        label: row.label,
        year: period.year,
        month: period.month,
        periodLabel: col.labelLine1,
        amountKeur,
        columnId: col.id,
        anchorYear: data?.year,
        anchorMonth: data?.month,
      }

      if (
        selection?.rowId === next.rowId &&
        selection.columnId === next.columnId
      ) {
        setSelection(null)
        return
      }
      setSelection(next)
    },
    [selection, planByPeriod, data?.year, data?.month],
  )

  const handleExport = useCallback(
    async (kind: PlExportKind) => {
      if (!data) return
      const title = STATEMENT_TITLES[data.statement] ?? 'Monthly View'
      if (kind === 'pptx') {
        await exportMonthlyTablePptx(data, reconciledExtras, planByPeriod, title, 'Monthly view — amounts in EURk', checkOpen)
        return
      }
      await exportMonthlyTableXlsx(data, reconciledExtras, planByPeriod, title, checkOpen)
    },
    [data, reconciledExtras, planByPeriod, checkOpen],
  )

  const scrollContainerRef = useRef<HTMLDivElement>(null)

  // Scroll to the right when data (or totals) change so newest columns are visible
  useEffect(() => {
    const el = scrollContainerRef.current
    if (!el) return
    el.scrollLeft = el.scrollWidth
  }, [data])

  if (loading) {
    return (
      <div
        className="rounded-xl p-8 text-center text-sm mt-4"
        style={{ background: '#FFF', border: '1px solid #E2E8F0', color: '#64748B' }}
      >
        Loading monthly view…
      </div>
    )
  }
  if (error) {
    return (
      <div
        className="rounded-xl p-8 text-center text-sm mt-4"
        style={{ background: '#FFF', border: '1px solid #FECACA', color: '#B91C1C' }}
      >
        {error}
      </div>
    )
  }
  if (!data?.rows.length) return null

  const currentPk = monthlyPeriodKey(data.year, data.month)
  const tableTitle = STATEMENT_TITLES[data.statement] ?? 'Financial statement — monthly view'
  const detailOpen = enableCellDetail && selection != null
  const hasExtras = reconciledExtras.length > 0
  const totalCols = 1 + displayCols.length + reconciledExtras.length

  function isAnnualYearEndMonth(y: number, m: number): boolean {
    if (!data) return false
    if (y === data.year) return m === data.month
    return m === 12
  }

  function isAnnualHighlightPeriod(y: number, m: number): boolean {
    // PL & CF: only FY total columns are highlighted, not period / anchor month columns.
    if (statement === 'pl' || statement === 'cf') return false
    return annualGrain && isAnnualYearEndMonth(y, m)
  }

  function isPeriodColumnHighlighted(y: number, m: number, pk: string): boolean {
    if (statement === 'cf') return false
    return pk === currentPk || isAnnualHighlightPeriod(y, m)
  }

  function isCfFyTotalColumn(total: MonthlyTotal): boolean {
    return statement === 'cf' && total.kind === 'fy'
  }

  function totalColumnBackground(total: MonthlyTotal): string | undefined {
    if (statement === 'cf') {
      return total.kind === 'fy' ? 'rgba(30,58,95,0.06)' : undefined
    }
    const highlightTotal = annualGrain && total.kind === 'ytd' && statement === 'pl'
    return highlightTotal ? 'rgba(30,58,95,0.08)' : 'rgba(30,58,95,0.06)'
  }

  function renderRow(row: MonthlyRow, depth: number): JSX.Element {
    const pad = 12 + depth * 14
    const isTitle = row.row_kind === 'title'
    const isKpiHeader = row.row_kind === 'kpi_header'
    const isKpi = row.row_kind === 'kpi'
    const isOpen = checkOpen(row.id)
    const showChevron = (row.children?.length ?? 0) > 0 || (row.accounts?.length ?? 0) > 0
    const rowClickable = enableCellDetail && isMonthlyRowClickable(row)

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
          <td
            className="px-3 py-2 text-xs font-semibold"
            style={{ color: '#1E3A5F', fontStyle: 'italic', paddingLeft: 12 }}
          >
            {row.label}
          </td>
          {displayCols.map(dc => {
            if (dc.type === 'period') {
              const pk = monthlyPeriodKey(dc.period.year, dc.period.month)
              const highlight = isPeriodColumnHighlighted(dc.period.year, dc.period.month, pk)
              return (
                <td
                  key={pk}
                  style={{
                    background: highlight ? 'rgba(30,58,95,0.04)' : '#F8FAFC',
                    borderLeft: highlight ? '1px solid rgba(30,58,95,0.12)' : undefined,
                    borderRight: highlight ? '1px solid rgba(30,58,95,0.12)' : undefined,
                  }}
                />
              )
            }
            const fyTotal = isCfFyTotalColumn(dc.total)
            return (
              <td
                key={`kpihdr-total-${dc.total.key}`}
                style={{
                  background: totalColumnBackground(dc.total) ?? '#F8FAFC',
                  borderLeft: fyTotal ? '2px solid rgba(30,58,95,0.15)' : '1px solid rgba(30,58,95,0.08)',
                }}
              />
            )
          })}
          {reconciledExtras.map(c => (
            <td key={`${row.id}-${c.id}`} style={{ background: '#F8FAFC', borderLeft: '1px solid rgba(30,58,95,0.08)' }} />
          ))}
        </tr>
      )
    }

    const isSubtotal = row.row_kind === 'subtotal'
    const isWc = statement === 'wc'
    const rowBold = isWc ? isWcMonthlyBoldRow(row) : row.is_bold || isSubtotal
    const wcMappingLine = isWc && !rowBold && !isKpi && row.row_kind !== 'account'

    return (
      <tr
        key={row.id}
        style={{
          borderBottom: isKpi ? 'none' : '1px solid #E2E8F0',
          borderTop: isWc ? (rowBold && depth === 0 ? '2px solid #E2E8F0' : undefined) : isSubtotal && depth === 0 ? '2px solid #E2E8F0' : undefined,
          background: isKpi ? '#F8FAFC' : isWc ? (rowBold && depth === 0 ? '#F8FAFC' : undefined) : isSubtotal && depth === 0 ? '#F8FAFC' : undefined,
          fontStyle: isKpi ? 'italic' : undefined,
        }}
      >
        <td
          className={`${FIN_TABLE_CELL_CLASS} text-left whitespace-nowrap`}
          style={{ minWidth: 168, paddingLeft: pad, paddingRight: 8, fontSize: FIN_TABLE_VALUE_FONT }}
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
              className={`text-xs ${rowBold ? '' : 'font-normal'}`}
              style={{
                fontWeight: rowBold ? 600 : 400,
                color: isKpi
                  ? '#64748B'
                  : row.row_kind === 'account' || wcMappingLine
                    ? '#475569'
                    : '#111827',
                fontStyle: isKpi ? 'italic' : undefined,
              }}
            >
              {row.label}
            </span>
          </div>
        </td>

        {displayCols.map(dc => {
          if (dc.type === 'period') {
            const p = dc.period
            const pk = monthlyPeriodKey(p.year, p.month)
            const isSelected =
              selection?.rowId === row.id &&
              selection.year === p.year &&
              selection.month === p.month &&
              !selection.columnId
            return (
              <NumCell
                key={pk}
                value={row.amounts?.[pk] ?? 0}
                bold={rowBold}
                isCurrent={isPeriodColumnHighlighted(p.year, p.month, pk)}
                isKpi={isKpi}
                isDays={isKpi && data!.statement === 'wc'}
                clickable={rowClickable && !isKpi}
                selected={isSelected}
                onClick={() => openMonthSelection(row, p.year, p.month, periodLabel(p.year, p.month))}
              />
            )
          }
          // Total column (FY or YTD)
          const total = dc.total
          const rawV = row.amounts?.[total.key]
          const totalBg = totalColumnBackground(total)
          const fyTotal = isCfFyTotalColumn(total)
          if (isKpi) {
            return (
              <td
                key={`total-${total.key}`}
                className={`${FIN_TABLE_CELL_CLASS} text-right whitespace-nowrap tabular-nums`}
                style={{
                  background: totalBg,
                  borderLeft: fyTotal ? '2px solid rgba(30,58,95,0.15)' : '1px solid rgba(30,58,95,0.08)',
                  fontSize: FIN_TABLE_VALUE_FONT,
                  color: '#475569',
                  fontStyle: 'italic',
                }}
              >
                {rawV != null ? fmtPct(rawV) : '—'}
              </td>
            )
          }
          return (
            <td
              key={`total-${total.key}`}
              className={`${FIN_TABLE_CELL_CLASS} text-right whitespace-nowrap tabular-nums`}
              style={{
                background: totalBg,
                borderLeft: fyTotal ? '2px solid rgba(30,58,95,0.15)' : '1px solid rgba(30,58,95,0.08)',
                fontWeight: rowBold ? 600 : 400,
                fontSize: FIN_TABLE_VALUE_FONT,
                color: '#111827',
              }}
            >
              {rawV != null ? fmtKpi(rawV) : '—'}
            </td>
          )
        })}

        {reconciledExtras.map((col, idx) => {
          const v = resolveMonthlyColumnValue(row, col, planByPeriod, monthlyRowToLineCodeFn) ?? 0
          const isSelected = selection?.rowId === row.id && selection.columnId === col.id
          return (
            <NumCell
              key={col.id}
              value={v}
              bold={rowBold}
              isKpi={isKpi}
              isDays={isKpi && data!.statement === 'wc'}
              isExtra={idx === 0}
              clickable={rowClickable && !isKpi}
              selected={isSelected}
              onClick={() => openExtraSelection(row, col)}
            />
          )
        })}
      </tr>
    )
  }

  function walkRows(rows: MonthlyRow[], depth: number): JSX.Element[] {
    const nodes: JSX.Element[] = []
    let kpiHeaderInserted = depth > 0
    for (const row of rows) {
      if (row.row_kind === 'kpi_header') {
        kpiHeaderInserted = true
        nodes.push(renderRow(row, depth))
        continue
      }
      if (
        row.row_kind === 'kpi'
        && !kpiHeaderInserted
        && data?.statement === 'bs'
      ) {
        kpiHeaderInserted = true
        nodes.push(
          <tr key="monthly-bs-kpi-header" style={{ background: '#F8FAFC', borderTop: '2px solid #E2E8F0' }}>
            <td
              className="px-3 py-2 text-xs font-semibold"
              style={{ color: '#1E3A5F', fontStyle: 'italic', paddingLeft: 12 }}
            >
              KPIs
            </td>
            {displayCols.map(dc => {
              if (dc.type === 'period') {
                const pk = monthlyPeriodKey(dc.period.year, dc.period.month)
                const highlight = isPeriodColumnHighlighted(dc.period.year, dc.period.month, pk)
                return (
                  <td
                    key={pk}
                    style={{
                      background: highlight ? 'rgba(30,58,95,0.04)' : '#F8FAFC',
                      borderLeft: highlight ? '1px solid rgba(30,58,95,0.12)' : undefined,
                      borderRight: highlight ? '1px solid rgba(30,58,95,0.12)' : undefined,
                    }}
                  />
                )
              }
              const fyTotal = isCfFyTotalColumn(dc.total)
              return (
                <td
                  key={`kpihdr-total-${dc.total.key}`}
                  style={{
                    background: totalColumnBackground(dc.total) ?? '#F8FAFC',
                    borderLeft: fyTotal ? '2px solid rgba(30,58,95,0.15)' : '1px solid rgba(30,58,95,0.08)',
                  }}
                />
              )
            })}
            {reconciledExtras.map(c => (
              <td key={`kpi-hdr-${c.id}`} style={{ background: '#F8FAFC', borderLeft: '1px solid rgba(30,58,95,0.08)' }} />
            ))}
          </tr>,
        )
      }
      nodes.push(renderRow(row, depth))
      if (!checkOpen(row.id)) continue
      if (row.children?.length) {
        for (const ch of row.children) nodes.push(...walkRows([ch], depth + 1))
      }
      const accounts = row.accounts
      if (accounts?.length) {
        for (const acc of accounts) nodes.push(...walkRows([acc], depth + 1))
      }
    }
    return nodes
  }

  function renderMonthlyTable() {
    return (
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr style={{ borderBottom: '2px solid #E2E8F0', background: '#F8FAFC' }}>
            <th
              className={`${FIN_TABLE_CELL_CLASS} text-left font-semibold`}
              style={{ color: '#64748B', minWidth: 168, fontSize: FIN_TABLE_VALUE_FONT }}
            >
              EURk
            </th>
            {displayCols.map(dc => {
              if (dc.type === 'period') {
                const p = dc.period
                const pk = monthlyPeriodKey(p.year, p.month)
                const highlight = isPeriodColumnHighlighted(p.year, p.month, pk)
                return (
                  <th
                    key={pk}
                    className={`${FIN_TABLE_CELL_CLASS} text-right font-semibold whitespace-nowrap`}
                    style={{
                      color: highlight ? '#1E3A5F' : '#64748B',
                      fontSize: FIN_TABLE_VALUE_FONT,
                      background: highlight ? 'rgba(30,58,95,0.06)' : undefined,
                      fontWeight: highlight ? 700 : 600,
                      borderLeft: highlight ? '1px solid rgba(30,58,95,0.12)' : undefined,
                      borderRight: highlight ? '1px solid rgba(30,58,95,0.12)' : undefined,
                    }}
                  >
                    {periodLabel(p.year, p.month)}
                  </th>
                )
              }
              // Total column header
              const total = dc.total
              const fyTotal = isCfFyTotalColumn(total)
              return (
                <th
                  key={`total-hdr-${total.key}`}
                  className={`${FIN_TABLE_CELL_CLASS} text-right font-bold whitespace-nowrap`}
                  style={{
                    color: '#1E3A5F',
                    fontSize: FIN_TABLE_VALUE_FONT,
                    background: totalColumnBackground(total),
                    borderLeft: fyTotal ? '2px solid rgba(30,58,95,0.15)' : '1px solid rgba(30,58,95,0.08)',
                  }}
                >
                  {total.label}
                </th>
              )
            })}
            {reconciledExtras.map((col, idx) => (
              <th
                key={col.id}
                className={`${FIN_TABLE_CELL_CLASS} text-right font-semibold whitespace-nowrap max-w-[120px]`}
                style={{
                  color: '#1E3A5F',
                  fontSize: FIN_TABLE_VALUE_FONT,
                  background: 'rgba(30,58,95,0.04)',
                  borderLeft: idx === 0 ? '2px solid rgba(30,58,95,0.15)' : '1px solid rgba(30,58,95,0.08)',
                }}
                title={col.labelLine1}
              >
                <span className="block truncate">{col.labelLine1}</span>
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{walkRows(data!.rows, 0)}</tbody>
      </table>
    )
  }

  return (
    <div
      className="rounded-xl mt-4 overflow-hidden flex flex-col"
      style={{
        background: '#FFFFFF',
        border: '1px solid #E2E8F0',
        boxShadow: '0 1px 3px rgba(0,0,0,0.05)',
        minHeight: detailOpen ? 480 : undefined,
      }}
    >
      <div
        className="px-4 pt-4 pb-3 flex items-start justify-between gap-3 shrink-0"
        style={{ borderBottom: '1px solid #F1F5F9' }}
      >
        <div className="min-w-0 flex-1">
          <span className="text-sm font-semibold" style={{ color: '#111827' }}>
            {tableTitle}
          </span>
          <p className="text-xs mt-0.5" style={{ color: '#94A3B8' }}>
            {totals.length > 0
              ? `${periods.length} months + FY totals — amounts in EURk`
              : 'Last 12 months — amounts in EURk'}
            {enableCellDetail ? ' · Click a cell for account detail' : ''}
            {hasExtras ? ` · ${reconciledExtras.length} custom column${reconciledExtras.length > 1 ? 's' : ''}` : ''}
          </p>
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {showColumnEditor && (
            <MonthlyColumnEditor
              periods={periods}
              columns={extraColumns}
              onChange={setExtraColumns}
              statement={statement}
              showPlanVariances={statement === 'pl'}
            />
          )}
          <PlExportMenu formats={['pptx', 'xlsx']} onExport={handleExport} disabled={!data} />
        </div>
      </div>

      {enableCellDetail ? (
        <div className="px-4 pt-2 pb-6">
          {detailOpen && selection ? (
            <div className={FIN_REPORT_SPLIT_GRID}>
              <div className="min-w-0 overflow-x-auto">{renderMonthlyTable()}</div>
              <div className="min-w-0">
                <PlMonthlyDetailPanel
                  selection={selection}
                  entity={entity}
                  statement={statement}
                  onClose={() => setSelection(null)}
                  columnLayout
                />
              </div>
            </div>
          ) : (
            <div className="overflow-x-auto w-full">{renderMonthlyTable()}</div>
          )}
        </div>
      ) : (
        <div ref={scrollContainerRef} className="overflow-x-auto px-4 pb-4">{renderMonthlyTable()}</div>
      )}
    </div>
  )
}
