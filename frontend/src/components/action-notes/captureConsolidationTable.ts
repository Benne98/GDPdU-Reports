import type { ConsolidationResponse, ConsolidationRow, ViewPinSnapshot } from '../../lib/api'

type TableSnap = NonNullable<ViewPinSnapshot['table']>

export function captureConsolidationSnapshot(
  data: ConsolidationResponse | null | undefined,
  component: string,
): TableSnap | null {
  if (!data?.rows?.length) return null

  const row_preview: TableSnap['row_preview'] = []
  const entityCodes = data.entities.map(e => e.code)

  function walk(rows: ConsolidationRow[]) {
    for (const row of rows) {
      if (row_preview.length >= 25) return
      if (row.row_kind !== 'title' && row.row_kind !== 'kpi_header') {
        const values: Record<string, string | number> = {}
        for (const code of entityCodes.slice(0, 4)) {
          const v = row.entity_amounts[code]
          values[code] = v != null ? v : '—'
        }
        values['aggregated'] = row.aggregated
        values['consolidation'] = row.consolidation
        row_preview.push({ id: row.id, label: row.label, values })
      }
      if (row.children?.length) walk(row.children)
    }
  }

  walk(data.rows)

  const visibleColumnIds = [
    ...entityCodes.slice(0, 4),
    'aggregated',
    'consolidation',
  ]

  return {
    component,
    expanded_row_ids: [],
    visible_column_ids: visibleColumnIds,
    row_preview,
  }
}
