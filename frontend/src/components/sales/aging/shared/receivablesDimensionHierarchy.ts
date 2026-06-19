import type { ReceivablesHierarchyBreakdownRow } from '../../../../lib/api'
import { countryDisplayName, countryGroupKey } from '../../../../lib/countryLabels'
import type { ReceivablesHierarchyDim } from './receivablesDimensionBreakdownConfig'

export type ReceivablesHierarchyNode = {
  key: string
  label: string
  rawLabel: string
  level: number
  dim?: ReceivablesHierarchyDim
  metrics: Record<string, number>
  children: ReceivablesHierarchyNode[]
  leaf?: boolean
}

function dimValue(row: ReceivablesHierarchyBreakdownRow, dim: ReceivablesHierarchyDim): string {
  const raw = String(row.dims[dim] ?? '—').trim() || '—'
  return dim === 'country' ? countryGroupKey(raw) : raw
}

export function formatHierarchyDimLabel(dim: ReceivablesHierarchyDim, raw: string): string {
  if (dim === 'country') return countryDisplayName(raw)
  return raw
}

function numericFields(row: ReceivablesHierarchyBreakdownRow): Record<string, number> {
  const out: Record<string, number> = {}
  for (const [k, v] of Object.entries(row)) {
    if (k === 'dims') continue
    if (typeof v === 'number') out[k] = v
  }
  return out
}

function addMetrics(target: Record<string, number>, source: Record<string, number>): void {
  for (const [k, v] of Object.entries(source)) {
    if (k.endsWith('_pct') || k.includes('overdue_pct')) {
      continue
    }
    target[k] = (target[k] ?? 0) + v
  }
}

function finalizePct(metrics: Record<string, number>, view: 'buckets' | 'due_overdue'): void {
  const total = metrics.total ?? 0
  if (total <= 0) {
    metrics.overdue_pct = 0
    if (metrics.pm_total != null) metrics.pm_overdue_pct = metrics.pm_overdue ?? 0 ? (metrics.pm_overdue! / metrics.pm_total!) * 100 : 0
    return
  }
  const overdue =
    view === 'due_overdue'
      ? metrics.overdue ?? 0
      : total - (metrics.not_yet_due ?? 0)
  metrics.overdue_pct = Math.round((overdue / total) * 1000) / 10

  for (const prefix of ['pm', 'py'] as const) {
    const t = metrics[`${prefix}_total`]
    if (t == null || t <= 0) continue
    const od =
      view === 'due_overdue'
        ? metrics[`${prefix}_overdue`] ?? 0
        : t - (metrics[`${prefix}_not_yet_due`] ?? 0)
    metrics[`${prefix}_overdue_pct`] = Math.round((od / t) * 1000) / 10
  }

  for (const base of Object.keys(metrics)) {
    if (base.startsWith('pm_') || base.startsWith('py_') || base.startsWith('delta_')) continue
    if (base.endsWith('_pct')) continue
    const cm = metrics[base] ?? 0
    const pm = metrics[`pm_${base}`]
    if (pm != null) metrics[`delta_pm_${base}`] = Math.round((cm - pm) * 100) / 100
    const py = metrics[`py_${base}`]
    if (py != null) metrics[`delta_py_${base}`] = Math.round((cm - py) * 100) / 100
  }
}

function buildLevel(
  rows: ReceivablesHierarchyBreakdownRow[],
  hierarchy: ReceivablesHierarchyDim[],
  depth: number,
  prefix: string,
  view: 'buckets' | 'due_overdue',
): ReceivablesHierarchyNode[] {
  if (!rows.length) return []
  if (depth >= hierarchy.length) return []

  const dim = hierarchy[depth]
  const isLeaf = depth === hierarchy.length - 1
  const groups = new Map<string, ReceivablesHierarchyBreakdownRow[]>()

  for (const r of rows) {
    const v = dimValue(r, dim)
    if (!groups.has(v)) groups.set(v, [])
    groups.get(v)!.push(r)
  }

  return [...groups.entries()]
    .map(([rawLabel, grp]) => {
      const key = `${prefix}|${dim}|${rawLabel}`
      const label = formatHierarchyDimLabel(dim, rawLabel)
      const metrics: Record<string, number> = {}
      for (const r of grp) addMetrics(metrics, numericFields(r))
      finalizePct(metrics, view)

      const children = isLeaf
        ? []
        : buildLevel(grp, hierarchy, depth + 1, key, view)

      return {
        key,
        label,
        rawLabel,
        level: depth,
        dim,
        metrics,
        children,
        leaf: isLeaf,
      }
    })
    .sort((a, b) => (b.metrics.total ?? 0) - (a.metrics.total ?? 0))
}

export function buildReceivablesHierarchy(
  rows: ReceivablesHierarchyBreakdownRow[],
  hierarchy: ReceivablesHierarchyDim[],
  view: 'buckets' | 'due_overdue',
): ReceivablesHierarchyNode[] {
  if (!hierarchy.length) return []
  return buildLevel(rows, hierarchy, 0, 'root', view)
}

export function flattenVisibleHierarchyNodes(
  nodes: ReceivablesHierarchyNode[],
  expanded: Set<string>,
): ReceivablesHierarchyNode[] {
  const out: ReceivablesHierarchyNode[] = []
  function walk(list: ReceivablesHierarchyNode[]) {
    for (const n of list) {
      out.push(n)
      if (n.children.length && expanded.has(n.key)) walk(n.children)
    }
  }
  walk(nodes)
  return out
}

/** Default: all entities expanded; first country under the top entity expanded. */
export function defaultExpandedHierarchyKeys(tree: ReceivablesHierarchyNode[]): Set<string> {
  const keys = new Set<string>()
  for (const entity of tree) {
    keys.add(entity.key)
  }
  const firstEntity = tree[0]
  if (firstEntity?.children[0]) {
    keys.add(firstEntity.children[0].key)
  }
  return keys
}
