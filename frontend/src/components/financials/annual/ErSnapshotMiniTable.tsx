import type { ErSnapshotResponse, ErStatementRow } from '../../../lib/api'
import type { FinancialsDrillOpen } from '../FinancialStatementTable'
import { DeltaCell, ValCell } from '../pl-two-view/plTableCore'
import type { ReportCommentMarkerMap } from '../statement-two-view/reportCommentMarkers'
import {
  AnnualRowLabel,
  annualCommentColSpan,
  renderAnnualCommentCell,
  sortAnnualChildRows,
} from './annualMiniTableCore'

import type { AnnualSnapshotColDef, AnnualSnapshotReportColId } from './annualSnapshotReportColumns'

type SnapCol = AnnualSnapshotReportColId

function lastDay(y: number, m: number): string {
  return new Date(y, m, 0).toISOString().slice(0, 10)
}

function periodRange(year: number, month: number, col: SnapCol): { from: string; to: string } {
  switch (col) {
    case 'dec_py2': return { from: `${year - 3}-01-01`, to: lastDay(year - 3, 12) }
    case 'fy_py': return { from: `${year - 2}-01-01`, to: lastDay(year - 2, 12) }
    case 'fy': return { from: `${year - 1}-01-01`, to: lastDay(year - 1, 12) }
    case 'cm_py': return { from: `${year - 1}-01-01`, to: lastDay(year - 1, month) }
    case 'fy_f': return { from: `${year}-01-01`, to: lastDay(year, 12) }
    case 'cm': return { from: `${year}-01-01`, to: lastDay(year, month) }
    case 'delta_f': return { from: `${year}-01-01`, to: lastDay(year, month) }
  }
}

type ColDef = AnnualSnapshotColDef

type Props = {
  data: ErSnapshotResponse
  year: number
  month: number
  columns: ColDef[]
  onDrill: (d: FinancialsDrillOpen) => void
  commentMarkersByLineCode?: ReportCommentMarkerMap
  checkOpen: (id: string) => boolean
  toggle: (id: string) => void
}

export default function ErSnapshotMiniTable({
  data,
  year,
  month,
  columns,
  onDrill,
  commentMarkersByLineCode,
  checkOpen,
  toggle,
}: Props) {
  const isWc = data.statement === 'wc'
  const hasCommentCol = Boolean(commentMarkersByLineCode)

  function openDrill(row: ErStatementRow, col: SnapCol, colLabel: string) {
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

  function renderRow(row: ErStatementRow, depth: number): JSX.Element {
    const isTitle = row.row_kind === 'title'
    const isKpi = row.row_kind === 'kpi'
    const isSubtotal = row.row_kind === 'subtotal'
    const isAccount = row.row_kind === 'account'
    const isOpen = checkOpen(row.id)
    const showChevron = (row.children?.length ?? 0) > 0 || (row.accounts?.length ?? 0) > 0
    const am = row.amounts ?? {}
    const marker = row.line_code ? commentMarkersByLineCode?.[row.line_code] : undefined

    if (isTitle) {
      return (
        <tr key={row.id} style={{ background: '#F8FAFC', borderTop: '1px solid #E2E8F0' }}>
          <td
            colSpan={annualCommentColSpan(columns.length, hasCommentCol)}
            className="px-3 py-2 text-xs font-bold"
            style={{ color: '#1E3A5F' }}
          >
            {row.label}
          </td>
        </tr>
      )
    }

    const get = (k: string) => Number((am as Record<string, number>)[k] ?? 0)

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
          if (col.isDelta) {
            const d = row.deltas as Record<string, number> | null | undefined
            const val = Number(d?.[col.id] ?? 0)
            if (isKpi) {
              if (isWc) {
                return (
                  <ValCell
                    key={`${row.id}-${col.id}`}
                    value={val}
                    isDays
                    italic
                    highlighted={col.highlighted}
                    compact
                  />
                )
              }
              return (
                <ValCell
                  key={`${row.id}-${col.id}`}
                  value={val}
                  isPct
                  italic
                  highlighted={col.highlighted}
                  compact
                />
              )
            }
            return (
              <DeltaCell
                key={`${row.id}-${col.id}`}
                value={val}
                maxAbs={1}
                invert={row.invert_delta}
                compact
              />
            )
          }

          const val = get(col.id)
          if (isKpi) {
            if (isWc) {
              return <ValCell key={`${row.id}-${col.id}`} value={val} isDays italic highlighted={col.highlighted} compact />
            }
            return <ValCell key={`${row.id}-${col.id}`} value={val} isPct italic highlighted={col.highlighted} compact />
          }
          return (
            <ValCell
              key={`${row.id}-${col.id}`}
              value={val}
              bold={row.is_bold || isSubtotal}
              highlighted={col.highlighted}
              compact
              onClick={row.drill && col.id !== 'delta_f' ? () => openDrill(row, col.id, col.labelLine1) : undefined}
            />
          )
        })}
      </tr>
    )
  }

  function walkRows(rows: ErStatementRow[], depth: number): JSX.Element[] {
    const nodes: JSX.Element[] = []
    for (const row of rows) {
      nodes.push(renderRow(row, depth))
      if (row.row_kind === 'title') continue
      if (!checkOpen(row.id)) continue
      for (const ch of sortAnnualChildRows(row.children ?? [], 'cm')) {
        nodes.push(...walkRows([ch], depth + 1))
      }
      for (const acc of sortAnnualChildRows(row.accounts ?? [], 'cm')) {
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
              <th
                key={c.id}
                className="px-1.5 py-1.5 text-right font-semibold whitespace-nowrap text-[0.65rem]"
                style={{
                  color: c.highlighted ? '#1E3A5F' : '#475569',
                  background: c.highlighted ? 'rgba(30,58,95,0.04)' : undefined,
                }}
              >
                {c.labelLine1}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>{walkRows(data.rows, 0)}</tbody>
      </table>
    </div>
  )
}
