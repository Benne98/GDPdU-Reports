/**
 * Flatten hierarchical table rows for export, respecting UI expand/collapse.
 */

export type ExportRowKind = 'section' | 'subtotal' | 'kpi' | 'data' | 'title' | 'blank'

export interface ExportFlatRow {
  id: string
  label: string
  values: (number | string | null)[]
  kind: ExportRowKind
  depth: number
  outlineLevel: number
  hidden: boolean
  kpiCols?: number[]
  /** For report-view “#” column (maps to comment index). */
  lineCode?: string
}

export type TreeExportNode = {
  id: string
  label: string
  row_kind: string
  amounts?: Record<string, number> | null
  deltas?: Record<string, number> | null
  is_bold?: boolean
  children?: TreeExportNode[]
  accounts?: TreeExportNode[]
}

export type FlattenTreeOptions = {
  isRowOpen: (id: string) => boolean
  mapRow?: (
    row: TreeExportNode,
    depth: number,
  ) => Omit<ExportFlatRow, 'id' | 'depth' | 'outlineLevel' | 'hidden'> | null
}

function defaultKind(rowKind: string): ExportRowKind {
  if (rowKind === 'title' || rowKind === 'kpi_header') return 'section'
  if (rowKind === 'subtotal') return 'subtotal'
  if (rowKind === 'kpi') return 'kpi'
  return 'data'
}

export function flattenTreeForExport<T extends TreeExportNode>(
  rows: T[],
  options: FlattenTreeOptions,
  depth = 0,
  parentOpen = true,
): ExportFlatRow[] {
  const { isRowOpen, mapRow } = options
  const out: ExportFlatRow[] = []

  for (const row of rows) {
    const children = [...(row.children ?? []), ...(row.accounts ?? [])]
    const hasChildren = children.length > 0
    const selfOpen = isRowOpen(row.id)
    /** Excel row hidden when ancestor collapsed or this row is collapsed in the UI. */
    const hidden = depth > 0 && (!parentOpen || !selfOpen)

    let mapped: ExportFlatRow | null = null
    if (mapRow) {
      const base = mapRow(row, depth)
      if (base) {
        mapped = {
          id: row.id,
          depth,
          outlineLevel: Math.min(depth, 7),
          hidden,
          ...base,
        }
      }
    } else {
      mapped = {
        id: row.id,
        label: row.label,
        values: [],
        kind: defaultKind(row.row_kind),
        depth,
        outlineLevel: Math.min(depth, 7),
        hidden,
      }
    }

    if (mapped) out.push(mapped)

    if (hasChildren) {
      out.push(...flattenTreeForExport(children, options, depth + 1, parentOpen && selfOpen))
    }
  }

  return out
}
