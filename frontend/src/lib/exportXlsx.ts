/**
 * Generic XLSX export — delegates to Finssentials design export.
 */
import {
  exportFinssentialsXlsx,
  flattenTreeForExport,
  type ExportFlatRow,
  type ExportRowKind,
} from './finssentialsExport'

export type XlsxRowKind = ExportRowKind

export interface XlsxRow {
  label: string
  values: (number | string | null)[]
  kind: XlsxRowKind
  indent?: number
  kpiCols?: number[]
}

export interface XlsxConfig {
  title: string
  subtitle?: string
  headers: string[]
  rows: XlsxRow[]
  filename: string
}

function xlsxRowsToFlat(rows: XlsxRow[]): ExportFlatRow[] {
  return rows.map((r, i) => ({
    id: `flat-${i}`,
    label: (r.label ?? '').replace(/^\s+/, ''),
    values: r.values,
    kind: r.kind,
    depth: r.indent ?? 0,
    outlineLevel: Math.min(r.indent ?? 0, 7),
    hidden: false,
    kpiCols: r.kpiCols,
  }))
}

export async function exportToXlsx(cfg: XlsxConfig): Promise<void> {
  await exportFinssentialsXlsx({
    tableTitle: cfg.title,
    subtitle: cfg.subtitle,
    headers: cfg.headers,
    rows: xlsxRowsToFlat(cfg.rows),
    filename: cfg.filename,
  })
}

export function flattenTree<T extends {
  id: string
  label: string
  row_kind: string
  is_bold?: boolean
  amounts: Record<string, number> | null
  deltas?: Record<string, number> | null
  children?: T[]
  accounts?: T[]
}>(
  rows: T[],
  amountKeys: string[],
  deltaKeys: string[],
  options?: { isRowOpen?: (id: string) => boolean },
): XlsxRow[] {
  const isRowOpen = options?.isRowOpen ?? (() => true)

  const flat = flattenTreeForExport(rows, {
    isRowOpen,
    mapRow: row => {
      const isKpi = row.row_kind === 'kpi'
      if (!row.amounts && row.row_kind !== 'kpi' && row.row_kind !== 'kpi_header') {
        if (row.row_kind === 'title') {
          return {
            label: row.label,
            values: amountKeys.concat(deltaKeys).map(() => ''),
            kind: 'title' as const,
          }
        }
        return null
      }

      const kind: XlsxRowKind =
        row.row_kind === 'title' || row.row_kind === 'kpi_header' ? 'section' :
        row.row_kind === 'subtotal' ? 'subtotal' :
        isKpi ? 'kpi' : 'data'

      const amounts = amountKeys.map(k => {
        const v = row.amounts?.[k]
        if (v == null) return null
        return isKpi ? +v.toFixed(1) : Math.round(v / 1000)
      })
      const deltas = deltaKeys.map(k => {
        const v = row.deltas?.[k]
        if (v == null) return null
        return isKpi ? +v.toFixed(1) : Math.round(v / 1000)
      })

      return {
        label: row.label,
        values: [...amounts, ...deltas],
        kind,
        kpiCols: isKpi ? amounts.map((_, i) => i) : undefined,
      }
    },
  })

  return flat.map(r => ({
    label: r.label,
    values: r.values,
    kind: r.kind,
    indent: r.depth,
    kpiCols: r.kpiCols,
  }))
}

/** Convert flattenTreeForExport output for direct Finssentials export. */
export function flattenTreeToExportRows<T extends {
  id: string
  label: string
  row_kind: string
  amounts: Record<string, number> | null
  deltas?: Record<string, number> | null
  children?: T[]
  accounts?: T[]
}>(
  rows: T[],
  amountKeys: string[],
  deltaKeys: string[],
  isRowOpen: (id: string) => boolean,
): ExportFlatRow[] {
  return flattenTreeForExport(rows, {
    isRowOpen,
    mapRow: row => {
      const isKpi = row.row_kind === 'kpi'
      if (!row.amounts && row.row_kind !== 'kpi' && row.row_kind !== 'kpi_header') {
        if (row.row_kind === 'title') {
          return {
            label: row.label,
            values: amountKeys.concat(deltaKeys).map(() => ''),
            kind: 'title' as const,
          }
        }
        return null
      }
      const kind: ExportRowKind =
        row.row_kind === 'title' || row.row_kind === 'kpi_header' ? 'section' :
        row.row_kind === 'subtotal' ? 'subtotal' :
        isKpi ? 'kpi' : 'data'
      const amounts = amountKeys.map(k => {
        const v = row.amounts?.[k]
        if (v == null) return null
        return isKpi ? +v.toFixed(1) : Math.round(v / 1000)
      })
      const deltas = deltaKeys.map(k => {
        const v = row.deltas?.[k]
        if (v == null) return null
        return isKpi ? +v.toFixed(1) : Math.round(v / 1000)
      })
      return {
        label: row.label,
        values: [...amounts, ...deltas],
        kind,
        kpiCols: isKpi ? amounts.map((_, i) => i) : undefined,
      }
    },
  })
}

export function todayStr(): string {
  return new Date().toISOString().slice(0, 10)
}
