import type {
  FinancialsEntityBreakdownResponse,
  FinancialsOverviewResponse,
  ViewPinSnapshot,
} from '../../lib/api'

type TableSnap = NonNullable<ViewPinSnapshot['table']>

export function captureExecutiveSummarySnapshot(
  data: FinancialsOverviewResponse | null | undefined,
): TableSnap | null {
  if (!data?.sections?.length) return null
  const rows = data.sections
    .flatMap(s => s.rows)
    .filter(r => r.row_kind !== 'section_header' && r.row_kind !== 'kpi_header')
  if (!rows.length) return null
  const cm = data.col_labels.cm
  const pm = data.col_labels.pm
  const row_preview: TableSnap['row_preview'] = rows.slice(0, 25).map(r => ({
    id: r.id,
    label: r.label,
    values: {
      [pm]: r.amounts.pm ?? '—',
      [cm]: r.amounts.cm ?? '—',
    },
  }))
  return { component: 'ExecutiveSummaryTable', expanded_row_ids: [], row_preview }
}

export function captureEntityBreakdownSnapshot(
  data: FinancialsEntityBreakdownResponse | null | undefined,
): TableSnap | null {
  if (!data?.rows?.length || !data?.entities?.length) return null
  const entityCodes = data.entities.slice(0, 5).map(e => e.code)
  const row_preview: TableSnap['row_preview'] = data.rows
    .filter(r => r.row_kind !== 'section_header' && r.row_kind !== 'kpi_header')
    .slice(0, 25)
    .map(r => {
      const values: Record<string, string | number> = {}
      for (const code of entityCodes) {
        const v = r.cm_by_entity[code]
        values[code] = v != null ? v : '—'
      }
      return { id: r.id, label: r.label, values }
    })
  return {
    component: 'EntityBreakdownTable',
    expanded_row_ids: [],
    visible_column_ids: entityCodes,
    row_preview,
  }
}
