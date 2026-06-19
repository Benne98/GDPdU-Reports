import type { TopCustomerData, ViewPinSnapshot } from '../../lib/api'

type TableSnap = NonNullable<ViewPinSnapshot['table']>

function previewFromGroups(data: TopCustomerData, component: string): TableSnap {
  const ytdKey = data.col_labels.ytd
  const row_preview: TableSnap['row_preview'] = []
  for (const g of data.groups) {
    for (const c of g.customers.slice(0, 8)) {
      if (row_preview.length >= 20) break
      row_preview.push({
        id: c.name,
        label: c.name,
        values: { [ytdKey]: c.ytd_cm },
      })
    }
    if (row_preview.length >= 20) break
  }
  return { component, expanded_row_ids: [], row_preview }
}

export function captureTopCustomerSnapshot(data: TopCustomerData): TableSnap {
  return previewFromGroups(data, 'TopCustomerTable')
}

export function captureTopSupplierSnapshot(data: TopCustomerData): TableSnap {
  return previewFromGroups(data, 'TopSupplierTable')
}
