import type { ReactNode } from 'react'
import type { FinancialStatementColLabels, FinancialStatementResponse, FinancialStatementRow } from '../../../lib/api'
import type { FinancialsDrillOpen } from '../FinancialStatementTable'
import {
  BlankValCell,
  DeltaCell,
  ExpandChevron,
  ValCell,
  collectNumericRows,
  headerCellBackground,
  isReportPeriodHighlightColumn,
  periodRange,
  type ValueCol,
} from './plTableCore'
import type { PlPlanMap } from './usePlStatementData'
import type { PlTableColumnDef } from './plColumnRegistry'
import { KPI_TABLE_COLUMN_KINDS, resolveCellValue } from './plColumnRegistry'
import type { MonthlyResponse } from '../../../lib/api'
import PlCommentIndexBadge from './PlCommentIndexBadge'
import type { ReportCommentMarkerMap } from '../statement-two-view/reportCommentMarkers'

/** Expandable children/accounts: largest CM first (PL); BS/WC keep backend order. */
function sortRowsByCmDesc(rows: FinancialStatementRow[]): FinancialStatementRow[] {
  return [...rows].sort(
    (a, b) => Math.abs(b.amounts?.cm ?? 0) - Math.abs(a.amounts?.cm ?? 0),
  )
}

function sortChildRows(statement: string | undefined, rows: FinancialStatementRow[]): FinancialStatementRow[] {
  if (statement === 'bs' || statement === 'wc') return rows
  return sortRowsByCmDesc(rows)
}

export interface PlTableRenderCtx {
  data: FinancialStatementResponse
  year: number
  month: number
  planMap: PlPlanMap
  monthly?: MonthlyResponse | null
  columns: PlTableColumnDef[]
  compact?: boolean
  /** Fixed px font for PPT screenshot export */
  exportFontPx?: number
  /** PDF/screenshot: hide row expand chevrons */
  exportMode?: boolean
  /** line_code → comment marker (report view "#" column) */
  commentMarkersByLineCode?: ReportCommentMarkerMap
  onDrill: (d: FinancialsDrillOpen) => void
  checkOpen: (id: string) => boolean
  toggle: (id: string) => void
}

function commentColSpan(ctx: PlTableRenderCtx, valueCols: number): number {
  return valueCols + 1 + (ctx.commentMarkersByLineCode ? 1 : 0)
}

function renderCommentIndexCell(ctx: PlTableRenderCtx, row: FinancialStatementRow): ReactNode | null {
  if (!ctx.commentMarkersByLineCode) return null
  const marker = ctx.commentMarkersByLineCode[row.line_code]
  return (
    <td
      className={`${ctx.compact ? 'px-0 py-0' : 'px-0 py-0'} align-middle whitespace-nowrap`}
      style={{ width: 20, minWidth: 20, maxWidth: 20 }}
    >
      {marker ? (
        <div className="flex items-center justify-center w-full min-h-[1.75rem]">
          <PlCommentIndexBadge marker={marker} />
        </div>
      ) : null}
    </td>
  )
}

function isSubtotalRow(row: FinancialStatementRow): boolean {
  return row.row_kind === 'subtotal'
}

