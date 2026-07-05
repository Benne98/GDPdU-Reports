import type { ReactNode } from 'react'
import type { ErStatementRow } from '../../../lib/api'
import PlCommentIndexBadge from '../pl-two-view/PlCommentIndexBadge'
import { ExpandChevron } from '../pl-two-view/plTableCore'
import type { ReportCommentMarker } from '../statement-two-view/reportCommentMarkers'

export function sortAnnualChildRows(rows: ErStatementRow[], amountKey: string): ErStatementRow[] {
  return [...rows].sort(
    (a, b) =>
      Math.abs(Number((b.amounts as Record<string, number> | undefined)?.[amountKey] ?? 0)) -
      Math.abs(Number((a.amounts as Record<string, number> | undefined)?.[amountKey] ?? 0)),
  )
}

/** BS/WC use backend display order; other snapshot statements keep magnitude sort. */
export function annualChildRows(
  statement: string | undefined,
  rows: ErStatementRow[],
  amountKey: string,
): ErStatementRow[] {
  if (statement === 'bs' || statement === 'wc') return rows
  return sortAnnualChildRows(rows, amountKey)
}

export function annualCommentColSpan(valueCols: number, hasCommentCol: boolean): number {
  return valueCols + 1 + (hasCommentCol ? 1 : 0)
}

export function renderAnnualCommentCell(
  marker: ReportCommentMarker | undefined,
  hasCommentCol: boolean,
): ReactNode | null {
  if (!hasCommentCol) return null
  return (
    <td
      className="px-0 py-0 align-middle whitespace-nowrap"
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

export function AnnualRowLabel({
  row,
  showChevron,
  isOpen,
  onToggle,
  isKpi,
  isAccount,
}: {
  row: ErStatementRow
  showChevron: boolean
  isOpen: boolean
  onToggle: () => void
  isKpi: boolean
  isAccount?: boolean
}) {
  return (
    <div className="flex items-center gap-0.5">
      {showChevron ? (
        <ExpandChevron open={isOpen} onToggle={onToggle} />
      ) : (
        <span style={{ width: 22 }} />
      )}
      <span
        className="text-xs"
        style={{
          fontWeight: row.row_kind === 'subtotal' || row.is_bold ? 600 : 400,
          fontStyle: isKpi ? 'italic' : undefined,
          color: isKpi ? '#64748B' : isAccount ? '#475569' : '#111827',
        }}
      >
        {row.label}
      </span>
    </div>
  )
}
