import type { ErFlowResponse, ErStatementRow } from '../../../lib/api'
import type { FinancialsDrillOpen } from '../FinancialStatementTable'
import { TwoLineHeader, DeltaCell, ValCell } from '../pl-two-view/plTableCore'
import type { ReportCommentMarkerMap } from '../statement-two-view/reportCommentMarkers'
import {
  AnnualRowLabel,
  annualCommentColSpan,
  renderAnnualCommentCell,
  sortAnnualChildRows,
} from './annualMiniTableCore'
import { shouldDisplayErStatementRow } from './annualRowVisibility'
import { IS_OVERVIEW_V2 } from '../../../lib/overviewV2Mode'

type FlowCol = 'fy1' | 'fy2' | 'fy3' | 'ytd' | 'ltm' | 'ytd_py' | 'ltm_py'

export type ErFlowColDef = {
  id: string
  kind: 'amount' | 'delta'
  labelLine1: string
  labelLine2?: string
  amountKey?: string
  deltaKey?: string
  flowCol?: FlowCol
  highlighted?: boolean
  isPct?: boolean
}

function pad2(n: number): string {
  return String(n).padStart(2, '0')
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

const KPI_HEADER_CELL_BG = '#F8FAFC'
const HIGHLIGHT_CELL_BG = 'rgba(30,58,95,0.04)'

function kpiHeaderCellBackground(col: ErFlowColDef): string {
  return col.highlighted ? HIGHLIGHT_CELL_BG : KPI_HEADER_CELL_BG
}

type Props = {
  data: ErFlowResponse
  year: number
  month: number
  columns: ErFlowColDef[]
  onDrill: (d: FinancialsDrillOpen) => void
  commentMarkersByLineCode?: ReportCommentMarkerMap
  checkOpen: (id: string) => boolean
  toggle: (id: string) => void
}

export default function ErFlowMiniTable({
  data,
  year,
  month,
  columns,
  onDrill,
  commentMarkersByLineCode,
  checkOpen,
  toggle,
}: Props) {
  const hasCommentCol = Boolean(commentMarkersByLineCode)

  const maxAbsByDeltaKey = (() => {
    const nonKpi: ErStatementRow[] = []
    function walk(r: ErStatementRow) {
      if (r.amounts && r.row_kind !== 'title') nonKpi.push(r)
      for (const c of r.children ?? []) walk(c)
      for (const a of r.accounts ?? []) walk(a)
    }
    for (const r of data.rows) walk(r)
    const filtered = nonKpi.filter(r => r.row_kind !== 'kpi')
    const out: Record<string, number> = {}
    for (const c of columns) {
      if (c.kind !== 'delta' || !c.deltaKey) continue
      out[c.deltaKey] = Math.max(1, ...filtered.map(r => Math.abs(Number((r.deltas ?? {})[c.deltaKey!] ?? 0))))
    }
    return out
  })()

  function openDrill(row: ErStatementRow, col: FlowCol, colLabel: string) {
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

  function renderKpiHeaderRow(key: string, label: string): JSX.Element {
    return (
      <tr key={key} style={{ background: KPI_HEADER_CELL_BG, borderTop: '2px solid #E2E8F0' }}>
        <td
          className="px-3 py-2 text-xs font-semibold italic"
          style={{ color: '#1E3A5F', paddingLeft: 12 }}
        >
          {label}
        </td>
        {hasCommentCol && <td style={{ background: KPI_HEADER_CELL_BG }} />}
        {columns.map(col => (
          <td key={`${key}-${col.id}`} style={{ background: kpiHeaderCellBackground(col) }} />
        ))}
      </tr>
    )
  }

  function renderRow(row: ErStatementRow, depth: number): JSX.Element {
    const isTitle = row.row_kind === 'title'
    const isKpiHeader = row.row_kind === 'kpi_header'
    const isKpi = row.row_kind === 'kpi'
    const isSubtotal = row.row_kind === 'subtotal'
    const isAccount = row.row_kind === 'account'
    const isOpen = checkOpen(row.id)
    const showChevron = (row.children?.length ?? 0) > 0 || (row.accounts?.length ?? 0) > 0
    const am = row.amounts ?? {}
    const deltas = row.deltas ?? {}
    const marker = row.line_code ? commentMarkersByLineCode?.[row.line_code] : undefined

    if (isTitle) {
      return (
        <tr key={row.id} style={{ background: KPI_HEADER_CELL_BG, borderTop: '1px solid #E2E8F0' }}>
          <td
            colSpan={annualCommentColSpan(columns.length, hasCommentCol)}
            className="px-3 py-2 text-xs font-semibold"
            style={{ color: '#1E3A5F' }}
          >
            {row.label}
          </td>
        </tr>
      )
    }
    if (isKpiHeader) {
      return renderKpiHeaderRow(row.id, row.label)
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
        <td
          className="py-1 text-left"
          style={{
            minWidth: 180,
            paddingLeft: 12 + depth * 14,
            paddingRight: 12,
            whiteSpace: 'nowrap',
          }}
        >
          <AnnualRowLabel
            row={row}
            showChevron={showChevron}
            isOpen={isOpen}
            onToggle={() => toggle(row.id)}
            isKpi={isKpi}
            isAccount={isAccount}
          />
        </td>
        {renderAnnualCommentCell(marker, hasCommentCol)}
        {columns.map(col => {
          if (col.kind === 'amount') {
            if (col.id === 'cagr') {
              if (isKpi) return <td key={`${row.id}-cagr`} className="px-1.5 py-1" />
              const fy2 = Number(am.fy2 ?? 0)
              const fy3 = Number(am.fy3 ?? 0)
              const cagr = Math.abs(fy2) > 1e-3 ? ((Math.pow(Math.abs(fy3) / Math.abs(fy2), 1 / 3) - 1) * 100) : 0
              return <ValCell key={`${row.id}-cagr`} value={cagr} isPct italic compact />
            }
            if (col.id === 'forecast') {
              const forecast = resolveForecastAmount(am)
              return (
                <ValCell
                  key={`${row.id}-forecast`}
                  value={forecast}
                  bold={row.is_bold || isSubtotal}
                  italic={isKpi}
                  isPct={isKpi}
                  compact
                  onClick={col.flowCol && row.drill ? () => openDrill(row, col.flowCol!, col.labelLine1) : undefined}
                />
              )
            }
            if (col.id === 'plan_cm') {
              const plan = Number(am.plan_cm ?? 0)
              return (
                <ValCell
                  key={`${row.id}-plan`}
                  value={plan}
                  bold={row.is_bold || isSubtotal}
                  italic={isKpi}
                  compact
                />
              )
            }
            if (col.id === 'coverage_pct') {
              if (IS_OVERVIEW_V2 && isKpi) {
                return <td key={`${row.id}-cov`} className="px-1.5 py-1" style={{ background: '#F8FAFC' }} />
              }
              const cov = resolveCoveragePct(am)
              return <ValCell key={`${row.id}-cov`} value={cov} isPct italic={isKpi} compact />
            }
            // Forecast column intentionally left blank (forecast methodology parked).
            const val = col.amountKey === 'fy_f' ? NaN : Number(am[col.amountKey ?? ''] ?? 0)
            return (
              <ValCell
                key={`${row.id}-${col.id}`}
                value={val}
                bold={row.is_bold || isSubtotal}
                highlighted={col.highlighted}
                isPct={isKpi || col.isPct}
                italic={isKpi}
                compact
                onClick={col.flowCol && row.drill ? () => openDrill(row, col.flowCol!, col.labelLine1) : undefined}
              />
            )
          }
          const dval = Number(deltas[col.deltaKey ?? ''] ?? 0)
          return (
            <DeltaCell
              key={`${row.id}-${col.id}`}
              value={dval}
              maxAbs={maxAbsByDeltaKey[col.deltaKey!] ?? 1}
              invert={row.invert_delta}
              compact
              onClick={col.flowCol && row.drill ? () => openDrill(row, col.flowCol!, col.labelLine1) : undefined}
            />
          )
        })}
      </tr>
    )
  }

  function walkRows(rows: ErStatementRow[], depth: number): JSX.Element[] {
    const nodes: JSX.Element[] = []
    let kpiHeaderInserted = false
    for (const row of rows) {
      if (row.row_kind === 'kpi_header') {
        const anyKpi = rows.some(r => r.row_kind === 'kpi' && shouldDisplayErStatementRow(r))
        if (!anyKpi) continue
        kpiHeaderInserted = true
        nodes.push(renderRow(row, depth))
        continue
      }
      if (!shouldDisplayErStatementRow(row)) continue
      if (row.row_kind === 'kpi' && !kpiHeaderInserted) {
        kpiHeaderInserted = true
        nodes.push(renderKpiHeaderRow('er-kpi-header-fallback', 'KPIs — as % of total output'))
      }
      nodes.push(renderRow(row, depth))
      if (row.row_kind === 'title') continue
      if (!checkOpen(row.id)) continue
      for (const ch of sortAnnualChildRows(row.children ?? [], 'fy3')) {
        nodes.push(...walkRows([ch], depth + 1))
      }
      for (const acc of sortAnnualChildRows(row.accounts ?? [], 'fy3')) {
        nodes.push(...walkRows([acc], depth + 1))
      }
    }
    return nodes
  }

  return (
    <div className="min-w-0 w-full">
      <table className="w-full border-collapse text-xs">
        <thead>
          <tr style={{ borderBottom: '2px solid #E2E8F0', background: '#F8FAFC' }}>
            <th className="px-2 py-2 text-left font-semibold text-xs" style={{ color: '#475569' }}>EURk</th>
            {hasCommentCol && (
              <th
                className="px-0 py-2 text-center font-medium align-middle"
                style={{ color: '#94A3B8', width: 20, minWidth: 20, maxWidth: 20, fontSize: '0.62rem' }}
              >
                #
              </th>
            )}
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
  )
}