function openDrill(
  ctx: PlTableRenderCtx,
  row: FinancialStatementRow,
  col: ValueCol,
  colLabel: string,
) {
  if (!row.drill) return
  const { from, to } = periodRange(ctx.year, ctx.month, col)
  ctx.onDrill({
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

function renderLegacyColumns(ctx: PlTableRenderCtx, row: FinancialStatementRow, lbl: FinancialStatementColLabels, maxMom: number, maxYoy: number, maxYtd: number): ReactNode {
  const am = row.amounts!
  const d = row.deltas!
  const inv = row.invert_delta
  return (
    <>
      <ValCell value={am.py_cm} bold={isSubtotalRow(row)} compact={ctx.compact} onClick={row.drill ? () => openDrill(ctx, row, 'py_cm', lbl.py_cm) : undefined} />
      <ValCell value={am.pm} bold={isSubtotalRow(row)} compact={ctx.compact} onClick={row.drill ? () => openDrill(ctx, row, 'pm', lbl.pm) : undefined} />
      <ValCell value={am.cm} bold={isSubtotalRow(row)} highlighted compact={ctx.compact} onClick={row.drill ? () => openDrill(ctx, row, 'cm', lbl.cm) : undefined} />
      <DeltaCell value={d.mom} maxAbs={maxMom} invert={inv} compact={ctx.compact} exportLayout={ctx.exportMode} onClick={row.drill ? () => openDrill(ctx, row, 'cm', `${lbl.cm} vs ${lbl.pm}`) : undefined} />
      <DeltaCell value={d.yoy} maxAbs={maxYoy} invert={inv} compact={ctx.compact} exportLayout={ctx.exportMode} onClick={row.drill ? () => openDrill(ctx, row, 'cm', `${lbl.cm} vs ${lbl.py_cm}`) : undefined} />
      <ValCell value={am.ytd} bold={isSubtotalRow(row)} highlighted compact={ctx.compact} onClick={row.drill ? () => openDrill(ctx, row, 'ytd', lbl.ytd) : undefined} />
      <ValCell value={am.ytd_py} bold={isSubtotalRow(row)} compact={ctx.compact} onClick={row.drill ? () => openDrill(ctx, row, 'ytd_py', lbl.ytd_py) : undefined} />
      <DeltaCell value={d.ytd} maxAbs={maxYtd} invert={inv} compact={ctx.compact} exportLayout={ctx.exportMode} onClick={row.drill ? () => openDrill(ctx, row, 'ytd', `${lbl.ytd} vs ${lbl.ytd_py}`) : undefined} />
    </>
  )
}

function renderDynamicColumns(
  ctx: PlTableRenderCtx,
  row: FinancialStatementRow,
  maxima: ReturnType<typeof computePlTableMaxima>,
): ReactNode {
  const inv = row.invert_delta
  const isKpi = row.row_kind === 'kpi'
  return (
    <>
      {ctx.columns.map(col => {
        const highlighted = isReportPeriodHighlightColumn(col.kind)
        if (isKpi && !KPI_TABLE_COLUMN_KINDS.has(col.kind)) {
          return <BlankValCell key={col.id} compact={ctx.compact} highlighted={highlighted} />
        }
        const v = resolveCellValue(row, col, ctx.planMap, ctx.monthly)
        const isPlanCol = col.kind === 'plan_cm' || col.kind === 'plan_vs_actual'
        if (v == null && isKpi) {
          return <BlankValCell key={col.id} compact={ctx.compact} highlighted={highlighted} />
        }
        if (v == null) {
          if (isPlanCol) return <ValCell key={col.id} value={0} compact={ctx.compact} />
          // For week-only fields that the API does not populate, and for ytg
          // when plan data is absent, show an explicit dash instead of muted 0.
          if (col.kind === 'mtg' || col.kind === 'coverage_mtd' || col.kind === 'ytg') {
            return (
              <td
                key={col.id}
                className={`${ctx.compact ? 'px-1.5 py-1' : 'px-2.5 py-2'} text-right whitespace-nowrap`}
                style={{ color: '#CBD5E1', fontSize: '0.75rem' }}
              >
                —
              </td>
            )
          }
          return <ValCell key={col.id} value={0} compact={ctx.compact} muted />
        }
        const isDelta =
          col.kind === 'mom' ||
          col.kind === 'yoy' ||
          col.kind === 'ytd_delta' ||
          col.kind === 'ytd_vs_plan' ||
          col.kind === 'plan_vs_actual' ||
          col.kind === 'month_mom' ||
          col.kind === 'month_yoy' ||
          col.kind === 'month_delta'
        const isCoverage = col.kind === 'coverage' || col.kind === 'coverage_mtd'
        if (isDelta) {
          const kpiKey =
            col.kind === 'ytd_delta' || col.kind === 'ytd_vs_plan'
              ? 'ytd'
              : col.kind === 'plan_vs_actual' || col.kind === 'month_mom' || col.kind === 'month_delta'
                ? 'mom'
                : col.kind === 'yoy' || col.kind === 'month_yoy'
                  ? 'yoy'
                  : col.kind
          const maxAbs = isKpi
            ? col.kind === 'plan_vs_actual'
              ? maxima.maxKpiPlanVs
              : kpiKey === 'mom'
                ? maxima.maxKpiMom
                : kpiKey === 'yoy'
                  ? maxima.maxKpiYoy
                  : maxima.maxKpiYtd
            : col.kind === 'mom' || col.kind === 'plan_vs_actual' || col.kind === 'month_mom' || col.kind === 'month_delta'
              ? maxima.maxMom
              : col.kind === 'yoy' || col.kind === 'month_yoy'
                ? maxima.maxYoy
                : maxima.maxYtd
          return (
            <DeltaCell
              key={col.id}
              value={v}
              maxAbs={maxAbs}
              invert={isKpi ? false : inv}
              isPct={isKpi}
              compact={ctx.compact}
              exportLayout={ctx.exportMode}
            />
          )
        }
        return (
          <ValCell
            key={col.id}
            value={v}
            isPct={isKpi || isCoverage}
            italic={isKpi}
            highlighted={highlighted}
            compact={ctx.compact}
            muted={!isKpi && col.kind === 'ytg' && Math.abs(v) < 1e-6}
            bold={isSubtotalRow(row)}
            onClick={
              !isKpi && row.drill && (col.kind === 'py_cm' || col.kind === 'pm' || col.kind === 'cm' || col.kind === 'ytd' || col.kind === 'ytd_py')
                ? () => openDrill(ctx, row, col.kind as ValueCol, ctx.data.col_labels[col.kind as keyof typeof ctx.data.col_labels] ?? col.labelLine1)
                : undefined
            }
          />
        )
      })}
    </>
  )
}

export function computePlTableMaxima(data: FinancialStatementResponse | null) {
  if (!data?.rows) {
    return { maxMom: 1, maxYoy: 1, maxYtd: 1, maxKpiMom: 1, maxKpiYoy: 1, maxKpiYtd: 1, maxKpiPlanVs: 5 }
  }
  const all = collectNumericRows(data.rows)
  const cur = all.filter(r => r.row_kind !== 'kpi')
  const kpi = all.filter(r => r.row_kind === 'kpi')
  const maxOf = (rows: FinancialStatementRow[], key: 'mom' | 'yoy' | 'ytd') =>
    Math.max(1, ...rows.map(r => Math.abs(r.deltas?.[key] ?? 0)))
  const maxKpiPlanVs = Math.max(
    5,
    ...kpi.map(r => Math.abs(r.amounts?.plan_vs_actual ?? 0)),
  )
  return {
    maxMom: maxOf(cur, 'mom'),
    maxYoy: maxOf(cur, 'yoy'),
    maxYtd: maxOf(cur, 'ytd'),
    maxKpiMom: maxOf(kpi, 'mom'),
    maxKpiYoy: maxOf(kpi, 'yoy'),
    maxKpiYtd: maxOf(kpi, 'ytd'),
    maxKpiPlanVs,
  }
}

function renderKpiSectionHeader(ctx: PlTableRenderCtx, columns: PlTableColumnDef[], label: string): ReactNode {
  return (
    <tr key="kpi-section-header" style={{ background: '#F8FAFC', borderTop: '2px solid #E2E8F0' }}>
      <td
        className="px-3 py-2 text-xs font-semibold"
        style={{ color: '#1E3A5F', fontStyle: 'italic', background: '#F8FAFC' }}
      >
        {label}
      </td>
      {ctx.commentMarkersByLineCode && <td style={{ background: '#F8FAFC' }} />}
      {columns.map(col => (
        <td key={col.id} style={{ background: headerCellBackground(col.kind) ?? '#F8FAFC' }} />
      ))}
    </tr>
  )
}

function kpiSectionHeaderLabel(statement?: string): string {
  if (statement === 'wc') return 'KPIs — working capital days'
  if (statement === 'bs') return 'KPIs'
  return 'KPIs — as % of total output'
}

export function renderPlTableRows(ctx: PlTableRenderCtx, rows: FinancialStatementRow[], depth: number): ReactNode[] {
  const lbl = ctx.data.col_labels
  const maxima = computePlTableMaxima(ctx.data)
  const { maxMom, maxYoy, maxYtd, maxKpiMom, maxKpiYoy, maxKpiYtd } = maxima
  const useDynamic = ctx.columns.length > 0
  const nodes: ReactNode[] = []
  let kpiHeaderInserted = depth === 0 ? false : true

  for (const row of rows) {
    if (row.row_kind === 'kpi_header') {
      kpiHeaderInserted = true
      const kpiHeaderCols: PlTableColumnDef[] = useDynamic
        ? ctx.columns
        : [
            { id: 'py_cm', kind: 'py_cm', labelLine1: '' },
            { id: 'pm', kind: 'pm', labelLine1: '' },
            { id: 'cm', kind: 'cm', labelLine1: '' },
            { id: 'mom', kind: 'mom', labelLine1: '' },
            { id: 'yoy', kind: 'yoy', labelLine1: '' },
            { id: 'ytd', kind: 'ytd', labelLine1: '' },
            { id: 'ytd_py', kind: 'ytd_py', labelLine1: '' },
            { id: 'ytd_delta', kind: 'ytd_delta', labelLine1: '' },
          ]
      nodes.push(renderKpiSectionHeader(ctx, kpiHeaderCols, row.label || kpiSectionHeaderLabel(ctx.data.statement)))
      continue
    }
    if (row.row_kind === 'kpi' && !kpiHeaderInserted) {
      kpiHeaderInserted = true
      const kpiHeaderCols: PlTableColumnDef[] = useDynamic
        ? ctx.columns
        : [
            { id: 'py_cm', kind: 'py_cm', labelLine1: '' },
            { id: 'pm', kind: 'pm', labelLine1: '' },
            { id: 'cm', kind: 'cm', labelLine1: '' },
            { id: 'mom', kind: 'mom', labelLine1: '' },
            { id: 'yoy', kind: 'yoy', labelLine1: '' },
            { id: 'ytd', kind: 'ytd', labelLine1: '' },
            { id: 'ytd_py', kind: 'ytd_py', labelLine1: '' },
            { id: 'ytd_delta', kind: 'ytd_delta', labelLine1: '' },
          ]
      nodes.push(renderKpiSectionHeader(ctx, kpiHeaderCols, kpiSectionHeaderLabel(ctx.data.statement)))
    }

    if (row.row_kind === 'title') {
      nodes.push(
        <tr key={row.id} style={{ background: '#F8FAFC', borderTop: '1px solid #E2E8F0' }}>
          <td
            colSpan={useDynamic ? commentColSpan(ctx, ctx.columns.length) : commentColSpan(ctx, 8)}
            className="px-3 py-2 text-xs font-bold"
            style={{ color: '#1E3A5F' }}
          >
            {row.label}
          </td>
        </tr>,
      )
      continue
    }

    const pad = 12 + depth * 14
    const isOpen = ctx.checkOpen(row.id)
    const showChevron = (row.children?.length ?? 0) > 0 || (row.accounts?.length ?? 0) > 0
    const isKpi = row.row_kind === 'kpi'
    const isAccount = row.row_kind === 'account'

    nodes.push(
      <tr
        key={row.id}
        style={{
          borderBottom: isKpi ? 'none' : '1px solid #E2E8F0',
          borderTop: row.row_kind === 'subtotal' && depth === 0 ? '2px solid #E2E8F0' : undefined,
          background: isKpi ? '#F8FAFC' : row.row_kind === 'subtotal' && depth === 0 ? '#F8FAFC' : undefined,
        }}
      >
        <td
          className={`${ctx.compact ? 'py-1' : 'py-2'} text-left`}
          style={{
            minWidth: ctx.exportMode ? 140 : 180,
            maxWidth: ctx.exportMode ? 280 : undefined,
            paddingLeft: pad,
            paddingRight: 12,
            overflow: ctx.exportMode ? 'hidden' : undefined,
            textOverflow: ctx.exportMode ? 'ellipsis' : undefined,
            whiteSpace: 'nowrap',
          }}
        >
          <PlRowLabel
            row={row}
            showChevron={showChevron && !ctx.exportMode}
            isOpen={isOpen}
            onToggle={() => ctx.toggle(row.id)}
            isKpi={isKpi}
            isAccount={isAccount}
            exportMode={ctx.exportMode}
          />
        </td>
        {renderCommentIndexCell(ctx, row)}
        {row.amounts && row.deltas && (
          useDynamic
            ? renderDynamicColumns(ctx, row, maxima)
            : isKpi
              ? (
                <>
                  <ValCell value={row.amounts.py_cm} isPct italic compact={ctx.compact} />
                  <ValCell value={row.amounts.pm} isPct italic compact={ctx.compact} />
                  <ValCell value={row.amounts.cm} isPct highlighted italic compact={ctx.compact} />
                  <DeltaCell value={row.deltas.mom} maxAbs={maxKpiMom} invert={false} isPct italic compact={ctx.compact} exportLayout={ctx.exportMode} />
                  <DeltaCell value={row.deltas.yoy} maxAbs={maxKpiYoy} invert={false} isPct italic compact={ctx.compact} exportLayout={ctx.exportMode} />
                  <ValCell value={row.amounts.ytd} isPct highlighted italic compact={ctx.compact} />
                  <ValCell value={row.amounts.ytd_py} isPct italic compact={ctx.compact} />
                  <DeltaCell value={row.deltas.ytd} maxAbs={maxKpiYtd} invert={false} isPct italic compact={ctx.compact} exportLayout={ctx.exportMode} />
                </>
              )
              : renderLegacyColumns(ctx, row, lbl, maxMom, maxYoy, maxYtd)
        )}
      </tr>,
    )

    if (!isOpen) continue
    if (row.children?.length) {
      for (const ch of sortChildRows(ctx.data.statement, row.children)) {
        nodes.push(...renderPlTableRows(ctx, [ch], depth + 1))
      }
    }
    if (row.accounts?.length) {
      for (const acc of sortChildRows(ctx.data.statement, row.accounts)) {
        nodes.push(...renderPlTableRows(ctx, [acc], depth + 1))
      }
    }
  }
  return nodes
}

function PlRowLabel({
  row,
  showChevron,
  isOpen,
  onToggle,
  isKpi,
  isAccount,
  exportMode,
}: {
  row: FinancialStatementRow
  showChevron: boolean
  isOpen: boolean
  onToggle: () => void
  isKpi: boolean
  isAccount?: boolean
  exportMode?: boolean
}) {
  return (
    <div className="flex items-center gap-0.5">
      {showChevron ? <ExpandChevron open={isOpen} onToggle={onToggle} /> : !exportMode ? <span style={{ width: 22 }} /> : null}
      <span
        className="text-xs"
        style={{
          fontWeight: row.row_kind === 'subtotal' ? 600 : 400,
          fontStyle: isKpi ? 'italic' : undefined,
          color: isKpi ? '#64748B' : isAccount ? '#475569' : '#111827',
        }}
      >
        {row.label}
      </span>
    </div>
  )
}
