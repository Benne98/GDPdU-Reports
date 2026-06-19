import type { EbitTableData } from '../../lib/api'
import type { ViewPinSnapshot } from '../../lib/api'

export function captureEbitTableSnapshot(data: EbitTableData): NonNullable<ViewPinSnapshot['table']> {
  const rows = data.rows.filter(r => r.entity_code !== '__total__').slice(0, 12)
  const total = data.rows.find(r => r.entity_code === '__total__')
  const row_preview = rows.map(r => ({
    id: r.entity_code,
    label: r.entity_name,
    values: {
      [data.col_labels.cm]: Math.round(r.to_cm / 1000),
      [data.col_labels.pm]: Math.round(r.to_pm / 1000),
    },
  }))
  if (total) {
    row_preview.unshift({
      id: '__total__',
      label: total.entity_name,
      values: {
        [data.col_labels.cm]: Math.round(total.to_cm / 1000),
        [data.col_labels.pm]: Math.round(total.to_pm / 1000),
      },
    })
  }
  return {
    component: 'EbitTable',
    expanded_row_ids: [],
    row_preview,
  }
}
