import type { MonthlyResponse, MonthlyRow, ViewPinSnapshot } from '../../lib/api'

type TableSnap = NonNullable<ViewPinSnapshot['table']>

export function captureMonthlySnapshot(
  data: MonthlyResponse | null | undefined,
  component: string,
  visibleColumnKeys: string[],
): TableSnap | null {
  if (!data?.rows?.length) return null

  const row_preview: TableSnap['row_preview'] = []
  // Cap values per row to keep snapshot lean
  const keySlice = visibleColumnKeys.slice(0, 8)

  function walk(rows: MonthlyRow[]) {
    for (const row of rows) {
      if (row_preview.length >= 25) return
      if (row.row_kind !== 'title' && row.row_kind !== 'kpi_header') {
        const values: Record<string, string | number> = {}
        for (const key of keySlice) {
          const v = row.amounts?.[key]
          values[key] = v != null ? v : '—'
        }
        row_preview.push({ id: row.id, label: row.label, values })
      }
      if (row.children?.length) walk(row.children)
    }
  }

  walk(data.rows)

  return {
    component,
    expanded_row_ids: [],
    visible_column_ids: visibleColumnKeys,
    row_preview,
  }
}
