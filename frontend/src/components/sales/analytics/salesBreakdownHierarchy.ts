import type { SalesBreakdownRow } from '../../../lib/api'
import { aggregateBreakdownRows, type BreakdownMetrics } from './salesBreakdownRender'
import type { BreakdownMiscConfig } from './salesBreakdownRegistry'
import { isCustomerBottomDim } from './salesBreakdownRegistry'

export const MISC_LABEL = 'Miscellaneous'

export type L3Leaf = { label: string; row: SalesBreakdownRow }
export type L2Group = {
  label: string
  leaves: L3Leaf[]
  /** Aggregated source rows when this L2 is a Miscellaneous bucket. */
  rollupRows?: SalesBreakdownRow[]
}
export type L1Group = { label: string; l2Groups: L2Group[]; leaves: L3Leaf[] }

function sortLeavesByCm(leaves: L3Leaf[]): L3Leaf[] {
  return [...leaves].sort((a, b) => (b.row.gs_cm ?? 0) - (a.row.gs_cm ?? 0))
}

function sortL2ByCm(groups: L2Group[]): L2Group[] {
  return [...groups].sort((a, b) => {
    const sa = aggregateBreakdownRows(rowsForL2(a)).gs_cm
    const sb = aggregateBreakdownRows(rowsForL2(b)).gs_cm
    return sb - sa
  })
}

function rowsForL2(g: L2Group): SalesBreakdownRow[] {
  if (g.rollupRows?.length) return g.rollupRows
  return g.leaves.map(l => l.row)
}

function syntheticLeaf(label: string, rows: SalesBreakdownRow[]): L3Leaf {
  const template = rows[0]
  const metrics = aggregateBreakdownRows(rows)
  return {
    label,
    row: {
      l1: template?.l1 ?? '',
      l2: template?.l2 ?? null,
      l3: label,
      ...metrics,
    },
  }
}

/** Keep top N leaves by CM; remainder → one Miscellaneous leaf. */
export function collapseLeavesToMisc(leaves: L3Leaf[], limit: number): L3Leaf[] {
  if (limit <= 0 || leaves.length <= limit) return sortLeavesByCm(leaves)
  const sorted = sortLeavesByCm(leaves)
  const top = sorted.slice(0, limit)
  const rest = sorted.slice(limit)
  if (!rest.length) return top
  return [...top, syntheticLeaf(MISC_LABEL, rest.map(l => l.row))]
}

function applyL3Misc(leaves: L3Leaf[], misc: BreakdownMiscConfig, dimBottom: string): L3Leaf[] {
  if (!misc.l3_enabled || !isCustomerBottomDim(dimBottom)) return sortLeavesByCm(leaves)
  return collapseLeavesToMisc(leaves, misc.l3_limit)
}

function applyL2Misc(groups: L2Group[], misc: BreakdownMiscConfig, dimBottom: string): L2Group[] {
  const withL3 = groups.map(g => ({
    ...g,
    leaves: applyL3Misc(g.leaves, misc, dimBottom),
  }))
  if (!misc.l2_enabled || misc.l2_limit <= 0) return withL3
  if (withL3.length <= misc.l2_limit) return withL3

  const sorted = sortL2ByCm(withL3)
  const top = sorted.slice(0, misc.l2_limit)
  const rest = sorted.slice(misc.l2_limit)
  const rollupRows = rest.flatMap(g => rowsForL2(g))
  if (!rollupRows.length) return top

  const miscGroup: L2Group = {
    label: MISC_LABEL,
    leaves: [],
    rollupRows,
  }
  return [...top, miscGroup]
}

export function buildBreakdownHierarchy(
  rows: SalesBreakdownRow[],
  hasMid: boolean,
  misc: BreakdownMiscConfig,
  dimBottom: string,
): L1Group[] {
  const l1Map = new Map<string, L1Group>()
  for (const row of rows) {
    const l1 = row.l1
    if (!l1Map.has(l1)) l1Map.set(l1, { label: l1, l2Groups: [], leaves: [] })
    const g1 = l1Map.get(l1)!
    if (hasMid && row.l2 && String(row.l2).trim()) {
      let g2 = g1.l2Groups.find(x => x.label === row.l2)
      if (!g2) {
        g2 = { label: String(row.l2), leaves: [] }
        g1.l2Groups.push(g2)
      }
      g2.leaves.push({ label: row.l3, row })
    } else {
      g1.leaves.push({ label: row.l3, row })
    }
  }

  const groups: L1Group[] = []
  for (const g1 of l1Map.values()) {
    g1.l2Groups = applyL2Misc(g1.l2Groups, misc, dimBottom)
    g1.leaves = applyL3Misc(g1.leaves, misc, dimBottom)
    groups.push(g1)
  }

  groups.sort((a, b) => {
    const allA = [...a.leaves.map(l => l.row), ...a.l2Groups.flatMap(g => rowsForL2(g))]
    const allB = [...b.leaves.map(l => l.row), ...b.l2Groups.flatMap(g => rowsForL2(g))]
    return aggregateBreakdownRows(allB).gs_cm - aggregateBreakdownRows(allA).gs_cm
  })
  return groups
}

export function l2GroupMetrics(g: L2Group): BreakdownMetrics {
  return aggregateBreakdownRows(rowsForL2(g))
}

export function l1GroupMetrics(g: L1Group): BreakdownMetrics {
  const all = [...g.leaves.map(l => l.row), ...g.l2Groups.flatMap(x => rowsForL2(x))]
  return aggregateBreakdownRows(all)
}
