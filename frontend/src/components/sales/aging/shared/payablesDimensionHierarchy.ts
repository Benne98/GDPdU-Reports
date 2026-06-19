import type { PayablesHierarchyBreakdownRow } from '../../../../lib/api'
import { countryDisplayName, countryGroupKey } from '../../../../lib/countryLabels'
import type { PayablesHierarchyDim } from './payablesDimensionBreakdownConfig'

export type PayablesHierarchyNode = {
  key: string
  label: string
  rawLabel: string
  level: number
  dim?: PayablesHierarchyDim
  metrics: Record<string, number>
  children: PayablesHierarchyNode[]
}

function dimValue(row: PayablesHierarchyBreakdownRow, dim: PayablesHierarchyDim): string {
  const raw = String(row.dims[dim] ?? '—').trim() || '—'
  return dim === 'country' ? countryGroupKey(raw) : raw
}
function formatHierarchyDimLabel(dim: PayablesHierarchyDim, raw: string): string { return dim === 'country' ? countryDisplayName(raw) : raw }
function numericFields(row: PayablesHierarchyBreakdownRow): Record<string, number> { const out: Record<string, number> = {}; for (const [k, v] of Object.entries(row)) if (k !== 'dims' && typeof v === 'number') out[k] = v; return out }
function addMetrics(target: Record<string, number>, source: Record<string, number>): void { for (const [k, v] of Object.entries(source)) { if (k.endsWith('_pct') || k.includes('overdue_pct')) continue; target[k] = (target[k] ?? 0) + v } }
function finalizePct(metrics: Record<string, number>, view: 'buckets' | 'due_overdue'): void {
  const total = metrics.total ?? 0
  if (total <= 0) { metrics.overdue_pct = 0; if (metrics.pm_total != null) metrics.pm_overdue_pct = metrics.pm_overdue ?? 0 ? (metrics.pm_overdue! / metrics.pm_total!) * 100 : 0; return }
  const overdue = view === 'due_overdue' ? metrics.overdue ?? 0 : total - (metrics.not_yet_due ?? 0)
  metrics.overdue_pct = Math.round((overdue / total) * 1000) / 10
  for (const prefix of ['pm', 'py'] as const) {
    const t = metrics[`${prefix}_total`]
    if (t == null || t <= 0) continue
    const od = view === 'due_overdue' ? metrics[`${prefix}_overdue`] ?? 0 : t - (metrics[`${prefix}_not_yet_due`] ?? 0)
    metrics[`${prefix}_overdue_pct`] = Math.round((od / t) * 1000) / 10
  }
  for (const base of Object.keys(metrics)) {
    if (base.startsWith('pm_') || base.startsWith('py_') || base.startsWith('delta_') || base.endsWith('_pct')) continue
    const cm = metrics[base] ?? 0
    const pm = metrics[`pm_${base}`]
    if (pm != null) metrics[`delta_pm_${base}`] = Math.round((cm - pm) * 100) / 100
    const py = metrics[`py_${base}`]
    if (py != null) metrics[`delta_py_${base}`] = Math.round((cm - py) * 100) / 100
  }
}

function buildLevel(rows: PayablesHierarchyBreakdownRow[], hierarchy: PayablesHierarchyDim[], depth: number, prefix: string, view: 'buckets' | 'due_overdue'): PayablesHierarchyNode[] {
  if (!rows.length || depth >= hierarchy.length) return []
  const dim = hierarchy[depth]
  const groups = new Map<string, PayablesHierarchyBreakdownRow[]>()
  for (const r of rows) { const v = dimValue(r, dim); if (!groups.has(v)) groups.set(v, []); groups.get(v)!.push(r) }
  return [...groups.entries()].map(([rawLabel, grp]) => {
    const key = `${prefix}|${dim}|${rawLabel}`
    const metrics: Record<string, number> = {}
    for (const r of grp) addMetrics(metrics, numericFields(r))
    finalizePct(metrics, view)
    return { key, label: formatHierarchyDimLabel(dim, rawLabel), rawLabel, level: depth, dim, metrics, children: depth === hierarchy.length - 1 ? [] : buildLevel(grp, hierarchy, depth + 1, key, view) }
  }).sort((a, b) => (b.metrics.total ?? 0) - (a.metrics.total ?? 0))
}

export function buildPayablesHierarchy(rows: PayablesHierarchyBreakdownRow[], hierarchy: PayablesHierarchyDim[], view: 'buckets' | 'due_overdue'): PayablesHierarchyNode[] {
  if (!hierarchy.length) return []
  return buildLevel(rows, hierarchy, 0, 'root', view)
}
export function flattenVisibleHierarchyNodes(nodes: PayablesHierarchyNode[], expanded: Set<string>): PayablesHierarchyNode[] { const out: PayablesHierarchyNode[] = []; const walk = (list: PayablesHierarchyNode[]) => { for (const n of list) { out.push(n); if (n.children.length && expanded.has(n.key)) walk(n.children) } }; walk(nodes); return out }
export function defaultExpandedHierarchyKeys(tree: PayablesHierarchyNode[]): Set<string> { const keys = new Set<string>(); for (const n of tree) keys.add(n.key); const first = tree[0]; if (first?.children[0]) keys.add(first.children[0].key); return keys }
