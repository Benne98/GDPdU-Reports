import type { SalesTopEntitiesColLabels, SalesTopEntity, ViewPinSnapshot } from '../../lib/api'

type TableSnap = NonNullable<ViewPinSnapshot['table']>

export function captureTopEntitiesSnapshot(
  rows: SalesTopEntity[],
  colLabels: SalesTopEntitiesColLabels,
  component: string,
): TableSnap {
  const row_preview: TableSnap['row_preview'] = rows.slice(0, 20).map(r => ({
    id: String(r.rank ?? r.name),
    label: r.name,
    values: {
      [colLabels.cm]: Math.round(r.cm / 1000),
      [colLabels.ytd]: Math.round(r.ytd / 1000),
    },
  }))
  return { component, expanded_row_ids: [], row_preview }
}
