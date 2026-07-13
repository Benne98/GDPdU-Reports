/** Rows that stay collapsed on first load (BS only). */
const BS_KEEP_CLOSED = new Set(['Deferred tax assets', 'Prepaid expenses'])

/** CF cluster subtotals collapsed by default (children visible on expand). */
const CF_KEEP_CLOSED = new Set(['Δ Other operating items', '∆ Other operating items'])

type ExpandableRow = { id: string; label: string; children?: ExpandableRow[] }

/**
 * Default expanded row ids — same rules as FinancialStatementTable / MonthlyTable.
 * BS: depth 0–1 open; WC: TWC/OWC + mapping lines visible (depth 1); CF: depth 0; PL: none.
 *
 * @param maxDepthOverride - When provided, replaces the per-statement maxDepth computation.
 *   Useful for opt-in callers (e.g. PL group consolidation) that need non-default expansion.
 *   All existing callers omit this parameter and are unaffected.
 */
export function computeAutoExpandedIds(
  rows: ExpandableRow[] | undefined,
  statement: string | undefined,
  maxDepthOverride?: number,
): Set<string> {
  if (!rows?.length) return new Set<string>()
  const stmt = statement ?? 'pl'
  const maxDepth = maxDepthOverride !== undefined
    ? maxDepthOverride
    : stmt === 'bs' ? 2 : stmt === 'wc' ? 1 : stmt === 'cf' ? 1 : 2
  if (maxDepth === 0) return new Set<string>()

  function collectIds(rs: ExpandableRow[], depth: number): string[] {
    if (depth >= maxDepth) return []
    const ids: string[] = []
    for (const row of rs) {
      if ((row.children?.length ?? 0) > 0) {
        if (stmt === 'bs' && BS_KEEP_CLOSED.has(row.label)) continue
        if (stmt === 'cf' && CF_KEEP_CLOSED.has(row.label)) continue
        ids.push(row.id)
        ids.push(...collectIds(row.children ?? [], depth + 1))
      }
    }
    return ids
  }
  return new Set(collectIds(rows, 0))
}
