import type { ErFlowResponse, ErSnapshotResponse, ErStatementRow, ViewPinSnapshot } from '../../lib/api'

type TableSnap = NonNullable<ViewPinSnapshot['table']>

function walkRows(rows: ErStatementRow[], component: string, max: number): TableSnap {
  const row_preview: TableSnap['row_preview'] = []
  function walk(list: ErStatementRow[]) {
    for (const row of list) {
      if (row_preview.length >= max) return
      if (row.row_kind !== 'title' && row.amounts) {
        row_preview.push({
          id: row.id,
          label: row.label,
          values: {
            ytd: row.amounts.ytd ?? '—',
            ltm: row.amounts.ltm ?? '—',
          },
        })
      }
      if (row.children?.length) walk(row.children)
    }
  }
  walk(rows)
  return { component, expanded_row_ids: [], row_preview }
}

export function captureErFlowSnapshot(data: ErFlowResponse, component: string): TableSnap {
  return walkRows(data.rows, component, 20)
}

export function captureErSnapshotSnapshot(data: ErSnapshotResponse, component: string): TableSnap {
  return walkRows(data.rows, component, 20)
}
