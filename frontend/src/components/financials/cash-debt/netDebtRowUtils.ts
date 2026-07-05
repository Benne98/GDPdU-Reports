import type { NetDebtRow } from '../../../lib/api'

export function accountGroupsForRow(row: NetDebtRow | null | undefined): string[] {
  if (!row) return []
  if (row.merged_account_groups?.length) return row.merged_account_groups
  if (row.account_number_group) return [row.account_number_group]
  return []
}

/** Account rows with GL backing — open the payment timeline chart. */
export function isDrillableRow(row: NetDebtRow): boolean {
  return row.row_kind === 'account' && accountGroupsForRow(row).length > 0
}

/** Section headers and cash sub-lines (on hand / at banks) expand/collapse. */
export function isExpandableRow(row: NetDebtRow & { hasChildren?: boolean }): boolean {
  return Boolean(row.hasChildren) && (row.row_kind === 'section_header' || row.row_kind === 'line')
}

/** Expand parent sections so a nested account row becomes visible. */
export function expandAncestorsForNetDebt(lineCode: string | undefined, expanded: Set<string>) {
  if (!lineCode) return
  const parts = lineCode.split('::')
  if (parts[0]) expanded.add(parts[0])
  if (parts[0] === 'cash' && parts.length >= 2) {
    expanded.add(`${parts[0]}::${parts[1]}`)
  }
}
