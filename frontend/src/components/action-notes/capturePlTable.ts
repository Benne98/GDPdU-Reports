import type { FinancialStatementResponse } from '../../lib/api'
import type { PlTableColumnDef } from '../financials/pl-two-view/plColumnRegistry'
import type { ViewPinSnapshot } from '../../lib/api'

export function capturePlTableSnapshot(
  data: FinancialStatementResponse,
  expandedIds: Set<string>,
  columns: PlTableColumnDef[],
  component: string,
): ViewPinSnapshot['table'] {
  const row_preview: NonNullable<ViewPinSnapshot['table']>['row_preview'] = []

  function walk(rows: FinancialStatementResponse['rows'], depth: number) {
    if (row_preview.length >= 25) return
    for (const row of rows) {
      if (row_preview.length >= 25) break
      const open = expandedIds.has(row.id)
      if (row.row_kind !== 'title') {
        row_preview.push({
          id: row.id,
          label: row.label,
          values: {
            cm: row.amounts?.cm ?? '—',
            mom: row.deltas?.mom ?? '—',
          },
        })
      }
      if (open && row.children?.length) walk(row.children, depth + 1)
    }
  }

  walk(data.rows, 0)

  return {
    component,
    expanded_row_ids: [...expandedIds],
    visible_column_ids: columns.map(c => c.id),
    row_preview,
  }
}
